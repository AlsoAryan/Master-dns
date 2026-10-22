#!/usr/bin/env python3
"""Several relays on one exit.

What has to hold: the exit's nginx lets in the relays from one file it
includes on every port it proxies - not the one relay an install was run
with, which cut the first relay off the day a second was installed; the
installer writes that file from the panel's list and the relay it was
given, each once; the admin panel adds and takes off relays - nginx checked
before the reload and put back if refused, then the panel's own list, which
the sync API and its firewall rule read - and hands out the token a new relay
is installed with, without the tunnel the exit keeps for its first relay.
"""
import hashlib
import importlib.machinery
import importlib.util
import os
import re
import shutil
import ssl
import subprocess
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
          ((" - " + str(detail)[:500]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


tmp = tempfile.mkdtemp()
print("the exit's nginx")
conf = read("templates/exit-nginx.conf")
check("every port it proxies lets in the relays from the one file",
      conf.count("include /etc/nginx/smartdns-relays.conf;") == 4 and "__RELAY_IP__" not in conf)
logic = read("tools/installer-logic.sh")
check("a single machine, whose gate is its firewall, has none of it",
      '"${SINGLE:+/smartdns-relays\\\\.conf;\\$/d}"' in logic)

print("the installer")
bash = shutil.which("bash")
body = logic[logic.index("relay_allows() {"):logic.index("\n}\n", logic.index("relay_allows() {")) + 3]
valid = logic[logic.index("valid_ip() {"):logic.index("\n}\n", logic.index("valid_ip() {")) + 3]
check("it writes the file before the exit's nginx, on an exit only",
      logic.index('if [ "$ROLE" = exit ] || [ "$ROLE" = node ]; then relay_allows; fi')
      < logic.index("install_payload EXIT_NGINX /etc/nginx/nginx.conf && NGINX_CHANGED=1"))
if bash:
    env_file = os.path.join(tmp, "panel.env")
    out_file = os.path.join(tmp, "relays.conf")
    with open(env_file, "w", newline="\n") as fh:
        fh.write("SYNC_SECRET=x\nRELAY_IP=198.51.100.1,198.51.100.2,bad\n")
    script = (valid + body + "note_file() { :; }\nRELAY_IP=198.51.100.3\nrelay_allows\n")
    script = script.replace("/etc/smart-dns/panel.env", posix(env_file)).replace(
        "RELAYS_CONF", "OUT").replace('"$OUT"', '"%s"' % posix(out_file))
    r = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    got = open(out_file).read() if os.path.exists(out_file) else r.stderr
    check("every relay on the panel's list, and the one this run was given, each once",
          re.findall(r"allow (\S+);", got) == ["198.51.100.1", "198.51.100.2", "198.51.100.3"], got)
    with open(env_file, "w", newline="\n") as fh:
        fh.write("RELAY_IP=198.51.100.3\n")
    subprocess.run([bash, "-c", script], capture_output=True, text=True)
    check("  and a relay already on the list is not written twice",
          re.findall(r"allow (\S+);", open(out_file).read()) == ["198.51.100.3"])

print("the admin panel")
spec = importlib.util.spec_from_loader("admin", importlib.machinery.SourceFileLoader(
    "admin", os.path.join(ROOT, "templates", "smartdns-admin")))
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)
admin.log = lambda *a, **k: None
admin.PANEL_ENV = os.path.join(tmp, "panel2.env")
admin.RELAYS_CONF = os.path.join(tmp, "smartdns-relays.conf")
admin.INSTALL_STATE = os.path.join(tmp, "install-state")
admin.SYNC_CRT = os.path.join(tmp, "sync.crt")
with open(admin.PANEL_ENV, "w", newline="\n") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=198.51.100.1\nTUNNEL=backpack\n")
with open(admin.RELAYS_CONF, "w") as fh:
    fh.write("allow 198.51.100.1;\n")
with open(admin.INSTALL_STATE, "w") as fh:
    fh.write("role exit\nrelay-ip 198.51.100.1\nexit-ip 203.0.113.9\n")
check("it knows this exit's own address and its relays",
      admin.exit_address() == "203.0.113.9" and admin.relay_list() == ["198.51.100.1"])

ran, nginx_ok = [], [True]


class R:
    def __init__(self, rc):
        self.returncode, self.stdout, self.stderr = rc, "", "" if rc == 0 else "emerg: nope"


def fake_run(cmd, **kw):
    ran.append(tuple(cmd))
    if cmd[:2] == ["nginx", "-t"]:
        return R(0 if nginx_ok[0] else 1)
    return R(0)


real_run = subprocess.run
admin.subprocess.run = fake_run
admin.systemctl = lambda *a: ran.append(("systemctl",) + a)
check("a relay is added: nginx checked, reloaded, the panel's list rewritten, the panel restarted",
      admin.set_relays(["198.51.100.1", "198.51.100.2"]) == ""
      and open(admin.RELAYS_CONF).read().count("allow ") == 4
      and "RELAY_IP=198.51.100.1,198.51.100.2" in open(admin.PANEL_ENV).read()
      and "SYNC_SECRET=s3cret" in open(admin.PANEL_ENV).read()
      and "TUNNEL=backpack" in open(admin.PANEL_ENV).read()
      and ran.index(("nginx", "-t")) < ran.index(("systemctl", "reload", "nginx"))
      < ran.index(("systemctl", "restart", "smartdns-panel")), repr(ran))
ran.clear()
nginx_ok[0] = False
check("one nginx refuses is put back, and the panel's list left as it was",
      admin.set_relays(["198.51.100.1", "198.51.100.2", "198.51.100.9"]).startswith("nginx")
      and "198.51.100.9" not in open(admin.RELAYS_CONF).read()
      and "198.51.100.9" not in open(admin.PANEL_ENV).read()
      and ("systemctl", "restart", "smartdns-panel") not in ran)
nginx_ok[0] = True
subprocess.run = real_run

made = False
try:
    key, crt = os.path.join(tmp, "k.pem"), admin.SYNC_CRT
    made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                           "-subj", "/CN=sync", "-keyout", key, "-out", crt],
                          capture_output=True).returncode == 0
