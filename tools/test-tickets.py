#!/usr/bin/env python3
"""Support tickets: the customer's panel, the admin panel and the bot.

One set of rules behind three doors. What has to hold: a customer reads and
writes only their own tickets, whose turn it is is always right (a reply from
here hands it back, a customer writing into a closed ticket opens it again),
one customer cannot flood the queue, and pictures are pictures.
"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(HERE, "..", "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("smartdns-panel", "panel")
admin = load("smartdns-admin", "admin")
sync = load("smartdns-sync", "sync")
panel.log = lambda *a: None
panel.print = lambda *a, **k: None
admin.log = lambda *a: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)

store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
for name, tg in (("ali", 111), ("sara", 222)):
    store.run("INSERT INTO users (username, first_name, telegram_id, created_at, status)"
              " VALUES (?, ?, ?, ?, 'active')", (name, name, tg, panel.now()))
ali, sara = store.open_session(1), store.open_session(2)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")
PNG64 = base64.b64encode(PNG).decode()


class Api:
    relays = ("198.51.100.1",)

    def __init__(self, store):
        self.store = store


for name in ("do_user_ticket", "do_user_info", "_session_user", "_must_choose"):
    setattr(Api, name, getattr(panel.API, name))
api = Api(store)


def t(path, session, **body):
    return api.do_user_ticket(path, dict(body, session=session))


print("opening a ticket from the web panel")
check("a subject is needed", t("/user-ticket-new", ali, body="سلام")["error"]
      == "subject_required")
check("and a message", t("/user-ticket-new", ali, subject="وصل نمی‌شه")["error"]
      == "body_required")
check("a picture has to be a picture", t("/user-ticket-new", ali, subject="x", body="y",
      image_type="text/html", image_data=PNG64)["error"] == "bad_type")
res = t("/user-ticket-new", ali, subject="بازی وصل نمی‌شه", body="خط اول\nخط دوم",
        image_type="image/png", image_data=PNG64)
check("a ticket is opened", res["ok"] and res["ticket_id"], str(res))
tid = res["ticket_id"]
got = t("/user-ticket", ali, id=tid)
check("it waits for the operator", got["ticket"]["status"] == "open")
check("line breaks survive", got["ticket"]["messages"][0]["body"] == "خط اول\nخط دوم")
check("the picture is kept", got["ticket"]["messages"][0]["has_image"])
mid = got["ticket"]["messages"][0]["id"]
pic = t("/user-ticket-image", ali, message_id=mid)
check("and handed back to its owner", pic["ok"] and base64.b64decode(pic["image_data"]) == PNG)

print("nobody else's")
check("another customer cannot read it", not t("/user-ticket", sara, id=tid)["ok"])
check("nor see its picture", not t("/user-ticket-image", sara, message_id=mid)["ok"])
check("nor write into it", not t("/user-ticket-reply", sara, id=tid, body="x")["ok"])
check("nor close it", not t("/user-ticket-close", sara, id=tid)["ok"])
check("nor list it", t("/user-tickets", sara)["tickets"] == [])
check("no session, nothing", not api.do_user_ticket("/user-tickets", {"session": "x"})["ok"])

print("the admin panel")


class Rec:
    def __init__(self, path="/p/tickets"):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = path
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "tickets", "ticket_page", "send_ticket_image"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)

check("the menu counts it", admin.tickets_waiting() == 1)
page = Rec().tickets()
check("the list shows it waiting", "بازی وصل نمی‌شه" in page and "منتظر جواب شما" in page)
page = Rec("/p/tickets?t=%d" % tid).tickets()
check("the ticket page shows the thread", "خط اول\nخط دوم" in page
      and "/p/ticket-image/%d" % mid in page, page[:400])
r = Rec()
r.send_ticket_image(str(mid))
check("and the picture", r.written == PNG and r.sent.get("Content-Type") == "image/png")

r = Rec()
r.action("ticket-reply", {"id": [str(tid)], "body": ["سلام، دی‌ان‌اس رو چک کنید"]})
row = store.one("SELECT * FROM tickets WHERE id = ?", (tid,))
check("a reply hands the turn to the customer", row["status"] == "answered")
check("the menu no longer counts it", admin.tickets_waiting() == 0)
last = store.one("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id DESC", (tid,))
check("it is the operator's", last["from_admin"] == 1)
check("the customer's page shows a reply waiting",
      api.do_user_info({"session": ali})["tickets_answered"] == 1)

# A picture from the admin, as the browser sends it.
boundary = "XyZ"
raw = ("--{b}\r\nContent-Disposition: form-data; name=\"id\"\r\n\r\n{t}\r\n"
       "--{b}\r\nContent-Disposition: form-data; name=\"body\"\r\n\r\nعکس راهنما\r\n"
       "--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\n"
       "Content-Type: image/png\r\n\r\n").format(b=boundary, t=tid).encode() + PNG + \
      ("\r\n--%s--\r\n" % boundary).encode()
r = Rec()
r.raw_body = raw
r.headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
r.action("ticket-reply", {})
last = store.one("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id DESC", (tid,))
check("the operator can send a picture", last["body"] == "عکس راهنما"
      and bytes(last["image_blob"]) == PNG and last["image_type"] == "image/png",
      urllib.parse.unquote(r.sent.get("Location", "")))
r = Rec()
r.raw_body = raw.replace(PNG, b"<script>alert(1)</script>")
r.headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
r.action("ticket-reply", {})
check("but not something pretending to be one",
      "m=!" in urllib.parse.unquote(r.sent.get("Location", "")))

print("whose turn it is")
t("/user-ticket-reply", ali, id=tid, body="درست شد ممنون")
check("the customer writing back hands it to the operator",
      store.one("SELECT status FROM tickets WHERE id = ?", (tid,))["status"] == "open")
check("closing", t("/user-ticket-close", ali, id=tid)["ok"]
      and store.one("SELECT status FROM tickets WHERE id = ?", (tid,))["status"] == "closed")
t("/user-ticket-reply", ali, id=tid, body="باز هم قطع شد")
check("writing into a closed ticket opens it again",
      store.one("SELECT status FROM tickets WHERE id = ?", (tid,))["status"] == "open")
r = Rec()
r.action("ticket-status", {"id": [str(tid)], "to": ["closed"]})
check("the operator can close it too",
      store.one("SELECT status FROM tickets WHERE id = ?", (tid,))["status"] == "closed")

print("one customer cannot flood the queue")
for i in range(3):
    t("/user-ticket-new", sara, subject="s%d" % i, body="b")
res = t("/user-ticket-new", sara, subject="s4", body="b")
check("a fourth open ticket is refused", res["error"] == "too_many_tickets", str(res))
panel.THROTTLE.clear("ticket:2")
sid = t("/user-tickets", sara)["tickets"][0]["id"]
results = [t("/user-ticket-reply", sara, id=sid, body="m%d" % i)["ok"] for i in range(21)]
check("and past twenty messages an hour, they wait", all(results[:17])
      and not results[-1], str(results))

print("pictures of closed tickets go after a while")
old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
store.run("UPDATE tickets SET updated_at = ? WHERE id = ?", (old, tid))
panel.prune_ticket_images(store)
check("dropped, and the words kept",
      store.one("SELECT count(*) c FROM ticket_messages WHERE ticket_id = ?"
                " AND image_blob IS NOT NULL", (tid,))["c"] == 0
      and store.one("SELECT count(*) c FROM ticket_messages WHERE ticket_id = ?",
                    (tid,))["c"] >= 5)

print("the customer's pages on the relay")


class Page:
    def __init__(self, path, session):
        self.path = path
        self._session = session
        self.out = None
        self.headers = {}

    def session(self):
        return self._session

    def ask_panel(self, endpoint, payload):
        return api.do_user_ticket(endpoint, dict(payload, session=self._session))

    def send_html(self, body, code=200, headers=None):
        self.out = (code, body)

    def send(self, body, code, headers):
        self.out = (code, body, headers)

    def redirect(self, where, message="", bad=False):
        self.out = (303, where, message, bad)

    def banner(self):
        return ""


for name in ("tickets_page", "ticket_page", "ticket_image", "ticket_id"):
    setattr(Page, name, getattr(sync.UserPanel, name))
sync.brand = lambda: "test"
store.run("INSERT INTO ticket_messages (ticket_id, from_admin, body, image_blob,"
          " image_type, created_at) VALUES (?, 1, 'x', ?, 'image/png', ?)",
          (tid, PNG, panel.now()))
pmid = store.one("SELECT max(id) m FROM ticket_messages")["m"]
p = Page("/tickets", ali)
p.tickets_page()
check("the list", p.out[0] == 200 and "بازی وصل نمی‌شه" in p.out[1]
      and "/ticket?id=%d" % tid in p.out[1])
check("with a form for a new one", "action='/ticket-new'" in p.out[1])
p = Page("/ticket?id=%d" % tid, ali)
p.ticket_page()
check("a ticket's thread", p.out[0] == 200 and "درست شد ممنون" in p.out[1]
      and "پشتیبانی" in p.out[1])
check("a closed one says writing opens it again", "دوباره باز می‌شود" in p.out[1])
p = Page("/ticket?id=%d" % tid, sara)
p.ticket_page()
check("somebody else's is not shown", p.out[0] == 303)
p = Page("/ticket-image?img=%d" % pmid, ali)
p.ticket_image()
check("a picture is served as what it is", p.out[0] == 200 and p.out[1] == PNG
      and p.out[2]["Content-Type"] == "image/png")
check("the account page links to support",
      "/tickets" in sync.support_box({"tickets_answered": 2})
      and "2 جواب تازه" in sync.support_box({"tickets_answered": 2}))

print("the bot")
store.run("INSERT INTO api_tokens (name, token_hash, created_at) VALUES ('b', ?, ?)",
          (panel.token_hash("dd_testkey"), panel.now()))
panel.BotAPI.store = store
server = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d/api/v1" % server.server_address[1]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Bearer dd_testkey")
    req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


panel.THROTTLE.clear("ticket:1")
code, res = call("POST", "/users/111/tickets", {"subject": "از ربات", "body": "سلام",
                                                 "image_type": "image/png",
                                                 "image_data": PNG64})
check("a ticket from the bot: 201", code == 201 and res["ticket"]["subject"] == "از ربات",
      str(res))
bt = res["ticket"]["id"]
code, res = call("GET", "/users/111/tickets")
check("the bot lists them", code == 200 and any(x["id"] == bt for x in res["tickets"]))
code, res = call("POST", "/users/111/tickets/%d/messages" % bt, {"body": "هنوز؟"})
check("and writes into one", code == 201 and len(res["ticket"]["messages"]) == 2)
bm = res["ticket"]["messages"][0]["id"]
code, res = call("GET", "/users/111/tickets/%d/messages/%d/image" % (bt, bm))
check("and fetches its picture", code == 200
      and base64.b64decode(res["image_data"]) == PNG)
code, res = call("GET", "/users/111/tickets/%d/messages/%d/image" % (tid, bm))
check("only under its own ticket", code == 404)
code, res = call("GET", "/users/222/tickets/%d" % bt)
check("somebody else's ticket: 404", code == 404 and res["error"] == "ticket_not_found")
code, res = call("POST", "/users/111/tickets/%d/close" % bt)
check("and closes it", code == 200
      and store.one("SELECT status FROM tickets WHERE id = ?", (bt,))["status"] == "closed")
code, res = call("GET", "/users/111")
check("the account says how many replies wait", "tickets_answered" in res["user"])

print("a picture alone is a message")
store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, created_at)"
          " VALUES ('a', ?, 'admin', 'https://bot.example/hook', ?)",
          (panel.token_hash("dd_adminkey"), panel.now()))
store.run("UPDATE api_tokens SET webhook_url = 'https://bot.example/hook' WHERE name = 'b'")
panel.THROTTLE.clear("ticket:1")
code, res = call("POST", "/users/111/tickets", {"subject": "خطا", "body": ""})
check("neither words nor a picture: refused", code == 400 and res["error"] == "body_required"
      and "عکس" in res["message"])
code, res = call("POST", "/users/111/tickets", {"subject": "خطا", "image_type": "image/png",
                                                 "image_data": PNG64})
check("a screenshot with no words opens a ticket", code == 201, str(res))
pt = res["ticket"]["id"]
pm = res["ticket"]["messages"][0]["id"]
code, res = call("POST", "/users/111/tickets/%d/messages" % pt,
                 {"image_type": "image/png", "image_data": PNG64})
check("and is written into one", code == 201 and len(res["ticket"]["messages"]) == 2)
sent = [json.loads(r["payload"]) for r in store.q(
    "SELECT payload FROM webhook_outbox WHERE event IN ('ticket.opened', 'ticket.message')"
    " ORDER BY id DESC LIMIT 2")]
check("the operator's bot is told which message has the picture",
      len(sent) == 2 and all(e["data"]["has_image"] and e["data"]["message_id"] for e in sent)
      and sent[-1]["data"]["message_id"] == pm and "🖼" in sent[-1]["data"]["text"], str(sent))


def admin_call(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization", "Bearer dd_adminkey")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


code, res = admin_call("/admin/tickets/%d/messages/%d/image" % (pt, pm))
check("and fetches it", code == 200 and base64.b64decode(res["data"]) == PNG
      and res["content_type"] == "image/png")
code, res = admin_call("/admin/tickets/%d/messages/%d/image" % (tid, pm))
check("  only under its own ticket", code == 404)
code, res = call("GET", "/admin/tickets/%d/messages/%d/image" % (pt, pm))
check("  and with admin rights only", code == 403)
res = panel.admin_ticket_reply(store, pt, {"image_type": "image/png", "image_data": PNG64})
told = json.loads(store.one("SELECT payload FROM webhook_outbox WHERE event = 'ticket.answered'"
                            " ORDER BY id DESC LIMIT 1")["payload"])["data"]
check("the operator may answer with a picture alone, and the customer's bot is told which",
      res["ok"] and told["has_image"] and told["message_id"]
      and call("GET", "/users/111/tickets/%d/messages/%d/image"
               % (pt, told["message_id"]))[0] == 200)
check("  but not with nothing", panel.admin_ticket_reply(store, pt, {})["error"]
      == "body_required")
server.shutdown()

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
