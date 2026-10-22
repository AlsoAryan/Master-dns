#!/usr/bin/env python3
"""Domains the operator sends to a resolver of their choosing.

What has to hold: what the panel sends is cleaned to addresses; each relay
says whether the resolver answers
from there, and the admin panel shows it; the admin panel refuses what is
not an address, a resolver on the relay itself, and a domain blocked for every
template. How a forward reaches each template's resolver is in test-blocked.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:500]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (sync, panel, admin):
    m.log = lambda *a, **k: None
panel.print = lambda *a, **k: None

ME = "198.51.100.1"
tmp = tempfile.mkdtemp()
d_main, d_base, d_prof = (os.path.join(tmp, n) for n in ("dnsmasq.d", "base", "profiles"))
for d in (d_main, d_base, d_prof):
    os.makedirs(d)


def put(path, lines):
    with open(path, "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


put(os.path.join(d_main, "smart-dns.conf"),
    ["no-resolv", "server=1.1.1.1", "address=/spotify.com/%s" % ME,
     "address=/google.com/%s" % ME])
put(os.path.join(d_main, "bypass.conf"), ["server=/accounts.spotify.com/#"])
sync.DNSMASQ_D, sync.BASE_DIR, sync.PROFILE_DIR = d_main, d_base, d_prof
sync.HIJACK_CONF = os.path.join(d_main, "smart-dns.conf")
sync.BYPASS_CONF = os.path.join(d_main, "bypass.conf")
sync.CUSTOM_CONF = os.path.join(d_main, "50-smartdns-custom.conf")
sync.EPIC_PINS = os.path.join(d_main, "epic-pins.conf")
sync.CFG = {"SELF_IP": ME}
sync.sync_base_dir = lambda: False


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


calls = []
sync.sh = lambda *a: calls.append(a) or R()
quiet = contextlib.redirect_stdout(io.StringIO())

print("what the panel sends")
got = sync.clean_forwards({"example.com": ["1.1.1.1", "10.0.0.2#5353", "bad", "1.1.1.1;x"],
                           "nothing.example": ["x"], "bad domain": ["1.1.1.1"]})
check("addresses, with a port where given, and nothing else",
      got == {"example.com": ["1.1.1.1", "10.0.0.2#5353"]}, repr(got))

print("checking the resolver answers")
srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
srv.bind(("127.0.0.1", 0))
port = srv.getsockname()[1]
answers = [3, 5]          # "no such name", then "refused"


def serve():
    for rcode in answers:
        data, addr = srv.recvfrom(512)
        srv.sendto(data[:2] + bytes([0x81, 0x80 | rcode]) + data[4:], addr)


threading.Thread(target=serve, daemon=True).start()
check("a resolver that says \"no such name\" is there",
      isinstance(sync.dns_probe("127.0.0.1#%d" % port, name="nothing.example",
                                any_answer=True), int))
check("one that refuses is not",
      sync.dns_probe("127.0.0.1#%d" % port, name="x.example", timeout=0.5, any_answer=True) is None)
srv.close()
src = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("the result rides on the sync",
      'payload["forward_check"] = {"at": int(FWD_CHECK["at"]), "ms": FWD_CHECK["ms"]}' in src)

print("the panel")
store = panel.Store(os.path.join(tmp, "panel.db"))
store.run("INSERT INTO dns_forwards (domain, servers, added_at) VALUES"
          " ('example.com', '1.1.1.1 8.8.8.8', ?)", (panel.now(),))
check("it keeps them", store.dns_forwards() == {"example.com": ["1.1.1.1", "8.8.8.8"]})
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("keeps each relay's check", 'self.store.set_setting("forward_check:" + who, text)' in psrc)
user = store.create_user(111, "ali", "علی")
reason = panel.qlog_reasoner(store, store.one("SELECT * FROM users WHERE id = ?", (user["id"],)),
                             panel.CATALOGUE)
check("the DNS report says which resolver answered",
      reason("www.example.com") == "forward:1.1.1.1 8.8.8.8"
      and "forward" in admin.QLOG_REASON and "forward" in sync.QLOG_REASON)

print("the admin panel")
admin.DB = os.path.join(tmp, "panel.db")
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(admin.DB)
admin.CATALOGUE = [{"key": "spotify", "label": "Spotify",
                    "groups": [{"key": "main", "domains": ["spotify.com"]}]},
                   {"key": "custom", "label": "x", "groups": [{"key": "main", "domains": []}]}]


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, **form):
    r = Rec()
    admin.Admin.action(r, what, {k: [v] for k, v in form.items()})
    return r.to


said = act("forward-add", domain="https://Spotify.com/", servers="1.1.1.1, 10.0.0.2#5353")
check("a forward is added, with its resolvers as dnsmasq will be given them",
      "پرسیده می‌شود" in said and admin.STORE.one(
          "SELECT servers FROM dns_forwards WHERE domain = 'spotify.com'")["servers"]
      == "1.1.1.1 10.0.0.2#5353", said)
check("and it says a name the service routes stops going through the relay",
      "Spotify" in said and "دیگر از رله نمی‌رود" in said)
check("not twice", "از قبل" in act("forward-add", domain="spotify.com", servers="8.8.8.8"))
for bad in ("", "1.1.1", "300.1.1.1", "1.1.1.1#0", "127.0.0.1", "dns.google"):
    if not act("forward-add", domain="bad%d.example" % len(bad), servers=bad).startswith("domains?m=!"):
        check("refuses %r" % bad, False)
check("anything that is not a resolver's address is refused, the relay itself included",
      not admin.STORE.one("SELECT 1 FROM dns_forwards WHERE domain LIKE 'bad%'"))
admin.STORE.run("INSERT INTO blocked_domains (domain, added_at) VALUES ('ads.example', 'x')")
admin.STORE.run("INSERT INTO blocked_domains (domain, added_at, all_templates)"
                " VALUES ('some.example', 'x', 0)")
check("a domain blocked for every template cannot be forwarded",
      act("forward-add", domain="x.ads.example", servers="1.1.1.1").startswith("domains?m=!"))
check("one blocked for some can - it works for the rest",
      not act("forward-add", domain="x.some.example", servers="1.1.1.1").startswith("domains?m=!"))
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('forward_check:198.51.100.7', ?)",
                (json.dumps({"at": 1, "ms": {"spotify.com": {"1.1.1.1": 23, "10.0.0.2#5353": None}}}),))
card = admin.forwards_card("p")
check("the card shows each relay's check",
      "از رله 198.51.100.7" in card and "23 ms" in card and "جواب نداد" in card
      and "action='/p/forward-del'" in card, card[:600])
act("forward-del", domain="spotify.com")
check("and a forward can be removed",
      not admin.STORE.one("SELECT 1 FROM dns_forwards WHERE domain = 'spotify.com'"))
asrc = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("the card is on the domains page", "out.append(forwards_card(p))" in asrc)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
