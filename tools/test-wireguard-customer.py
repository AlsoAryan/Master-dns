#!/usr/bin/env python3
"""WireGuard from the customer's side: their page and the bot.

What has to hold: a customer is offered WireGuard only when it is on, their
plan has it and a relay that serves them has it ready - or when they already
have configs to fetch. They make a config while devices are free and delete
one, when the operator lets them; otherwise support does it. Each config is
handed over as its text, a file under the admin's name, and a QR code the
WireGuard app scans (none, said plainly, without qrencode). The page shows
it all, and the bot has a button for it beside the iPhone profile, sends the
QR and the file, and makes and deletes them. Another customer's config is
never theirs to see or delete.
"""
import base64
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("templates/smartdns-panel", "panel")
sync = load("templates/smartdns-sync", "sync")
bot = load("examples/telegram-bot/bot.py", "bot")
panel.log = sync.log = lambda *a: None
bot.log = lambda *a: None
RELAY = "198.51.100.1"
relays = [RELAY]

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
now = panel.now()
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)", (now,))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ماهانه', 1, 30, 0, 1, ?)", (now,))
store.run("INSERT INTO users (username, telegram_id, created_at, status, max_ips, plan_id,"
          " expires_at) VALUES ('ali', 111, ?, 'active', 2, 1, '2099-01-01T00:00:00+00:00')",
          (now,))
store.run("INSERT INTO users (username, telegram_id, created_at, status, max_ips)"
          " VALUES ('sara', 222, ?, 'active', 1)", (now,))
ali = lambda: store.one("SELECT * FROM users WHERE id = 1")
sara = lambda: store.one("SELECT * FROM users WHERE id = 2")
_, SERVER_PUB = panel.wg_keypair()

print("offered or not")
check("off: not offered", panel.wg_view(store, ali(), relays)["on"] is False
      and panel.user_view(store, ali(), relays)["wg_on"] is False)
store.set_setting("wg_on", "1")
check("on, but no relay ready: not yet", panel.wg_view(store, ali(), relays)["on"] is False)
store.set_setting("wg_pub:" + RELAY, SERVER_PUB)
view = panel.wg_view(store, ali(), relays)
check("on and ready: offered, theirs to make, one config for the one relay",
      view["on"] and view["self"] and view["room"] == 1 and view["devices"] == []
      and view["bind"] is True
      and panel.user_view(store, ali(), relays)["wg_on"] is True)
store.set_setting("wg_plans", '["9"]')
check("their plan without it: not offered", panel.wg_view(store, ali(), relays)["on"] is False)
store.set_setting("wg_plans", "")

print("making and deleting")
store.set_setting("wg_self", "0")
r = panel.wg_customer_new(store, ali(), relays)
check("the operator's to make: the customer is told to ask",
      r.get("error") == "wg_admin_only" and "پشتیبانی" in r["message"])
store.set_setting("wg_self", "1")
r = panel.wg_customer_new(store, ali(), relays)
check("theirs to make: made, the keys not in the answer", r.get("ok")
      and r.get("device_id") and "config" not in r and "سرور" in r["message"], str(r))
dev = r["device_id"]
view = panel.wg_view(store, ali(), relays)
check("  listed, the relay's one config taken, not yet connected",
      view["room"] == 0 and view["relays"] == [] and len(view["devices"]) == 1
      and view["devices"][0]["file"] == "master-dns-%d.conf" % dev
      and view["devices"][0]["online"] is False and view["devices"][0]["last"] == "")
r = panel.wg_customer_new(store, ali(), relays)
check("  a second for the same relay: refused, whatever the devices",
      r.get("error") == "wg_have")
check("somebody else's config: not theirs to delete",
      panel.wg_customer_delete(store, sara(), dev).get("error") == "wg_not_found")
store.set_setting("wg_self", "0")
check("  nor theirs when the operator keeps it",
      panel.wg_customer_delete(store, ali(), dev).get("error") == "wg_admin_only")
store.set_setting("wg_self", "1")
check("their own: deleted, and theirs to make again",
      panel.wg_customer_delete(store, ali(), dev).get("ok")
      and panel.wg_view(store, ali(), relays)["room"] == 1)
