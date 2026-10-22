#!/usr/bin/env python3
"""The customer panel taken off a server that has a domain.

What has to hold: a server with a domain shows the customer panel on it, and
the admin can untick that per server, but never the last one; the bot's and
the links' address moves off a server as soon as its panel is off, and a
server reporting in never puts it back; the server itself then sends
everybody to another server's panel at the same address - a one-time
sign-in link keeps its token - while DoH's setup page on its own name, and
the font that page uses, stay; and a server never sends people to itself.
"""
import http.client
import http.server
import importlib.machinery
import importlib.util
import os
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
sync = load("templates/smartdns-sync", "sync")
admin.log = panel.log = sync.log = lambda *a: None
sync.log_access = lambda *a, **k: None

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1,198.51.100.2,198.51.100.3\n")
admin.SYNC_ENV_HERE = os.path.join(tmp, "no-sync.env")
A, B, C = "198.51.100.1", "198.51.100.2", "198.51.100.3"
URL_A, URL_B = "https://a.example.com:8443/", "https://b.example.com:8443/"


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


print("the servers say where their panels are")
panel.note_panel_url(store, A, URL_A)
panel.note_panel_url(store, B, URL_B)
panel.note_panel_url(store, C, "")
check("each is kept, and the last becomes where people are sent",
      store.setting("panel_url:" + A) == URL_A and store.setting("panel_url:" + B) == URL_B
      and store.setting("customer_panel_url") == URL_B)
card = admin.customer_servers_card("p")
check("a tick beside each server with a panel, ticked", "name='panel_%s' value='1' checked" % A
      in card and "name='panel_%s' value='1' checked" % B in card)
check("  none beside one with no domain", "name='panel_%s'" % C not in card)
check("the panels answer where they are", panel.panel_elsewhere(store, A) == ""
      and panel.panel_elsewhere(store, B) == "")

print("taking one off")
loc = act("server-list-save", **{"panel_" + A: "1"})
check("saved", "m=!" not in loc and store.setting("panel_off:" + B) == "1"
      and store.setting("panel_off:" + A) != "1", loc)
check("people are sent to the one still on, at once",
      store.setting("customer_panel_url") == URL_A)
panel.note_panel_url(store, B, URL_B)
check("the server that is off reporting in does not take that back",
      store.setting("customer_panel_url") == URL_A)
check("it is told where to send people", panel.panel_elsewhere(store, B) == URL_A)
check("  the other, nothing", panel.panel_elsewhere(store, A) == "")
check("the page shows it unticked", "name='panel_%s' value='1'>" % B
      in admin.customer_servers_card("p"))
loc = act("server-list-save")
check("the last one is never taken off", "m=!" in loc and store.setting("panel_off:" + A) != "1"
      and store.setting("panel_off:" + B) == "1", loc)
store.set_setting("customer_panel_url", URL_B)
check("an address kept from before, of a server now off, is not where people go",
      panel.panel_elsewhere(store, B) == URL_A)
act("server-list-save", **{"panel_" + A: "1", "panel_" + B: "1"})
check("ticked again, it is on", store.setting("panel_off:" + B) != "1"
      and panel.panel_elsewhere(store, B) == "")

print("on the server itself")
sync.CFG = {"PANEL_DOMAIN": "b.example.com"}
check("the address it is told is taken", sync.panel_to(URL_A) == URL_A)
check("  never its own", sync.panel_to(URL_B) == "")
check("  nor anything that is not an https address",
      sync.panel_to("javascript:alert(1)") == "" and sync.panel_to(None) == "")
httpd = http.server.HTTPServer(("127.0.0.1", 0), sync.UserPanel)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
sync.font_bytes = lambda: b"wOF2font"


def ask(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    c.request(method, path, body=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"} if body else {})
    r = c.getresponse()
    r.read()
    c.close()
    return r.status, r.getheader("Location")


sync.PANEL_TO["url"] = URL_A
check("its panel sends people to the other", ask("GET", "/") == (302, URL_A))
check("  at the same address, so a one-time sign-in link keeps its token",
      ask("GET", "/go/abcdefghijklmnopqrstuvwxyz") ==
      (302, "https://a.example.com:8443/go/abcdefghijklmnopqrstuvwxyz"))
check("  a form too", ask("POST", "/login", "username=ali&password=x")[0] == 303)
check("the font stays", ask("GET", "/vazirmatn.woff2")[0] == 200)
status, where = ask("GET", "/doh-setup/sometoken")
check("DoH's setup page on its own name stays", where is None or "a.example.com" not in where)
sync.PANEL_TO["url"] = ""
check("with its panel on, nothing is sent anywhere", ask("GET", "/ping") == (204, None))
httpd.shutdown()

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
