#!/usr/bin/env python3
"""Plans: a template for some days, with an allowance and a price.

Buying one is choosing it, paying, and sending the slip; approving the slip
is the whole of the operator's work. What has to hold is money-shaped: an
empty box never becomes an unlimited plan, one receipt is applied once however
often the button is pressed, renewing early loses nothing, a different plan
starts fresh, and a price comes from the plan rather than from the form.
"""
import base64
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone

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


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(HERE, "..", "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("smartdns-panel", "panel")
admin = load("smartdns-admin", "admin")
sync = load("smartdns-sync", "sync")

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
admin.log = lambda *a: None

store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('بازی', 0, ?)",
          (panel.now(),))
FULL, GAME = 1, 2
store.run("INSERT INTO users (username, first_name, created_at, status)"
          " VALUES ('ali', 'علی', ?, 'pending')", (panel.now(),))
session = store.open_session(1)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "receipts", "plans",
             "users"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


class Api:
    relays = ("198.51.100.1",)

    def __init__(self, store):
        self.store = store


for name in ("do_user_receipt", "do_user_info", "_session_user", "_must_choose"):
    setattr(Api, name, getattr(panel.API, name))
api = Api(store)


def pay(plan_id, **extra):
    body = {"session": session, "content_type": "image/png",
            "data": base64.b64encode(PNG).decode(), "plan_id": plan_id}
    body.update(extra)
    return api.do_user_receipt(body)


def user():
    return store.one("SELECT * FROM users WHERE id = 1")


print("before there are plans, a receipt is what it always was")
check("accepted with no plan", pay(0).get("ok"))
check("the admin page still says to set it by hand",
      "سهمیه و زمان" in Rec().receipts())
store.run("DELETE FROM transactions")

print("making a plan")
where = act("plan-save", name="گیمینگ ماهانه", template_id=str(GAME), days="30",
            quota_gb="", price="200000")
check("an empty allowance is refused, not taken as unlimited", "m=!" in where, where)
check("and nothing was made", not store.one("SELECT 1 FROM plans"))
where = act("plan-save", name="گیمینگ ماهانه", template_id=str(GAME), days="30",
            quota_gb="0", price="200000")
check("so is zero", "m=!" in where, where)
where = act("plan-save", name="x", template_id="99", days="30", quota_gb="5",
            price="1")
check("a template that is not there is refused", "m=!" in where, where)
where = act("plan-save", name="x", template_id=str(GAME), days="2.5",
            quota_gb="5", price="1")
check("half a day is refused", "m=!" in where, where)
where = act("plan-save", name="گیمینگ ماهانه", template_id=str(GAME), days="۳۰",
            quota_gb="۱۰۰", price="۲۰۰,۰۰۰", speed_mb="20", note="فقط بازی‌ها")
check("Persian digits and a thousands comma are understood", "m=!" not in where, where)
game = store.one("SELECT * FROM plans WHERE name = 'گیمینگ ماهانه'")
check("the plan holds what was typed",
      game and (game["template_id"], game["days"], game["quota_bytes"],
                game["price"], game["speed_kbps"]) == (GAME, 30, 100 * GB, 200000,
                                                       20000),
      str(dict(game)) if game else "none")
act("plan-save", name="کامل ماهانه", template_id=str(FULL), days="30",
    quota_gb="", unlimited="1", price="500000")
full = store.one("SELECT * FROM plans WHERE name = 'کامل ماهانه'")
check("unlimited only with its own tick", full and full["quota_bytes"] == 0)
page = Rec().plans()
check("the plans page lists both", "گیمینگ ماهانه" in page and "کامل ماهانه" in page)

print("the customer sees them")
info = api.do_user_info({"session": session})
check("both are offered", [p["name"] for p in info["plans"]]
      == ["کامل ماهانه", "گیمینگ ماهانه"], str(info["plans"]))
box = sync.receipt_box(info)
check("the page groups them under their template",
      "<div class='t'>بازی</div>" in box and "<div class='t'>کامل</div>" in box)
check("a choice is required", "type='radio' name='plan'" in box and " required" in box)
check("the price is shown", "200,000 تومان" in box, box[:400])
check("unlimited reads as unlimited", "حجم نامحدود" in box)

print("where to pay")
check("with nothing set, the page says nothing about a card", "💳" not in box)
act("pay-save", text="به کارت 6037-1111 به نام مهدی\r\nو رسید را بفرستید")
check("the admin writes it in settings", store.setting("pay_text")
      == "به کارت 6037-1111 به نام مهدی\nو رسید را بفرستید")
