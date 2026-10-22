#!/usr/bin/env python3
"""A reseller's own Telegram bot.

What has to hold: a reseller sets up a bot of their own - its own settings
file, its own unit and port, its own key - and the token of a bot already
at work on this panel is refused; that key reaches only the reseller's
customers: whoever starts the bot becomes theirs, it lists their plans and
card number, and it cannot see, approve or answer anything of anybody
else's; what happens to one of their customers goes to their bot, and to
the owner's only while they have none; a broadcast reaches each customer
through the bot they talk to; and deleting the reseller stops their bot.
"""
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
admin.log = panel.log = lambda *a: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
store.ensure_default_template([])
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p", "ADMIN_CERT": "/etc/letsencrypt/live/panel.example.com/f"}
admin.STORE = admin.Store(db_path)
admin.BOT_BIN = os.path.join(tmp, "master-dns-bot")
admin.BOT_ENV = os.path.join(tmp, "master-dns-bot.env")
admin.BOT_SELLER_ENV = os.path.join(tmp, "master-dns-bot-%d.env")
open(admin.BOT_BIN, "w").close()
calls = []
admin.systemctl = lambda *a: calls.append(a) or True
OWNER_TOKEN = "111111111:AAH" + "o" * 32
SELLER_TOKEN = "222222222:AAH" + "s" * 32
admin.bot_getme = lambda token: ({OWNER_TOKEN: "owner_bot", SELLER_TOKEN: "sara_bot"}
                                 .get(token), None)


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/bot"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "bot_page", "queue_bot_test"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(who, rest, **form):
    admin.REQ.admin = who
    r = Rec()
    r.action(rest, {k: v if isinstance(v, list) else [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


did = store.one("SELECT id FROM templates WHERE is_default = 1")["id"]
sid = store.run("INSERT INTO admins (username, password_hash, password_salt, perms, own_only,"
                " max_users, created_at, ref_code) VALUES ('sara', 'x', 'y', ?, 1, 3, ?,"
                " 'sarasara01')", (json.dumps(["users", "bot"]), panel.now())).lastrowid
sara = store.one("SELECT * FROM admins WHERE id = ?", (sid,))

print("setting up")
act(None, "bot-setup", token=OWNER_TOKEN, admins="1000")
act(sara, "bot-setup", token=SELLER_TOKEN, admins="2000")
env = admin.read_env(admin.BOT_SELLER_ENV % sid)
check("the reseller's bot has its own settings, on a port of its own",
      env.get("BOT_TOKEN") == SELLER_TOKEN and env.get("LISTEN") == "127.0.0.1:%d"
      % (admin.BOT_SELLER_PORT + sid) and env.get("ADMIN_IDS") == "2000")
check("  its own unit", ("restart", "master-dns-bot@%d" % sid) in calls)
key_row = store.one("SELECT * FROM api_tokens WHERE admin_id = ?", (sid,))
check("  and a key that is theirs", key_row is not None and key_row["scope"] == "admin")
check("  the owner's bot is untouched", admin.read_env(admin.BOT_ENV)["BOT_TOKEN"] == OWNER_TOKEN)
check("its link is theirs", store.setting("bot_link:%d" % sid) == "https://t.me/sara_bot"
      and store.setting("bot_link") == "https://t.me/owner_bot")
check("the owner's bot's token is refused for the reseller",
      "حساب دیگری" in act(sara, "bot-setup", token=OWNER_TOKEN, admins="2000"))

print("through the reseller's key")
SKEY, OKEY = "dd_sellerkey", "dd_ownerkey"
store.run("UPDATE api_tokens SET token_hash = ? WHERE admin_id = ?", (panel.token_hash(SKEY), sid))
store.run("UPDATE api_tokens SET token_hash = ? WHERE admin_id IS NULL", (panel.token_hash(OKEY),))
panel.BotAPI.store = store
server = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]


def call(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        res = urllib.request.urlopen(req, timeout=10)
        return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, res = call("POST", "/api/v1/users", SKEY, {"telegram_id": 5001, "name": "a"})
cust = store.user_by_telegram(5001)
check("whoever starts their bot is theirs", code == 201 and cust["owner_admin"] == sid, str(res))
call("POST", "/api/v1/users", OKEY, {"telegram_id": 6001, "name": "b"})
mine = store.user_by_telegram(6001)
check("  the owner's customer is not found through it",
      call("GET", "/api/v1/users/6001", SKEY)[0] == 404)
check("  nor taken over by starting it", call("POST", "/api/v1/users", SKEY,
                                              {"telegram_id": 6001})[0] == 409
      and store.user_by_telegram(6001)["owner_admin"] is None)
call("POST", "/api/v1/users", SKEY, {"telegram_id": 5002})
call("POST", "/api/v1/users", SKEY, {"telegram_id": 5003})
check("  not past the number the owner allowed",
      call("POST", "/api/v1/users", SKEY, {"telegram_id": 5004})[0] == 403)
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, active, created_at,"
          " owner_admin) VALUES ('sara-plan', ?, 30, 0, 50000, 1, ?, ?)", (did, panel.now(), sid))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, active, created_at)"
          " VALUES ('owner-plan', ?, 30, 0, 90000, 1, ?)", (did, panel.now()))
