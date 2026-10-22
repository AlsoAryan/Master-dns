#!/usr/bin/env python3
"""Shamsi or Gregorian dates, as the admin picks.

What has to hold: one choice in the admin panel's settings - Gregorian until
somebody picks Shamsi - and every date shown follows it, in Tehran time: the
admin panel's, the panel's own sentences ("active until ..."), the dates it
gives the customer's page and the bot, the customer's page and the bot. The
four places convert alike and correctly, leap years and all; a moment late in
a UTC day is already the next day in Tehran; a short chart day is month/day
in either; and what is not a date - "never" - is left as it is.
"""
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

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
admin = load("templates/smartdns-admin", "admin")
sync = load("templates/smartdns-sync", "sync")
bot = load("examples/telegram-bot/bot.py", "bot")
admin.log = panel.log = sync.log = lambda *a: None

print("the conversion")
KNOWN = [((2026, 3, 21), (1405, 1, 1)), ((2026, 9, 29), (1405, 7, 7)),
         ((2025, 3, 20), (1403, 12, 30)), ((2025, 3, 21), (1404, 1, 1)),
         ((2024, 3, 20), (1403, 1, 1)), ((2026, 9, 22), (1405, 6, 31)),
         ((2026, 9, 23), (1405, 7, 1)), ((2027, 3, 21), (1406, 1, 1)),
         ((1979, 2, 11), (1357, 11, 22))]
for name, mod in (("admin", admin), ("panel", panel), ("page", sync), ("bot", bot)):
    got = [mod.to_jalali(*g) for g, _ in KNOWN]
    check("%s: known days, leap years and the turn of the year" % name,
          got == [j for _, j in KNOWN], str(got))
every = all(admin.to_jalali(d.year, d.month, d.day) == panel.to_jalali(d.year, d.month, d.day)
            == sync.to_jalali(d.year, d.month, d.day) == bot.to_jalali(d.year, d.month, d.day)
            for d in (date(2020, 1, 1) + timedelta(days=i) for i in range(0, 3000, 7)))
check("  all four alike, day after day for years", every)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)

LATE = "2026-09-29T21:00:00+00:00"        # 00:30 on the 30th in Tehran

print("the admin panel")
admin.CALENDAR["at"] = 0.0
check("Gregorian until somebody picks", admin.calendar() == "gregorian"
      and admin.show_date("2026-09-29") == "2026-09-29")
check("  a moment in Tehran time, the next day when UTC is late",
      admin.show_date(LATE, True) == "2026-09-30 00:30")
check("  a chart's day", admin.month_day(date(2026, 9, 29)) == "09/29")
check("  what is not a date is left as it is",
      admin.show_date("هرگز", True) == "هرگز" and admin.show_date("") == "")


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]
admin.REQ.admin = None
r = Rec()
admin.Admin.action(r, "calendar-save", {"cal": ["jalali"]})
check("picked in the settings", store.setting("calendar") == "jalali" and "m=" in r.to)
check("  and at once", admin.show_date("2026-09-29") == "1405/07/07"
      and admin.show_date(LATE, True) == "1405/07/08 00:30"
      and admin.month_day("2026-09-29") == "07/07")
src = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("the settings page has the choice",
      "action='/%s/calendar-save'" in src and "شمسی (1405/07/07)" in src
      and '"calendar-save": "settings"' in src)
left = [l.strip() for l in src.splitlines()
        if re.search(r'\[:16\]\.replace\("T", " "\)|_at"\] or ""\)\[:10\]', l)]
check("  no date on its pages is cut out of the stored text any more", left == [], str(left))

print("the panel")
cal = panel.calendar_of(store)
check("reads the same choice", cal == "jalali")
check("its own dates", panel.show_date(LATE, cal) == "1405/07/08"
      and panel.show_date(LATE, "gregorian") == "2026-09-30"
      and panel.show_date("", cal) == "")
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ماهانه', 1, 30, 0, 1, ?)", (panel.now(),))
store.run("INSERT INTO users (username, created_at, status) VALUES ('ali', ?, 'pending')",
          (panel.now(),))
done = panel.apply_plan(store, 1, 1)
check("  \"active until\" in the chosen one",
      re.search(r"فعال شد تا 14\d\d/\d\d/\d\d$", done or "") is not None, done)
user = store.one("SELECT * FROM users WHERE id = 1")
view = panel.user_view(store, user, ["198.51.100.1"])
check("  told to the bot with every account", view["calendar"] == "jalali")
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("  and to the relays at every sync", '"calendar": calendar_of(self.store),' in psrc)
check("  the customer's page gets its dates ready",
      '"expires": show_date(user["expires_at"], calendar_of(self.store)),' in psrc
      and '"renews": show_date(user["quota_reset_at"], calendar_of(self.store)),' in psrc)

print("the customer's page")
check("Gregorian until the panel says", sync.show_date(LATE, True) == "2026-09-30 00:30")
ssrc = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("  takes the panel's word at each sync",
      'CALENDAR["v"] = "jalali" if answer.get("calendar") == "jalali" else "gregorian"'
      in ssrc)
sync.CALENDAR["v"] = "jalali"
check("  then its tickets, wallet and charts in Shamsi",
      sync.show_date(LATE, True) == "1405/07/08 00:30"
      and sync.month_day(date(2026, 9, 29)) == "07/07")
left = [l.strip() for l in ssrc.splitlines() if re.search(r'\[:16\]\.replace\("T", " "\)', l)]
check("  no date on its pages cut out of the stored text", left == [], str(left))

print("the bot")
check("the account's end and the wallet's days, in the chosen one",
      bot.show_date(LATE, "jalali") == "1405/07/08"
      and bot.show_date(LATE, "gregorian") == "2026-09-30"
      and bot.show_date("2026-09-29") == "2026-09-29" and bot.show_date("") == "")
bsrc = open(BOT_PATH := os.path.join(ROOT, "examples", "telegram-bot", "bot.py"),
            encoding="utf-8").read()
check("  as the account says", 'show_date(u["expires_at"], u.get("calendar"))' in bsrc
      and 'show_date(m.get("at"), cal)' in bsrc)

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