except OSError:
    pass
if made:
    der = ssl.PEM_cert_to_DER_cert(open(crt).read())
    check("the token a new relay is installed with: the secret and this exit's certificate, no tunnel",
          admin.pairing_token() == "s3cret." + hashlib.sha256(der).hexdigest())
else:
    print("  (no openssl here - the token is left to the live test)")


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, ip):
    r = Rec()
    admin.Admin.action(r, what, {"ip": [ip]})
    return r.to


for bad in ("", "1.2.3", "10.0.0.5", "203.0.113.9", "198.51.100.1"):
    if not act("relay-add", bad).startswith("nodes?m=!"):
        check("refuses %r" % bad, False)
check("not an address, a private one, this exit itself, or one already there",
      admin.relay_list() == ["198.51.100.1", "198.51.100.2"])
admin.set_relays = lambda ips: (ran.append(("set", tuple(ips))), "")[1]
check("a public one is added", "اضافه شد" in act("relay-add", "93.184.216.34")
      and ran[-1] == ("set", ("198.51.100.1", "198.51.100.2", "93.184.216.34")))
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1\n")
check("the last relay cannot be taken off", act("relay-del", "198.51.100.1").startswith("nodes?m=!"))

print("the settings page")
admin.DB = os.path.join(tmp, "panel.db")
spec2 = importlib.util.spec_from_loader("panel", importlib.machinery.SourceFileLoader(
    "panel", os.path.join(ROOT, "templates", "smartdns-panel")))
panel = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(panel)
panel.print = lambda *a, **k: None
panel.Store(admin.DB)
admin.STORE = admin.Store(admin.DB)
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=198.51.100.1,198.51.100.2\n")
admin.STORE.run("INSERT INTO metrics (host, at) VALUES ('198.51.100.1', ?)", (admin.now(),))
card = admin.relays_card("p")
check("lists each relay with when it last reported", "198.51.100.1" in card and "وصل" in card
      and "هنوز گزارشی نداده" in card)
check("with a way to add one, take one off, and install one",
      "action='/p/relay-add'" in card and "action='/p/relay-del'" in card
      and ("s3cret." in card or not made) and "203.0.113.9" in card)
