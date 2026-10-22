#!/usr/bin/env python3
"""The admin panel's picture of the whole service's usage.

What has to hold: every customer's usage is added up from the same tables as
their own charts; today, the week and the month are set against the period
before; the charts are drawn; the heaviest users of the week are listed, each
a link to their own page, with their share; and a panel with nothing yet
says so instead of drawing empty charts. The per-service breakdown is not in
it - that stays the customer's.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []
GB = 1024 ** 3


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:300]) if detail and not cond else ""))
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
panel.print = lambda *a, **k: None
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)

print("with nothing yet")
card = admin.total_usage_card("p")
check("it says so rather than drawing empty charts", "هنوز مصرفی ثبت نشده" in card and "<svg" not in card)

print("with three customers")
a = store.create_user(111, "ali", "علی")
b = store.create_user(222, "sara", "سارا")
c = store.create_user(333, "reza", "رضا")
T = admin.TEHRAN
today = datetime.now(T)


def day(n):
    return (today - timedelta(days=n)).strftime("%Y-%m-%d")


def put(user, grain, bucket, up, down):
    store.run("INSERT INTO usage (user_id, grain, bucket, up, down) VALUES (?, ?, ?, ?, ?)",
              (user["id"], grain, bucket, up, down))


put(a, "1d", day(0), 1 * GB, 9 * GB)          # the heaviest, today
put(b, "1d", day(0), 0, 2 * GB)
put(b, "1d", day(3), 0, 4 * GB)
put(c, "1d", day(10), 0, 5 * GB)              # last week only
put(a, "1d", day(40), 0, 40 * GB)             # the month before, bigger
current = today.replace(minute=today.minute - today.minute % 5, second=0, microsecond=0)
put(a, "5m", current.strftime("%Y-%m-%dT%H:%M"), 10 ** 6, 3 * 10 ** 8)
put(a, "1h", today.strftime("%Y-%m-%dT%H:00"), 10 ** 6, 3 * 10 ** 8)
view = admin.total_usage()
check("everybody's day is added up", (day(0), GB, 11 * GB) in view["days"], repr(view["days"]))
card = admin.total_usage_card("p")
check("today's total, with download and upload apart",
      admin.human(12 * GB) in card and "دانلود %s" % admin.human(11 * GB) in card)
check("the week against the week before",
      admin.human(16 * GB) in card and "بیشتر از هفتهٔ قبل" in card)
check("the month against the month before",
      admin.human(21 * GB) in card and "کمتر از ماه قبل" in card)
check("the speed now, from the last five minutes", "Mbps" in card and "↓" in card)
check("the daily chart, the day's speed and the busy hours", card.count("<svg") == 3)
top = card[card.index("پرمصرف‌ترین‌ها"):]
check("the week's heaviest first, each a link to their own charts",
      top.index("ali") < top.index("sara")
      and "href='/p/usage?u=%d'" % a["id"] in top and "href='/p/usage?u=%d'" % b["id"] in top)
check("with their share of the week", "62٪" in top and "38٪" in top, top[:400])
check("and somebody who used nothing this week is not on it", "reza" not in top)
check("no per-service breakdown - that is the customer's", "Steam" not in card and "سرویس" not in card)

src = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
home = src[src.index("    def home(self):"):src.index("    def user_usage_page(self):")]
check("it is on the admin panel's home page", "total_usage_card(CFG[\"ADMIN_PATH\"])" in home)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
