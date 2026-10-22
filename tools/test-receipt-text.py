#!/usr/bin/env python3
"""What the operator's bot writes under a receipt's photo.

What has to hold: everything needed to decide it, not just a plan's name -
who sent it, what for, the plan's days, allowance, speed, devices and
template, its price, a discount code, the amount paid, the customer's note,
and what the customer has now: the plan running and that this one will wait
reserved, the wallet before a top-up, the devices before one more. The same
text in the event a new receipt sends and in the list of receipts waiting,
and the bot puts it under the photo whole, its 🧾 once.
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
bot = load("examples/telegram-bot/bot.py", "bot")
panel.log = lambda *a: None

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
now = panel.now()
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('Hunter', 1, ?)", (now,))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, speed_kbps, devices,"
          " created_at) VALUES ('ماهانه', 1, 30, ?, 150000, 20000, 2, ?)", (50 * 2 ** 30, now))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('بی‌حد', 1, 90, 0, 400000, ?)", (now,))
store.run("INSERT INTO users (username, first_name, telegram_id, created_at, status, plan_id,"
          " expires_at, wallet, max_ips) VALUES ('ali_gamer', 'علی', 111222333, ?, 'active', 1,"
          " '2099-11-26T22:21:02+00:00', 25000, 2)", (now,))
store.run("INSERT INTO users (username, created_at, status) VALUES ('sara', ?, 'pending')",
          (now,))
store.run("INSERT INTO discount_codes (code, kind, value, created_at)"
          " VALUES ('NOROOZ', 'percent', 20, ?)", (now,))
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at, plan_id, code_id,"
          " list_price, note) VALUES (1, 120000, 'card', 'pending', ?, 1, 1, 150000,"
          " 'کارت به کارت از ملت')", (now,))
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
          " VALUES (1, 200000, 'topup', 'pending', ?)", (now,))
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
          " VALUES (1, 50000, 'device', 'pending', ?)", (now,))
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at, plan_id)"
          " VALUES (2, 400000, 'card', 'pending', ?, 2)", (now,))

print("a plan bought")
text = panel.receipt_text(store, 1)
for label, line in (("which receipt, and what for", "🧾 رسید #1 — خرید پلن"),
                    ("who, with their username and Telegram",
                     "👤 مشتری: علی (ali_gamer، تلگرام 111222333)"),
                    ("the plan", "📦 پلن: ماهانه"), ("its days", "⏳ مدت: 30 روز"),
                    ("its allowance", "💾 حجم: 50 گیگابایت"), ("its speed", "🚀 سرعت: 20 مگابیت"),
                    ("its devices", "📱 دستگاه: 2"), ("its template", "🗂 قالب: Hunter"),
                    ("its price", "🏷 قیمت پلن: 150,000 تومان"),
                    ("the discount code", "🎟 کد تخفیف: NOROOZ"),
                    ("the amount paid", "💳 مبلغ واریزی: 120,000 تومان"),
                    ("the customer's note", "📝 یادداشت مشتری: کارت به کارت از ملت")):
    check("  " + label, line in text.split("\n"), text)
check("  the plan running, and that this one waits reserved",
      "📌 پلن فعلی: ماهانه تا 2099-11-27 — این پلن رزرو می‌شود" in text, text)
check("  when it came", "🕒 زمان: " in text)
check("  well within a photo's caption", len(text) < 1024, str(len(text)))
other = panel.receipt_text(store, 4)
check("no running plan: said so, nothing reserved, and no limits",
      "📌 پلن فعلی: ندارد" in other and "رزرو" not in other
      and "💾 حجم: نامحدود" in other and "🚀 سرعت: بی‌حد" in other
      and "کد تخفیف" not in other and "یادداشت" not in other, other)

print("a top-up and a device")
top = panel.receipt_text(store, 2)
check("a top-up says the wallet as it is", "🧾 رسید #2 — شارژ کیف پول" in top
      and "👛 کیف پول الان: 25,000 تومان" in top and "📦" not in top
      and "رزرو می‌شود" not in top, top)
dev = panel.receipt_text(store, 3)
check("a device says how many there are", "🧾 رسید #3 — دستگاه اضافه" in dev
      and "📱 دستگاه‌های الان: 2" in dev, dev)
check("a receipt that is not there is nothing", panel.receipt_text(store, 99) == "")

print("where it goes")
events = []
panel.emit_admin = lambda st, kind, data: events.append((kind, data))
store.run("UPDATE users SET wallet = 0 WHERE id = 2")
res = panel.create_receipt(store, store.one("SELECT * FROM users WHERE id = 2"),
                           {"content_type": "image/png", "plan_id": 2,
                            "data": base64.b64encode(b"\x89PNG fake").decode()})
check("a new receipt's event carries all of it",
      res.get("ok") and events and events[-1][0] == "receipt.submitted"
      and events[-1][1]["text"] == panel.receipt_text(store, res["receipt_id"]), str(res))
row = store.one(panel.RECEIPT_ROWS + " WHERE t.id = 1")
check("  and so does each receipt in the list the bot asks for",
      panel.admin_receipt(store, row)["text"] == text)


class Shown(Exception):
    pass


posted = []


class FakeBot:
    def __init__(self):
        self.cfg = {"admins": [42]}
        self.seen = []
        self.lock = __import__("threading").Lock()

    def post_receipt(self, chat, rid, caption):
        posted.append(caption)

    def say(self, chat, text, markup=None):
        posted.append(text)


fb = FakeBot()
bot.Bot.on_event(fb, {"id": "e1", "audience": "admin", "event": "receipt.submitted",
                      "data": {"receipt_id": 1, "text": text}})
check("the bot puts it under the photo whole, its 🧾 once",
      posted[-1] == text and posted[-1].count("🧾") == 1)
bot.Bot.on_event(fb, {"id": "e2", "audience": "admin", "event": "receipt.submitted",
                      "data": {"receipt_id": 1, "text": "رسید تازه از علی"}})
check("  an older panel's short line still gets its 🧾", posted[-1] == "🧾 رسید تازه از علی")

store.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