where = act("pay-save", text="x" * 501)
check("within a length", "m=!" in where and store.setting("pay_text").startswith("به کارت"))
box = sync.receipt_box(api.do_user_info({"session": session}))
check("the customer sees it beside the plans", "💳 به کارت 6037-1111 به نام مهدی" in box)
for name in ("pay_page", "settings"):
    setattr(Rec, name, getattr(admin.Admin, name))
admin.CFG.update(ADMIN_PORT="9443")
page = Rec().pay_page()
check("it has its own page, with a preview of what the customer sees",
      "action='/p/pay-save'" in page and "💳 به کارت 6037-1111" in page)
check("and is no longer in Settings", "pay-save" not in Rec().settings())
check("and it is escaped", "<script>" not in sync.pay_box({"pay_text": "<script>"}))

print("paying for a plan")
check("a receipt with no plan is now refused", not pay(0).get("ok"))
check("a plan that is not on sale is refused", not pay(999).get("ok"))
check("naming a plan is accepted", pay(game["id"], amount="1").get("ok"))
t = store.one("SELECT * FROM transactions WHERE status = 'pending'")
check("the price is the plan's, not what the form said", t["amount"] == 200000,
      str(t["amount"]))
check("the receipt remembers the plan", t["plan_id"] == game["id"])
info = api.do_user_info({"session": session})
check("the customer sees it waiting", info["receipt_waiting"]
      and info["receipt_waiting"]["plan"] == "گیمینگ ماهانه")
page = Rec().receipts()
check("the admin sees which plan it pays for", "گیمینگ ماهانه" in page
      and "200,000 تومان" in page)

print("approving it")
where = act("receipt-decide", id=str(t["id"]), to="approved")
u = user()
check("the plan is on the account", u["plan_id"] == game["id"], where)
check("with its template", u["template_id"] == GAME)
check("its allowance", u["quota_bytes"] == 100 * GB)
check("its speed", u["speed_kbps"] == 20000)
check("a pending account becomes active", u["status"] == "active")
ends = admin.parse_ts(u["expires_at"])
now = datetime.now(timezone.utc)
check("it runs for thirty days from now",
      ends and abs((ends - now) - timedelta(days=30)) < timedelta(minutes=1))
check("and the message says so", "فعال شد" in where, where)

where = act("receipt-decide", id=str(t["id"]), to="approved")
check("pressing approve again does nothing", "m=!" in where, where)
check("the plan was not applied twice", user()["expires_at"] == u["expires_at"])

print("buying while a plan runs: reserved")
store.run("UPDATE users SET used_bytes = ? WHERE id = 1", (60 * GB,))
reserved = lambda: [r["plan_id"] for r in store.q(
    "SELECT plan_id FROM reserved_plans WHERE user_id = 1 ORDER BY id")]
box = sync.receipt_box(api.do_user_info({"session": session}))
check("the page says a plan bought now is reserved", "رزرو" in box and "از نو شروع" not in box)
check("and marks the current plan", "پلن فعلی" in box)
pay(game["id"])
t = store.one("SELECT * FROM transactions WHERE status = 'pending'")
check("the admin is told it will be reserved", "با تأیید رزرو می‌شود" in Rec().receipts())
act("receipt-decide", id=str(t["id"]), to="approved")
r = user()
check("the running plan is left as it was",
      (r["plan_id"], r["quota_bytes"], r["used_bytes"], r["expires_at"])
      == (game["id"], 100 * GB, 60 * GB, u["expires_at"]))
check("  the same plan bought again waits its turn", reserved() == [game["id"]])
pay(full["id"])
t = store.one("SELECT * FROM transactions WHERE status = 'pending'")
act("receipt-decide", id=str(t["id"]), to="approved")
check("  and another behind it", reserved() == [game["id"], full["id"]])
info = api.do_user_info({"session": session})
check("the customer sees them waiting", info["reserved"] == ["گیمینگ ماهانه", "کامل ماهانه"])

print("the running plan ends, and the next one starts")
store.run("UPDATE users SET used_bytes = quota_bytes WHERE id = 1")
panel.enforce_quotas(store)
r = user()
check("its allowance over, the first reserved one starts, fresh",
      (r["plan_id"], r["status"], r["used_bytes"], r["quota_bytes"])
      == (game["id"], "active", 0, 100 * GB)
      and abs(admin.parse_ts(r["expires_at"]) - datetime.now(timezone.utc)
              - timedelta(days=30)) < timedelta(minutes=1))
