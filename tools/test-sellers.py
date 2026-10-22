#!/usr/bin/env python3
"""Resellers: an admin who sells the service to customers of their own.

What has to hold: a customer who signs up by a reseller's link is theirs, and
one invited by that customer too - but not past the number of customers the
owner allowed; the reseller sees and touches only their own customers, their
receipts and tickets, never anybody else's, and never what is set for every
customer at once; their customers are offered only the reseller's plans, pay
to the reseller's card, get no trial and no owner's discount code; their
templates are the ticked ones, or their own up to a number; when the
reseller's traffic cap or days run out, every one of their customers stops -
and nobody else - and the owner is told, once for each step.
"""
import http.client
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
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
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
store.ensure_default_template([])
store.set_setting("owner_username", "boss")
store.set_setting("customer_panel_url", "https://my.example.com/")
SECRET = "adminpath"
SALT = "c3" * 16
admin.CFG = {"ADMIN_PATH": SECRET, "ADMIN_SALT": SALT, "ADMIN_PORT": "0",
             "ADMIN_HASH": admin.hash_password("owner-pw-1", SALT)}
admin.STORE = admin.Store(db)
admin.CATALOGUE = []
srv = admin.make_admin_server(None, 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


class Browser:
    def __init__(self):
        self.cookie = ""

    def go(self, method, path, form=None):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        c.request(method, "/%s/%s" % (SECRET, path),
                  body=urllib.parse.urlencode(form, doseq=True) if form is not None else None,
                  headers=headers)
        r = c.getresponse()
        body = r.read().decode("utf-8", "replace")
        m = re.search(r"sdns=([^;]*)", r.getheader("Set-Cookie") or "")
        if m:
            self.cookie = "sdns=" + m.group(1)
        loc = urllib.parse.unquote(r.getheader("Location") or "")
        c.close()
        return r.status, loc, body

    def login(self, username, password):
        self.cookie = ""
        return self.go("POST", "", {"username": username, "password": password})

    def post(self, path, **form):
        return self.go("POST", path, form)

    def get(self, path=""):
        return self.go("GET", path)


owner = Browser()
owner.login("boss", "owner-pw-1")
did = store.one("SELECT id FROM templates WHERE is_default = 1")["id"]
t_owner = store.run("INSERT INTO templates (name, is_default, created_at) VALUES"
                    " ('owner-only', 0, ?)", (panel.now(),)).lastrowid
t_given = store.run("INSERT INTO templates (name, is_default, created_at) VALUES"
                    " ('given', 0, ?)", (panel.now(),)).lastrowid

print("making a reseller")
owner.post("admin-save", id="0", username="sara", password="sara-pw-123",
           perm=["users", "receipts", "tickets", "plans", "templates", "settings"],
           own_only="1", max_users="2", cap_gb="1", days="30", templates_mode="pick",
           tpl=[str(t_given)])
sara = store.one("SELECT * FROM admins WHERE username = 'sara'")
check("with the code of their sign-up link", sara["ref_code"]
      and panel.REF_CODE_RE.fullmatch(sara["ref_code"]))

print("customers")


class Handler:
    pass


for name in ("do_user_signup", "_session_user"):
    setattr(Handler, name, getattr(panel.API, name))
api = Handler()
api.store = store
r = api.do_user_signup({"ip": "198.51.100.9", "username": "cust1", "password": "pw-123456",
                        "ref": sara["ref_code"]})
c1 = store.one("SELECT * FROM users WHERE username = 'cust1'")
check("signing up by the reseller's link makes them the reseller's",
      r.get("ok") is not False and c1["owner_admin"] == sara["id"], str(r))
store.set_setting("ref_on", "1")
store.set_setting("ref_percent", "10")
code1 = panel.ref_code(store, c1)
api.do_user_signup({"ip": "198.51.100.10", "username": "cust2", "password": "pw-123456",
                    "ref": code1})
c2 = store.one("SELECT * FROM users WHERE username = 'cust2'")
check("  and whoever they invite is the reseller's too", c2["owner_admin"] == sara["id"])
r = api.do_user_signup({"ip": "198.51.100.11", "username": "cust3", "password": "pw-123456",
                        "ref": sara["ref_code"]})
check("  not past the number the owner allowed", r.get("ok") is False
      and not store.user_by_username("cust3"))
api.do_user_signup({"ip": "198.51.100.12", "username": "mine", "password": "pw-123456"})
own = store.one("SELECT * FROM users WHERE username = 'mine'")
check("without the link, the owner's", own["owner_admin"] is None)
for u, ip in ((c1, "10.0.0.1"), (c2, "10.0.0.2"), (own, "10.0.0.3")):
    store.run("UPDATE users SET status = 'active' WHERE id = ?", (u["id"],))
    store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)",
              (u["id"], ip, panel.now()))

