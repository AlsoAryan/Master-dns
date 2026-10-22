#!/usr/bin/env python3
"""The name DoH and DoT answer on, set from the admin panel.

What has to hold: nginx takes the DoH certificate and names from two files it
includes, which the installer writes with the relay's own name and leaves
alone once smartdns-sync has moved DoH elsewhere; the relay gets one
certificate for the new name and its own - or the new name alone, when its
own no longer passes - in a thread, and only then points nginx at it,
checked by nginx -t and put back if refused; customers are given the new name
from then on, and not before; a failure is reported and not retried for a
while; going back restores the installer's certificate; the admin panel sets
and clears the name and shows each relay's progress; smartdns-cert can issue
and renew a certificate for several names.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

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


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (sync, panel, admin):
    m.log = lambda *a, **k: None
sync.print = lambda *a, **k: None
panel.print = lambda *a, **k: None

print("nginx and the installer")
for conf in ("templates/relay-nginx.conf", "templates/exit-nginx.conf"):
    text = read(conf)
    check("%s takes DoH's names and certificate from the two files" % conf.split("/")[1],
          "include /etc/nginx/smartdns-doh-names.map;" in text
          and text.count("include /etc/nginx/smartdns-doh-cert.conf;") == 3
          and "__DOH_CERT__" not in text)
    check("  the gate's DoH door lets through only those names, and shuts the rest",
          text.count("include /etc/nginx/smartdns-doh-names.map;") == 2
          and '""      127.0.0.1:9;' in text and "listen 5297;" in text
          and "listen 8853 ssl;" in text and "proxy_set_header X-Gate 1;" in text
          and 'proxy_set_header X-Gate "";' in text)
logic = read("tools/installer-logic.sh")
body = logic[logic.index("doh_includes() {"):logic.index("\n}\n", logic.index("doh_includes() {"))]
check("the installer writes them with the relay's own name and certificate",
      '"$DOH_CERT" "$DOH_KEY"' in body and "'%s 127.0.0.1:8453;\\n' \"$DOH_HOST\"" in body)
check("and leaves them to smartdns-sync once DoH has a name of its own",
      "[ -s /etc/smart-dns/doh-name ]" in body and "return 0" in body)
check("before nginx is written, on an install and on DoH coming on",
      'if [ -n "$DOH_ON" ]; then doh_includes; fi' in logic
      and logic.index("    doh_includes\n    if [ -z \"$was\" ]") < logic.index("install_payload RELAY_NGINX /etc/nginx/nginx.conf || true"))
cert = read("templates/smartdns-cert")
check("smartdns-cert issues one certificate for several names",
      "--names)" in cert and 'issue "$NAME" "$@"' in cert
      and 'for d in "${@:-$domain}"; do names+=(-d "$d"); done' in cert)
check("and renews it for the same names", 'issue "$domain" $(cert_names "$path/fullchain.pem")' in cert)

print("the relay")
tmp = tempfile.mkdtemp()
live = os.path.join(tmp, "live")
os.makedirs(os.path.join(live, "users.example.com"))
sync.DOH_FLAG = os.path.join(tmp, "doh")
open(sync.DOH_FLAG, "w").close()
sync.DOH_NAME_FILE = os.path.join(tmp, "doh-name")
sync.DOH_DEFAULT_CERT = os.path.join(tmp, "doh-cert.default")
sync.DOH_CERT_CONF = os.path.join(tmp, "smartdns-doh-cert.conf")
sync.DOH_NAMES_MAP = os.path.join(tmp, "smartdns-doh-names.map")
sync.LE_LIVE = live
sync.CFG = {"PANEL_DOMAIN": "users.example.com"}
INSTALLER = ("ssl_certificate     %s/users.example.com/fullchain.pem;\n"
             "ssl_certificate_key %s/users.example.com/privkey.pem;\n" % (live, live))
with open(sync.DOH_CERT_CONF, "w") as fh:
    fh.write(INSTALLER)
with open(sync.DOH_NAMES_MAP, "w") as fh:
    fh.write("users.example.com 127.0.0.1:8453;\n")


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


calls, nginx_ok, issued = [], [True], {}
passes = {"users.example.com", "dns.example.net", "dns2.example.net"}


def fake_sh(*args):
    calls.append(args)
    if args == ("nginx", "-t"):
        return R(0 if nginx_ok[0] else 1)
    return R()


def fake_run(cmd, **kw):
    calls.append(tuple(cmd))
    if cmd[0] == sync.CERT_TOOL:
        lineage, names = cmd[2], cmd[3:]
        if not all(n in passes for n in names):
            return R(1, err="Some challenges have failed.")
        os.makedirs(os.path.join(live, lineage), exist_ok=True)
        issued[lineage] = list(names)
        return R()
    if cmd[0] == "openssl":
        lineage = os.path.basename(os.path.dirname(cmd[-1]))
        return R(out="X509v3 Subject Alternative Name:\n    " +
                 ", ".join("DNS:" + n for n in issued.get(lineage, [])))
    return R()


class Now:
    def __init__(self, target, args=(), daemon=True):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


sync.sh = fake_sh
sync.subprocess.run = fake_run
sync.threading.Thread = Now

check("before the admin panel names one, customers get the relay's own",
      sync.doh_public_name() == "users.example.com")
sync.apply_doh_name("DNS.example.net")
names = open(sync.DOH_NAMES_MAP).read()
check("a certificate for the new name with the relay's own beside it",
      issued.get("doh-dns.example.net") == ["dns.example.net", "users.example.com"], repr(issued))
check("nginx answers both names from it, checked before it is reloaded",
      "dns.example.net 127.0.0.1:8453;" in names and "users.example.com 127.0.0.1:8453;" in names
      and "doh-dns.example.net/fullchain.pem" in open(sync.DOH_CERT_CONF).read()
      and calls.index(("nginx", "-t")) < calls.index(("systemctl", "reload", "nginx")))
check("and customers are given the new name from then on",
      sync.doh_public_name() == "dns.example.net"
      and sync.doh_name_report() == {"want": "dns.example.net", "active": "dns.example.net",
                                     "error": "", "working": False})
check("the installer's certificate is kept for the way back",
      open(sync.DOH_DEFAULT_CERT).read() == INSTALLER)
calls.clear()
sync.apply_doh_name("dns.example.net")
check("asked again, nothing is done", not calls)

print("when the relay's own name no longer passes")
passes.discard("users.example.com")
sync.apply_doh_name("dns2.example.net")
check("the new name alone, and the one before it let go",
      issued.get("doh-dns2.example.net") == ["dns2.example.net"]
      and open(sync.DOH_NAMES_MAP).read() == "dns2.example.net 127.0.0.1:8453;\n"
      and ("certbot", "delete", "--non-interactive", "--cert-name", "doh-dns.example.net") in calls)

print("when it cannot be had")
sync.apply_doh_name("nope.example.org")
st = sync.doh_name_report()
check("the old name stays, and the panel is told why",
      sync.doh_public_name() == "dns2.example.net" and st["want"] == "nope.example.org"
      and "challenges have failed" in st["error"], repr(st))
calls.clear()
sync.apply_doh_name("nope.example.org")
check("and it is not tried again for a while", not calls)
sync.DOH_NAME["next"] = time.time() - 1
passes.add("nope.example.org")
nginx_ok[0] = False
before = open(sync.DOH_CERT_CONF).read()
sync.apply_doh_name("nope.example.org")
check("a certificate nginx refuses is put back, and said",
      open(sync.DOH_CERT_CONF).read() == before and "nginx refused" in sync.DOH_NAME["error"]
      and sync.doh_public_name() == "dns2.example.net")
nginx_ok[0] = True

print("going back")
sync.apply_doh_name("")
check("the installer's certificate and the relay's own name again",
      open(sync.DOH_CERT_CONF).read() == INSTALLER
      and open(sync.DOH_NAMES_MAP).read() == "users.example.com 127.0.0.1:8453;\n"
      and not os.path.exists(sync.DOH_NAME_FILE) and sync.doh_public_name() == "users.example.com")
os.unlink(sync.DOH_FLAG)
calls.clear()
sync.apply_doh_name("dns.example.net")
check("a relay without DoH does nothing about it", not calls and sync.doh_public_name() == "")
src = read("templates/smartdns-sync")
check("the report rides on every sync, the name comes back with the answer",
      '"doh_host": doh_public_name(),' in src and '"doh_name": doh_name_report(),' in src
      and 'apply_doh_name(answer.get("doh_name"))' in src)
check("and the relay's own customer page shows the name customers are given",
      src.count("host = doh_public_name()") == 2)

print("the panel")
psrc = read("templates/smartdns-panel")
check("sends the name with every sync, and keeps each relay's progress",
      '"doh_name": self.store.setting("doh_name") or "",' in psrc
      and 'self.store.set_setting("doh_name_state:" + who, text)' in psrc)

print("the admin panel")
db = os.path.join(tmp, "panel.db")
panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
check("with no relay on DoH, it says so", "هیچ رله‌ای هنوز" in admin.doh_name_card("p"))
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('doh_host', 'users.example.com')")


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(**form):
    r = Rec()
    admin.Admin.action(r, "doh-name", {k: [v] for k, v in form.items()})
    return r.to


check("a name is saved", "ذخیره شد" in act(name="https://DNS.example.net/")
      and admin.STORE.one("SELECT value FROM settings WHERE key = 'doh_name'")["value"]
      == "dns.example.net")
check("an address is not a name", act(name="1.2.3.4").startswith("settings?m=!"))
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('doh_name_state:198.51.100.7', ?)",
                (json.dumps({"want": "dns.example.net", "active": "", "error": "",
                             "working": True}),))
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('doh_name_state:198.51.100.8', ?)",
                (json.dumps({"want": "dns.example.net", "active": "", "working": False,
                             "error": "no certificate for dns.example.net: challenge failed"}),))
card = admin.doh_name_card("p")
check("the card shows the name customers get, and each relay's progress",
      "users.example.com" in card and "در حال گرفتن گواهی" in card
      and "challenge failed" in card and "value='dns.example.net'" in card
      and "برگشت به نام خود رله" in card, card[:500])
act(name="")
check("and clearing it goes back",
      not admin.STORE.one("SELECT 1 FROM settings WHERE key = 'doh_name'"))
check("it is on the settings page", "out.append(doh_name_card(p))" in read("templates/smartdns-admin"))

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
