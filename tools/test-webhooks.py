#!/usr/bin/env python3
"""The panel telling a bot what happened.

Events are queued where they happen and sent by a worker, so what matters is:
each thing is said once (a sync every thirty seconds must not repeat a
warning), only about customers a bot can reach, only to bots that asked, with
a signature the bot can check, and a bot that is down gets it later rather
than never - without anybody's click waiting on it.
"""
import hashlib
import hmac
import http.server
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse
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
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)

store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ماهانه', 1, 30, ?, 200000, ?)", (10 * GB, panel.now()))
store.run("INSERT INTO users (telegram_id, first_name, created_at, status)"
          " VALUES (111, 'علی', ?, 'pending')", (panel.now(),))
store.run("INSERT INTO users (username, first_name, created_at, status)"
          " VALUES ('webonly', 'وب', ?, 'pending')", (panel.now(),))
store.run("INSERT INTO api_tokens (name, token_hash, created_at) VALUES ('ربات', 'h1', ?)",
          (panel.now(),))
store.run("INSERT INTO api_tokens (name, token_hash, created_at) VALUES ('بی‌خبر', 'h2', ?)",
          (panel.now(),))


class Rec:
    def __init__(self, path="/p/api"):
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


for name in ("action", "redirect", "send", "api_keys"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return r


def events(event=None):
    rows = store.q("SELECT * FROM webhook_outbox ORDER BY id")
    return [r for r in rows if event is None or r["event"] == event]


# A bot, listening on this machine.
received = []
answer = [200]


class Bot(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        received.append(({k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(answer[0])
        self.end_headers()

    def log_message(self, *a):
        pass


bot = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Bot)
threading.Thread(target=bot.serve_forever, daemon=True).start()
HOOK = "http://127.0.0.1:%d/hook" % bot.server_address[1]

print("setting the bot's address")
r = act("api-webhook-save", id="1", url="http://example.com/hook")
check("plain http to somewhere else is refused",
      "m=!" in urllib.parse.unquote(r.sent.get("Location", "")))
r = act("api-webhook-save", id="1", url="https://user:pw@example.com/hook")
check("so is an address with a password in it",
      "m=!" in urllib.parse.unquote(r.sent.get("Location", "")))
r = act("api-webhook-save", id="1", url=HOOK)
found = re.findall(r"whsec_[A-Za-z0-9_-]+", r.written.decode("utf-8"))
check("an address on this machine is taken, and its secret shown once", bool(found))
SECRET = found[0] if found else ""
row = store.one("SELECT * FROM api_tokens WHERE id = 1")
check("stored", row["webhook_url"] == HOOK and row["webhook_secret"] == SECRET)
check("the page shows it", HOOK in Rec().api_keys())

print("nothing is said about who a bot cannot reach")
panel.emit(store, store.one("SELECT * FROM users WHERE id = 2"), "quota.warning", {})
check("a web-only account produces nothing", events() == [])
panel.emit(store, store.one("SELECT * FROM users WHERE id = 1"), "quota.warning", {"x": 1})
check("a Telegram account produces one message, for the one bot that asked",
      len(events()) == 1 and events()[0]["token_id"] == 1)
store.run("DELETE FROM webhook_outbox")

print("things the operator does")
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at, plan_id)"
          " VALUES (1, 200000, 'card', 'pending', ?, 1)", (panel.now(),))
act("receipt-decide", id="1", to="approved")
ev = events("receipt.approved")
check("approving a receipt tells the bot", len(ev) == 1)
data = json.loads(ev[0]["payload"])["data"] if ev else {}
check("with the plan and a ready message",
      data.get("plan", {}).get("name") == "ماهانه" and "تأیید شد" in data.get("text", "")
      and "ماهانه" in data.get("text", ""), str(data))
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
          " VALUES (1, 0, 'card', 'pending', ?)", (panel.now(),))
act("receipt-decide", id="2", to="rejected")
check("rejecting one too", len(events("receipt.rejected")) == 1)
store.run("INSERT INTO tickets (user_id, subject, status, created_at, updated_at)"
          " VALUES (1, 'وصل نمی‌شه', 'open', ?, ?)", (panel.now(), panel.now()))
act("ticket-reply", id="1", body="DNS رو چک کنید")
ev = events("ticket.answered")
data = json.loads(ev[0]["payload"])["data"] if ev else {}
check("a ticket reply, with the words", data.get("body") == "DNS رو چک کنید"
      and "وصل نمی‌شه" in data.get("text", ""), str(data))
act("user-plan", id="1", plan_id="1")
check("a plan given by hand", len(events("plan.activated")) == 1)
store.run("DELETE FROM reserved_plans")         # given over a running plan: reserved

print("things a sync finds - each said once")
store.run("DELETE FROM webhook_outbox")
store.run("UPDATE users SET status = 'active', quota_bytes = ?, used_bytes = ?, warned = 0,"
          " expires_at = ? WHERE id = 1",
          (10 * GB, int(8.5 * GB),
           (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(timespec="seconds")))
panel.enforce_quotas(store)
panel.enforce_quotas(store)
ev = events("quota.warning")
check("80% is said once, not every sync", len(ev) == 1
      and json.loads(ev[0]["payload"])["data"]["percent"] == 80, str(len(ev)))
check("in Persian, with what is left", "گیگابایت" in json.loads(ev[0]["payload"])["data"]["text"])
store.run("UPDATE users SET used_bytes = ? WHERE id = 1", (int(9.6 * GB),))
panel.enforce_quotas(store)
panel.enforce_quotas(store)
ev = events("quota.warning")
check("then 95%, once", len(ev) == 2
      and json.loads(ev[1]["payload"])["data"]["percent"] == 95)
store.run("DELETE FROM webhook_outbox")
store.run("UPDATE users SET used_bytes = ?, warned = 0 WHERE id = 1", (int(9.7 * GB),))
panel.enforce_quotas(store)
ev = events("quota.warning")
check("past both lines in one go is one message, the higher", len(ev) == 1
      and json.loads(ev[0]["payload"])["data"]["percent"] == 95, str(len(ev)))
store.run("UPDATE users SET used_bytes = ? WHERE id = 1", (10 * GB,))
panel.enforce_quotas(store)
panel.enforce_quotas(store)
check("running out, once", len(events("quota.exhausted")) == 1)

store.run("DELETE FROM webhook_outbox")
store.run("UPDATE users SET status = 'active', used_bytes = 0, warned = 0, expires_at = ?"
          " WHERE id = 1", ((datetime.now(timezone.utc) + timedelta(days=2, hours=1))
                            .isoformat(timespec="seconds"),))
panel.enforce_quotas(store)
panel.enforce_quotas(store)
ev = events("plan.expiring")
check("the term ending soon, once", len(ev) == 1
      and json.loads(ev[0]["payload"])["data"]["days_left"] == 3, str(len(ev)))
store.run("UPDATE users SET expires_at = ? WHERE id = 1",
          ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),))