print("what the reseller sees")
b = Browser()
b.login("sara", "sara-pw-123")
_, _, body = b.get("users")
check("their customers only", "cust1" in body and "cust2" in body and ">mine<" not in body)
check("  no DNS or exit column unless allowed", "user-relays" not in body)
check("  someone else's customer cannot be touched",
      b.post("user-status", id=str(own["id"]), to="suspended")[0] == 403
      and store.one("SELECT status FROM users WHERE id = ?", (own["id"],))[0] == "active")
check("  their own can", b.post("user-status", id=str(c1["id"]), to="suspended")[0] == 303
      and store.one("SELECT status FROM users WHERE id = ?", (c1["id"],))[0] == "suspended")
store.run("UPDATE users SET status = 'active' WHERE id = ?", (c1["id"],))
check("  nor someone else's usage page", "پیدا نشد" in b.get("usage?u=%d" % own["id"])[2])
check("not the settings, even ticked", b.get("settings")[0] == 403)
check("not the domains page", b.get("domains")[0] == 403)
check("not what is set for everyone", b.post("discount-save", code="X")[0] == 403
      and b.post("wallet-settings")[0] == 403)
tx_own = store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
                   " VALUES (?, 1000, 'card', 'pending', ?)", (own["id"], panel.now())).lastrowid
tx_c1 = store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
                  " VALUES (?, 2000, 'card', 'pending', ?)", (c1["id"], panel.now())).lastrowid
_, _, body = b.get("receipts")
check("their customers' receipts only", "cust1" in body and ">mine<" not in body
      and "mine ·" not in body)
check("  not deciding someone else's", b.post("receipt-decide", id=str(tx_own),
                                              to="rejected")[0] == 403)
check("  nor seeing its picture", b.get("receipt/%d" % tx_own)[0] == 404)
tk = store.run("INSERT INTO tickets (user_id, subject, status, created_at, updated_at)"
               " VALUES (?, 'help-owner', 'open', ?, ?)",
               (own["id"], panel.now(), panel.now())).lastrowid
check("their customers' tickets only", "help-owner" not in b.get("tickets")[2]
      and "help-owner" not in b.get("tickets?t=%d" % tk)[2])

print("plans")
check("a plan on a template not given to them is refused", b.post(
    "plan-save", id="0", name="x", template_id=str(t_owner), days="30", quota_gb="10",
    price="1000")[0] == 403)
check("  no trial", b.post("plan-save", id="0", name="t", template_id=str(t_given), days="1",
                           quota_gb="1", trial="1")[0] == 403)
b.post("plan-save", id="0", name="sara-30", template_id=str(t_given), days="30",
       quota_gb="10", price="50000")
sp = store.one("SELECT * FROM plans WHERE name = 'sara-30'")
check("their plan is theirs", sp and sp["owner_admin"] == sara["id"])
op = store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, active,"
               " created_at) VALUES ('owner-30', ?, 30, 0, 90000, 1, ?)",
               (did, panel.now())).lastrowid
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, active, is_trial,"
          " created_at) VALUES ('trial', ?, 1, 0, 0, 1, 1, ?)", (did, panel.now()))
check("  the owner's plans are not on their page", "owner-30" not in b.get("plans")[2])
check("  nor can they change one", b.post("plan-active", id=str(op), to="0")[0] == 403)
c1 = store.one("SELECT * FROM users WHERE id = ?", (c1["id"],))
own = store.one("SELECT * FROM users WHERE id = ?", (own["id"],))
check("their customer is offered only their plans",
      [p["name"] for p in store.plans_for_sale(panel.seller_of(c1))] == ["sara-30"])
