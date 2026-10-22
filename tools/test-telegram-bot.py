#!/usr/bin/env python3
"""The sample Telegram bot, against the real bot API and a stand-in Telegram.

Telegram itself is faked - what the bot sends is recorded, and the customer's
taps and photos are handed to it as updates - but everything behind it is
real: the panel's API over a socket, receipts, approval, and the panel's own
signed messages arriving at the bot's listener. So what is checked is the
whole round a customer and an operator make: buy, pay, get approved; ask for
help, get an answer; link a web account; and the operator doing it all from
Telegram.
"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
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
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, note, created_at)"
          " VALUES ('ماهانه', 1, 30, ?, 200000, 'همه‌چیز', ?)", (50 * GB, panel.now()))

# The panel's API, for real.
panel.BotAPI.store = store
panel.BotAPI.relays = ("198.51.100.4",)
api = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=api.serve_forever, daemon=True).start()

ADMIN_TG = 900
cfg = botmod.settings({"BOT_TOKEN": "t", "API_URL": "http://127.0.0.1:%d/api/v1"
                       % api.server_address[1], "API_KEY": "dd_botkey",
                       "WEBHOOK_SECRET": "whsec_test", "LISTEN": "127.0.0.1:0",
                       "ADMIN_IDS": str(ADMIN_TG), "PAY_TEXT": "کارت ۶۰۳۷-۱۲۳۴ به نام مهدی"})

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")


class FakeTelegram:
    """Records what the bot says; hands back files by id."""

    def __init__(self):
        self.out = []
        self.docs = []
        self.files = {"PHOTO1": PNG, "TEXTFILE": b"not a picture"}

    def document(self, chat, filename, blob, caption=""):
        self.docs.append({"chat_id": chat, "name": filename, "bytes": blob, "caption": caption})

    def call(self, method, http_timeout=30, **params):
        self.out.append((method, params))
        return {"username": "test_bot"} if method == "getMe" else True

    def send(self, chat, text, markup=None):
        self.out.append(("sendMessage", {"chat_id": chat, "text": text, "reply_markup": markup}))

    def photo(self, chat, blob, caption, markup=None):
        self.out.append(("sendPhoto", {"chat_id": chat, "caption": caption,
                                       "reply_markup": markup, "bytes": blob}))

    def download(self, file_id):
        return self.files[file_id]

    def last(self, chat, method=None):
        for m, p in reversed(self.out):
            if p.get("chat_id") == chat and (method is None or m == method):
                return p
        return {}

    def recent(self, chat, n=3):
        """The text of the last few messages to `chat`, joined."""
        return " | ".join(p.get("text") or "" for m, p in self.out
                          if p.get("chat_id") == chat)[-4000:] if n else ""

    def buttons(self, chat, method=None):
        markup = self.last(chat, method).get("reply_markup") or {}
        return [b for row in markup.get("inline_keyboard", []) for b in row]


tg = FakeTelegram()
bot = botmod.Bot(cfg, telegram=tg)
work = queue.Queue()
hook = botmod.webhook_server(bot, work)
threading.Thread(target=hook.serve_forever, daemon=True).start()

# The key the bot uses: admin rights, and its webhook pointing at the bot.
store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, webhook_secret,"
          " created_at) VALUES ('ربات', ?, 'admin', ?, 'whsec_test', ?)",
          (panel.token_hash("dd_botkey"), "http://127.0.0.1:%d/hook" % hook.server_address[1],
           panel.now()))

updates = [0]


def message(uid, text=None, photo=None, name="علی"):
    updates[0] += 1
    msg = {"message_id": updates[0], "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "first_name": name}}
    if text is not None:
        msg["text"] = text
    if photo:
        msg["photo"] = [{"file_id": "small"}, {"file_id": photo}]
    bot.handle({"update_id": updates[0], "message": msg})
    return msg


def tap(uid, data):
    updates[0] += 1
    bot.handle({"update_id": updates[0], "callback_query": {
        "id": str(updates[0]), "data": data, "from": {"id": uid},
        "message": {"message_id": 1, "chat": {"id": uid}}}})


def panel_speaks():
    """Deliver what the panel queued, and let the bot act on it."""
    panel.deliver_due(store)
    while True:
        try:
            bot.on_event(work.get(timeout=1))
        except queue.Empty:
            return


CUSTOMER = 111

print("a customer arrives")
message(CUSTOMER, "/start")
check("greeted, with the menu", "خوش آمدید" in tg.last(CUSTOMER)["text"]
      and tg.last(CUSTOMER)["reply_markup"] == botmod.MENU)
check("and no account is opened just for saying hello", not store.user_by_telegram(CUSTOMER))
store.set_setting("customer_panel_url", "https://user.example.com:8443/")
message(CUSTOMER, botmod.B_ACCOUNT)
check("the account is opened on first use", store.user_by_telegram(CUSTOMER) is not None)
check("and the first thing asked is a name, with the Telegram one offered",
      "اسمتان" in tg.last(CUSTOMER)["text"]
      and tg.last(CUSTOMER)["reply_markup"]["keyboard"] == [["علی"]])
message(CUSTOMER, "علی رضایی")
check("then a username", "نام کاربری" in tg.last(CUSTOMER)["text"])
message(CUSTOMER, "ع")
check("a bad one is refused, and asked again", "⚠️" in tg.last(CUSTOMER)["text"]
      and bot.state[CUSTOMER][0] == "onb_user")
store.create_web_user("taken_name", "x", "whatever-pass")
message(CUSTOMER, "Taken_Name")
check("so is one somebody has", "گرفته شده" in tg.last(CUSTOMER)["text"])
message(CUSTOMER, "Ali_Gamer")
made = [p["text"] for m, p in tg.out if p.get("chat_id") == CUSTOMER and "حساب پنل شما" in
        (p.get("text") or "")]
u = store.user_by_telegram(CUSTOMER)
check("a web sign-in is made: the name, the username, a password",
      u["username"] == "ali_gamer" and u["first_name"] == "علی رضایی" and u["password_hash"])
password = re.findall(r"رمز: ([a-z2-9-]{14})", made[0])[0] if made else ""
check("and sent, with the address", made and "https://user.example.com:8443/" in made[0]
      and panel.check_password(u, password), str(made))
check("then straight on to registering the address, with a one-time link",
      "/go/" in tg.last(CUSTOMER)["text"])
sent_params = {}
botmod.Telegram.call = lambda self, method, http_timeout=30, **kw: sent_params.update(kw)
botmod.Telegram({"telegram": "x", "token": "t"}).send(1, "https://example.com/go/abc")
check("links are sent without a preview, so a preview cannot use one up",
      sent_params.get("link_preview_options") == {"is_disabled": True})
bot.state.pop(CUSTOMER, None)
message(CUSTOMER, botmod.B_ACCOUNT)
check("after that, the account as usual", "در انتظار خرید پلن" in tg.last(CUSTOMER)["text"])
check("without the DNS address - that is the DNS button's",
      "198.51.100.4" not in tg.last(CUSTOMER)["text"]
      and "DNS" not in tg.last(CUSTOMER)["text"], tg.last(CUSTOMER).get("text"))

print("the web panel, from the bot")
message(CUSTOMER, botmod.B_WEB)
check("the username and the address", "ali_gamer" in tg.last(CUSTOMER)["text"]
      and [b["callback_data"] for b in tg.buttons(CUSTOMER)] == ["login", "newpw"])
tap(CUSTOMER, "login")
link = re.findall(r"https://user\.example\.com:8443/go/([A-Za-z0-9_-]+)",
                  tg.last(CUSTOMER)["text"])
check("a one-time sign-in link", bool(link))
res = panel.use_login_link(store, link[0]) if link else {}
check("which gives a session for this customer", res.get("ok") and store.one(
    "SELECT user_id FROM panel_sessions WHERE token = ?", (res["session"],))["user_id"] == u["id"])
check("once", not panel.use_login_link(store, link[0])["ok"] if link else False)
tap(CUSTOMER, "newpw")
fresh = re.findall(r"رمز تازهٔ پنل: ([a-z2-9-]{14})", tg.last(CUSTOMER)["text"])
check("a new password", fresh and panel.check_password(store.user_by_telegram(CUSTOMER),
                                                       fresh[0])
      and not panel.check_password(store.user_by_telegram(CUSTOMER), password))
check("and whoever was signed in with the old one is out", not store.one(
    "SELECT 1 FROM panel_sessions WHERE user_id = ?", (u["id"],)))

print("buying")
message(CUSTOMER, botmod.B_BUY)
plans = tg.buttons(CUSTOMER)
check("the plans, as buttons with the price", len(plans) == 1
      and "200,000" in plans[0]["text"] and plans[0]["callback_data"] == "buy:1", str(plans))
tap(CUSTOMER, "buy:1")
check("how to pay, from its own settings while the panel has none",
      "کارت ۶۰۳۷-۱۲۳۴" in tg.recent(CUSTOMER)[-1500:])
store.set_setting("pay_text", "کارت پنل 6219-5555 به نام مهدی")
tap(CUSTOMER, "buy:1")
plan_msg = next(p["text"] for m, p in reversed(tg.out)
                if p.get("chat_id") == CUSTOMER and "عکس رسید" in (p.get("text") or ""))
check("the admin panel's payment details win, with no restart",
      "کارت پنل 6219-5555" in plan_msg and "۶۰۳۷-۱۲۳۴" not in plan_msg)
message(CUSTOMER, "این رسید است")
check("words are not a receipt", "عکس رسید" in tg.last(CUSTOMER)["text"]
      and not store.one("SELECT 1 FROM transactions"))
sent = message(CUSTOMER, photo="PHOTO1")
check("a photo is", store.one("SELECT count(*) c FROM transactions")["c"] == 1
      and "رسید فرستاده شد" in tg.last(CUSTOMER)["text"])
t = store.one("SELECT * FROM transactions")
check("for the plan chosen, at its price, with the picture",
      t["plan_id"] == 1 and t["amount"] == 200000 and bytes(t["receipt_blob"]) == PNG)
# Telegram retries an update the bot was slow to take; the same message again.
bot.state[CUSTOMER] = ("receipt", 1)
bot.handle({"update_id": 999, "message": sent})
check("the same Telegram message twice is one receipt",
      store.one("SELECT count(*) c FROM transactions")["c"] == 1)

print("the operator, in Telegram")
panel_speaks()
pic = tg.last(ADMIN_TG, "sendPhoto")
check("the new receipt arrives as its picture", pic.get("bytes") == PNG
      and "200,000" in pic.get("caption", ""), str(pic.get("caption")))
check("with approve and reject", [b["callback_data"] for b in tg.buttons(ADMIN_TG, "sendPhoto")]
      == ["ok:%d" % t["id"], "no:%d" % t["id"]])
tap(CUSTOMER, "ok:%d" % t["id"])
check("a customer pressing it does nothing", store.one(
    "SELECT status FROM transactions WHERE id = ?", (t["id"],))["status"] == "pending")
tap(ADMIN_TG, "ok:%d" % t["id"])
check("the operator approves it", store.user_by_telegram(CUSTOMER)["status"] == "active")
check("and is told", "تأیید شد" in tg.last(ADMIN_TG)["text"])
check("the buttons are taken off", any(m == "editMessageReplyMarkup" for m, _ in tg.out))
tap(ADMIN_TG, "ok:%d" % t["id"])
check("pressing again changes nothing", "قبلاً بررسی شده" in tg.last(ADMIN_TG)["text"])
panel_speaks()
check("the customer hears it was approved", "تأیید شد" in tg.last(CUSTOMER)["text"]
      and "ماهانه" in tg.last(CUSTOMER)["text"])
message(CUSTOMER, botmod.B_ACCOUNT)
check("and their account shows the plan", "ماهانه" in tg.last(CUSTOMER)["text"]
      and "فعال" in tg.last(CUSTOMER)["text"])
check("  with the days left, beside the end date",
      re.search(r"زمان باقی‌مانده: (29|30) روز", tg.last(CUSTOMER)["text"]) is not None,
      tg.last(CUSTOMER).get("text"))
_now = botmod.datetime(2026, 9, 29, 12, tzinfo=botmod.timezone.utc)
check("  in hours on the last day, and said when it is over",
      botmod.time_left_fa("2026-09-29T17:30:00+00:00", _now) == "5 ساعت"
      and botmod.time_left_fa("2026-09-29T11:00:00+00:00", _now) == "تمام شده"
      and botmod.time_left_fa("", _now) == "")
message(ADMIN_TG, "/stats")
check("the operator's numbers", "گزارش روزانه" in tg.last(ADMIN_TG)["text"]
      and "200,000" in tg.last(ADMIN_TG)["text"])

print("an address")
message(CUSTOMER, botmod.B_IP)
check("the one-time link is offered first", "/go/" in tg.last(CUSTOMER)["text"])
message(CUSTOMER, "192.168.1.1")
check("a LAN address is refused, and asked again", "آی‌پی نامعتبر" in tg.last(CUSTOMER)["text"]
      and bot.state.get(CUSTOMER, (None,))[0] == "ip")
message(CUSTOMER, "۵.۱۲۰.۱.۲")
check("a real one, even in Persian digits, is registered",
      store.one("SELECT ip FROM ips")["ip"] == "5.120.1.2")
check("  and the answer does not repeat the DNS address", "DNS را روی" not in tg.last(CUSTOMER)["text"])

print("the DNS button")
panel.BotAPI.relays = ("198.51.100.4", "198.51.100.5")
message(CUSTOMER, "/dns")
first = tg.last(CUSTOMER)["text"]
check("every server of the customer's with its DNS, and before one has DoH, no DoH",
      "198.51.100.4" in first and "198.51.100.5" in first and "DoH" not in first, first)
check("  and no iPhone profile yet", tg.docs == [])
store.set_setting("doh_host:198.51.100.4", "r1.example.com")
store.set_setting("doh_host:198.51.100.5", "r2.example.com")
message(CUSTOMER, botmod.B_DNS)
doh_text = tg.last(CUSTOMER)["text"]
token = store.one("SELECT doh_token FROM users WHERE telegram_id = ?", (CUSTOMER,))["doh_token"]
check("then each server's own DoT name and the customer's DoH address on it",
      "https://r1.example.com/dns-query/%s" % token in doh_text
      and "https://r2.example.com/dns-query/%s" % token in doh_text
      and "r1.example.com" in doh_text and "آی‌پی ثبت نکرده" not in doh_text, doh_text)
import plistlib
check("with a button for the iPhone profile, beside the other two, on a row of its own",
      [b["callback_data"] for b in tg.buttons(CUSTOMER)] == ["login", "dohnew", "iosprofile"]
      and tg.buttons(CUSTOMER)[2]["text"] == "دریافت پروفایل آیفون📱"
      and len(tg.last(CUSTOMER)["reply_markup"]["inline_keyboard"]) == 2)
check("  nothing is sent until it is pressed", tg.docs == [])
tap(CUSTOMER, "iosprofile")
profiles = [plistlib.loads(d["bytes"]) for d in tg.docs]
check("pressed, the profile comes as a file for each server",
      [d["name"] for d in tg.docs] == ["dns-1.mobileconfig", "dns-2.mobileconfig"]
      and all(d["chat_id"] == CUSTOMER for d in tg.docs), str([d["name"] for d in tg.docs]))
check("  each turning on the customer's own DoH address there, by that server's IP",
      [p["PayloadContent"][0]["DNSSettings"] for p in profiles] == [
          {"DNSProtocol": "HTTPS", "ServerURL": "https://r1.example.com/dns-query/%s" % token,
           "ServerAddresses": ["198.51.100.4"]},
          {"DNSProtocol": "HTTPS", "ServerURL": "https://r2.example.com/dns-query/%s" % token,
           "ServerAddresses": ["198.51.100.5"]}])
check("  named for its server, and saying how to install it",
      "1" in tg.docs[0]["caption"] and "Save to Files" in tg.docs[0]["caption"]
      and "Profile Downloaded" in tg.docs[0]["caption"])
check("  the same file again replaces the profile, a new address makes a new one",
      plistlib.loads(botmod.ios_profile("https://r1.example.com/dns-query/%s" % token,
                                        "198.51.100.4"))["PayloadUUID"]
      == profiles[0]["PayloadUUID"] != profiles[1]["PayloadUUID"]
      and plistlib.loads(botmod.ios_profile("https://r1.example.com/dns-query/other"))[
          "PayloadUUID"] != profiles[0]["PayloadUUID"])
check("  and the message points to the button, not to the web page",
      "دریافت پروفایل آیفون" in doh_text and "در پنل وب، بخش" not in doh_text)
tg.docs.clear()
cust = store.one("SELECT id FROM users WHERE telegram_id = ?", (CUSTOMER,))["id"]
store.run("UPDATE users SET relays = '198.51.100.5' WHERE id = ?", (cust,))
message(CUSTOMER, botmod.B_DNS)
picked = tg.last(CUSTOMER)["text"]
tap(CUSTOMER, "iosprofile")
check("only the profiles of the servers the customer is given",
      [d["name"] for d in tg.docs] == ["dns.mobileconfig"]
      and "r2.example.com" in plistlib.loads(tg.docs[0]["bytes"])[
          "PayloadContent"][0]["DNSSettings"]["ServerURL"])
check("the ticks that pick a customer's DNS pick their DoT and DoH too",
      "r2.example.com" in picked and "r1.example.com" not in picked
      and "198.51.100.4" not in picked, picked)
store.run("UPDATE users SET relays = NULL WHERE id = ?", (cust,))
check("with a way into the web page too",
      tg.buttons(CUSTOMER)[0]["callback_data"] == "login")
check("and the button is on the menu, in place of the old one",
      botmod.B_DNS in sum(botmod.MENU["keyboard"], [])
      and not any("DNS امن" in b for b in sum(botmod.MENU["keyboard"], [])))
tap(CUSTOMER, "dohnew")
fresh = store.one("SELECT doh_token FROM users WHERE telegram_id = ?", (CUSTOMER,))["doh_token"]
check("the new-address button replaces it, and shows the new one",
      fresh != token and "https://r1.example.com/dns-query/%s" % fresh in tg.last(CUSTOMER)["text"])
panel.BotAPI.relays = ("198.51.100.4",)

print("support")
message(CUSTOMER, botmod.B_SUPPORT)
tap(CUSTOMER, "tknew")
message(CUSTOMER, "بازی وصل نمی‌شه")
message(CUSTOMER, "از دیشب FC26 بالا نمیاد")
tk = store.one("SELECT * FROM tickets")
check("a ticket is opened", tk and tk["subject"] == "بازی وصل نمی‌شه")
panel_speaks()
check("the operator hears, with a reply button", "بازی وصل نمی‌شه" in tg.last(ADMIN_TG)["text"]
      and tg.buttons(ADMIN_TG)[0]["callback_data"] == "areply:%d" % tk["id"])
tap(ADMIN_TG, "areply:%d" % tk["id"])
message(ADMIN_TG, "DNS رو روی هر دو خانه بذارید")
check("the operator answers from Telegram", store.one(
    "SELECT status FROM tickets WHERE id = ?", (tk["id"],))["status"] == "answered")
panel_speaks()
check("the customer gets the answer, with a way to reply",
      "DNS رو روی هر دو خانه بذارید" in tg.last(CUSTOMER)["text"]
      and tg.buttons(CUSTOMER)[0]["callback_data"] == "tkr:%d" % tk["id"])
tap(CUSTOMER, "tkr:%d" % tk["id"])
message(CUSTOMER, "درست شد ممنون")
tap(CUSTOMER, "tk:%d" % tk["id"])
check("and sees the whole conversation", "درست شد ممنون" in tg.last(CUSTOMER)["text"]
      and "پشتیبانی" in tg.last(CUSTOMER)["text"])

print("a screenshot of the error")
panel.THROTTLE.clear("ticket:%d" % store.one("SELECT user_id FROM tickets")["user_id"])
tap(CUSTOMER, "tknew")
message(CUSTOMER, "خطای بازی")
check("the bot says a picture will do", "عکس خطا" in tg.last(CUSTOMER)["text"])
message(CUSTOMER, photo="PHOTO1")
shot = store.one("SELECT * FROM tickets ORDER BY id DESC LIMIT 1")
first = store.one("SELECT * FROM ticket_messages WHERE ticket_id = ?", (shot["id"],))
check("a photo with no words opens the ticket", shot["subject"] == "خطای بازی"
      and first["body"] == "" and first["image_blob"] is not None)
panel_speaks()
pic = tg.last(ADMIN_TG, "sendPhoto")
check("the operator gets the photo itself, the ticket under it, with the reply button",
      pic.get("bytes") == PNG and "خطای بازی" in pic.get("caption", "")
      and pic["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "areply:%d"
      % shot["id"])
tap(CUSTOMER, "tkr:%d" % shot["id"])
updates[0] += 1
bot.handle({"update_id": updates[0], "message": {
    "message_id": updates[0], "chat": {"id": CUSTOMER, "type": "private"},
    "from": {"id": CUSTOMER, "first_name": "علی"}, "caption": "این یکی هم",
    "document": {"file_id": "PHOTO1", "mime_type": "image/png", "file_name": "s.png"}}})
last = store.one("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id DESC LIMIT 1",
                 (shot["id"],))
check("a screenshot sent as a file is taken too", last["body"] == "این یکی هم"
      and last["image_blob"] is not None)
tap(CUSTOMER, "tkr:%d" % shot["id"])
updates[0] += 1
bot.handle({"update_id": updates[0], "message": {
    "message_id": updates[0], "chat": {"id": CUSTOMER, "type": "private"},
    "from": {"id": CUSTOMER, "first_name": "علی"},
    "document": {"file_id": "TEXTFILE", "mime_type": "image/png", "file_name": "x.png"}}})
check("  a file that is no picture is refused, and the bot waits for another",
      "JPG" in tg.last(CUSTOMER)["text"]
      and bot.state.get(CUSTOMER, (None,))[0] == "ticket_reply")
message(CUSTOMER, botmod.B_CANCEL)
panel_speaks()
tap(ADMIN_TG, "areply:%d" % shot["id"])
message(ADMIN_TG, photo="PHOTO1")
panel_speaks()
pic = tg.last(CUSTOMER, "sendPhoto")
check("the operator answers with a photo alone, and the customer gets the photo",
      pic.get("bytes") == PNG and "خطای بازی" in pic.get("caption", "")
      and pic["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "tkr:%d"
      % shot["id"])

print("linking a web account")
web = store.create_web_user("sara", "سارا", "sara-password")
SARA = 222
message(SARA, botmod.B_ACCOUNT, name="سارا")      # an empty bot account appears
bot.state.pop(SARA, None)                          # and they do not finish the questions
res = panel.link_code(store, store.one("SELECT * FROM users WHERE id = ?", (web["id"],)),
                      "sara-password")
message(SARA, "/start link_" + res["code"], name="سارا")
check("the link from the web panel works through /start",
      "وصل شد" in tg.last(SARA)["text"]
      and store.user_by_telegram(SARA)["id"] == web["id"], tg.last(SARA).get("text"))
check("and the empty account the bot had opened is gone",
      store.one("SELECT count(*) c FROM users WHERE telegram_id = ?", (SARA,))["c"] == 1)

print("an account deleted in the admin panel")
GONE = 333
message(GONE, botmod.B_ACCOUNT, name="رضا")
first = store.user_by_telegram(GONE)
bot.state.pop(GONE, None)                          # they leave before the questions
admin.STORE.delete_user(first["id"])
message(GONE, botmod.B_ACCOUNT, name="رضا")
again = store.user_by_telegram(GONE)
check("the same Telegram id opens a new one, with no restart of the bot",
      again is not None, tg.last(GONE).get("text"))
check("and is asked the questions again, not shown an error",
      "اسمتان چیست" in tg.last(GONE)["text"], tg.last(GONE).get("text"))
bot.state.pop(GONE, None)
admin.STORE.delete_user(again["id"])
store.set_setting("ref_on", "1")
store.set_setting("ref_percent", "10")
inviter = store.user_by_telegram(CUSTOMER)
message(GONE, "/start ref_" + panel.ref_code(store, inviter), name="رضا")
check("so does one coming back through an invitation, which is written down",
      (store.user_by_telegram(GONE) or {"referred_by": None})["referred_by"] == inviter["id"])

print("a channel to join first")


class ChannelTelegram(FakeTelegram):
    """Answers who is in the channel from `members`."""
    members = {}

    def call(self, method, http_timeout=30, **params):
        if method != "getChatMember":
            return super().call(method, http_timeout, **params)
        self.out.append((method, params))
        status = self.members.get(params["user_id"], "left")
        if status == "broken":
            raise RuntimeError("telegram getChatMember: Bad Request: member list is "
                               "inaccessible")
        return {"status": status}


ctg = ChannelTelegram()
gated = botmod.Bot(botmod.settings(dict(
    BOT_TOKEN="t", API_URL=cfg["api"], API_KEY="dd_botkey", ADMIN_IDS=str(ADMIN_TG),
    JOIN_CHANNEL="@dns_sales", JOIN_TITLE="فروش DNS")), telegram=ctg)
NEW = 444


def say_to(b, uid, text):
    updates[0] += 1
    b.handle({"update_id": updates[0], "message": {
        "message_id": updates[0], "chat": {"id": uid, "type": "private"},
        "from": {"id": uid, "first_name": "نیما"}, "text": text}})


def tap_on(b, uid, data):
    updates[0] += 1
    b.handle({"update_id": updates[0], "callback_query": {
        "id": str(updates[0]), "data": data, "from": {"id": uid},
        "message": {"message_id": 1, "chat": {"id": uid}}}})


def join_buttons(uid):
    markup = ctg.last(uid).get("reply_markup") or {}
    return [b for row in markup.get("inline_keyboard", []) for b in row]


say_to(gated, NEW, "/start")
check("somebody not in it is greeted and asked to join first",
      "خوش آمدید" in ctg.last(NEW)["text"] and "فروش DNS" in ctg.last(NEW)["text"]
      and [b.get("url") or b.get("callback_data") for b in join_buttons(NEW)]
      == ["https://t.me/dns_sales", "joined"], ctg.last(NEW).get("text"))
say_to(gated, NEW, botmod.B_ACCOUNT)
check("every button asks the same, and no account is opened",
      "عضو کانال" in ctg.last(NEW)["text"] and not store.user_by_telegram(NEW))
tap_on(gated, NEW, "plans")
check("inline buttons too", "عضو کانال" in ctg.last(NEW)["text"])
tap_on(gated, NEW, "joined")
check("'I have joined' before joining says not yet", "هنوز" in ctg.last(NEW)["text"])
ChannelTelegram.members[NEW] = "member"
tap_on(gated, NEW, "joined")
check("after joining, the menu", ctg.last(NEW)["reply_markup"] == botmod.MENU)
say_to(gated, NEW, botmod.B_ACCOUNT)
check("and the bot serves them", store.user_by_telegram(NEW) is not None
      and "اسمتان چیست" in ctg.last(NEW)["text"])
gated.state.pop(NEW, None)
asked = sum(1 for m, _ in ctg.out if m == "getChatMember")
say_to(gated, NEW, botmod.B_HELP)
check("Telegram is not asked again at every tap",
      sum(1 for m, _ in ctg.out if m == "getChatMember") == asked)
say_to(gated, ADMIN_TG, "/stats")
check("the operator is never asked to join", "گزارش روزانه" in ctg.last(ADMIN_TG)["text"])
ChannelTelegram.members[555] = "broken"
say_to(gated, 555, "/start")
check("when Telegram cannot say - the bot no longer an admin there - nobody is kept out",
      ctg.last(555)["reply_markup"] == botmod.MENU)
ChannelTelegram.members[666] = "restricted"
say_to(gated, 666, "/start")
check("somebody restricted who has left is not in it", "عضو کانال" in ctg.last(666)["text"])
ChannelTelegram.members[NEW] = "left"
gated.members.clear()
say_to(gated, NEW, botmod.B_HELP)
check("and somebody who leaves is asked again", "عضو کانال" in ctg.last(NEW)["text"])
code = panel.ref_code(store, store.user_by_telegram(CUSTOMER))
say_to(gated, 777, "/start ref_" + code)
check("an invitation is written down even before they join", "عضو کانال" in ctg.last(777)["text"]
      and store.user_by_telegram(777)["referred_by"] == store.user_by_telegram(CUSTOMER)["id"])
open_bot = botmod.settings(dict(BOT_TOKEN="t", API_URL="x", API_KEY="k",
                                JOIN_CHANNEL="-1001234567890"))
check("a private channel with no link to join by asks nobody", open_bot["channel"] == "")

print("the panel's messages must be the panel's")
req = urllib.request.Request("http://127.0.0.1:%d/" % hook.server_address[1],
                             data=b'{"event":"x"}', method="POST")
req.add_header("X-MasterDNS-Signature", "t=1,v1=bad")
try:
    urllib.request.urlopen(req, timeout=5)
    code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("an unsigned message is refused", code == 401 and work.empty())
before = len(tg.out)
bot.on_event({"id": 12345, "event": "quota.warning", "telegram_id": CUSTOMER,
              "data": {"text": "۸۰٪"}})
bot.on_event({"id": 12345, "event": "quota.warning", "telegram_id": CUSTOMER,
              "data": {"text": "۸۰٪"}})
check("one delivered twice is said once", len(tg.out) == before + 1)

api.shutdown()
hook.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
