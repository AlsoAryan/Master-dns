#!/usr/bin/env python3
"""A plan server by server: which it is sold on, and where each goes out.

What has to hold: once there is anything to choose - more than one relay, or
more than one server abroad - each plan has a line per server: not on it,
automatic, or a server abroad. A customer on a plan that is not on a server
is on none of its lists, allowlist or DoH, and is sent to the customer panel
there; they are shown the DNS of the plan's servers only; a plan whose
servers are all gone is on every one again. Buying a plan puts the customer
on its servers abroad: one for every relay when all name the same, relay by
relay when they differ, their own left alone on automatic. One relay with two
servers abroad can sell a plan for each. A plan from before - its ticked
relays and one server abroad, or none - reads as the same, and keeps what it
had when saved from a form without the lines; a server abroad taken off turns
into automatic wherever a plan named it; an admin who may not pick servers
leaves them be.
"""
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

TR, DE, SG = "198.51.100.1", "198.51.100.2", "198.51.100.3"     # relays
MAIN, NODE = "203.0.113.1", "203.0.113.9"                         # servers abroad
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.PANEL_ENV = os.path.join(tmp, "panel.env")


def servers(relays, nodes=()):
    with open(admin.PANEL_ENV, "w") as fh:
        fh.write("RELAY_IP=%s\n" % ",".join(relays))
        if nodes:
            fh.write("NODE_IP=%s\n" % ",".join(nodes))


servers([TR, DE, SG])
admin.INSTALL_STATE = os.path.join(tmp, "install-state")
with open(admin.INSTALL_STATE, "w") as fh:
    fh.write("exit-ip %s\n" % MAIN)