check("  and leaves the queue", reserved() == [full["id"]])
info = api.do_user_info({"session": session})
check("the customer's page says it started",
      "پلن رزرو «گیمینگ ماهانه» فعال شد" in sync.account_notice(info)
      and info["reserved"] == ["کامل ماهانه"])
store.run("UPDATE users SET expires_at = ? WHERE id = 1",
          ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),))
panel.enforce_quotas(store)
r = user()
check("its days over, the next one starts",
      (r["plan_id"], r["template_id"], r["quota_bytes"], r["status"])
      == (full["id"], FULL, 0, "active") and reserved() == [])

print("a plan that ran out starts fresh too")
store.run("UPDATE users SET expires_at = ?, status = 'expired' WHERE id = 1",
          ((now - timedelta(days=1)).isoformat(timespec="seconds"),))
check("expired, held plan", admin.STORE.apply_plan(1, full["id"]))
r = user()
check("it is active again", r["status"] == "active")
check("from now", admin.parse_ts(r["expires_at"]) > now + timedelta(days=29))

print("given by hand while a plan runs")
where = admin.STORE.apply_plan(1, game["id"])
check("reserved too, and said so", "رزرو شد" in (where or "") and reserved() == [game["id"]])
cell = admin.plan_cell(user(), admin.STORE.q("SELECT * FROM plans"))
rid = store.one("SELECT id FROM reserved_plans WHERE user_id = 1")["id"]
check("the users table shows it, with a way to take it back",
      "رزرو: گیمینگ ماهانه" in cell and "/p/user-reserve-cancel" in cell
      and "+1 رزرو" in cell)
act("user-reserve-cancel", id=str(rid))
check("taken back, it is gone", reserved() == [])
store.run("UPDATE users SET expires_at = ?, status = 'active' WHERE id = 1",
          ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),))
panel.enforce_quotas(store)
check("with nothing reserved, a plan ends as it always did", user()["status"] == "expired")

print("a block stays a block")
store.run("UPDATE users SET status = 'suspended' WHERE id = 1")
admin.STORE.apply_plan(1, game["id"])
check("a suspended account is not unblocked by a receipt",
      user()["status"] == "suspended")
store.run("UPDATE users SET status = 'active' WHERE id = 1")

print("rejecting")
pay(game["id"])
t = store.one("SELECT * FROM transactions WHERE status = 'pending'")
before = dict(user())
act("receipt-decide", id=str(t["id"]), to="rejected")
check("a rejected receipt changes nothing", dict(user()) == before)

print("switching a plan off, and deleting")
act("plan-active", id=str(full["id"]), to="0")
info = api.do_user_info({"session": session})
check("it is no longer offered", all(p["id"] != full["id"] for p in info["plans"]))
check("and cannot be paid for", not pay(full["id"]).get("ok"))
held = dict(user())
act("plan-active", id=str(game["id"]), to="0")
check("whoever holds a plan switched off keeps it", dict(user()) == held
      and held["plan_id"] == game["id"] and held["status"] == "active")
act("plan-active", id=str(game["id"]), to="1")
where = act("plan-delete", id=str(game["id"]))
check("a plan somebody holds is not deleted", "m=!" in where
      and store.one("SELECT 1 FROM plans WHERE id = ?", (game["id"],)), where)
where = act("plan-delete", id=str(full["id"]))
check("one nobody holds is", "m=!" not in where
      and not store.one("SELECT 1 FROM plans WHERE id = ?", (full["id"],)), where)
where = act("template-delete", id=str(GAME))
check("a template a plan sells is not deleted", "m=!" in where
      and store.one("SELECT 1 FROM templates WHERE id = ?", (GAME,)), where)

print("giving a plan by hand")
store.run("INSERT INTO users (username, created_at, status)"
          " VALUES ('cash', ?, 'pending')", (panel.now(),))
where = act("user-plan", id="2", plan_id=str(game["id"]))
u2 = store.one("SELECT * FROM users WHERE id = 2")
check("the operator can give one without a receipt",
      u2["plan_id"] == game["id"] and u2["status"] == "active", where)
check("the users page shows it", "<summary>گیمینگ ماهانه</summary>" in Rec().users())

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