check("  the owner's customer only the owner's",
      [p["name"] for p in store.plans_for_sale(panel.seller_of(own))] == ["owner-30"])
check("  no trial for them", panel.trial_offer(store, c1)[0] is None
      and panel.trial_offer(store, own)[0] is not None)
store.run("INSERT INTO discount_codes (code, kind, value, active, created_at) VALUES"
          " ('OFF10', 'percent', 10, 1, ?)", (panel.now(),))
price, row, why = panel.discount_for(store, c1, sp, "OFF10")
check("  the owner's discount code is not for their plans", row is None and price == 50000)
b.post("pay-save", text="card 6037 sara")
check("their card number, for their customers only",
      panel.seller_pay_text(store, c1) == "card 6037 sara"
      and panel.seller_pay_text(store, own) != "card 6037 sara")

print("templates")
_, _, body = b.get("templates")
check("the ones ticked for them, not the owner's other ones",
      "given" in body and "owner-only" not in body)
import re as _re
default_name = store.one("SELECT name FROM templates WHERE id = ?", (did,))["name"]
check("  the default one only when the owner ticked it",
      "(پیش‌فرض)" not in body and did not in admin.seller_templates(sara)
      and "value='%d'" % did in owner.get("admins?id=%d" % sara["id"])[2])
owner.post("admin-save", id=str(sara["id"]), username="sara", password="",
           perm=["users", "receipts", "tickets", "plans", "templates"], own_only="1",
           max_users="2", cap_gb="1", days="30", templates_mode="pick",
           tpl=[str(t_given), str(did)])
b.login("sara", "sara-pw-123")
body = b.get("templates")[2]
counts = dict(_re.findall(r"<tr><td>([^<]+?)(?: <span[^>]*>[^<]*</span>)?</td><td>(\d+)</td>", body))
theirs = store.one("SELECT count(*) c FROM users WHERE owner_admin = ?", (sara["id"],))["c"]
check("  ticked, it is theirs to give, each template counting their own customers only",
      "(پیش‌فرض)" in body and sum(int(v) for v in counts.values()) == theirs, str(counts))
check("  not making one when they are to pick", b.post("template-new", name="mine")[0] == 403)
check("  nor changing the owner's", b.post("template-rules", id=str(t_given))[0] == 403)
owner.post("admin-save", id=str(sara["id"]), username="sara", password="",
           perm=["users", "receipts", "tickets", "plans", "templates"], own_only="1",
           max_users="2", cap_gb="1", days="30", templates_mode="own", templates_max="1",
           tpl=[str(t_given)])
b.login("sara", "sara-pw-123")
b.post("template-new", name="sara-t")
st = store.one("SELECT * FROM templates WHERE name = 'sara-t'")
check("making their own when allowed", st and st["owner_admin"] == sara["id"])
check("  up to the number", b.post("template-new", name="sara-t2")[0] == 403)
check("  and giving it to their customer", b.post(
    "user-template", id=str(c1["id"]), template_id=str(st["id"]))[0] == 303)

print("the cap and the days")
names = lambda: {r["uid"] for r in store.allowed()}
check("while they last, everyone is served", names() == {c1["id"], c2["id"], own["id"]})
store.run("UPDATE admins SET used_bytes = cap_bytes WHERE id = ?", (sara["id"],))
check("at the cap, all of the reseller's customers stop - and only theirs",
      names() == {own["id"]})