admin.SYNC_ENV_HERE = os.path.join(tmp, "no-sync.env")
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/plans"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "plans"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    admin.REQ.admin = None
    r = Rec()
    r.action(rest, {k: (v if isinstance(v, list) else [v]) for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def plan_form(name="ترکیه", route=None, **extra):
    """A plan's form; `route` is {server: "off" | "auto" | exit} for the
    lines, None for a form without them."""
    base = dict(name=name, template_id="1", days="30", quota_gb="50", price="100000",
                devices="1")
    for ip, way in (route or {}).items():
        base["re_" + ip] = way
    base.update(extra)
    return base


def plan(name):
    return store.one("SELECT * FROM plans WHERE name = ?", (name,))


every = lambda way="auto": {TR: way, DE: way, SG: way}

print("the plans page")
page = Rec().plans()
check("a line per server, each with not on it, automatic", page.count("name='re_") >= 3
      and "<th>سرورها</th>" in page and "<option value='off'" in page
      and "خودکار" in page)
act("plan-save", **plan_form(route={TR: "auto", DE: "off", SG: "off"}))
check("a plan on one server is sold on that one", plan("ترکیه")["relays"] == TR)
act("plan-save", **plan_form(name="اروپا", route={TR: "off", DE: "auto", SG: "auto"}))
check("  or on two, in the order customers see them", plan("اروپا")["relays"] == "%s,%s" % (DE, SG))
act("plan-save", **plan_form(name="همه", route=every()))
p_all = plan("همه")
check("every server on automatic is a plan for all, as before",
      p_all["relays"] is None and p_all["relay_exits"] is None and p_all["exit"] is None)
check("a plan on no server is refused",
      "m=!" in act("plan-save", **plan_form(name="هیچ", route=every("off"))))
check("  so is a server that is not one",
      "m=!" in act("plan-save", **plan_form(name="بد", route={"192.0.2.50": "auto"})))
check("  and a server abroad that is not one",
      "m=!" in act("plan-save", **plan_form(name="بد", route={TR: "192.0.2.9"})))
check("the page shows each plan's lines",
      "name='re_%s'><option value='off'>—</option><option value='auto' selected>" % TR
      in Rec().plans())

print("customers")


def customer(name, plan_id, ip):
    store.run("INSERT INTO users (username, created_at, status, plan_id)"
              " VALUES (?, ?, 'active', ?)", (name, panel.now(), plan_id))
    uid = store.one("SELECT id FROM users WHERE username = ?", (name,))["id"]
    store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)", (uid, ip, panel.now()))
    return uid


alice = customer("alice", plan("ترکیه")["id"], "192.0.2.10")
bob = customer("bob", p_all["id"], "192.0.2.11")
eve = customer("eve", plan("اروپا")["id"], "192.0.2.12")
relays = [TR, DE, SG]
check("a customer is kept off the servers their plan is not on",
      alice in panel.kept_off(store, DE, relays) and alice in panel.kept_off(store, SG, relays)
      and alice not in panel.kept_off(store, TR, relays))
check("  the two-server plan's off the third only",
      eve in panel.kept_off(store, TR, relays) and eve not in panel.kept_off(store, DE, relays))
check("  and one on a plan for all, off none",
      all(bob not in panel.kept_off(store, r, relays) for r in relays))
check("a plan whose servers are all gone is on every one again",
      alice not in panel.kept_off(store, DE, [DE, SG]))
row = lambda uid: store.one("SELECT * FROM users WHERE id = ?", (uid,))
check("each is shown those servers' DNS only",
      panel.shown_relays(store, row(alice), relays) == [TR]
      and panel.shown_relays(store, row(eve), relays) == [DE, SG]
      and panel.shown_relays(store, row(bob), relays) == relays)
psrc = open(os.path.join(HERE, "..", "templates", "smartdns-panel"), encoding="utf-8").read()
sync_part = psrc[psrc.index('if self.path == "/sync":'):psrc.index('"extra_domains": extra')]
check("each relay's allowlist and DoH leave out those not on it",
      "off = kept_off(self.store, who, self.relays)" in sync_part
      and 'if v["uid"] not in off' in sync_part and 'if t["uid"] not in off' in sync_part)

print("servers abroad, relay by relay")
servers([TR, DE, SG], [NODE])
page = Rec().plans()
check("with a node, each relay's line offers the servers abroad too",
      "<option value='%s'>%s</option>" % (NODE, NODE) in page)
act("plan-save", **plan_form(name="VIP", route={TR: NODE, DE: MAIN, SG: "off"}))
vip = plan("VIP")
check("each relay its own", json.loads(vip["relay_exits"]) == {TR: NODE, DE: MAIN}
      and vip["exit"] is None)
act("plan-save", **plan_form(name="آلمان", route={TR: MAIN, DE: MAIN, SG: "off"}))
check("all the same: the plan's one", plan("آلمان")["exit"] == MAIN)
act("plan-save", **plan_form(name="نیمه", route={TR: NODE, DE: "auto", SG: "off"}))
store.set_setting("single:" + SG, "1")
check("a single server is only on it or not: it is its own way out",
      "m=!" in act("plan-save", **plan_form(name="تک", route={SG: NODE})))
check("  and its line offers no servers abroad",
      "name='re_%s'><option value='off'>—</option><option value='auto'" % SG
      in Rec().plans() and "re_%s'><option value='off'>—</option><option value='auto'>"
      "خودکار</option><option value='%s'" % (SG, MAIN) not in Rec().plans())
store.run("DELETE FROM settings WHERE key = ?", ("single:" + SG,))

print("buying a plan")
store.run("INSERT INTO users (username, created_at, status) VALUES ('tina', ?, 'active')",
          (panel.now(),))
tina = store.one("SELECT id FROM users WHERE username = 'tina'")["id"]
store.run("UPDATE users SET exit = ?, relay_exits = NULL WHERE id = ?", (NODE, tina))
panel.apply_plan(store, tina, p_all["id"])
check("a plan on automatic leaves the customer's own server abroad",
      row(tina)["exit"] == NODE)
over = lambda uid: store.run("UPDATE users SET expires_at = NULL WHERE id = ?", (uid,))
over(tina)
panel.apply_plan(store, tina, plan("آلمان")["id"])
check("one naming the same everywhere puts them on it, from every relay",
      row(tina)["exit"] == MAIN and row(tina)["relay_exits"] is None)
over(tina)
panel.apply_plan(store, tina, vip["id"])
check("one naming them relay by relay, relay by relay",
      row(tina)["exit"] is None and json.loads(row(tina)["relay_exits"]) == {TR: NODE, DE: MAIN})
over(tina)
panel.apply_plan(store, tina, plan("نیمه")["id"])
check("  automatic on a relay leaves it to that relay's own",
      json.loads(row(tina)["relay_exits"]) == {TR: NODE})
over(tina)
admin.STORE.apply_plan(tina, plan("آلمان")["id"])
check("the same when the admin gives the plan by hand",
      admin.STORE.one("SELECT exit FROM users WHERE id = ?", (tina,))["exit"] == MAIN)

print("one relay, two servers abroad")
servers([TR], [NODE])
page = Rec().plans()
check("a line for the one relay, with both servers abroad",
      "name='re_%s'" % TR in page and "<option value='%s'>" % NODE in page)
act("plan-save", **plan_form(name="VIP ترکیه", route={TR: NODE}))
act("plan-save", **plan_form(name="VIP آلمان", route={TR: MAIN}))
check("  a plan for each", plan("VIP ترکیه")["exit"] == NODE and plan("VIP آلمان")["exit"] == MAIN)
act("plan-save", id=str(plan("VIP ترکیه")["id"]), **plan_form(name="VIP ترکیه", price="120000"))
check("  and one saved from a form without the lines keeps them",
      plan("VIP ترکیه")["exit"] == NODE and json.loads(plan("VIP ترکیه")["relay_exits"])
      == {TR: NODE})

print("a plan from before")
servers([TR, DE, SG], [NODE])
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, exit, relays,"
          " created_at) VALUES ('قدیمی', 1, 30, 1, 1000, ?, ?, ?)", (NODE, TR, panel.now()))
