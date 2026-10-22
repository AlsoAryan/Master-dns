#!/usr/bin/env python3
"""A domain for a relay or single server, given from the admin panel.

What has to hold: the admin writes a domain beside a server and saves; a bad
one, or one given to two servers, is refused; the panel tells that server
until it has it; the server first checks the name points at it and says so
plainly when not, without running anything; when it does, the server runs
the installer the panel names - its hash checked - with that domain, which
is what turns DoH and DoT on; how it went comes back to the nodes page, and
a failure is tried again only after a quarter of an hour.
"""
import hashlib
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
sync = load("templates/smartdns-sync", "sync")
admin.log = panel.log = sync.log = lambda *a: None
told = []
panel.emit_admin = lambda store, ev, data: told.append(data["text"])

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1,198.51.100.2\n")
admin.SYNC_ENV_HERE = os.path.join(tmp, "no-sync.env")


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


def act(rest, **form):
    admin.REQ.admin = None
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


print("the admin panel")
card = admin.customer_servers_card("p")
check("a domain box beside each server", "domain_198.51.100.1" in card and "دامنه" in card)
loc = act("server-list-save", **{"domain_198.51.100.1": "not a domain"})
check("a bad domain is refused", "m=!" in loc and not store.setting("domain_want:198.51.100.1"))
loc = act("server-list-save", **{"domain_198.51.100.1": "dns.example.com",
                                 "domain_198.51.100.2": "dns.example.com"})
check("  and one given to two servers", "m=!" in loc)
act("server-list-save", **{"domain_198.51.100.1": "R1.Example.com."})
check("saved, as a name", store.setting("domain_want:198.51.100.1") == "r1.example.com")
check("  the page says it is on its way", "در حال گرفتن گواهی" in admin.customer_servers_card("p"))

print("the panel tells the server")
BLOB = b'#!/bin/bash\nVERSION="0.9.0"\n'
SHA = hashlib.sha256(BLOB).hexdigest()
panel.installer_on_hand = lambda: ("0.9.0", SHA, BLOB)
check("that server is told the name and the installer",
      panel.domain_order(store, "198.51.100.1") == {"name": "r1.example.com", "sha": SHA})
check("  the other nothing", panel.domain_order(store, "198.51.100.2") is None)

print("the server")
sync.DOMAIN_DIR = os.path.join(tmp, "domain")
sync.CFG = {"SELF_IP": "198.51.100.1", "PANEL_DOMAIN": ""}
ran = []


class R:
    returncode, stdout, stderr = 0, "", ""


def fake_sh(*args):
    ran.append(args)
    r = R()
    r.returncode = 3 if args[:2] == ("systemctl", "is-active") else 0
    return r


sync.sh = fake_sh
sync.fetch = lambda path: (BLOB, {})
sync.name_points_here = lambda name: (False, ["203.0.113.7"])
order = {"name": "r1.example.com", "sha": SHA}
sync.start_domain(order)
res = sync.domain_result()
check("a name not pointing here is said plainly, and nothing is run",
      res and not res["ok"] and "203.0.113.7" in res["error"]
      and not any(a[0] == "systemd-run" for a in ran), str(res))
panel.domain_report(store, "198.51.100.1", res)
check("  the page shows why, and the admin is told",
      "203.0.113.7" in admin.customer_servers_card("p") and told)
sync.domain_heard()
sync.name_points_here = lambda name: (True, ["198.51.100.1"])
sync.start_domain(order)
check("  and it is not tried again for a quarter of an hour",
      not any(a[0] == "systemd-run" for a in ran))
os.unlink(os.path.join(sync.DOMAIN_DIR, "tried"))
sync.start_domain(order)
run = [a for a in ran if a[0] == "systemd-run"]
check("pointing here: the installer is run with the domain",
      run and "PANEL_DOMAIN=r1.example.com" in run[-1][-1]
      and open(os.path.join(sync.DOMAIN_DIR, "master-dns.sh"), "rb").read() == BLOB)
sync.fetch = lambda path: (BLOB + b"x", {})
os.unlink(os.path.join(sync.DOMAIN_DIR, "tried"))
try:
    sync.start_domain(order)
    refused = False
except RuntimeError:
    refused = True
check("  not an installer other than the one the panel named", refused)
with open(os.path.join(sync.DOMAIN_DIR, "run.log"), "w") as fh:
    fh.write("...\nDNS over HTTPS on https://r1.example.com/dns-query, over TLS on ...\n")
with open(os.path.join(sync.DOMAIN_DIR, "rc"), "w") as fh:
    fh.write("0\n")
res = sync.domain_result()
check("how it went comes back", res["ok"] and res["name"] == "r1.example.com")
panel.domain_report(store, "198.51.100.1", res)
check("  the page says it is on, and the server is not told again",
      "✓ DoH و DoT روشن" in admin.customer_servers_card("p")
      and panel.domain_order(store, "198.51.100.1") is None)
sync.CFG["PANEL_DOMAIN"] = "r1.example.com"
ran.clear()
sync.start_domain(order)
check("a server that has the name does nothing", not ran)

cert = open(os.path.join(HERE, "..", "templates", "smartdns-cert"), encoding="utf-8").read()
check("one certbot at a time: the renewal timer cannot trip up a new certificate",
      "flock -w 600 9" in cert and "pgrep -x certbot" in cert)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
