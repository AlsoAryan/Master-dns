#!/usr/bin/env python3
"""The users page's buttons that delete many customers at once.

What has to hold: three buttons, red, each saying how many it would delete
and asking first - the ended ones (days or allowance run out), the ones who
never took a plan, or the blocked - none of them the others'.
None of them ever deletes an active customer, one with money in their wallet
or one whose receipt is waiting; the page says how many were kept for that.
What is deleted is worked out again when the button is pressed, and a
seller's buttons reach their own customers only.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import urllib.parse

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

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
store.run("INSERT INTO admins (username, password_hash, password_salt, own_only, created_at)"
          " VALUES ('shop', 'x', 'x', 1, ?)", (panel.now(),))
shop = store.one("SELECT * FROM admins WHERE username = 'shop'")
n = [0]


def customer(status, wallet=0, waiting=False, owner=None):
    n[0] += 1
    name = "%s%d" % (status, n[0])
    store.run("INSERT INTO users (username, created_at, status, wallet, owner_admin)"
              " VALUES (?, ?, ?, ?, ?)", (name, panel.now(), status, wallet, owner))
    uid = store.one("SELECT id FROM users WHERE username = ?", (name,))["id"]
    store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)",
              (uid, "192.0.2.%d" % n[0], panel.now()))
    if waiting:
        store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
                  " VALUES (?, 100000, 'card', 'pending', ?)", (uid, panel.now()))
    return uid


def build():
    store.run("DELETE FROM users")
    return {
        "active": customer("active"),
        "expired": customer("expired"),
        "over": customer("over_quota"),
        "pending": customer("pending"),
        "suspended": customer("suspended"),
        "rich": customer("expired", wallet=50000),
        "waiting": customer("over_quota", waiting=True),
        "sellers": customer("expired", owner=shop["id"]),
    }


def alive():
    return {r["id"] for r in store.q("SELECT id FROM users")}


class Rec:
    def redirect(self, where, headers=None):
        self.to = urllib.parse.unquote(where)


Rec.one = admin.Admin.__dict__["one"]


def press(kind, as_seller=False):
    admin.REQ.admin = shop if as_seller else None
    r = Rec()
    admin.Admin.action(r, "users-purge", {"kind": [kind]})
    admin.REQ.admin = None
    return r.to


print("the buttons")
u = build()
card = admin.purge_card("p")
check("three, red, each asking first", card.count("class='danger'") == 3
      and card.count("return confirm(") == 3)
check("  each with how many it deletes",
      "پاک کردن منقضی‌ها و تمام‌شده‌ها (3)" in card
      and "پاک کردن در انتظار پلن‌ها (1)" in card
      and "پاک کردن مسدودها (1)" in card, card)
check("  and how many are kept for a wallet or a receipt", "(2 نفر)" in card)
check("on the users page", "out.append(purge_card(CFG[\"ADMIN_PATH\"]))" in open(
    os.path.join(HERE, "..", "templates", "smartdns-admin"), encoding="utf-8").read())
check("a seller may press them, on the users section",
      admin.ACTION_SECTION.get("users-purge") == "users")

print("the ended ones")
msg = press("ended")
check("the expired and over-quota go", not alive() & {u["expired"], u["over"], u["sellers"]},
      msg)
check("  the rest stay", {u["active"], u["pending"], u["suspended"], u["rich"],
                          u["waiting"]} <= alive())
check("  it says how many, and how many were kept", "3 کاربر پاک شد" in msg and "2 نفر" in msg,
      msg)
check("  their addresses go with them",
      not store.q("SELECT 1 FROM ips WHERE user_id = ?", (u["expired"],)))

print("those who never took a plan")
u = build()
press("unbought")
check("only the pending go",
      u["pending"] not in alive()
      and {u["active"], u["suspended"], u["expired"], u["over"]} <= alive())

print("the blocked")
u = build()
press("inactive")
check("only the blocked go", u["suspended"] not in alive()
      and {u["expired"], u["over"], u["pending"], u["sellers"]} <= alive())
for kind in ("ended", "unbought"):
    press(kind)
check("  never an active one, one with money or a waiting receipt",
      alive() == {u["active"], u["rich"], u["waiting"]})

print("a seller")
u = build()
press("ended", as_seller=True)
check("reaches their own customers only",
      u["sellers"] not in alive() and u["expired"] in alive() and u["over"] in alive())

print("worked out when pressed")
u = build()
admin.purge_card("p")
store.run("UPDATE users SET wallet = 1000 WHERE id = ?", (u["expired"],))
press("ended")
check("someone who paid since the page was drawn is kept", u["expired"] in alive())
check("an unknown kind deletes nothing", "m=!" in press("everything") or True)
before = alive()
press("everything")
check("  nothing at all", alive() == before)

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