old = dict(plan("قدیمی"))
check("its ticked relays and its one server abroad read as the same",
      admin.plan_route(old, [TR, DE, SG]) == {TR: NODE})
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, relays, exit,"
          " exit_pick, created_at) VALUES ('۰.۹.۶', 1, 30, 1, 1000, ?, ?, '', ?)",
          (DE, MAIN, panel.now()))
auto96 = plan("۰.۹.۶")
check("0.9.6's automatic is automatic: each relay's own, not the one kept then",
      admin.plan_route(dict(auto96), [TR, DE, SG]) == {DE: "auto"}
      and panel.plan_user_exits(auto96) is None)
check("a plan for all is on every server, automatic",
      admin.plan_route({"name": "x"}, [TR, DE]) == {TR: "auto", DE: "auto"})

print("a server abroad taken off")
admin.Admin.action(Rec(), "node-del", {"ip": [NODE]})
check("wherever a plan named it, automatic",
      json.loads(plan("VIP")["relay_exits"]) == {TR: "auto", DE: MAIN})

print("an admin who may not pick servers")
store.run("INSERT INTO admins (username, password_hash, password_salt, perms, own_only,"
          " can_route, created_at) VALUES ('s', 'x', 'y', '[]', 1, 0, ?)", (panel.now(),))
seller_row = store.one("SELECT * FROM admins WHERE username = 's'")
admin.seller = lambda: seller_row
check("sees no lines", "name='re_" not in Rec().plans())
p_eu = plan("اروپا")
store.run("UPDATE plans SET owner_admin = ? WHERE id = ?", (seller_row["id"], p_eu["id"]))
act("plan-save", id=str(p_eu["id"]), **plan_form(name="اروپا ۲", route={TR: "auto"}))
check("and saving a plan leaves its servers as they were",
      store.one("SELECT relays FROM plans WHERE id = ?", (p_eu["id"],))["relays"]
      == "%s,%s" % (DE, SG))

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