store.set_setting("pay_text:%d" % sid, "sara card")
store.set_setting("pay_text", "owner card")
code, res = call("GET", "/api/v1/plans", SKEY)
check("their plans and their card", [p["name"] for p in res["plans"]] == ["sara-plan"]
      and res["pay_text"] == "sara card")
code, res = call("GET", "/api/v1/plans", OKEY)
check("  the owner's bot, the owner's", [p["name"] for p in res["plans"]] == ["owner-plan"]
      and res["pay_text"] == "owner card")
code, res = call("GET", "/api/v1/admin/users", SKEY)
check("its admin sees only their customers",
      sorted(u["id"] for u in res["users"]) == sorted(
          r["id"] for r in store.q("SELECT id FROM users WHERE owner_admin = ?", (sid,))))
check("  not anybody else's", call("GET", "/api/v1/admin/users/%d" % mine["id"], SKEY)[0] == 404)
tx = store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
               " VALUES (?, 1000, 'card', 'pending', ?)", (mine["id"], panel.now())).lastrowid
check("  nor approve their receipt", call("POST", "/api/v1/admin/receipts/%d/approve" % tx,
                                          SKEY, {})[0] == 404
      and store.one("SELECT status FROM transactions WHERE id = ?", (tx,))[0] == "pending")
code, res = call("GET", "/api/v1/admin/receipts", SKEY)
check("  nor list it", code == 200 and not res["receipts"])
code, res = call("GET", "/api/v1/admin/stats", SKEY)
check("  its report counts their customers", res.get("users") == 3
      and res.get("receipts_pending") == 0, str(res))

print("messages")
store.run("DELETE FROM webhook_outbox")
panel.emit(store, cust, "plan.activated", {"text": "x"})
panel.emit(store, mine, "plan.activated", {"text": "y"})
rows = store.q("SELECT token_id, payload FROM webhook_outbox")
by = {json.loads(r["payload"])["telegram_id"]: r["token_id"] for r in rows}
okey_id = store.one("SELECT id FROM api_tokens WHERE admin_id IS NULL")["id"]
check("their customer's news goes to their bot, the owner's to the owner's",
      len(rows) == 2 and by == {5001: key_row["id"], 6001: okey_id})
store.run("DELETE FROM webhook_outbox")
panel.emit_admin(store, "receipt.new", {"user_id": cust["id"], "text": "r"})
check("a receipt of theirs is told to their bot only",
      [r["token_id"] for r in store.q("SELECT token_id FROM webhook_outbox")] == [key_row["id"]])
store.run("DELETE FROM webhook_outbox")
panel.emit_admin(store, "seller.limit", {"admin_id": sid, "text": "cap"})
check("their cap, to both", sorted(r["token_id"] for r in store.q(
    "SELECT token_id FROM webhook_outbox")) == sorted([key_row["id"], okey_id]))
store.run("DELETE FROM webhook_outbox")
act(None, "broadcast-send", text="hello all", target="all")
got = {json.loads(r["payload"])["telegram_id"]: r["token_id"]
       for r in store.q("SELECT token_id, payload FROM webhook_outbox")}
check("the owner's broadcast reaches each through the bot they talk to",
      got.get(5001) == key_row["id"] and got.get(6001) == okey_id)
store.run("DELETE FROM webhook_outbox")
act(sara, "broadcast-send", text="hello mine", target="all")
got = {json.loads(r["payload"])["telegram_id"]: r["token_id"]
       for r in store.q("SELECT token_id, payload FROM webhook_outbox")}
check("the reseller's, only their customers, through their bot",
      6001 not in got and set(got.values()) == {key_row["id"]} and 5001 in got)
admin.REQ.admin = sara
check("  and their page lists only theirs", "hello all" not in admin.broadcast_card("p"))
store.run("UPDATE api_tokens SET revoked_at = ? WHERE admin_id = ?", (panel.now(), sid))
store.run("DELETE FROM webhook_outbox")
panel.emit(store, cust, "plan.activated", {"text": "x"})
check("with no bot of theirs, the owner's is used",
      [r["token_id"] for r in store.q("SELECT token_id FROM webhook_outbox")] == [okey_id])

print("deleting the reseller")
act(None, "admin-delete", id=str(sid))
check("their bot is stopped and its settings gone",
      ("disable", "--now", "master-dns-bot@%d" % sid) in calls
      and not os.path.exists(admin.BOT_SELLER_ENV % sid))

print("the installer")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("puts the unit for resellers' bots, restarts them on upgrade, removes them on uninstall",
      "payload BOT_SELLER_SERVICE" in logic and 'try-restart "master-dns-bot@$sb.service"'
      in logic and 'disable --now "master-dns-bot@$sb.service"' in logic)

server.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