dev = panel.wg_customer_new(store, ali(), relays)["device_id"]

print("handed over")
check("somebody else's: not found", panel.wg_handout(store, 2, dev).get("error")
      == "wg_not_found")
panel.QRENCODE = os.path.join(tmp, "no-qrencode-here")
h = panel.wg_handout(store, 1, dev)
check("its text and its file's name; without qrencode, no QR - said, not broken",
      h.get("ok") and h["config"].startswith("[Interface]") and h["file"].endswith(".conf")
      and h["qr"] == "", str(h)[:200])
PNG = b"\x89PNG\r\n\x1a\nfake"


class Made:
    returncode, stdout = 0, PNG


seen = {}


def fake_run(cmd, input=None, **kw):
    seen["cmd"], seen["input"] = cmd, input
    return Made()


panel.QRENCODE = sys.executable
panel.subprocess.run = fake_run
h = panel.wg_handout(store, 1, dev)
check("with qrencode: the QR of exactly that config, a PNG",
      base64.b64decode(h["qr"]) == PNG and seen["input"] == h["config"].encode()
      and seen["cmd"][1:3] == ["-t", "PNG"])
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("the customer's page gets it with everything else, and its three calls",
      '"wg": wg_view(self.store, user, self.relays),' in psrc
      and '("/user-wg-new", "/user-wg-del", "/user-wg-config", "/user-wg-all")' in psrc)
check("the bot's API: list, make, fetch, delete",
      all(x in psrc for x in ('"wg_list"', '"wg_new"', '"wg_get"', '"wg_delete"')))


class FakeAPI:
    store = None

    def __init__(self, user):
        self.store, self.relays, self._seller, self.user = store, relays, None, user

    def customer(self, tg):
        return self.user, None


panel.wg_customer_delete(store, ali(), dev)
code, body = panel.BotAPI.api_wg_new(FakeAPI(ali()), {}, 111)
dev = body.get("device_id", dev)
check("the bot makes one: 201, with the config, its file and QR at once",
      code == 201 and body["config"].startswith("[Interface]") and body["qr"], str(code))
code, body = panel.BotAPI.api_wg_new(FakeAPI(ali()), {}, 111)
check("  a second for the relay: 409", code == 409 and body["error"] == "wg_have")
code, body = panel.BotAPI.api_wg_get(FakeAPI(sara()), {}, 222, str(dev))
check("another's, by the bot: 404", code == 404)
code, body = panel.BotAPI.api_wg_list(FakeAPI(ali()), {}, 111)
check("the list, by the bot", code == 200 and len(body["wg"]["devices"]) == 1)

print("the customer's page")
check("off: nothing", sync.wg_box({"wg": {"on": False}}) == "")
info = {"wg": panel.wg_view(store, ali(), relays)}
box = sync.wg_box(info)
check("its configs: how each is doing, and one link to them all",
      "href='/wg'" in box and "هنوز وصل نشده" in box and "WireGuard" in box)
info["wg"]["room"] = 1
check("  a free device: a button for another", "action='/wg-new'" in sync.wg_box(info))
info["wg"]["self"] = False
box = sync.wg_box(info)
check("  the operator's to make: no buttons to make or delete",
      "action='/wg-new'" not in box and "action='/wg-del'" not in box)
fresh = {"on": True, "self": True, "room": 1, "devices": []}
check("no config yet: one button to get it",
      "دریافت کانفیگ وایرگارد" in sync.wg_box({"wg": fresh}))
full = dict(fresh, room=0, relays=[], devices=[{"id": 9, "address": "10.66.0.9",
                                                "relay": "198.51.100.1", "server": "سرور 1"}])
check("  a config for every server: no button to make more, the link to them",
      "href='/wg'" in sync.wg_box({"wg": full})
      and "action='/wg-new'" not in sync.wg_box({"wg": full}))
check("  bound: the page says it works where the address is registered",
      "آی‌پی‌شان را ثبت کرده‌اید" in sync.wg_box({"wg": dict(fresh, bind=True)})
      and "آی‌پی ثبت کردن لازم نیست" in sync.wg_box({"wg": dict(fresh, bind=False)}))