panel.enforce_quotas(store)
panel.enforce_quotas(store)
check("and ended, once", len(events("plan.expired")) == 1)

print("delivering")
store.run("DELETE FROM webhook_outbox")
panel.emit(store, store.one("SELECT * FROM users WHERE id = 1"), "quota.warning",
           {"percent": 80, "text": "سلام"})
sent = panel.deliver_due(store)
check("delivered to the bot", sent == 1 and len(received) == 1)
headers, body = received[-1] if received else ({}, b"{}")
msg = json.loads(body)
check("with the event, the customer and an id", msg.get("event") == "quota.warning"
      and msg.get("telegram_id") == 111 and msg.get("id") == events()[0]["id"], str(msg))
sig = dict(p.split("=", 1) for p in headers.get("x-masterdns-signature", "").split(","))
want = hmac.new(SECRET.encode(), ("%s." % sig.get("t", "")).encode() + body,
                hashlib.sha256).hexdigest()
check("signed, and the bot can check it", sig.get("v1") == want)
check("marked delivered", events()[0]["delivered_at"] is not None)
check("and not sent again", panel.deliver_due(store) == 0 and len(received) == 1)

print("a bot that is down")
answer[0] = 500
panel.emit(store, store.one("SELECT * FROM users WHERE id = 1"), "plan.expired", {})
panel.deliver_due(store)
row = events("plan.expired")[0]
check("a failure is kept, to try again later", row["delivered_at"] is None
      and row["attempts"] == 1 and row["last_error"] == "HTTP 500")
check("not straight away", panel.deliver_due(store) == 0 and len(received) == 2)
check("the gaps grow, and stop at an hour",
      [panel.webhook_backoff(n) for n in (1, 2, 3)] == [30, 60, 120]
      and panel.webhook_backoff(20) == 3600)
answer[0] = 200
store.run("UPDATE webhook_outbox SET next_at = ? WHERE id = ?", (panel.now(), row["id"]))
panel.deliver_due(store)
check("and it arrives once the bot is back",
      events("plan.expired")[0]["delivered_at"] is not None)

print("a bot the operator has stopped telling")
panel.emit(store, store.one("SELECT * FROM users WHERE id = 1"), "plan.expiring", {})
store.run("UPDATE api_tokens SET revoked_at = ? WHERE id = 1", (panel.now(),))
before = len(received)
panel.deliver_due(store)
check("nothing goes to a revoked key", len(received) == before
      and events("plan.expiring")[0]["attempts"] >= panel.WEBHOOK_MAX_TRIES)
store.run("UPDATE api_tokens SET revoked_at = NULL WHERE id = 1")

print("the test button, and the log on the page")
act("api-webhook-test", id="1")
panel.deliver_due(store)
check("a test message reaches the bot", json.loads(received[-1][1])["event"] == "ping")
page = Rec().api_keys()
check("the page lists what was sent and whether it arrived",
      "quota.warning" in page and "رسید" in page and "ping" in page)

print("tidying up")
old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
store.run("UPDATE webhook_outbox SET created_at = ?", (old,))
panel.prune_webhooks(store)
check("delivered and dead messages go after a week", events() == [])
r = act("api-webhook-save", id="1", url="")
check("the address can be taken away",
      store.one("SELECT webhook_url FROM api_tokens WHERE id = 1")["webhook_url"] is None)

bot.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