asrc = read("templates/smartdns-admin")
check("on a page of its own, 'نود', not in the settings",
      '"nodes": ("نود", nodes_page),' in asrc and '("nodes", "نود")' in asrc
      and "out.append(relays_card(p))" not in asrc)
check("  which a single machine, with no relays of its own, does not list",
      'if path in ("nodes", "wireguard") and one_server():' in asrc)
admin.CFG = {"ADMIN_PATH": "p"}
admin.one_server = lambda: False
check("  the page is the relays card", "رله‌ها (" in admin.nodes_page())
admin.one_server = lambda: True
check("  and on a single machine says there are none", "تک‌سرور" in admin.nodes_page())

print("which relays a customer is shown")
store = panel.Store(admin.DB)
user = store.create_user(111, "ali", "علی")
row = lambda: store.one("SELECT * FROM users WHERE id = ?", (user["id"],))
relays = ("198.51.100.1", "198.51.100.2", "198.51.100.3")
check("with nothing chosen, all of them", panel.shown_relays(store, row(), relays) == list(relays))
store.run("UPDATE users SET relays = '198.51.100.3,198.51.100.2' WHERE id = ?", (user["id"],))
check("or the ones picked for this customer, in the panel's order",
      panel.shown_relays(store, row(), relays) == ["198.51.100.2", "198.51.100.3"])
store.run("UPDATE users SET relays = '203.0.113.99' WHERE id = ?", (user["id"],))
check("and never none - a relay since taken off is left out, and then all are shown",
      panel.shown_relays(store, row(), relays) == list(relays))
psrc = read("templates/smartdns-panel")
check("the bot's record carries them",
      '"dns": shown_relays(store, user, relays),' in psrc)
check("  and so does what the customer's page is built from",
      '"dns": shown_relays(self.store, user, self.relays),' in
      psrc[psrc.index("def do_user_info"):psrc.index("def ", psrc.index("def do_user_info") + 10)])

spec3 = importlib.util.spec_from_loader("sync", importlib.machinery.SourceFileLoader(
    "sync", os.path.join(ROOT, "templates", "smartdns-sync")))
sync = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(sync)
sync.CFG = {"SELF_IP": "198.51.100.9"}
one_box = sync.dns_box({"dns": ["198.51.100.1"]})
two_box = sync.dns_box({"dns": ["198.51.100.1", "198.51.100.2"]})
check("the customer's page: one address, to be typed twice",
      "198.51.100.1" in one_box and "همین آدرس" in one_box)
check("  two: DNS 1 and DNS 2, both ours",
      "DNS اول" in two_box and "DNS دوم" in two_box and "198.51.100.2" in two_box
      and "همین آدرس" not in two_box)
check("  and from an older panel that sends none, this relay's own",
      "198.51.100.9" in sync.dns_box({}) and "198.51.100.9" in sync.dns_box(None))

with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1,198.51.100.2\n")
urow = lambda: admin.STORE.one("SELECT * FROM users WHERE id = ?", (user["id"],))
admin.Admin.action(Rec(), "user-relays", {"id": [str(user["id"])], "ip": ["198.51.100.1", "203.0.113.5"]})
check("the admin panel saves one customer's relays, only real ones",
      urow()["relays"] == "198.51.100.1")
cell = admin.relays_cell("p", urow(), ["198.51.100.1", "198.51.100.2"])
check("  shown in the users table by their addresses, with a box that ticks them all",
      "198.51.100.1</span></summary>" in cell and "action='/p/user-relays'" in cell
      and "> همه</label>" in cell
      and "مثل همه" not in cell and "خاص" not in cell)
admin.Admin.action(Rec(), "user-relays", {"id": [str(user["id"])],
                                          "ip": ["198.51.100.1", "198.51.100.2"]})
check("  all of them is kept as none picked, so a relay added later is given too",
      urow()["relays"] is None and "همه (2)</summary>" in
      admin.relays_cell("p", urow(), ["198.51.100.1", "198.51.100.2"]))
admin.Admin.action(Rec(), "user-relays", {"id": [str(user["id"])]})
check("  and none ticked is all of them, never none", urow()["relays"] is None)
card = admin.relays_card("p")
check("the settings card no longer picks relays for everybody",
      "form='shown'" not in card and "relays-shown" not in read("templates/smartdns-admin"))

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
