#!/usr/bin/env python3
"""Discount codes.

What has to hold: a code takes a percent or a sum off a plan's price, and the
customer sees that price before paying and pays it - by receipt or from the
wallet; a code is refused past its date, past its uses, on a plan it is not
for, and twice by the same customer when it is once each; a use is counted
when the purchase goes through, not when a receipt is only sent; and the
inviter's share is of what was paid.
"""
import base64
import importlib.machinery
import importlib.util
import json
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
for m in (panel, admin, sync):
    m.log = lambda *a: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
for name, price in (("ماهانه", 200000), ("سالانه", 1000000)):
    store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
              " VALUES (?, 1, 30, ?, ?, ?)", (name, 50 * GB, price, panel.now()))
MONTH, YEAR = 1, 2


class Rec:
    def __init__(self, path="/p/plans"):
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


for name in ("action", "redirect", "send", "plans", "receipts"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: v if isinstance(v, list) else [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def user(uid):
    return store.one("SELECT * FROM users WHERE id = ?", (uid,))


PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")).decode()

print("making codes")
act("discount-save", code="norooz30", value="30", kind="percent", days="", max_uses="",
    once="1")
act("discount-save", code="MINUS50", value="50,000", kind="amount", days="", max_uses="1")
act("discount-save", code="YEARONLY", value="10", kind="percent", days="", max_uses="",
    once="1", plan=[str(YEAR)])
check("a code is kept upper-case, with its terms",
      store.one("SELECT * FROM discount_codes WHERE code = 'NOROOZ30'")["value"] == 30)
check("a percent over 100 is refused", "!" in act("discount-save", code="X100", value="150",
                                                   kind="percent"))
check("the same code twice is refused", "!" in act("discount-save", code="NOROOZ30",
                                                   value="5", kind="percent"))
check("the plans page lists them", "NOROOZ30" in Rec().plans() and "discount-save" in Rec().plans())

print("using one")
ali = store.create_web_user("ali", "علی", "ali-password")
plans, msg, ok = panel.discount_view(store, ali, "norooz30")
month = next(p for p in plans if p["id"] == MONTH)
check("the customer sees the price after it before paying", ok and month["price"] == 140000
      and month["list_price"] == 200000 and "NOROOZ30" in msg)
check("  and the page shows both", "<s>200,000</s> 140,000" in sync.plan_line(month))
check("a code that is nobody's is refused", not panel.discount_view(store, ali, "NOPE")[2])
res = panel.create_receipt(store, ali, {"plan_id": MONTH, "code": "NOROOZ30",
                                        "content_type": "image/png", "data": PNG})
t = store.one("SELECT * FROM transactions WHERE id = ?", (res["receipt_id"],))
check("a receipt with it is for the lower sum", t["amount"] == 140000 and t["list_price"] == 200000)
check("  not counted while it only waits", store.one(
    "SELECT uses FROM discount_codes WHERE code = 'NOROOZ30'")["uses"] == 0)
check("  the receipts page says which code", "NOROOZ30" in Rec("/p/receipts").receipts())
act("receipt-decide", id=str(t["id"]), to="approved")
check("counted when approved", store.one(
    "SELECT uses FROM discount_codes WHERE code = 'NOROOZ30'")["uses"] == 1)
res = panel.create_receipt(store, user(ali["id"]), {"plan_id": MONTH, "code": "NOROOZ30",
                                                    "content_type": "image/png", "data": PNG})
check("once each: not a second time", not res["ok"] and res["error"] == "bad_code")
check("a code only for another plan is refused", not panel.create_receipt(
    store, user(ali["id"]), {"plan_id": MONTH, "code": "YEARONLY",
                             "content_type": "image/png", "data": PNG})["ok"])

print("from the wallet")
store.run("UPDATE users SET wallet = 200000 WHERE id = ?", (ali["id"],))
res = panel.buy_with_wallet(store, user(ali["id"]), MONTH, "MINUS50")
check("from the wallet, the price after the code", res["ok"]
      and user(ali["id"])["wallet"] == 50000, str(res))
bob = store.create_web_user("bob", "", "bob-password")
store.run("UPDATE users SET wallet = 500000 WHERE id = ?", (bob["id"],))
res = panel.buy_with_wallet(store, user(bob["id"]), MONTH, "MINUS50")
check("a code used up is refused, and nothing is spent", not res["ok"]
      and user(bob["id"])["wallet"] == 500000)

print("an expired code")
store.run("UPDATE discount_codes SET expires_at = '2020-01-01T00:00:00+00:00'"
          " WHERE code = 'NOROOZ30'")
check("is refused", "مهلت" in panel.discount_view(store, bob, "NOROOZ30")[1])
act("discount-active", id="1", to="0")
check("a code switched off is refused", not panel.discount_view(store, bob, "NOROOZ30")[2])

print("the inviter's share is of what was paid")
act("ref-settings", on="1", percent="10", mode="every")
code = panel.ref_code(store, user(bob["id"]))
cara = store.create_web_user("cara", "", "cara-password")
panel.set_referrer(store, cara["id"], code)
act("discount-save", code="HALF", value="50", kind="percent", days="", max_uses="")
store.run("UPDATE users SET wallet = 200000 WHERE id = ?", (cara["id"],))
before = user(bob["id"])["wallet"]
panel.buy_with_wallet(store, user(cara["id"]), MONTH, "HALF")
check("ten percent of the 100,000 paid, not of 200,000",
      user(bob["id"])["wallet"] - before == 10000)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
