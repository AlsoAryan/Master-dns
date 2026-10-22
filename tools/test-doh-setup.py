#!/usr/bin/env python3
"""The encrypted-DNS section, and the setup page a browser is sent to.

What has to hold: the account page has the DoH address and the DoT name and
no QR code - it only ever led to a copy of the same page. A browser that
opens the DoH address itself is sent by nginx to a setup page, while DoH
clients are not; that page opens for a customer's current token and nothing
else, shows the addresses and hands out the iPhone profile for that token.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = load("templates/smartdns-sync", "sync")
sync.log = lambda *a: None
tmp = tempfile.mkdtemp()
sync.CFG = {"PANEL_DOMAIN": "users.example.com", "SELF_IP": "198.51.100.7"}
sync.DOH_FLAG = os.path.join(tmp, "doh")
open(sync.DOH_FLAG, "w").close()
sync.DOH_STATE = os.path.join(tmp, "doh.json")
TOKEN = "Tk3v9QpZr2LmXw8bYc4dNa"
with open(sync.DOH_STATE, "w") as fh:
    json.dump({"tokens": {hashlib.sha256(TOKEN.encode()).hexdigest(): {"uid": 2, "port": 53}}}, fh)

print("the account page")
box = sync.doh_box({"doh_token": TOKEN})
check("no QR code, and nothing left of one",
      "<svg" not in box and "qr" not in box.lower() and not hasattr(sync, "qr_svg"))
check("the DoH address is there to copy",
      "https://users.example.com/dns-query/%s" % TOKEN in box)
check("and the profile comes from the signed-in page", "href='/doh.mobileconfig'" in box)
check("DoT is written out too: the name, its port and the tls:// form",
      "DoT — DNS over TLS" in box and "value='users.example.com'" in box
      and "۸۵۳" in box and "tls://users.example.com" in box)
check("no DoH on this relay: no section at all",
      (os.remove(sync.DOH_FLAG) or sync.doh_box({"doh_token": TOKEN})) == "")
open(sync.DOH_FLAG, "w").close()


class Page:
    """Enough of the handler to call doh_setup and see what it sent."""
    def __init__(self):
        self.sent = None

    def send_html(self, body, code=200, headers=None):
        self.sent = (code, "html", body)

    def send(self, body, code, headers):
        self.sent = (code, headers.get("Content-Type"), body)


def open_page(path):
    p = Page()
    sync.UserPanel.doh_setup(p, path)
    return p.sent


print("the setup page")
code, kind, body = open_page("/doh-setup/" + TOKEN)
check("a current token opens it, with the section unfolded",
      code == 200 and "<details class='pw doh' open>" in body
      and "https://users.example.com/dns-query/%s" % TOKEN in body)
check("with the DoT name", "DoT — DNS over TLS" in body)
check("its profile is the token's own, not the session's",
      "href='/doh-setup/%s.mobileconfig'" % TOKEN in body)
code, kind, body = open_page("/doh-setup/%s.mobileconfig" % TOKEN)
check("which is a real profile for that address",
      code == 200 and kind == "application/x-apple-aspen-config"
      and "https://users.example.com/dns-query/%s" % TOKEN in body)
check("the way nginx sends a browser works too",
      open_page("/doh-setup/dns-query/" + TOKEN)[0] == 200)
for bad in ("/doh-setup/AAAAAAAAAAAAAAAAAAAAAA", "/doh-setup/dns-query",
            "/doh-setup/x", "/doh-setup/%s.mobileconfig" % ("B" * 22)):
    check("an unknown token is refused: %s" % bad, open_page(bad)[0] == 404)

print("nginx")
logic = open(os.path.join(ROOT, "templates/relay-nginx.conf"), encoding="utf-8").read()
check("only a GET without dns= is a browser",
      '"GET:"  1;' in logic and 'map "$request_method:$arg_dns" $doh_browser' in logic)
check("and it is sent to the setup page on the panel's port",
      "return 302 https://__DOH_HOST__:8443/doh-setup$uri;" in logic)
start = logic.index("# doh begin\nhttp {")
doh = logic[start:logic.index("# doh end", start)]
check("all of it inside the DoH block, so a relay without DoH has none of it",
      "$doh_browser" in doh and logic.count("$doh_browser") == doh.count("$doh_browser"))

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
