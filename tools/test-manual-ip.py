#!/usr/bin/env python3
"""The panels' header and version line, and a customer typing an address by hand.

Somebody signing up on mobile data for a service they want at home used to have
to go home first: the page could only register the address it was opened from.
Now they can type the home address. What the relay has to refuse is everything
that could never be somebody's internet connection - the exit already refuses an
address that belongs to another account.
"""
import http.client
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


def load(name, fname):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(
            name, os.path.join(HERE, "..", "templates", fname)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


admin = load("admin", "smartdns-admin")
sync = load("sync", "smartdns-sync")
RELAY, EXIT = "45.12.34.56", "3.3.3.3"
sync.CFG = {"PANEL_DOMAIN": "panel.example.com", "PANEL_HOST": EXIT, "SELF_IP": RELAY}

tmp = tempfile.mkdtemp()
vf = os.path.join(tmp, "version")
with open(vf, "w") as fh:
    fh.write("0.3.99\n")
admin.VERSION_FILE = sync.VERSION_FILE = vf

print("the header and the version")
p = admin.page("کاربران", "<p>x</p>", {"ADMIN_PATH": "p"}, "users")
check("admin pages carry the name, large", "master dns" in p and "class='brand'" in p)
check("above the page's own title", p.index("class='brand'") < p.index("<h1>"))
check("and the version at the foot", "master dns v0.3.99</footer>" in p, p[-200:])
login = admin.login_page({"ADMIN_PATH": "p"})
check("so does the admin login page", "class='brand'" in login and "v0.3.99" in login)
u = sync.user_page("<h1>x</h1>")
check("customer pages carry both",
      "class='brand'" in u and "master dns v0.3.99</footer>" in u, u[-300:])
check("around the card, not inside it",
      u.index("class='brand'") < u.index('class="card"') < u.index("<footer>"))
admin.VERSION_FILE = sync.VERSION_FILE = os.path.join(tmp, "missing")
check("with no version file the foot says only the name",
      "<footer>master dns</footer>" in admin.page("t", "", {"ADMIN_PATH": "p"})
      and "<footer>master dns</footer>" in sync.user_page("x"))
sync.VERSION_FILE = vf

print("an address typed by hand")
for typed, want in [("5.123.45.67", "5.123.45.67"),
                    ("  5.123.45.67 ", "5.123.45.67"),
                    ("۵.۱۲۳.۴۵.۶۷", "5.123.45.67"),       # a Persian keyboard
                    ("5٫123٫45٫67", "5.123.45.67")]:     # and its decimal mark
    got, why = sync.typed_ip(typed)
    check("%r is taken as %s" % (typed, want), got == want, "%r %r" % (got, why))
for typed, reason, word in [
        ("192.168.1.10", "the modem's inside address", "عمومی"),
        ("10.0.0.5", "a private range", "عمومی"),
        ("172.16.3.4", "a private range", "عمومی"),
        ("127.0.0.1", "loopback", "عمومی"),
        ("100.64.1.1", "carrier-grade NAT", "عمومی"),
        ("169.254.1.1", "link-local", "عمومی"),
        ("0.0.0.0", "unspecified", "عمومی"),
        ("255.255.255.255", "broadcast", "عمومی"),
        ("224.0.0.1", "multicast", "عمومی"),
        (RELAY, "this relay", "سرور"),
        (EXIT, "the exit", "سرور"),
        ("abc", "not an address", "درست"),
        ("1.2.3", "three parts", "درست"),
        ("1.2.3.4.5", "five parts", "درست"),
        ("256.1.1.1", "out of range", "درست"),
        ("05.1.2.3", "a leading zero", "درست"),
        ("5.123.45.67/24", "a range", "درست")]:
    got, why = sync.typed_ip(typed)
    check("%s refused (%s), and says why" % (typed, reason),
          got == "" and word in why, "%r %r" % (got, why))

print("the page offers it")
page = sync.register_ip_page("5.200.1.2")
check("the address it sees is still the first thing", page.index("5.200.1.2") < page.index("name='ip'"))
check("and a box to type another, posting to the same place",
      "name='ip'" in page and page.count("action='/register-ip'") == 2)

print("through the real server")
seen = []


EXTRA = {}


def fake_post(path, payload):
    seen.append((path, dict(payload)))
    if path == "/user-info":
        return dict({"ok": True, "name": "علی", "ip": "5.200.1.2", "used": 0,
                     "quota": 0, "status": "active", "wallet": 0, "plan": "",
                     "renews": "", "expires": "", "speed_kbps": 0, "warned": 0,
                     "seen_ip": payload.get("ip")}, **EXTRA)
    return {"ok": True, "message": "آی‌پی %s ثبت شد" % payload.get("ip")}


sync.post = fake_post
httpd = sync.make_panel_server(None, port=0)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def post(body, cookie="sdu=tok"):
    seen.clear()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/register-ip", body=body.encode("utf-8"),
              headers={"Content-Type": "application/x-www-form-urlencoded",
                       "Cookie": cookie})
    r = c.getresponse()
    r.read()
    c.close()
    return r.status, urllib.parse.unquote(r.getheader("Location") or "")


