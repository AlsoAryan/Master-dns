#!/usr/bin/env python3
"""The wallet and invitations.

What has to hold: a top-up is a receipt like any other, and the money arrives
only when the operator approves it - once, for the sum they say; a plan paid
from the wallet is the money and the plan together, never one without the
other, and never more than is there; somebody who came by a customer's
invitation link pays that customer the operator's share of what they buy -
of every purchase or only the first, as the operator chose, and never of a
top-up; an account's inviter is written when it is opened and never after,
and nobody invites themselves. The admin panel and the bot API follow the
same rules.
"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import urllib.parse

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
botmod = load("examples/telegram-bot/bot.py", "bot")
panel.log = lambda *a: None
panel.print = lambda *a, **k: None
admin.log = lambda *a: None
botmod.log = lambda *a: None

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
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('سه‌ماهه', 1, 90, ?, 500000, ?)", (150 * GB, panel.now()))
MONTH, SEASON = 1, 2
store.set_setting("bot_link", "https://t.me/doctor_bot")
store.set_setting("customer_panel_url", "https://user.example.com:8443/")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")
SLIP = {"content_type": "image/png", "data": base64.b64encode(PNG).decode()}


class Rec:
    def __init__(self, path="/p/pay"):
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


for name in ("action", "redirect", "send", "pay_page", "wallet_page", "receipts", "users"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def user(uid):
    return store.one("SELECT * FROM users WHERE id = ?", (uid,))


def moves(uid):
    return [dict(r) for r in store.q("SELECT * FROM wallet_moves WHERE user_id = ?"
                                     " ORDER BY id", (uid,))]


def outbox(event):
    return [json.loads(r["payload"]) for r in store.q(
        "SELECT payload FROM webhook_outbox WHERE event = ? ORDER BY id", (event,))]


# A bot key, so there is somebody to tell.
store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, webhook_secret,"
          " created_at) VALUES ('b', ?, 'admin', 'https://b.example/h', 'whsec_x', ?)",
          (panel.token_hash("dd_key"), panel.now()))

print("topping the wallet up")
ali = store.create_web_user("ali", "علی", "ali-password")
store.run("UPDATE users SET telegram_id = 501 WHERE id = ?", (ali["id"],))
res = panel.create_receipt(store, user(ali["id"]), dict(SLIP, kind="topup", amount="300000"))
check("closed until the operator opens it", not res["ok"] and res["error"] == "wallet_off")
page = Rec().pay_page()
check("the Payment page has the switch, off", "wallet-settings" in page
      and "name='on' value='1'>" in page.split("wallet-settings")[1][:200])
act("wallet-settings", on="1")
check("  and opens it", store.setting("wallet_on") == "1")
res = panel.create_receipt(store, user(ali["id"]), dict(SLIP, kind="topup", amount="5000"))
check("a top-up below the least is refused", not res["ok"] and res["error"] == "bad_amount")
res = panel.create_receipt(store, user(ali["id"]),
                           dict(SLIP, kind="topup", amount="۳۰۰,۰۰۰", plan_id=MONTH))
tid = res.get("receipt_id")
row = store.one("SELECT * FROM transactions WHERE id = ?", (tid,))
check("a top-up is a receipt for the sum written, Persian digits and commas and all",
      res["ok"] and row["kind"] == "topup" and row["amount"] == 300000
      and row["plan_id"] is None and row["status"] == "pending", str(res))
check("  and nothing is in the wallet yet", user(ali["id"])["wallet"] == 0)
check("  the operator is told it is a top-up",
      "شارژ کیف پول" in outbox("receipt.submitted")[-1]["data"]["text"])
page = Rec().receipts()
check("the receipts page shows it as a top-up, the sum to be credited editable",
      "شارژ کیف پول" in page and "name='amount' value='300000'" in page)
where = act("receipt-decide", id=str(tid), to="approved", amount="250,000")
check("approving it with the sum the slip really says puts that sum in",
      user(ali["id"])["wallet"] == 250000 and "250,000" in where, where)
check("  written down, with the receipt", moves(ali["id"])[-1]["kind"] == "topup"
      and moves(ali["id"])[-1]["amount"] == 250000
      and moves(ali["id"])[-1]["transaction_id"] == tid)
check("  and the customer told the balance",
      "250,000" in outbox("receipt.approved")[-1]["data"]["text"])
act("receipt-decide", id=str(tid), to="approved")
res = panel.decide_receipt(store, tid, "approved")
check("once: neither the panel nor the bot pays it again",
      user(ali["id"])["wallet"] == 250000 and not res["ok"])
res = panel.create_receipt(store, user(ali["id"]), dict(SLIP, kind="topup", amount="100000"))
res = panel.decide_receipt(store, res["receipt_id"], "approved")
check("the bot's approval does the same", res["ok"] and user(ali["id"])["wallet"] == 350000)
res = panel.create_receipt(store, user(ali["id"]), dict(SLIP, kind="topup", amount="100000"))
panel.decide_receipt(store, res["receipt_id"], "rejected")
check("a rejected one puts nothing in", user(ali["id"])["wallet"] == 350000)

print("paying from the wallet")
res = panel.buy_with_wallet(store, user(ali["id"]), SEASON)
check("more than is there is refused, saying how much is missing",
      not res["ok"] and res["error"] == "wallet_short" and "150,000" in res["message"])
check("  and nothing moved", user(ali["id"])["wallet"] == 350000
      and user(ali["id"])["plan_id"] is None
      and not store.one("SELECT 1 FROM transactions WHERE kind = 'wallet'"))
res = panel.buy_with_wallet(store, user(ali["id"]), MONTH)
u = user(ali["id"])
check("enough buys the plan at once: the plan on, the price off",
      res["ok"] and u["plan_id"] == MONTH and u["status"] == "active"
      and u["wallet"] == 150000 and "150,000" in res["message"], str(res))
t = store.one("SELECT * FROM transactions WHERE kind = 'wallet'")
check("  recorded as a purchase, already approved",
      t and t["status"] == "approved" and t["plan_id"] == MONTH and t["amount"] == 200000)
check("  and the operator told", "کیف پول" in outbox("wallet.bought")[-1]["data"]["text"])
res = panel.buy_with_wallet(store, user(ali["id"]), MONTH)
check("buying the same plan again with too little is refused, the plan untouched",
      not res["ok"] and user(ali["id"])["expires_at"] == u["expires_at"])
store.run("UPDATE plans SET active = 0 WHERE id = ?", (SEASON,))
res = panel.buy_with_wallet(store, user(ali["id"]), SEASON)
check("a plan no longer on sale cannot be bought", not res["ok"]
      and res["error"] == "plan_not_on_sale")
store.run("UPDATE plans SET active = 1 WHERE id = ?", (SEASON,))
store.set_setting("require_telegram", "1")
store.set_setting("bot_link", "https://t.me/doctor_bot")
nat = store.create_web_user("nat", "", "nat-password")
store.run("UPDATE users SET wallet = 999999 WHERE id = ?", (nat["id"],))
store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, webhook_secret,"
          " created_at) VALUES ('c', ?, 'customer', 'https://c.example/h', 'w', ?)",
          (panel.token_hash("dd_c"), panel.now()))
res = panel.buy_with_wallet(store, user(nat["id"]), MONTH)
check("where Telegram is needed to buy, it is needed to buy from the wallet too",
      not res["ok"] and res["error"] == "telegram_required")
store.set_setting("require_telegram", "0")

print("two purchases at once")
store.run("UPDATE users SET wallet = 200000 WHERE id = ?", (nat["id"],))
results = []
threads = [threading.Thread(target=lambda: results.append(
    panel.buy_with_wallet(store, user(nat["id"]), MONTH)["ok"])) for _ in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
check("the same money buys one plan, not four", results.count(True) == 1
      and user(nat["id"])["wallet"] == 0, str(results))

print("invitations")
check("off, nobody has a link", panel.ref_view(store, user(ali["id"])) is None)
where = act("ref-settings", on="1", percent="0", mode="every")
check("a percent of nothing is refused", "!" in where and store.setting("ref_on") != "1")
act("ref-settings", on="1", percent="10", mode="every")
check("on, at ten percent of every purchase", panel.referral_terms(store) == (10, "every"))
ref = panel.ref_view(store, user(ali["id"]))
code = ref and ref["code"]
check("the customer gets a code, and a link to the bot and to the sign-up page",
      ref and ref["bot_link"] == "https://t.me/doctor_bot?start=ref_" + code
      and ref["web_link"] == "https://user.example.com:8443/signup?ref=" + code, str(ref))
check("  the same code every time", panel.ref_view(store, user(ali["id"]))["code"] == code)
check("  nobody invited yet", ref["invited"] == 0 and ref["earned"] == 0)


class Handler:
    pass


for name in ("do_user_signup", "_session_user"):
    setattr(Handler, name, getattr(panel.API, name))
h = Handler()
h.store = store
res = h.do_user_signup({"username": "bahar", "password": "bahar-pass", "name": "بهار",
                        "ip": "93.184.216.9", "ref": code.upper()})
bahar = store.user_by_username("bahar")
check("signing up on the web by the link writes down who invited them",
      res["ok"] and bahar["referred_by"] == ali["id"])
check("  and not again, by another link", panel.set_referrer(store, bahar["id"],
      panel.ref_code(store, user(nat["id"]))) is None
      and user(bahar["id"])["referred_by"] == ali["id"])
check("nobody invites themselves", panel.set_referrer(store, nat["id"],
      panel.ref_code(store, user(nat["id"]))) is None and user(nat["id"])["referred_by"] is None)
check("a code that is nobody's is nothing", panel.set_referrer(store, nat["id"], "zzzzzzzz")
      is None)
h.do_user_signup({"username": "kian", "password": "kian-pass", "ip": "93.184.216.9",
                  "ref": "<script>"})
check("  nor is one that is not a code", store.user_by_username("kian")["referred_by"] is None)

print("the inviter's share")
res = panel.create_receipt(store, user(bahar["id"]), dict(SLIP, plan_id=MONTH))
panel.decide_receipt(store, res["receipt_id"], "approved")
check("a plan bought by receipt pays the inviter ten percent of it",
      user(ali["id"])["wallet"] == 150000 + 20000)
m = moves(ali["id"])[-1]
check("  written down as an invitation, naming who bought",
      m["kind"] == "referral" and m["amount"] == 20000 and m["other_user"] == bahar["id"])
told = outbox("wallet.referral")[-1]
check("  and the inviter is told, by Telegram", told["telegram_id"] == 501
      and "20,000" in told["data"]["text"])
check("  their page counts it", panel.ref_view(store, user(ali["id"]))["invited"] == 1
      and panel.ref_view(store, user(ali["id"]))["earned"] == 20000)
store.run("UPDATE users SET wallet = 500000 WHERE id = ?", (bahar["id"],))
panel.buy_with_wallet(store, user(bahar["id"]), SEASON)
check("a plan bought from the wallet pays it too", user(ali["id"])["wallet"] == 170000 + 50000)
res = panel.create_receipt(store, user(bahar["id"]), dict(SLIP, kind="topup", amount="200000"))
panel.decide_receipt(store, res["receipt_id"], "approved")
check("a top-up pays nothing: the money is counted when it buys something",
      user(ali["id"])["wallet"] == 220000)
res = panel.create_receipt(store, user(bahar["id"]), dict(SLIP, plan_id=MONTH))
act("receipt-decide", id=str(res["receipt_id"]), to="approved")
check("the admin panel's approval pays the same share",
      user(ali["id"])["wallet"] == 240000)
act("ref-settings", on="1", percent="10", mode="first")
res = panel.create_receipt(store, user(bahar["id"]), dict(SLIP, plan_id=MONTH))
panel.decide_receipt(store, res["receipt_id"], "approved")
check("set to the first purchase only, a later one pays nothing",
      user(ali["id"])["wallet"] == 240000)
omid = store.create_web_user("omid", "", "omid-password")
panel.set_referrer(store, omid["id"], code)
res = panel.create_receipt(store, user(omid["id"]), dict(SLIP, plan_id=SEASON))
act("receipt-decide", id=str(res["receipt_id"]), to="approved")
check("  but a first one does", user(ali["id"])["wallet"] == 290000)
act("ref-settings", percent="10", mode="every")
res = panel.create_receipt(store, user(omid["id"]), dict(SLIP, plan_id=MONTH))
panel.decide_receipt(store, res["receipt_id"], "approved")
check("switched off, nothing is paid", user(ali["id"])["wallet"] == 290000
      and panel.ref_view(store, user(ali["id"])) is None)
act("ref-settings", on="1", percent="10", mode="every")
admin.Store.delete_user(admin.STORE, ali["id"])
res = panel.create_receipt(store, user(omid["id"]), dict(SLIP, plan_id=MONTH))
res = panel.decide_receipt(store, res["receipt_id"], "approved")
check("an inviter whose account is gone is simply not paid", res["ok"])

print("the operator's hand")
where = act("wallet-adjust", id=str(bahar["id"]), amount="50,000", note="هدیه")
check("money put in by hand", user(bahar["id"])["wallet"] == 250000 and "250,000" in where,
      where)
check("  with the reason, told to the customer too",
      moves(bahar["id"])[-1]["note"] == "هدیه" and moves(bahar["id"])[-1]["kind"] == "admin")
act("wallet-adjust", id=str(bahar["id"]), amount="-100000")
check("  and taken out", user(bahar["id"])["wallet"] == 150000)
where = act("wallet-adjust", id=str(bahar["id"]), amount="-900000")
check("  never below nothing", user(bahar["id"])["wallet"] == 150000 and "!" in where)
page = Rec("/p/wallet?u=%d" % bahar["id"]).wallet_page()
check("each customer's wallet page: the balance, its moves, who invited them",
      "150,000" in page and "هدیه" in page and "حسابی که دیگر نیست" in page)
page = Rec().users()
check("the users page links to it", "wallet?u=%d" % bahar["id"] in page)

print("the customer's page")
info = {"wallet": 250000, "wallet_on": True, "topup_min": 10000, "pay_text": "کارت ۶۰۳۷",
        "wallet_moves": panel.wallet_moves(store, bahar["id"]),
        "plans": store.plans_for_sale(), "plan_id": None,
        "ref": panel.ref_view(store, user(bahar["id"]))}
box = sync.wallet_box(info)
check("the wallet: its balance, a top-up form and its moves",
      "250,000" in box and "action='/wallet-topup'" in box and "هدیه" in box)
check("  closed to top-ups, it still shows what is there",
      "250,000" in sync.wallet_box(dict(info, wallet_on=False))
      and "wallet-topup" not in sync.wallet_box(dict(info, wallet_on=False)))
check("  and an empty wallet nobody can fill is not shown",
      sync.wallet_box({"wallet": 0, "wallet_on": False}) == "")
check("buying offers the wallet, on the same form as the receipt",
      "formaction='/wallet-buy'" in sync.receipt_box(info)
      and "formaction='/wallet-buy'" not in sync.receipt_box(dict(info, wallet=0)))
box = sync.ref_box(info, "relay.example.com")
check("the invitation: both links, the terms, what it has brought",
      "ref_" + info["ref"]["code"] in box and "signup?ref=" + info["ref"]["code"] in box
      and "10٪" in box)
store.set_setting("customer_panel_url", "")
box = sync.ref_box(dict(info, ref=panel.ref_view(store, user(bahar["id"]))),
                   "relay.example.com:8443")
check("  with the page's own address when the panel does not know one",
      "https://relay.example.com:8443/signup?ref=" in box)
check("  and not with a Host header that is not a name",
      "signup?ref" not in sync.ref_box(dict(info, ref=dict(info["ref"], web_link=None)),
                                       "evil.com/<x>"))
form = sync.signup_form("", "abcd2345")
check("the sign-up page carries the code on", "name='ref' value='abcd2345'" in form
      and "دعوت" in form)
check("  and nothing that is not a code", "name='ref'" not in sync.signup_form("", "x'><b>"))

print("the bot")
panel.BotAPI.store = store
panel.BotAPI.relays = ("198.51.100.4",)
api = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=api.serve_forever, daemon=True).start()
store.run("INSERT INTO api_tokens (name, token_hash, scope, created_at)"
          " VALUES ('ربات', ?, 'admin', ?)", (panel.token_hash("dd_botkey"), panel.now()))
cfg = botmod.settings({"BOT_TOKEN": "t", "API_URL": "http://127.0.0.1:%d/api/v1"
                       % api.server_address[1], "API_KEY": "dd_botkey",
                       "WEBHOOK_SECRET": "whsec_test", "LISTEN": "127.0.0.1:0",
                       "PAY_TEXT": "کارت ۶۰۳۷"})


class FakeTelegram:
    def __init__(self):
        self.out = []

    def call(self, method, http_timeout=30, **params):
        self.out.append((method, params))
        return True

    def send(self, chat, text, markup=None):
        self.out.append(("sendMessage", {"chat_id": chat, "text": text, "reply_markup": markup}))

    def download(self, file_id):
        return PNG

    def texts(self, chat):
        return [p["text"] for m, p in self.out if p.get("chat_id") == chat and "text" in p]

    def buttons(self, chat):
        for m, p in reversed(self.out):
            if p.get("chat_id") == chat and p.get("reply_markup"):
                return [b for row in (p["reply_markup"].get("inline_keyboard") or [])
                        for b in row]
        return []


tg = FakeTelegram()
bot = botmod.Bot(cfg, telegram=tg)
n = [0]


def message(uid, text=None, photo=False):
    n[0] += 1
    msg = {"message_id": n[0], "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "first_name": "سارا"}}
    if text is not None:
        msg["text"] = text
    if photo:
        msg["photo"] = [{"file_id": "p"}]
    bot.handle({"update_id": n[0], "message": msg})


def tap(uid, data):
    n[0] += 1
    bot.handle({"update_id": n[0], "callback_query": {
        "id": str(n[0]), "data": data, "from": {"id": uid},
        "message": {"message_id": 1, "chat": {"id": uid}}}})


bahar_code = panel.ref_code(store, user(bahar["id"]))
SARA = 777
message(SARA, "/start ref_" + bahar_code)
sara = store.user_by_telegram(SARA)
check("a start by an invitation link opens the account, with the inviter",
      sara and sara["referred_by"] == bahar["id"])
message(SARA, "/start ref_" + panel.ref_code(store, user(omid["id"])))
check("  and a second link changes nothing", user(sara["id"])["referred_by"] == bahar["id"])
store.run("UPDATE users SET username = 'sara' WHERE id = ?", (sara["id"],))
check("the menu has the wallet", botmod.B_WALLET in sum(botmod.MENU["keyboard"], []))
message(SARA, botmod.B_WALLET)
text = tg.texts(SARA)[-1]
check("the wallet: the balance, and no invitation link",
      "موجودی: 0" in text and "start=ref_" not in text, text)
check("  with a button to top up", any(b["callback_data"] == "topup" for b in tg.buttons(SARA)))
check("the invitation has a button of its own", botmod.B_INVITE in sum(botmod.MENU["keyboard"], []))
message(SARA, botmod.B_INVITE)
text = tg.texts(SARA)[-1]
check("  with the links and what it has earned",
      "https://t.me/doctor_bot?start=ref_" in text and "پورسانت" in text, text)
message(SARA, botmod.B_WALLET)
tap(SARA, "topup")
message(SARA, "۵ هزار")
check("a top-up asks the sum, and refuses what is not one", "⚠️" in tg.texts(SARA)[-1])
message(SARA, "400,000 تومان")
check("  then where to pay", "کارت" in tg.texts(SARA)[-1] and "400,000" in tg.texts(SARA)[-1])
message(SARA, photo=True)
t = store.one("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (sara["id"],))
check("  and the slip is a top-up receipt for that sum",
      t["kind"] == "topup" and t["amount"] == 400000 and t["status"] == "pending")
panel.decide_receipt(store, t["id"], "approved")
bot.state.pop(SARA, None)
message(SARA, botmod.B_BUY)
tap(SARA, "buy:%d" % MONTH)
check("choosing a plan offers the wallet when it is enough",
      any(b["callback_data"] == "wbuy:%d" % MONTH for b in tg.buttons(SARA)))
before = user(bahar["id"])["wallet"]
tap(SARA, "wbuy:%d" % MONTH)
u = user(sara["id"])
check("  and one tap buys it", u["plan_id"] == MONTH and u["wallet"] == 200000
      and "فعال" in " ".join(tg.texts(SARA)[-2:]), str(tg.texts(SARA)[-2:]))
check("  the inviter paid their share", user(bahar["id"])["wallet"] == before + 20000)
check("  and the bot stops waiting for a slip", SARA not in bot.state
      or bot.state[SARA][0] != "receipt")
message(SARA, botmod.B_BUY)
tap(SARA, "buy:%d" % SEASON)
check("with too little, it says how much is missing, and offers no button",
      any("300,000" in x for x in tg.texts(SARA)[-3:])
      and not any(b["callback_data"].startswith("wbuy") for b in tg.buttons(SARA)))
check("typed sums: Persian digits, commas, the word toman",
      botmod.typed_toman("۱۵۰,۰۰۰ تومان") == 150000 and botmod.typed_toman("abc") is None)

print("the admin API")
status, body = None, None
import urllib.request


def call(method, path, payload=None):
    req = urllib.request.Request("http://127.0.0.1:%d/api/v1%s" % (api.server_address[1], path),
                                 data=json.dumps(payload).encode() if payload is not None
                                 else None, method=method)
    req.add_header("Authorization", "Bearer dd_botkey")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


import urllib.error
code_, body = call("POST", "/admin/users/%d/wallet" % sara["id"], {"amount": "-50000",
                                                                   "note": "اصلاح"})
check("the operator's bot can change a wallet too", code_ == 200
      and user(sara["id"])["wallet"] == 150000, str(body))
code_, body = call("POST", "/admin/users/%d/wallet" % sara["id"], {"amount": "-900000"})
check("  never below nothing", code_ == 409 and user(sara["id"])["wallet"] == 150000)
res = panel.create_receipt(store, user(sara["id"]), dict(SLIP, kind="topup", amount="100000"))
code_, body = call("GET", "/admin/receipts")
check("its receipts say which are top-ups",
      any(r["kind"] == "topup" for r in body["receipts"]))
code_, body = call("POST", "/admin/receipts/%d/approve" % res["receipt_id"],
                   {"amount": "90000"})
check("  and approve one for a corrected sum", code_ == 200
      and user(sara["id"])["wallet"] == 240000, str(body))
code_, body = call("GET", "/users/%d/wallet" % SARA)
check("a customer's wallet by the API: balance, moves, invitation",
      body["balance"] == 240000 and body["moves"] and body["ref"]["code"])

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
