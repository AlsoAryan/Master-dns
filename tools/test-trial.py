#!/usr/bin/env python3
"""The free trial: one tap, no receipt - once.

What has to hold: it needs a linked Telegram account; each Telegram account
and each panel account gets it once, and deleting the account or unlinking
Telegram does not make either new again; it never replaces a plan somebody is
running; and it is given, never sold - a receipt cannot name it.
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


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(HERE, "..", path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
sync = load("templates/smartdns-sync", "sync")
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
          " VALUES ('ماهانه', 1, 30, ?, 200000, ?)", (50 * GB, panel.now()))
store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, webhook_secret,"
          " created_at) VALUES ('b', ?, 'admin', 'https://b.example/h', 'whsec_x', ?)",
          (panel.token_hash("dd_key"), panel.now()))


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/plans"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "plans"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def user(uid):
    return store.one("SELECT * FROM users WHERE id = ?", (uid,))


print("making the trial")
ali = store.create_web_user("ali", "علی", "ali-password")
check("with none made, nobody is offered one", panel.trial_view(store, user(ali["id"])) is None)
act("plan-save", name="تست یک‌روزه", template_id="1", days="1", quota_gb="1", price="5000",
    trial="1")
trial = store.one("SELECT * FROM plans WHERE is_trial = 1")
check("a plan ticked as a trial", trial is not None)
check("costs nothing, whatever was typed", trial["price"] == 0)
where = act("plan-save", name="تست دوم", template_id="1", days="2", quota_gb="1", price="0",
            trial="1")
check("only one trial on offer at a time", "m=!" in where
      and store.one("SELECT count(*) c FROM plans WHERE is_trial = 1")["c"] == 1)
check("the plans page shows it ticked", "name='trial' value='1' checked" in Rec().plans())
check("it is not among what is for sale",
      [p["name"] for p in store.plans_for_sale()] == ["ماهانه"])
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")).decode()
res = panel.create_receipt(store, user(ali["id"]), {"plan_id": trial["id"],
                                                     "content_type": "image/png", "data": PNG})
check("and a receipt cannot name it", res["error"] == "plan_not_on_sale")

print("who may take it")
view = panel.trial_view(store, user(ali["id"]))
check("without Telegram: shown, with what to do", view and not view["available"]
      and "تلگرام" in view["why"])
check("and refused", panel.take_trial(store, user(ali["id"]))["error"] == "trial_telegram")
box = sync.trial_box({"trial": view})
check("the page says so, with no button", "تلگرام" in box and "action='/trial'" not in box)
store.run("UPDATE users SET telegram_id = 1001 WHERE id = ?", (ali["id"],))
view = panel.trial_view(store, user(ali["id"]))
check("with Telegram: offered", view["available"])
check("the page has the button", "action='/trial'" in sync.trial_box({"trial": view}))
res = panel.take_trial(store, user(ali["id"]))
u = user(ali["id"])
check("one tap puts it on", res["ok"] and u["status"] == "active" and u["plan_id"] == trial["id"]
      and u["quota_bytes"] == GB, str(res))
check("the operator's bot is told", any(json.loads(r["payload"])["event"] == "trial.started"
      for r in store.q("SELECT payload FROM webhook_outbox")))

print("once")
store.run("UPDATE users SET status = 'expired' WHERE id = ?", (ali["id"],))
check("the same account cannot take it again",
      panel.take_trial(store, user(ali["id"]))["error"] == "trial_used")
check("and is no longer offered it", panel.trial_view(store, user(ali["id"])) is None)
store.run("UPDATE users SET telegram_id = NULL WHERE id = ?", (ali["id"],))
second = store.create_web_user("ali2", "علی", "ali-password")
store.run("UPDATE users SET telegram_id = 1001 WHERE id = ?", (second["id"],))
check("nor can a new account with the same Telegram",
      panel.take_trial(store, user(second["id"]))["error"] == "trial_used")
store.run("DELETE FROM users WHERE id = ?", (ali["id"],))
check("not even once the first account is deleted",
      panel.take_trial(store, user(second["id"]))["error"] == "trial_used")
store.run("UPDATE users SET telegram_id = 1002 WHERE id = ?", (second["id"],))
check("another Telegram account is another person",
      panel.take_trial(store, user(second["id"]))["ok"])

print("never over a plan somebody is running")
sara = store.create_web_user("sara", "سارا", "sara-password")
store.run("UPDATE users SET telegram_id = 2001 WHERE id = ?", (sara["id"],))
admin.STORE.apply_plan(sara["id"], 1)
view = panel.trial_view(store, user(sara["id"]))
check("an account on a running plan is told why", view and not view["available"]
      and "پلن فعال" in view["why"])
check("and refused", panel.take_trial(store, user(sara["id"]))["error"] == "trial_running")
check("its plan untouched", user(sara["id"])["plan_id"] == 1)
store.run("UPDATE users SET status = 'expired' WHERE id = ?", (sara["id"],))
check("once it has run out, a paying customer may still have the trial",
      panel.take_trial(store, user(sara["id"]))["ok"])

print("the bot")
panel.BotAPI.store = store
server = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d/api/v1" % server.server_address[1]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Bearer dd_key")
    req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


call("POST", "/users", {"telegram_id": 3001, "name": "ربات"})
code, res = call("GET", "/users/3001")
check("the account shows the trial on offer", res["user"]["trial"]["available"])
code, res = call("POST", "/users/3001/trial")
check("the bot takes it", code == 200 and res["user"]["plan"]["id"] == trial["id"], str(res))
code, res = call("POST", "/users/3001/trial")
check("once: 409", code == 409 and res["error"] == "trial_used")
code, res = call("GET", "/admin/stats")
check("today's trials are counted", res["trials_today"] == 4 and "تست رایگان امروز: 4" in res["text"],
      str(res.get("trials_today")))
server.shutdown()

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
