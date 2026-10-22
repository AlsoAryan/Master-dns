#!/usr/bin/env python3
"""A forgotten password, fixed by the customer alone through Telegram.

Two steps. Linking: the web panel shows a code, the customer sends it to the
bot, and the bot tells the panel which Telegram account that was. Resetting:
six digits go through the bot to that Telegram account, and with them the
customer chooses a new password.

What has to hold: a code works once, for one account and one purpose, and not
after it runs out; guessing the six digits is cut off after a few tries; the
reset page says the same thing whether a name exists or not; and one Telegram
account cannot be linked to two panel accounts.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
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
ali = store.create_web_user("ali", "علی", "old-password-1")
sara = store.create_web_user("sara", "سارا", "sara-password")
store.run("INSERT INTO users (telegram_id, first_name, created_at) VALUES (555, 'از ربات', ?)",
          (panel.now(),))
ali_session = store.open_session(ali["id"])
ali_other = store.open_session(ali["id"])


def user(uid):
    return store.one("SELECT * FROM users WHERE id = ?", (uid,))


def outbox(event):
    return [json.loads(r["payload"]) for r in
            store.q("SELECT payload FROM webhook_outbox WHERE event = ?", (event,))]


print("without a bot there is nothing to link to")
res = panel.link_code(store, user(ali["id"]), "old-password-1")
check("no code is handed out", res["error"] == "no_bot")
check("and the account page does not offer it",
      sync.telegram_box({"telegram_ready": False}) == "")

store.run("INSERT INTO api_tokens (name, token_hash, webhook_url, webhook_secret, created_at)"
          " VALUES ('ربات', ?, 'https://bot.example.com/h', 'whsec_x', ?)",
          (panel.token_hash("dd_key"), panel.now()))

print("linking")
box = sync.telegram_box({"telegram_ready": True})
check("the account page offers it once a bot listens, asking for the password",
      "/telegram-link" in box and "name='password'" in box)
res = panel.link_code(store, user(ali["id"]), "wrong-password")
check("a code needs the account's password, not only a session",
      res["error"] == "wrong_password")
res = panel.link_code(store, user(ali["id"]), "old-password-1")
check("a code of eight easy letters", res["ok"] and re.match(r"^[a-z2-9]{8}$", res["code"]),
      str(res))
code = res["code"]
check("only its hash is kept", code not in json.dumps(
    [dict(r) for r in store.q("SELECT * FROM user_codes")]))
page = sync.link_code_page(res)
check("the page shows it", code in page)
store.set_setting("bot_link", "https://t.me/MyShopBot")
page = sync.link_code_page(panel.link_code(store, user(ali["id"]), "old-password-1"))
code = re.findall(r"<div class='big'>([a-z2-9]{8})</div>", page)[0]
check("with a bot address set, one tap opens the bot with the code",
      "https://t.me/MyShopBot?start=link_%s" % code in page)
check("a new code replaces the old one",
      store.one("SELECT count(*) c FROM user_codes WHERE kind = 'link'")["c"] == 1)

print("the bot, over the API")
panel.BotAPI.store = store
server = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d/api/v1" % server.server_address[1]


def call(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer dd_key")
    req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


code_now, res = call("/link", {"telegram_id": 777, "code": "zzzzzzzz"})
check("a wrong code is refused", code_now == 400 and res["error"] == "bad_code")
code_now, res = call("/link", {"telegram_id": 555, "code": code})
check("a Telegram account already on another panel account: 409",
      code_now == 409 and res["error"] == "telegram_in_use")
code_now, res = call("/link", {"telegram_id": 777, "code": code.upper()})
check("the right code links, whatever the case it was typed in",
      code_now == 200 and res["user"]["telegram_id"] == 777 and user(ali["id"])["telegram_id"] == 777,
      str(res))
code_now, res = call("/link", {"telegram_id": 777, "code": code})
check("and works only once", code_now == 400)
check("a linked account is told so",
      "وصل است" in sync.telegram_box({"telegram_linked": True}))
check("and asking for another code says it is linked already",
      panel.link_code(store, user(ali["id"]), "old-password-1")["error"] == "already_linked")
c2 = panel.link_code(store, user(sara["id"]), "sara-password")["code"]
store.run("UPDATE user_codes SET expires_at = ? WHERE user_id = ?",
          ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), sara["id"]))
code_now, res = call("/link", {"telegram_id": 888, "code": c2})
check("a code that ran out is refused", res["error"] == "code_expired"
      and user(sara["id"])["telegram_id"] is None)
server.shutdown()

print("asking for a reset")
same = panel.reset_request(store, "nobody-here", "1.2.3.4")
check("an unknown name gets the ordinary answer", same["ok"] and outbox("password.reset_code") == [])
res = panel.reset_request(store, "sara", "1.2.3.4")
check("so does a name with no Telegram, and nothing is sent",
      res["message"] == same["message"] and outbox("password.reset_code") == [])
res = panel.reset_request(store, "ALI", "1.2.3.4")
sent = outbox("password.reset_code")
check("a linked account: the same answer, and six digits go to the bot",
      res["message"] == same["message"] and len(sent) == 1
      and re.match(r"^\d{6}$", sent[0]["data"]["code"]), str(sent))
check("addressed to that Telegram account", sent and sent[0]["telegram_id"] == 777)
check("with a message ready to pass on", sent and sent[0]["data"]["code"] in sent[0]["data"]["text"])
six = sent[0]["data"]["code"]

print("using it")
res = panel.reset_confirm(store, "ali", six, "short", "1.2.3.4")
check("a short password is refused", res["error"] == "weak_password")
wrong = "%06d" % ((int(six) + 1) % 10 ** 6)
res = panel.reset_confirm(store, "ali", wrong, "brand-new-pass", "1.2.3.4")
check("a wrong code is refused", res["error"] == "bad_code")
check("and counted", store.one("SELECT attempts FROM user_codes WHERE user_id = ?"
                               " AND kind = 'reset'", (ali["id"],))["attempts"] == 1)
res = panel.reset_confirm(store, "ali", six, "brand-new-pass", "1.2.3.4")
check("the right code sets the password and signs in", res["ok"] and res.get("session"))
check("the new password works", panel.check_password(user(ali["id"]), "brand-new-pass"))
check("the old one does not", not panel.check_password(user(ali["id"]), "old-password-1"))
check("everybody else signed in is out", not store.one(
    "SELECT 1 FROM panel_sessions WHERE token IN (?, ?)", (ali_session, ali_other)))
res2 = panel.reset_confirm(store, "ali", six, "another-pass-1", "1.2.3.4")
check("the code does not work twice", res2["error"] == "bad_code")

print("guessing")
panel.THROTTLE.clear("reset:%d" % ali["id"])
panel.reset_request(store, "ali", "5.6.7.8")
six = outbox("password.reset_code")[-1]["data"]["code"]
for i in range(panel.RESET_TRIES):
    panel.reset_confirm(store, "ali", "%06d" % ((int(six) + 1 + i) % 10 ** 6),
                        "guessed-pass", "9.9.9.%d" % i)
res = panel.reset_confirm(store, "ali", six, "guessed-pass", "9.9.9.99")
check("after five wrong tries even the right code is dead", res["error"] == "bad_code"
      and not panel.check_password(user(ali["id"]), "guessed-pass"))
panel.THROTTLE.clear("reset:%d" % ali["id"])
panel.reset_request(store, "ali", "5.6.7.9")
six = outbox("password.reset_code")[-1]["data"]["code"]
store.run("UPDATE user_codes SET expires_at = ? WHERE user_id = ? AND kind = 'reset'",
          ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), ali["id"]))
res = panel.reset_confirm(store, "ali", six, "late-pass-12", "9.9.9.100")
check("a code past its ten minutes is dead", res["error"] == "bad_code")
panel.THROTTLE.clear("reset:%d" % ali["id"])
before = len(outbox("password.reset_code"))
for i in range(5):
    panel.reset_request(store, "ali", "7.7.7.%d" % i)
check("no more than three codes an hour for one account",
      len(outbox("password.reset_code")) - before == 3)
panel.THROTTLE.clear("resetip:1.1.1.1")
for i in range(10):
    panel.reset_request(store, "x%d" % i, "1.1.1.1")
check("and one address cannot ask without end",
      panel.reset_request(store, "y", "1.1.1.1")["error"] == "slow_down")

print("taking Telegram off again")
res = panel.unlink_telegram(store, user(ali["id"]), "not-it")
check("the customer needs their password for it", res["error"] == "wrong_password"
      and user(ali["id"])["telegram_id"] == 777)
res = panel.unlink_telegram(store, user(ali["id"]), "brand-new-pass")
check("with it, Telegram comes off", res["ok"] and user(ali["id"])["telegram_id"] is None)
check("and so does any code waiting for it",
      not store.one("SELECT 1 FROM user_codes WHERE user_id = ?", (ali["id"],)))
check("a reset no longer goes anywhere",
      panel.reset_request(store, "ali", "8.8.8.1")["ok"]
      and store.one("SELECT 1 FROM user_codes WHERE user_id = ?", (ali["id"],)) is None)
bot_only = store.user_by_telegram(555)
check("an account opened through the bot keeps its Telegram - it has no other way in",
      panel.unlink_telegram(store, bot_only, "")["error"] == "telegram_only")
check("the account page offers unlinking when linked",
      "/telegram-unlink" in sync.telegram_box({"telegram_linked": True}))

print("the pages")
check("the sign-in page offers it", "/forgot" in sync.forgot_line())
check("the first page asks for the name", "action='/forgot'" in sync.forgot_page())
page = sync.forgot_code_page("ali'><script>")
check("the second page carries the name, escaped",
      "<script>" not in page and "action='/forgot-code'" in page)
check("Persian digits in the code are understood",
      "۱۲۳۴۵۶".translate(sync.FA_DIGITS) == "123456")

print("Telegram required for buying")
import base64 as _b64
PNG64 = _b64.b64encode(_b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")).decode()
slip = {"content_type": "image/png", "data": PNG64}
nolink = store.create_web_user("nolink", "بی‌تلگرام", "nolink-password")
nolink = user(nolink["id"])
check("while it is off, anybody may pay", panel.create_receipt(store, nolink, slip)["ok"])
store.set_setting("require_telegram", "1")
res = panel.create_receipt(store, nolink, slip)
check("once on, an account without Telegram may not", res["error"] == "telegram_required")
box = sync.receipt_box({"telegram_required": True, "plans": []})
check("and its page offers the link instead of the form",
      "/telegram-link" in box and "/receipt" not in box and " open" in box)
store.run("UPDATE users SET telegram_id = 4242 WHERE id = ?", (nolink["id"],))
check("a linked account may", panel.create_receipt(store, user(nolink["id"]), slip)["ok"])
store.run("UPDATE users SET telegram_id = NULL WHERE id = ?", (nolink["id"],))
store.run("UPDATE api_tokens SET webhook_url = NULL")
check("with no bot to link to, the rule waits rather than lock everybody out",
      panel.create_receipt(store, user(nolink["id"]), slip)["ok"])
store.run("UPDATE api_tokens SET webhook_url = 'https://bot.example.com/h'")
store.set_setting("require_telegram", "")

print("the bot's address, in the admin panel")


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
for name in ("users",):
    setattr(Rec, name, getattr(admin.Admin, name))
store.run("UPDATE users SET telegram_id = 999 WHERE id = ?", (sara["id"],))
page = Rec().users()
check("the users page offers to unlink a web account",
      "user-unlink-telegram" in page and page.count("user-unlink-telegram") == 1)
r = Rec()
r.action("user-unlink-telegram", {"id": [str(bot_only["id"])]})
check("but refuses for a bot-only account", "m=!" in urllib.parse.unquote(r.sent["Location"])
      and user(bot_only["id"])["telegram_id"] == 555)
r = Rec()
r.action("user-unlink-telegram", {"id": [str(sara["id"])]})
check("the admin can unlink a web account", user(sara["id"])["telegram_id"] is None)

for name in ("settings",):
    setattr(Rec, name, getattr(admin.Admin, name))
r = Rec()
r.action("telegram-required", {"on": ["1"]})
check("the admin turns it on in settings", store.setting("require_telegram") == "1")
admin.CFG.update(ADMIN_PORT="9443")
check("and the page shows it ticked", "name='on' value='1' checked" in Rec().settings())
r = Rec()
r.action("telegram-required", {})
check("and off", store.setting("require_telegram") == "")

r = Rec()
r.action("bot-link-save", {"link": ["https://evil.example.com/x"]})
check("only a t.me address is taken", "m=!" in urllib.parse.unquote(r.sent["Location"]))
r = Rec()
r.action("bot-link-save", {"link": ["https://t.me/Another_Bot"]})
check("a t.me address is", store.setting("bot_link") == "https://t.me/Another_Bot")
check("and shown on the API page", "https://t.me/Another_Bot" in Rec().api_keys())

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