check("  their page says why", panel.seller_stopped(store, sara["id"]) == "cap")
sent = []
panel.emit_admin = lambda st, ev, data: sent.append(data["text"])
panel.check_sellers(store)
panel.check_sellers(store)
check("  the owner is told, once", len(sent) == 1 and "sara" in sent[0])
store.run("UPDATE admins SET used_bytes = 0, warned = 0,"
          " expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (sara["id"],))
check("past their days, the same", names() == {own["id"]}
      and panel.seller_stopped(store, sara["id"]) == "expired")
store.run("UPDATE admins SET expires_at = NULL WHERE id = ?", (sara["id"],))
check("renewed, back", names() == {c1["id"], c2["id"], own["id"]})
sync = load("templates/smartdns-sync", "sync")
check("their customer's page says whom to ask", "فروشنده" in sync.account_notice(
    {"status": "active", "seller_stopped": "cap"}) and not sync.account_notice(
    {"status": "active", "seller_stopped": None}))
store.run("UPDATE admins SET used_bytes = 0 WHERE id = ?", (sara["id"],))
store.fold_counters("r1", {"10.0.0.1": 100, "10.0.0.3": 100})
store.fold_counters("r1", {"10.0.0.1": 600, "10.0.0.3": 900})
check("their customers' traffic adds to theirs, not the owner's customers'",
      store.one("SELECT used_bytes FROM admins WHERE id = ?", (sara["id"],))[0] == 600)
check("their account page", "حساب فروشنده" in b.get("me")[2]
      and sara["ref_code"] in b.get("me")[2])
store.run("UPDATE admins SET used_bytes = 5000, warned = 7 WHERE id = ?", (sara["id"],))
check("the owner's form has a button to reset their usage",
      "admin-reset-usage" in owner.get("admins?id=%d" % sara["id"])[2]
      and "reset_used" not in owner.get("admins?id=%d" % sara["id"])[2])
check("  the seller cannot press it", b.post("admin-reset-usage", id=str(sara["id"]))[0] == 403)
owner.post("admin-reset-usage", id=str(sara["id"]))
row = store.one("SELECT used_bytes, warned FROM admins WHERE id = ?", (sara["id"],))
check("  the owner can, and the warnings are due again", row[0] == 0 and row[1] == 0)
owner.post("admin-save", id="0", username="staff1", password="staff-pw-123", perm=["users"],
           can_route="1", templates_mode="pick")
check("the relay tick is only a seller's: not kept for staff",
      store.one("SELECT can_route FROM admins WHERE username = 'staff1'")[0] == 0
      and "route-tick" in owner.get("admins?id=0")[2])
print("the seller's own page, for the owner")
_, _, body = owner.get("admins")
check("the admins list: each name opens their page, and the buttons are in the row",
      "admins?stats=%d" % sara["id"] in body and "admin-disable" in body
      and "admin-reset-usage" in body and "admin-delete" in body)
_, _, body = owner.get("admins?stats=%d" % sara["id"])
check("  their page: customers, traffic, days, sales and their customers' usage",
      "فروش این ماه" in body and "فروش کل" in body and "مصرف مشتری‌های sara" in body
      and "رسید در انتظار" in body and "admin-disable" in body)
staff = store.one("SELECT id FROM admins WHERE username = 'staff1'")["id"]
_, _, body = owner.get("admins?stats=%d" % staff)
check("  a staff member's page says they have no customers of their own",
      "کارمند است" in body and "admin-reset-usage" not in body)
status, loc, _ = owner.post("admin-reset-usage", id=str(sara["id"]), back="stats")
check("  a button pressed there comes back there", "admins?stats=%d" % sara["id"] in loc)
status, loc, _ = owner.post("admin-reset-usage", id=str(sara["id"]), back="list")
check("  and one pressed in the list, to the list", loc.split("/")[-1].startswith("admins?m="))
check("  not for the seller to see", b.get("admins?stats=%d" % sara["id"])[0] == 403)
home = b.get("")[2]
check("the seller's own home page draws their customers' usage, and nobody else's",
      "مصرف مشتری‌های من" in home and "همهٔ مشتری‌ها" not in home)
total = lambda o: sum(d for _, _, d in admin.total_usage(o)["days"])
check("the owner's usage is everybody's = their own customers' + each seller's",
      total(None) == total("own") + total(sara["id"]) and total(sara["id"]) > 0
      and total("own") > 0, "%s %s %s" % (total(None), total("own"), total(sara["id"])))
home = owner.get("")[2]
check("  the owner's home: everybody's with each one's share, then their own customers'",
      "مصرف کل — مشتری‌های شما و فروشنده‌ها" in home and "سهم هر کدام" in home
      and "مصرف مشتری‌های خودم" in home)
check("the home page's sellers open their page",
      "admins?stats=%d" % sara["id"] in owner.get("")[2])
check("the owner's home lists them", "sara" in owner.get("")[2])

srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
