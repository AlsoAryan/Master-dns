#!/usr/bin/env python3
"""DoH and DoT for each server, not one name for all.

What has to hold: each relay or single server's own DoH name is kept apart,
and the one name the panel used to give no longer changes hands between
relays; a customer is given every server of theirs - the ones ticked for
them, or all - with its plain DNS, its DoT name and their DoH address on it;
their page shows each, with an iPhone profile per server that points at
that server; and a server entry that does not add up is left out.
"""
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
sync = load("templates/smartdns-sync", "sync")
panel.log = sync.log = lambda *a: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
store.ensure_default_template([])
RELAYS = ("198.51.100.1", "198.51.100.2", "198.51.100.3")

print("the panel")
src = open(os.path.join(HERE, "..", "templates", "smartdns-panel"), encoding="utf-8").read()
check("each relay's DoH name is kept under its own address",
      'self.store.set_setting("doh_host:" + who, doh_host)' in src)
check("  and the shared one is set once, not handed from relay to relay",
      'if doh_host and not self.store.setting("doh_host"):' in src)
u = store.create_web_user("ali", "Ali", "ali-pass-1")
store.set_setting("doh_host:198.51.100.1", "r1.example.com")
store.set_setting("doh_host:198.51.100.3", "r3.example.com")
store.set_setting("single:198.51.100.3", "1")
servers = panel.customer_servers(store, store.one("SELECT * FROM users WHERE id = ?", (u["id"],)),
                                 RELAYS)
token = store.one("SELECT doh_token FROM users WHERE id = ?", (u["id"],))["doh_token"]
check("every server, in order, with DoT and DoH where it has them",
      [s["ip"] for s in servers] == list(RELAYS)
      and servers[0]["doh"] == "https://r1.example.com/dns-query/%s" % token
      and servers[1]["dot"] is None and servers[2]["single"], str(servers))
store.run("UPDATE users SET relays = '198.51.100.3' WHERE id = ?", (u["id"],))
picked = panel.customer_servers(store, store.one("SELECT * FROM users WHERE id = ?",
                                                 (u["id"],)), RELAYS)
check("the ticks that pick a customer's DNS pick these too",
      [s["ip"] for s in picked] == ["198.51.100.3"])
view = panel.user_view(store, store.one("SELECT * FROM users WHERE id = ?", (u["id"],)), RELAYS)
check("the bot is given them, and the one DoH it knew is theirs",
      view["servers"] == picked and view["doh"]["dot_host"] == "r3.example.com")

print("the customer's page")
sync.CFG = {"SELF_IP": "198.51.100.1", "PANEL_DOMAIN": "r1.example.com"}
info = {"doh_token": token, "servers": servers + [
    {"ip": "198.51.100.9", "dot": "evil.example.com", "doh": "https://other.example.com/x"}]}
good = sync.servers_doh(info)
check("the servers with DoH, and not one whose address is not its own name",
      [s["dot"] for s in good] == ["r1.example.com", "r3.example.com"])
box = sync.doh_box(info)
check("each server's DoH, DoT and its own iPhone profile",
      "r1.example.com/dns-query/" in box and "r3.example.com/dns-query/" in box
      and "/doh.mobileconfig?h=r3.example.com" in box and "تک‌سرور" in box)
prof = sync.mobileconfig(info, good[1])
check("the profile points at that server, not at this one",
      "https://r3.example.com/dns-query/%s" % token in prof and "198.51.100.3" in prof
      and "198.51.100.1" not in prof)

print("order, words and hiding")
admin = load("templates/smartdns-admin", "admin")
admin.log = lambda *a: None
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=%s\n" % ",".join(RELAYS))
import urllib.parse


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/nodes"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(who, rest, **form):
    admin.REQ.admin = who
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return r


check("the nodes page has the card, a row for each server",
      all(ip in admin.customer_servers_card("p") for ip in RELAYS)
      and "customer_servers_card" in admin.nodes_page.__code__.co_names)
act(None, "server-list-save", order_198_51_100_1="3", **{
    "order_198.51.100.1": "3", "order_198.51.100.2": "2", "order_198.51.100.3": "1",
    "note_198.51.100.3": "  مخصوص   ایرانسل ", "note_198.51.100.1": "تهران",
    "hide_198.51.100.2": "1"})
store.run("UPDATE users SET relays = NULL WHERE id = ?", (u["id"],))
row = lambda: store.one("SELECT * FROM users WHERE id = ?", (u["id"],))
got = panel.customer_servers(store, row(), RELAYS)
check("the customer sees them in the admin's order, without the hidden one",
      [s["ip"] for s in got] == ["198.51.100.3", "198.51.100.1"], str([s["ip"] for s in got]))
check("  each with its words", got[0]["note"] == "مخصوص ایرانسل" and got[1]["note"] == "تهران")
check("  and numbered as shown", [s["n"] for s in got] == [1, 2])
store.run("UPDATE users SET relays = '198.51.100.2' WHERE id = ?", (u["id"],))
check("a customer whose only server is hidden is still given one",
      [s["ip"] for s in panel.customer_servers(store, row(), RELAYS)] == ["198.51.100.3", "198.51.100.1"])
store.run("UPDATE users SET relays = NULL WHERE id = ?", (u["id"],))

sid = store.run("INSERT INTO admins (username, password_hash, password_salt, perms, own_only,"
                " can_route, created_at) VALUES ('sara', 'x', 'y', '[]', 1, 1, ?)",
                (panel.now(),)).lastrowid
sara = store.one("SELECT * FROM admins WHERE id = ?", (sid,))
check("a seller who may pick servers has a card of their own",
      "198.51.100.3" in admin.seller_notes_card("p", sara)
      and "198.51.100.2" not in admin.seller_notes_card("p", sara))
act(sara, "seller-server-notes", **{"note_198.51.100.3": "سرور ویژهٔ فروشگاه سارا"})
store.run("UPDATE users SET owner_admin = ? WHERE id = ?", (sid, u["id"]))
got = panel.customer_servers(store, row(), RELAYS)
check("  their customers see their words, the owner's where they wrote none",
      got[0]["note"] == "سرور ویژهٔ فروشگاه سارا" and got[1]["note"] == "تهران")
store.run("UPDATE admins SET can_route = 0 WHERE id = ?", (sid,))
sara = store.one("SELECT * FROM admins WHERE id = ?", (sid,))
check("  not without that permission", panel.customer_servers(store, row(), RELAYS)[0]["note"]
      == "مخصوص ایرانسل" and admin.seller_notes_card("p", sara) == ""
      and act(sara, "seller-server-notes").sent.get("code") == 403)

print("shown to the customer")
info = {"dns": ["198.51.100.3", "198.51.100.1"], "servers": got}
box = sync.dns_box(info)
check("their page's DNS box says the words in place of DNS 1 and DNS 2",
      "سرور ویژهٔ فروشگاه سارا" in box and "تهران" in box
      and "<span class='k'>DNS اول</span>" not in box
      and "<span class='k'>DNS دوم</span>" not in box
      and "کدام برای شما بهتر است" in box, box)
box = sync.dns_box({"dns": ["198.51.100.3", "198.51.100.1"],
                    "servers": [dict(got[0], note=""), got[1]]})
check("  and DNS 1 still where a server has no words",
      "<span class='k'>DNS اول</span>" in box and "<span class='k'>تهران</span>" in box)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
