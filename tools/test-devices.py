#!/usr/bin/env python3
"""Devices: how many addresses an account holds at once.

What has to hold: a plan gives its number of devices; an extra device bought
on top - from the wallet or by a receipt - lasts while the same plan is
renewed and ends with another; when the number goes down the oldest
addresses go; the admin can set it by hand; and a customer registering new
addresses is held to the admin's number a day, which the admin is not.
"""
import base64
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


for name in ("action", "redirect", "send", "plans", "users", "receipts"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def user(uid):
    return store.one("SELECT * FROM users WHERE id = ?", (uid,))


def ips(uid):
    return [r["ip"] for r in store.user_ips(uid)]


print("plans")
act("plan-save", name="یک دستگاه", template_id="1", days="30", quota_gb="50", price="200000",
    devices="1")
act("plan-save", name="سه دستگاه", template_id="1", days="30", quota_gb="50", price="400000",
    devices="3")
check("a plan with more than five devices is refused",
      "!" in act("plan-save", name="x", template_id="1", days="30", quota_gb="1", price="1",
                 devices="6"))
ONE, THREE = 1, 2
check("the plans page has the devices column and the settings card",
      "name='devices' value='3'" in Rec().plans() and "devices-settings" in Rec().plans())
sale = {p["name"]: p for p in store.plans_for_sale()}
check("plans on sale carry their devices", sale["سه دستگاه"]["devices"] == 3)
check("  and the customer reads them", "3 دستگاه" in sync.plan_line(sale["سه دستگاه"])
      and "دستگاه" not in sync.plan_line(sale["یک دستگاه"]))

print("buying")
ali = store.create_web_user("ali", "علی", "ali-password")["id"]
panel.apply_plan(store, ali, THREE)
check("a plan gives its devices", user(ali)["max_ips"] == 3)
for ip in ("93.184.216.1", "93.184.216.2", "93.184.216.3"):
    panel.register_ip(store, ali, ip)
check("  that many addresses at once", len(ips(ali)) == 3)
panel.register_ip(store, ali, "93.184.216.4")
check("  a fourth lets the oldest go", ips(ali) == ["93.184.216.2", "93.184.216.3",
                                                    "93.184.216.4"])

print("an extra device")
check("not sold until priced", panel.device_offer(store, user(ali)) is None)
act("devices-settings", price="50,000", limit="")
offer = panel.device_offer(store, user(ali))
check("priced, it is offered to a customer on a plan", offer and offer["available"]
      and offer["price"] == 50000)
bob = store.create_web_user("bob", "", "bob-password")["id"]
check("  not to one without a plan", not panel.device_offer(store, user(bob))["available"])
res = panel.buy_device_with_wallet(store, user(ali))
check("not without the money", not res["ok"] and res["error"] == "wallet_short")
store.run("UPDATE users SET wallet = 120000 WHERE id = ?", (ali,))
res = panel.buy_device_with_wallet(store, user(ali))
check("from the wallet: one more device, the price off",
      res["ok"] and user(ali)["max_ips"] == 4 and user(ali)["extra_devices"] == 1
      and user(ali)["wallet"] == 70000, str(res))
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")).decode()
res = panel.create_receipt(store, user(ali), {"kind": "device", "content_type": "image/png",
                                             "data": PNG})
t = store.one("SELECT * FROM transactions WHERE id = ?", (res["receipt_id"],))
check("by a receipt for the device's price", t["kind"] == "device" and t["amount"] == 50000)
check("  the receipts page says so", "دستگاه اضافه" in Rec("/p/receipts").receipts())
act("receipt-decide", id=str(t["id"]), to="approved")
check("  approved, one more device", user(ali)["max_ips"] == 5 and user(ali)["extra_devices"] == 2)
res = panel.create_receipt(store, user(ali), {"kind": "device", "content_type": "image/png",
                                             "data": PNG})
panel.decide_receipt(store, res["receipt_id"], "approved")
check("the bot's approval does the same", user(ali)["max_ips"] == 6)

print("renewing, and another plan")
panel.apply_plan(store, ali, THREE)
check("renewing the same plan keeps the extra devices", user(ali)["max_ips"] == 6
      and user(ali)["extra_devices"] == 3)
for ip in ("93.184.216.5", "93.184.216.6", "93.184.216.7"):
    panel.register_ip(store, ali, ip)
store.run("UPDATE users SET expires_at = ?, status = 'expired' WHERE id = ?",
          ("2020-01-01T00:00:00+00:00", ali))
panel.apply_plan(store, ali, ONE)
check("another plan ends them, and keeps the newest address only",
      user(ali)["max_ips"] == 1 and user(ali)["extra_devices"] == 0
      and ips(ali) == ["93.184.216.7"])
store.run("UPDATE users SET expires_at = NULL WHERE id = ?", (ali,))
admin.STORE.apply_plan(ali, THREE)
check("a plan given in the admin panel sets them too", user(ali)["max_ips"] == 3)

print("by hand")
act("user-devices", id=str(ali), devices="2")
check("the admin sets the number", user(ali)["max_ips"] == 2)
check("  within 1 to 10", "!" in act("user-devices", id=str(ali), devices="11"))
check("the users page shows the devices on the address",
      "📱" in Rec("/p/users").users() and "user-devices" in Rec("/p/users").users())

print("new addresses a day")
act("devices-settings", price="50000", limit="2")
cara = store.create_web_user("cara", "", "cara-password")["id"]
panel.apply_plan(store, cara, ONE)
r1 = panel.register_ip(store, cara, "93.184.217.1", limited=True)
r2 = panel.register_ip(store, cara, "93.184.217.2", limited=True)
r3 = panel.register_ip(store, cara, "93.184.217.3", limited=True)
check("two new addresses in a day, not a third", r1["ok"] and r2["ok"] and not r3["ok"]
      and r3["error"] == "ip_changes", str(r3))
check("  the same address again is no change", panel.register_ip(
    store, cara, "93.184.217.2", limited=True)["ok"])
check("  and the admin is not held to it", panel.register_ip(store, cara, "93.184.217.9")["ok"])
act("devices-settings", price="50000", limit="")
check("empty is unlimited", panel.register_ip(store, cara, "93.184.217.4", limited=True)["ok"])

print("the customer's page")
store.run("UPDATE users SET wallet = 100000 WHERE id = ?", (cara,))
info = {"device_offer": panel.device_offer(store, user(cara)), "wallet": 100000,
        "pay_text": "کارت"}
box = sync.device_box(info)
check("offers an extra device, from the wallet and by receipt",
      "/device-wallet" in box and "/device-receipt" in box and "50,000" in box)
check("  not without a price", sync.device_box({"device_offer": None}) == "")

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
