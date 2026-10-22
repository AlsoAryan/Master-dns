#!/usr/bin/env python3
"""WireGuard with more than one relay, and in the operator's numbers.

What has to hold: a customer may have configs on every relay that has
WireGuard ready, the admin left on and serves them - Server 1, Server 2, as
beside their DNS - and chooses which when there is more than one, on their
page, in the bot and in the admin panel; a relay that is not one of those is
refused. A relay taken off the panel takes its configs, and the devices they
held, with it. The home page counts WireGuard online beside online, and the
morning report says how many configs there are and how many are connected;
typed addresses are counted apart from configs.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

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
admin = load("templates/smartdns-admin", "admin")
bot = load("examples/telegram-bot/bot.py", "bot")
panel.log = sync.log = admin.log = lambda *a: None
bot.log = lambda *a: None
A, B = "198.51.100.1", "198.51.100.2"
relays = [A, B]

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
now = panel.now()
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)", (now,))
store.run("INSERT INTO users (username, telegram_id, created_at, status, max_ips, expires_at)"
          " VALUES ('ali', 111, ?, 'active', 3, '2099-01-01T00:00:00+00:00')", (now,))
user = lambda: store.one("SELECT * FROM users WHERE id = 1")
store.set_setting("wg_on", "1")
for r in relays:
    store.set_setting("wg_pub:" + r, panel.wg_keypair()[1])

store.run("INSERT INTO users (username, telegram_id, created_at, status, max_ips)"
          " VALUES ('sara', 222, ?, 'active', 1)", (now,))
sara = lambda: store.one("SELECT * FROM users WHERE id = 2")

print("which server")
view = panel.wg_view(store, user(), relays)
check("both relays offered, named as beside the DNS",
      view["relays"] == [{"ip": A, "label": "سرور 1", "note": ""},
                         {"ip": B, "label": "سرور 2", "note": ""}]
      and view["room"] == 2)
r = panel.wg_customer_new(store, user(), relays, B)
check("a config on the server chosen", r.get("ok") and store.one(
    "SELECT relay FROM wg_devices WHERE id = ?", (r["device_id"],))["relay"] == B)
r = panel.wg_customer_new(store, user(), relays)
check("  none chosen: the first without one", r.get("ok") and store.one(
    "SELECT relay FROM wg_devices WHERE id = ?", (r["device_id"],))["relay"] == A)
r = panel.wg_customer_new(store, user(), relays)
check("  one for each relay and no more", r.get("error") == "wg_have"
      and panel.wg_view(store, user(), relays)["room"] == 0)
check("  a server that is not one of those: refused",
      panel.wg_customer_new(store, sara(), relays, "203.0.113.9").get("error") == "wg_bad_relay")
store.set_setting("wg_relays", '["%s"]' % A)
check("  nor one the admin left off",
      panel.wg_customer_new(store, sara(), relays, B).get("error") == "wg_bad_relay"
      and [x["ip"] for x in panel.wg_view(store, sara(), relays)["relays"]] == [A])
store.set_setting("wg_relays", "")
devices = panel.wg_view(store, user(), relays)["devices"]
check("each config says its server", {d["server"] for d in devices} == {"سرور 1", "سرور 2"})
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("the customer's page and the bot pass the server chosen",
      'wg_customer_new(self.store, user, self.relays, body.get("relay"))' in psrc
      and psrc.count('body.get("relay")') >= 2)

print("everything at once")
store.set_setting("server_note:" + A, "مخابرات")
store.set_setting("server_note:" + B, "ایرانسل")
r = panel.wg_customer_new(store, sara(), relays)
check("nothing chosen: a config for every server in one go",
      r.get("ok") and len(r["device_ids"]) == 2 and {store.one(
          "SELECT relay FROM wg_devices WHERE id = ?", (i,))["relay"] for i in r["device_ids"]}
      == {A, B} and "همهٔ سرورها" in r["message"])
check("  and again: nothing left to make",
      panel.wg_customer_new(store, sara(), relays).get("error") == "wg_have")
every = panel.wg_handout_all(store, sara(), relays)
check("all of them handed out together, in the DNS's order, each with its server and its words",
      [(c["server"], c["note"]) for c in every["configs"]]
      == [("سرور 1", "مخابرات"), ("سرور 2", "ایرانسل")]
      and all(c["config"].startswith("[Interface]") for c in every["configs"]))
store.run("UPDATE users SET relays = ? WHERE id = 2", (B,))
check("a customer given only the second server: it is their Server 1, as beside the DNS",
      [c["server"] for c in panel.wg_handout_all(store, sara(), relays)["configs"]
       if c["relay"] == B] == ["سرور 1"])
store.run("UPDATE users SET relays = NULL WHERE id = 2")
page = sync.wg_all_page(dict(every, self=True))
check("the customer's page: every config on one page, each under its server and its words",
      page.count("data:image/png;base64,") + page.count("/wg/") >= 2
      and page.index("سرور 1") < page.index("مخابرات") < page.index("سرور 2")
      < page.index("ایرانسل") and page.count("<textarea") == 2
      and page.count("action='/wg-del'") == 2 and "action='/wg-new'" not in page)
box = sync.wg_box({"wg": panel.wg_view(store, sara(), relays)})
check("  the account page: how each is doing and one link, no button for another server",
      "href='/wg'" in box and "مخابرات" in box and "ایرانسل" in box
      and "action='/wg-new'" not in box and "<select" not in box)
fresh = {"on": True, "self": True, "room": 2, "devices": [],
         "relays": [{"ip": A, "label": "سرور 1"}, {"ip": B, "label": "سرور 2"}]}
check("  none yet: one button for all, nothing to pick",
      "action='/wg-new'" in sync.wg_box({"wg": fresh}) and "<select" not in sync.wg_box(
          {"wg": fresh}) and "name='relay'" not in sync.wg_box({"wg": fresh}))
ssrc = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("  made or deleted: back to all of them together",
      'return self.redirect("/wg", res.get("message", ""))' in ssrc
      and 'self.ask_panel("/user-wg-all", {})' in ssrc)

print("the bot")
said, photos, docs = [], [], []


class FakePanel:
    def call(self, method, path, body=None, idem=None):
        if path.endswith("/wg/all"):
            return panel.wg_handout_all(store, sara(), relays)
        return {"wg": panel.wg_view(store, sara(), relays)}


class FakeTg:
    def photo(self, chat, png, caption):
        photos.append(caption)

    def document(self, chat, name, blob, caption):
        docs.append(name)


b = bot.Bot.__new__(bot.Bot)
b.panel, b.en, b.tg = FakePanel(), False, FakeTg()
b.say = lambda chat, text, markup=None: said.append((text, markup))
b.t = lambda text: text
bot.Bot.show_wg(b, 5, {"id": 222})
text, markup = said[-1]
flat = [x for row in markup["inline_keyboard"] for x in row]
check("one button for all the configs, a delete for each server, no server to pick",
      any(x["callback_data"] == "wgall" for x in flat)
      and sum(x["callback_data"].startswith("wgdel:") for x in flat) == 2
      and not any(x["callback_data"].startswith("wgnew") for x in flat)
      and "مخابرات" in text and "ایرانسل" in text)
bot.Bot.send_wg_all(b, 5, [dict(c, qr="iVBORw0KGgo=") for c in
                           panel.wg_handout_all(store, sara(), relays)["configs"]])
check("  sent one after another, each QR under its server and its words",
      len(docs) == 2 and len(photos) == 2 and ((photos[0].startswith("🛡 سرور 1" + chr(10) + "مخابرات")
                                         and photos[1].startswith("🛡 سرور 2" + chr(10) + "ایرانسل"))))
bsrc = open(os.path.join(ROOT, "examples", "telegram-bot", "bot.py"), encoding="utf-8").read()
check("  a new one makes them all and sends them all",
      "return self.send_wg_all(chat, res.get(\"configs\") or [res])" in bsrc)
for d in store.q("SELECT id FROM wg_devices WHERE user_id = 2"):
    panel.delete_wg_device(store, 2, d["id"])

print("the admin panel")
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
open(admin.PANEL_ENV, "w").write("RELAY_IP=%s,%s\n" % (A, B))
admin.REQ.admin = None
check("a config on the relay chosen", not admin.wg_create(2, B).startswith("!")
      and store.one("SELECT count(*) c FROM wg_devices WHERE relay = ?", (B,))["c"] == 2)
check("  not a second on it", admin.wg_create(2, B).startswith("!"))
check("  a relay without it ready: refused", admin.wg_create(2, "203.0.113.9").startswith("!"))
page = admin.wg_page("/p/wg?u=2")
check("  the customer's page in the admin panel lets the admin pick",
      "<select name='relay'>" in page and "value='%s'" % B in page)
gone = [d["address"] for d in store.q("SELECT address FROM wg_devices WHERE relay = ?", (B,))]
admin.forget_server(B)
check("a relay taken off the panel: its configs gone, their rows with them, its key forgotten",
      store.one("SELECT count(*) c FROM wg_devices WHERE relay = ?", (B,))["c"] == 0
      and all(store.one("SELECT 1 FROM ips WHERE ip = ?", (a,)) is None for a in gone)
      and store.setting("wg_pub:" + B) == ""
      and store.one("SELECT count(*) c FROM wg_devices WHERE relay = ?", (A,))["c"] == 1)

print("the numbers")
store.run("UPDATE wg_devices SET last_handshake = ?",
          (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
stats = panel.admin_stats(store)
check("the morning numbers: configs, and connected now",
      stats["wg_configs"] == 1 and stats["wg_online"] == 1)
report = panel.daily_report_text(stats)
check("  and the report says so", "وایرگارد: 1 کانفیگ، 1 آنلاین" in report)
check("  nothing about it where there are none",
      "وایرگارد" not in panel.daily_report_text(dict(stats, wg_configs=0, wg_online=0)))
asrc = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("the home page: WireGuard online beside online, configs not counted as addresses",
      '"🛡 وایرگارد آنلاین"' in asrc and '" WHERE i.wg = 0" + extra, args)' in asrc)

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