page = sync.wg_config_page(dict(h, id=dev))
check("its page: the QR, the file to download, the text to copy",
      "data:image/png;base64," + h["qr"] in page and "href='/wg/%d.conf'" % dev in page
      and "<textarea" in page and "[Interface]" in page)
check("  without a QR, the file and text still", "<img" not in sync.wg_config_page(
    dict(h, id=dev, qr="")) and "/wg/%d.conf" % dev in sync.wg_config_page(dict(h, id=dev, qr="")))
ssrc = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("the page has it beside DoH, and its addresses",
      "body.append(wg_box(info))" in ssrc and 'r"/wg/(\\d{1,12})(\\.conf)?"' in ssrc
      and 'if path in ("/wg-new", "/wg-del"):' in ssrc
      and '"Content-Disposition": "attachment; filename=%s" % res["file"]' in ssrc)

print("the bot")
said, photos, docs, calls = [], [], [], []


class FakePanel:
    def call(self, method, path, body=None, idem=None):
        calls.append((method, path))
        if method == "GET" and path.endswith("/wg"):
            return {"wg": panel.wg_view(store, ali(), relays)}
        if method == "GET":
            return panel.wg_handout(store, 1, int(path.rsplit("/", 1)[1]))
        if method == "DELETE":
            return {"ok": True, "message": "کانفیگ وایرگارد حذف شد"}
        return dict(panel.wg_handout(store, 1, dev), ok=True)


class FakeTg:
    def photo(self, chat, blob, caption, markup=None):
        photos.append((blob, caption))

    def document(self, chat, filename, blob, caption=""):
        docs.append((filename, blob))


b = bot.Bot.__new__(bot.Bot)
b.panel, b.tg, b.en = FakePanel(), FakeTg(), False
b.say = lambda chat, text, markup=None: said.append((text, markup))
bot.Bot.show_wg(b, 5, {"id": 111})
text, markup = said[-1]
flat = [x for row in markup["inline_keyboard"] for x in row]
check("its list: all configs in one button, each to delete, and the way to install",
      any(x["callback_data"] == "wgall" for x in flat)
      and any(x["callback_data"] == "wgdel:%d" % dev for x in flat) and "WireGuard" in text)
bot.Bot.send_wg(b, 5, panel.wg_handout(store, 1, dev))
check("a config sent: its QR as a photo, its text as a file under its name",
      photos and photos[-1][0] == PNG and docs[-1][0] == "master-dns-%d.conf" % dev
      and docs[-1][1].startswith(b"[Interface]"))
check("the DNS buttons: two in a row, each after on its own",
      bot.Bot.dns_rows([1, 2, 3, 4]) == [[1, 2], [3], [4]] and bot.Bot.dns_rows([1, 2]) == [[1, 2]])
bsrc = open(os.path.join(ROOT, "examples", "telegram-bot", "bot.py"), encoding="utf-8").read()
check("the button beside the iPhone profile's, when offered, in both DNS screens",
      bsrc.count('if u.get("wg_on"):\n            buttons.append({"text": B_WG, "callback_data": "wg"})')
      == 2 or bsrc.count('buttons.append({"text": B_WG, "callback_data": "wg"})') == 2)
check("  and its answers: list, fetch, make, delete",
      all(x in bsrc for x in ('if kind == "wg":', 'if kind in ("wgget", "wgnew", "wgall"):',
                              'if kind == "wgdel":')))

print("the admin and the installer")
asrc = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("the admin decides whether customers make their own",
      "name='self' value='1'" in asrc and '"wg_self": "1" if one("self") == "1" else "0",' in asrc)
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("the exit gets qrencode for the QR, and uninstall may take it",
      'python3 openssl nftables qrencode"' in logic
      and "wireguard-tools qrencode $packages" in logic)
docs_text = open(os.path.join(ROOT, "docs", "bot-api.md"), encoding="utf-8").read()
check("the bot API's page says how", "/users/{telegram_id}/wg" in docs_text)

store.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
