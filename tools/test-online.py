#!/usr/bin/env python3
"""Who is online now.

What has to hold: a relay's half-minute report that carries more than a
trickle of a customer's traffic marks them seen, now, on that relay; a
trickle - a phone's background chatter - does not. Seen in the last three
minutes is online. The home page counts them and lists them, busiest first,
with the server, their speed of the last ten minutes or so and how long ago;
a seller's home lists their own customers only, and the owner's says whose
customer each is. The users table puts a green dot beside the ones online,
and the bot's report and /stats say how many.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
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

RELAY, OTHER = "198.51.100.1", "198.51.100.2"


def customer(name, ip, owner=None):
    store.run("INSERT INTO users (username, created_at, status, owner_admin)"
              " VALUES (?, ?, 'active', ?)", (name, panel.now(), owner))
    uid = store.one("SELECT id FROM users WHERE username = ?", (name,))["id"]
    store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)", (uid, ip, panel.now()))
    return uid


store.run("INSERT INTO admins (username, password_hash, password_salt, own_only, created_at)"
          " VALUES ('shop', 'x', 'x', 1, ?)", (panel.now(),))
shop = store.one("SELECT id FROM admins WHERE username = 'shop'")["id"]
ali = customer("ali", "192.0.2.10")
sara = customer("sara", "192.0.2.11", owner=shop)
reza = customer("reza", "192.0.2.12")
row = lambda uid: store.one("SELECT * FROM users WHERE id = ?", (uid,))

print("marked seen by a relay's report")
MB = 1024 * 1024
store.fold_counters(RELAY, {"192.0.2.10": 5 * MB, "192.0.2.11": 2 * MB, "192.0.2.12": 1000},
                    {"192.0.2.10": [MB, 4 * MB], "192.0.2.11": [MB, MB],
                     "192.0.2.12": [500, 500]})
check("more than a trickle: seen now, on that relay",
      row(ali)["last_seen"] and row(ali)["last_server"] == RELAY)
check("a trickle is not being online", row(reza)["last_seen"] is None)
store.fold_counters(OTHER, {"192.0.2.10": 3 * MB}, {"192.0.2.10": [MB, 2 * MB]})
check("the next report through another relay moves them there", row(ali)["last_server"] == OTHER)
old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
store.run("UPDATE users SET last_seen = ? WHERE id = ?", (old, reza))

print("the home page")
rows = admin.online_rows()
check("the ones seen in the last three minutes, and not before",
      {r["username"] for r in rows} == {"ali", "sara"})
check("busiest first", [r["username"] for r in rows] == ["ali", "sara"])
check("with a speed from the last minutes", rows[0]["down_bps"] > rows[1]["down_bps"] > 0)
card = admin.online_card("p", rows, show_seller=True)
check("listed with the server and how long ago",
      "آنلاین الان (2)" in card and OTHER in card and "href='/p/usage?u=%d'" % ali in card
      and "همین الان" in card)
check("  and whose customer each is, on the owner's",
      "<th>فروشنده</th>" in card and "<td>shop</td>" in card)
mine = admin.online_rows(" AND u.owner_admin = ?", (shop,))
check("a seller's lists their own only", [r["username"] for r in mine] == ["sara"])
check("nobody online says so", "کسی آنلاین نیست" in admin.online_card("p", []))
src = open(os.path.join(HERE, "..", "templates", "smartdns-admin"), encoding="utf-8").read()
home = src[src.index("    def home(self):"):src.index("    def user_usage_page(self):")]
check("the home page counts them in the summary and shows the card",
      '"آنلاین"' in home and "online_rows(extra, args)" in home and "online_card(" in home)
check("  for an admin who may see the customers", 'if may("users") else None' in home)

print("the users table")
check("a dot beside the ones online", admin.is_online(row(ali)) and admin.is_online(row(sara))
      and not admin.is_online(row(reza)))
users = src[src.index("    def users(self, rows_only=False):"):]
check("  drawn in the name's cell", "<span class='dot on' title='آنلاین'></span>" in users
      and "if is_online(r)" in users)
store.run("UPDATE users SET created_at = '2026-09-28T22:00:00+00:00' WHERE id = ?", (ali,))
check("under each name: when the account was made, in Tehran, and that it is online now",
      admin.joined_and_seen(row(ali)) == "ثبت‌نام: 2026-09-29 · آنلاین",
      admin.joined_and_seen(row(ali)))
check("  or when it was last online", "آخرین آنلاین: 10 دقیقه پیش" in
      admin.joined_and_seen(row(reza)), admin.joined_and_seen(row(reza)))
store.run("INSERT INTO users (username, created_at, status) VALUES ('nobody', ?, 'pending')",
          (panel.now(),))
check("  or that it never was", "هنوز آنلاین نشده" in
      admin.joined_and_seen(store.one("SELECT * FROM users WHERE username = 'nobody'")))
check("  drawn in the users table, on two short lines", "joined_and_seen(r).split(" in users
      and "<small class='seen'>%s</small><small class='seen'>%s</small>" in users)
store.run("DELETE FROM users WHERE username = 'nobody'")

print("the bot")
s = panel.admin_stats(store)
check("the report counts them", s["online"] == 2 and "آنلاین الان: 2" in panel.daily_report_text(s))
check("  a seller's, their own", panel.admin_stats(store, shop)["online"] == 1)

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