status, where = post("")
check("the button with nothing typed still registers the connection's own address",
      seen and seen[0][1].get("ip") == "127.0.0.1", str(seen))
status, where = post(urllib.parse.urlencode({"ip": "5.123.45.67"}))
check("a typed address is what gets registered",
      seen and seen[0] == ("/user-claim", {"session": "tok", "ip": "5.123.45.67"}),
      str(seen))
check("and the customer is told so", status == 303 and "5.123.45.67" in where, where)
status, where = post(urllib.parse.urlencode({"ip": "۵.۱۲۳.۴۵.۶۸"}))
check("typed in Persian digits too",
      seen and seen[0][1].get("ip") == "5.123.45.68", str(seen))
status, where = post(urllib.parse.urlencode({"ip": "192.168.1.10"}))
check("a private address never reaches the exit", not seen, str(seen))
check("the customer is sent back to the page, with the reason",
      where.startswith("/register-ip") and "e=1" in where and "عمومی" in where, where)
status, where = post(urllib.parse.urlencode({"ip": RELAY}))
check("nor does the relay's own address", not seen, str(seen))

c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
c.request("GET", "/register-ip", headers={"Cookie": "sdu=tok"})
body = c.getresponse().read().decode("utf-8")
check("the page itself is served with the box on it", "name='ip'" in body)

print("and on the account page")
c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
c.request("GET", "/", headers={"Cookie": "sdu=tok"})
body = c.getresponse().read().decode("utf-8")
check("the account page has the box too",
      "name='ip'" in body and "name='back' value='/'" in body, body[-600:])
status, where = post(urllib.parse.urlencode({"ip": "10.0.0.1", "back": "/"}))
check("a refusal there comes back to the account page, with the reason",
      where.startswith("/?") and "e=1" in where and "عمومی" in where, where)
status, where = post(urllib.parse.urlencode({"ip": "10.0.0.1",
                                             "back": "https://elsewhere.example/"}))
check("and to nowhere else, whatever the form says",
      where.startswith("/register-ip"), where)
status, where = post(urllib.parse.urlencode({"ip": "5.123.45.69", "back": "/"}))
check("a good address typed there is registered like any other",
      seen and seen[0][1].get("ip") == "5.123.45.69", str(seen))

print("the days left")
ends = sync.datetime.now(sync.timezone.utc) + sync.timedelta(days=12, hours=3)
EXTRA.update(expires=ends.isoformat()[:10], expires_at=ends.isoformat())
c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
c.request("GET", "/", headers={"Cookie": "sdu=tok"})
body = c.getresponse().read().decode("utf-8")
check("beside the end date on the account page", "زمان باقی‌مانده" in body
      and "12 روز" in body and ends.isoformat()[:10] in body)
EXTRA.pop("expires_at")
c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
c.request("GET", "/", headers={"Cookie": "sdu=tok"})
body = c.getresponse().read().decode("utf-8")
check("  and a panel that does not send the moment yet shows the date alone",
      "زمان باقی‌مانده" not in body and ends.isoformat()[:10] in body)
httpd.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
