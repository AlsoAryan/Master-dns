#!/usr/bin/env python3
"""0.1.3: the limit on new addresses a day, on mobile data.

What has to hold: an address on a mobile operator's network is held to the admin's number
for mobile data, or to three times the usual one when there is none; an address on a
fixed-line operator, on no operator, or with no list to say, is held to the usual number;
no limit stays no limit; a refused address says so, and one that is only being renewed
is not counted; and the admin can set the mobile number from the page.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" - " + str(detail)[:300]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, fname):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(
        name, os.path.join(HERE, "..", "templates", fname)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


panel = load("panel", "smartdns-panel")
admin = load("admin", "smartdns-admin")
panel.print = lambda *a, **k: None
tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
MCI, TCI, FREE = "5.112.7.9", "2.176.0.5", "93.184.216.34"
ops = os.path.join(tmp, "operators.json")
with open(ops, "w", encoding="utf-8") as fh:
    json.dump({"operators": {
        "197207": {"name": "همراه اول", "prefixes": ["5.112.0.0/12"]},
        "44244": {"name": "ایرانسل", "prefixes": ["5.208.0.0/12"]},
        "58224": {"name": "مخابرات", "prefixes": ["2.176.0.0/12", "5.112.7.0/24"]}}}, fh)
panel.OPERATORS_FILE = ops

print("which operator an address is on")
check("a mobile operator's block", panel.operator_of("5.112.200.1") == "همراه اول")
check("the longest block wins, as in the admin panel", panel.operator_of(MCI) == "مخابرات")
check("a fixed-line operator", panel.operator_of(TCI) == "مخابرات")
check("an address in none of them is nobody's, not unknown", panel.operator_of(FREE) == "")
check("not an address is unknown", panel.operator_of("nonsense") is None)
panel.OPERATORS_FILE = os.path.join(tmp, "missing.json")
check("with no list at all, unknown", panel.operator_of("5.112.200.1") is None)
panel.OPERATORS_FILE = ops

print("the limit")
MOBILE, FIXED = "5.112.200.1", TCI
store.set_setting("ip_changes_per_day", "2")
check("a mobile address is held to three times the usual number", panel.ip_change_limit(store, MOBILE) == 6)
check("a fixed-line one to the usual", panel.ip_change_limit(store, FIXED) == 2)
check("an address on no operator to the usual", panel.ip_change_limit(store, FREE) == 2)
check("no address given: the usual", panel.ip_change_limit(store) == 2)
check("Irancell is mobile too", panel.ip_change_limit(store, "5.208.1.1") == 6)
store.set_setting("ip_changes_mobile", "10")
check("the admin's number for mobile data wins over three times", panel.ip_change_limit(store, MOBILE) == 10)
check("and leaves the fixed-line number alone", panel.ip_change_limit(store, FIXED) == 2)
store.set_setting("ip_changes_mobile", "garbage")
check("an unreadable number falls back to three times", panel.ip_change_limit(store, MOBILE) == 6)
store.set_setting("ip_changes_per_day", "")
store.set_setting("ip_changes_mobile", "10")
check("no limit stays no limit, whatever the mobile number says", panel.ip_change_limit(store, MOBILE) is None)

print("registering")
store.set_setting("ip_changes_per_day", "2")
store.set_setting("ip_changes_mobile", "")
uid = store.run("INSERT INTO users (username, created_at, status, max_ips) VALUES ('ali', ?, 'active', 1)",
                (panel.now(),)).lastrowid
results = [panel.register_ip(store, uid, "5.112.200.%d" % n, limited=True) for n in range(1, 8)]
check("a customer on mobile data registers six new addresses a day", all(r.get("ok") for r in results[:6]),
      [r.get("error") for r in results])
check("and is stopped at the seventh, with the reason", results[6].get("error") == "ip_changes", results[6])
other = store.run("INSERT INTO users (username, created_at, status, max_ips) VALUES ('sara', ?, 'active', 1)",
                  (panel.now(),)).lastrowid
fixed = [panel.register_ip(store, other, "2.176.0.%d" % n, limited=True) for n in range(1, 4)]
check("on a fixed line the same customer is stopped at the third",
      fixed[0].get("ok") and fixed[1].get("ok") and fixed[2].get("error") == "ip_changes", fixed)
check("the admin is not held to either", panel.register_ip(store, uid, "5.112.200.99", limited=False).get("ok"))
check("an address already registered is renewed, not counted",
      panel.register_ip(store, uid, "5.112.200.99", limited=True).get("ok"))

print("the admin's page")
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
card = admin.devices_card("p")
check("the page has a field for it, with what an empty one means",
      "name='limit_mobile'" in card and "خالی = ۳ برابر" in card and "همراه اول، ایرانسل، رایتل" in card)


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(**form):
    r = Rec()
    admin.Admin.action(r, "devices-settings", {k: [v] for k, v in form.items()})
    return r.to


check("it is saved", act(price="", limit="3", limit_mobile="9").startswith("plans?m=")
      and admin.setting("ip_changes_mobile") == "9" and admin.setting("ip_changes_per_day") == "3")
check("empty means three times: it is emptied", act(price="", limit="3", limit_mobile="")
      and admin.setting("ip_changes_mobile") == "")
check("a number that is not one is refused, and nothing is changed",
      act(price="", limit="3", limit_mobile="many").startswith("plans?m=!")
      and admin.setting("ip_changes_per_day") == "3")
check("so is zero", act(price="", limit="3", limit_mobile="0").startswith("plans?m=!"))

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
