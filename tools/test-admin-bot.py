#!/usr/bin/env python3
"""The operator's own bot: approving receipts and running accounts by API.

A key made with admin rights may do what the admin panel does with money and
accounts. What has to hold: only such a key gets in; a receipt approved from
the bot does exactly what one approved in the panel does - the two processes
each have their own copy of the rule, so this holds them to the same answers
- and is approved once; and the operator is told what waits for them: a new
receipt, a new ticket, a new customer, and the morning's numbers.
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
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []
GB = 1024 ** 3


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
panel.log = lambda *a: None
panel.print = lambda *a, **k: None
admin.log = lambda *a: None
tmp = tempfile.mkdtemp()

print("the two copies of applying a plan give the same answers")
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
CASES = {
    "a new customer": dict(status="pending", plan_id=None, expires_at=None,
                           quota_bytes=0, used_bytes=0),
    "the same plan, still running": dict(status="active", plan_id=1,
                                         expires_at=(NOW + timedelta(days=10)).isoformat(),
                                         quota_bytes=10 * GB, used_bytes=4 * GB),
    "the same plan, out of allowance": dict(status="over_quota", plan_id=1,
                                            expires_at=(NOW + timedelta(days=3)).isoformat(),
                                            quota_bytes=10 * GB, used_bytes=10 * GB),
    "the same plan, run out": dict(status="expired", plan_id=1,
                                   expires_at=(NOW - timedelta(days=1)).isoformat(),
                                   quota_bytes=10 * GB, used_bytes=1),
    "another plan": dict(status="active", plan_id=2,
                         expires_at=(NOW + timedelta(days=10)).isoformat(),
                         quota_bytes=0, used_bytes=5 * GB),
    "blocked": dict(status="suspended", plan_id=None, expires_at=None,
                    quota_bytes=0, used_bytes=0),
}
made = []
for label, start in CASES.items():
    for plan_id in (1, 2):
        rows, words = [], []
        for side in ("admin", "panel"):
            made.append(side)
            path = os.path.join(tmp, "parity-%d.db" % len(made))
            st = panel.Store(path)
            st.run("INSERT INTO templates (name, is_default, created_at) VALUES ('t', 1, ?)",
                   (panel.now(),))
            st.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, speed_kbps,"
                   " created_at) VALUES ('p1', 1, 30, ?, 1, 5000, ?)", (10 * GB, panel.now()))
            st.run("INSERT INTO plans (name, template_id, days, quota_bytes, price,"
                   " created_at) VALUES ('p2', 1, 90, 0, 1, ?)", (panel.now(),))
            st.run("INSERT INTO users (username, created_at, status, plan_id, expires_at,"
                   " quota_bytes, used_bytes) VALUES ('u', ?, ?, ?, ?, ?, ?)",
                   (panel.now(), start["status"], start["plan_id"], start["expires_at"],
                    start["quota_bytes"], start["used_bytes"]))
            if side == "admin":
                words.append(admin.Store(path).apply_plan(1, plan_id, NOW))
            else:
                words.append(panel.apply_plan(st, 1, plan_id, NOW))
            rows.append(dict(st.one("SELECT plan_id, template_id, quota_bytes, used_bytes,"
                                    " speed_kbps, expires_at, quota_mode, status, warned"
                                    " FROM users WHERE id = 1")))
        check("%s, buying plan %d" % (label, plan_id), rows[0] == rows[1] and
              words[0] == words[1], "%s / %s" % (rows, words))

# ---------------------------------------------------------------- the rest
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ماهانه', 1, 30, ?, 200000, ?)", (50 * GB, panel.now()))
for name, scope, hook in (("shop", "customer", "https://shop.example/h"),
                          ("boss", "admin", "https://boss.example/h"),
                          ("boss-quiet", "admin", None)):
    store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, webhook_secret,"
              " created_at) VALUES (?, ?, ?, ?, 'whsec_x', ?)",
              (name, panel.token_hash("dd_" + name), scope, hook, panel.now()))
SHOP, BOSS = 1, 2

panel.BotAPI.store = store
panel.BotAPI.relays = ("198.51.100.4",)
server = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d/api/v1" % server.server_address[1]


def call(method, path, body=None, key="dd_boss"):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def admin_events(event, token=BOSS):
    return [json.loads(r["payload"]) for r in store.q(
        "SELECT payload FROM webhook_outbox WHERE event = ? AND token_id = ?", (event, token))]


PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")).decode()

print("only a key with admin rights")
code, res = call("GET", "/admin/stats", key="dd_shop")
check("a customer key is refused", code == 403 and res["error"] == "admin_only")
code, res = call("GET", "/admin/stats")
check("an admin key gets in", code == 200 and res["ok"] and "text" in res, str(res))

print("the operator hears about new customers, receipts and tickets")
call("POST", "/users", {"telegram_id": 111, "name": "علی"}, key="dd_shop")
ev = admin_events("user.created")
check("a customer opened through the bot", len(ev) == 1 and "علی" in ev[0]["data"]["text"])
check("only the admin key with a webhook is told",
      not admin_events("user.created", SHOP) and not admin_events("user.created", 3))
check("addressed to no customer", ev[0]["telegram_id"] is None and ev[0]["audience"] == "admin")
call("POST", "/users/111/receipts", {"plan_id": 1, "content_type": "image/png", "data": PNG},
     key="dd_shop")
ev = admin_events("receipt.submitted")
check("a receipt, with its plan and amount", len(ev) == 1 and "200,000" in ev[0]["data"]["text"]
      and ev[0]["data"]["plan"]["name"] == "ماهانه", str(ev))
call("POST", "/users/111/tickets", {"subject": "کمک", "body": "وصل نمی‌شه"}, key="dd_shop")
check("a ticket", len(admin_events("ticket.opened")) == 1)
tid = store.one("SELECT id FROM tickets")["id"]
call("POST", "/users/111/tickets/%d/messages" % tid, {"body": "هنوز"}, key="dd_shop")
check("and a customer writing into one", len(admin_events("ticket.message")) == 1)

print("receipts")
code, res = call("GET", "/admin/receipts")
check("the pending ones, with who and what", code == 200 and len(res["receipts"]) == 1
      and res["receipts"][0]["user"]["label"] == "علی"
      and res["receipts"][0]["plan"]["name"] == "ماهانه", str(res))
rid = res["receipts"][0]["id"]
code, res = call("GET", "/admin/receipts/%d/image" % rid)
check("and the picture", code == 200 and res["data"] == PNG)
code, res = call("POST", "/admin/receipts/%d/approve" % rid)
check("approving puts the plan on", code == 200 and "فعال شد" in res["message"], str(res))
u = store.user_by_telegram(111)
check("the account is active on it", u["status"] == "active" and u["plan_id"] == 1
      and u["quota_bytes"] == 50 * GB)
check("the customer's bot is told", any(json.loads(r["payload"])["event"] == "receipt.approved"
      for r in store.q("SELECT payload FROM webhook_outbox WHERE token_id = ?", (SHOP,))))
code, res = call("POST", "/admin/receipts/%d/approve" % rid)
check("approving twice: 409, nothing twice", code == 409
      and store.user_by_telegram(111)["expires_at"] == u["expires_at"])
code, res = call("GET", "/admin/receipts/%d/image" % rid)
check("the picture is gone after the decision, as in the panel", code == 404)
call("POST", "/users/111/receipts", {"plan_id": 1, "content_type": "image/png", "data": PNG},
     key="dd_shop")
rid2 = store.one("SELECT max(id) m FROM transactions")["m"]
code, res = call("POST", "/admin/receipts/%d/reject" % rid2)
check("rejecting", code == 200 and store.one("SELECT status FROM transactions WHERE id = ?",
                                             (rid2,))["status"] == "rejected")
code, res = call("GET", "/admin/receipts?status=all")
check("and the history", len(res["receipts"]) == 2)

print("accounts")
store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, '5.6.7.8', ?)",
          (u["id"], panel.now()))
for q in ("علی", "111", "5.6.7.8"):
    code, res = call("GET", "/admin/users?q=" + urllib.request.quote(q))
    check("found by %s" % q, code == 200 and [x["id"] for x in res["users"]] == [u["id"]],
          str(res))
code, res = call("POST", "/admin/users/%d/status" % u["id"], {"status": "suspended"})
check("blocking", code == 200 and store.user_by_telegram(111)["status"] == "suspended")
code, res = call("POST", "/admin/users/%d/status" % u["id"], {"status": "active"})
check("and back", store.user_by_telegram(111)["status"] == "active")
code, res = call("POST", "/admin/users/%d/status" % u["id"], {"status": "deleted"})
check("nothing else", code == 400)
store.run("INSERT INTO users (username, first_name, created_at, status)"
          " VALUES ('cash', 'نقدی', ?, 'pending')", (panel.now(),))
cash = store.user_by_username("cash")
code, res = call("POST", "/admin/users/%d/plan" % cash["id"], {"plan_id": 1})
check("giving a plan by hand", code == 200 and res["user"]["plan"]["name"] == "ماهانه")
code, res = call("POST", "/admin/users/%d/plan" % cash["id"], {"plan_id": 99})
check("a plan that is not there: 404", code == 404)
code, res = call("GET", "/admin/users/9999")
check("a customer who is not there: 404", code == 404)

print("tickets")
code, res = call("GET", "/admin/tickets")
check("the ones waiting", code == 200 and [t["id"] for t in res["tickets"]] == [tid])
code, res = call("GET", "/admin/tickets/%d" % tid)
check("one, with its messages and whose it is", len(res["ticket"]["messages"]) == 2
      and res["ticket"]["user"]["label"] == "علی")
code, res = call("POST", "/admin/tickets/%d/reply" % tid, {"body": "DNS رو چک کنید"})
check("answering", code == 200 and res["ticket"]["status"] == "answered"
      and res["ticket"]["messages"][-1]["from"] == "admin")
check("the customer's bot is told", any(json.loads(r["payload"])["event"] == "ticket.answered"
      for r in store.q("SELECT payload FROM webhook_outbox WHERE token_id = ?", (SHOP,))))
code, res = call("POST", "/admin/tickets/%d/close" % tid)
check("closing", code == 200 and store.one("SELECT status FROM tickets WHERE id = ?",
                                           (tid,))["status"] == "closed")

print("the morning's numbers")
before = datetime.now(timezone.utc).replace(hour=5, minute=0, second=0, microsecond=0)
check("not before its hour", not panel.daily_report_due(store, before))
after = before.replace(hour=6)
check("after it, once", panel.daily_report_due(store, after)
      and not panel.daily_report_due(store, after + timedelta(hours=3)))
ev = admin_events("report.daily")
check("with the day's income and what waits", len(ev) == 1 and ev[0]["data"]["users"] == 2
      and "200,000" in ev[0]["data"]["text"], str(ev[0]["data"] if ev else ""))
check("again the next day", panel.daily_report_due(store, after + timedelta(days=1)))

print("the admin panel")


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/api"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "api_keys"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)
r = Rec()
r.action("api-key-new", {"name": ["ربات مدیر"], "allow_ips": [""], "admin": ["1"]})
r = Rec()
r.action("api-key-new", {"name": ["ربات فروش"], "allow_ips": [""]})
check("a key made with the tick has admin rights, one without has not",
      store.one("SELECT scope FROM api_tokens WHERE name = 'ربات مدیر'")["scope"] == "admin"
      and store.one("SELECT scope FROM api_tokens WHERE name = 'ربات فروش'")["scope"]
      == "customer")
check("the page says which is which", "ادمین" in Rec().api_keys())

server.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
