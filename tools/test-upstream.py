#!/usr/bin/env python3
"""Picking the public resolvers: the admin panel's choice, and the relay's.

What has to hold: the admin panel refuses what is not a public address and
what does not answer from the exit, and points the exit's own nginx at the
pick - checked with nginx -t and put back if nginx refuses it. The panel hands
the pick to the relays; a relay takes it only if the first one answers from
there, drops a second that does not, and does not try a failed pick again
every half minute. Every place that asked 1.1.1.1 by name - the bypasses, a
template's resolver, `smartdns bypass`, the exit's nginx - now asks whatever
was picked, and an upgrade does not put the defaults back. epic-pin is the
exception, as it was before: it asks Google on purpose.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
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
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
sync = load("templates/smartdns-sync", "sync")
for m in (panel, admin, sync):
    m.log = lambda *a: None

# ---------------------------------------------------------------- the probe
print("the probe")


def fake_resolver(rcode=0, answers=1):
    """A UDP resolver on loopback that answers every query the given way."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))

    def run():
        while True:
            try:
                data, addr = s.recvfrom(512)
            except OSError:
                return
            reply = data[:2] + bytes([0x81, 0x80 | rcode, 0, 1, 0, answers, 0, 0, 0, 0]) + data[12:]
            s.sendto(reply, addr)
    threading.Thread(target=run, daemon=True).start()
    return s


good, refusing = fake_resolver(), fake_resolver(rcode=5, answers=0)
real_sendto = socket.socket.sendto


def probe_on(sock, mod):
    """Run mod.dns_probe against a loopback resolver instead of port 53."""
    port = sock.getsockname()[1]
    orig = socket.socket.sendto

    class S(socket.socket):
        def sendto(self, data, addr):
            return orig(self, data, ("127.0.0.1", port))
    saved = mod.socket.socket
    mod.socket.socket = S
    try:
        return mod.dns_probe("192.0.2.1", timeout=0.5)
    finally:
        mod.socket.socket = saved


check("an answer with a record is a time in milliseconds",
      isinstance(probe_on(good, sync), int))
check("a refusal is no answer", probe_on(refusing, sync) is None)
silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
silent.bind(("127.0.0.1", 0))
check("silence is no answer", probe_on(silent, sync) is None)
check("the admin panel's probe is the relay's",
      read("templates/smartdns-admin").count("def dns_probe(") == 1
      and admin.dns_probe.__code__.co_code == sync.dns_probe.__code__.co_code)

# ---------------------------------------------------------------- the relay
print("the relay")
tmp = tempfile.mkdtemp()
sync.UPSTREAM_CONF = os.path.join(tmp, "upstream.conf")
with open(sync.UPSTREAM_CONF, "w") as fh:
    fh.write("# the public resolvers\nno-resolv\nserver=1.1.1.1\nserver=9.9.9.9\n")
ran = []
answers = {}


def fake_sh(*args):
    ran.append(args)
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


sync.sh = fake_sh
sync.dns_probe = lambda ip: answers.get(ip)

check("it reads what it asks now", sync.current_upstream() == ["1.1.1.1", "9.9.9.9"])
check("the pick it already has changes nothing, and is not probed",
      sync.apply_upstream(["1.1.1.1", "9.9.9.9"]) is False and ran == [])
check("nonsense and private addresses are not a pick",
      sync.wanted_upstream(["x", "10.0.0.1", "127.0.0.1", "8.8.8.8", "8.8.8.8", "1.1.1.1", "9.9.9.9"])
      == ["8.8.8.8", "1.1.1.1"] and sync.apply_upstream("1.1.1.1") is False)

answers = {"9.9.9.9": 30}
check("a first choice that does not answer from here is not taken",
      sync.apply_upstream(["1.1.1.3", "9.9.9.9"]) is False
      and sync.current_upstream() == ["1.1.1.1", "9.9.9.9"]
      and "1.1.1.3" in sync.UPSTREAM["error"])
answers = {"1.1.1.3": 20, "9.9.9.9": 30}
check("and is not tried again every half minute",
      sync.apply_upstream(["1.1.1.3", "9.9.9.9"]) is False)
sync.UPSTREAM["at"] -= sync.UPSTREAM_RETRY + 1
ran.clear()
check("but is, a while later - and taken once it answers",
      sync.apply_upstream(["1.1.1.3", "9.9.9.9"]) is True
      and sync.current_upstream() == ["1.1.1.3", "9.9.9.9"]
      and ("dnsmasq", "--test") in ran and ("systemctl", "restart", "dnsmasq") in ran
      and sync.UPSTREAM["error"] == "")
text = open(sync.UPSTREAM_CONF).read()
check("the file is no-resolv and the servers, nothing domain-specific",
      "no-resolv" in text and "server=/" not in text)
answers = {"208.67.222.222": 40}
check("a second that does not answer is left out, and said so",
      sync.apply_upstream(["208.67.222.222", "94.140.14.14"]) is True
      and sync.current_upstream() == ["208.67.222.222"]
      and "94.140.14.14" in sync.UPSTREAM["error"])
rep = sync.upstream_report()
check("the report says what was wanted, what is in use and how each did",
      rep["want"] == ["208.67.222.222", "94.140.14.14"] and rep["applied"] == ["208.67.222.222"]
      and rep["tested"] == {"208.67.222.222": 40, "94.140.14.14": None})


def refuse(*args):
    ran.append(args)
    return type("R", (), {"returncode": 1 if args[:2] == ("dnsmasq", "--test") else 0})()


sync.sh = refuse
answers = {"1.1.1.1": 5, "1.0.0.1": 5}
before = open(sync.UPSTREAM_CONF).read()
check("if dnsmasq refused the file, the old one is put back",
      sync.apply_upstream(["1.1.1.1", "1.0.0.1"]) is False
      and open(sync.UPSTREAM_CONF).read() == before)
sync.sh = fake_sh

src = read("templates/smartdns-sync")
check("a new pick restarts every template's resolver too",
      "restart=changed or new_upstream" in src)
check("a template's bypasses ask the usual resolvers, not 1.1.1.1 by name",
      '"server=/%s/#" % d' in src and "server=/%s/1.1.1.1" not in src)
check("the upstream file reaches the templates through the base directory",
      "upstream.conf" not in re.search(r"def sync_base_dir.*?return changed", src, re.S).group(0))

# ---------------------------------------------------------------- everywhere else
print("everywhere else")
bypass = read("common/bypass.conf")
check("the shipped bypasses ask the usual resolvers",
      "server=/gosredirector.ea.com/#" in bypass
      and not re.search(r"^server=/[^/]+/\d", bypass, re.M))
cmd = read("templates/smartdns")
check("so does `smartdns bypass`, and it knows a bypass of either form",
      "server=/%s/#" in cmd and 'grep -q "^server=/$d/"' in cmd and "1.1.1.1" not in cmd)
# epic-pin is left alone: it asks Google on purpose (test-no-google-dns.py),
# for the subnet, and it answers no customer.
check("epic-pin keeps its own resolvers", "upstream.conf" not in read("templates/epic-pin"))
logic = read("tools/installer-logic.sh")
check("the installer keeps the upstream out of the file it rewrites",
      "server=1.1.1.1\\nserver=9.9.9.9\\n'\n        printf 'cache-size" not in logic
      and "printf 'no-resolv\\nserver=1.1.1.1" not in logic.split('step "dnsmasq: the public resolvers"')[0])
check("and writes upstream.conf only when it is missing",
      'if [ -f /etc/dnsmasq.d/upstream.conf ]; then' in logic)
nginx = read("templates/exit-nginx.conf")
check("the exit's nginx has no resolver by name",
      "resolver 1.1.1.1" not in nginx and nginx.count("resolver __RESOLVERS__ ") == 5)
check("the installer fills it from the pick, or the defaults",
      "s#__RESOLVERS__#${RESOLVERS:-1.1.1.1 9.9.9.9}#g" in logic
      and "/etc/smart-dns/upstream" in logic)

# ---------------------------------------------------------------- panel
print("the panels")
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
check("the default is the two that do not send the asker's subnet",
      panel.DEFAULT_UPSTREAM == "1.1.1.1 9.9.9.9" == admin.DEFAULT_UPSTREAM)
psrc = read("templates/smartdns-panel")
check("the sync reply carries the pick",
      '"upstream": (self.store.setting("dns_upstream")' in psrc)

admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.NGINX_CONF = os.path.join(tmp, "nginx.conf")
admin.UPSTREAM_FILE = os.path.join(tmp, "etc", "upstream")
rendered = nginx.replace("__RESOLVERS__", "1.1.1.1 9.9.9.9").replace("__RELAY_IP__", "198.51.100.7")
with open(admin.NGINX_CONF, "w") as fh:
    fh.write(rendered)
calls = []


def run(args, **kw):
    calls.append(tuple(args))
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


admin.subprocess.run = run
check("the exit's nginx gets the pick in every place, flags kept",
      admin.set_exit_resolvers(["9.9.9.9", "149.112.112.112"]) == ""
      and open(admin.NGINX_CONF).read().count("resolver 9.9.9.9 149.112.112.112 ipv6=off;") == 4
      and "resolver 9.9.9.9 149.112.112.112 ipv4=off;" in open(admin.NGINX_CONF).read()
      and "resolver_timeout 5s;" in open(admin.NGINX_CONF).read()
      and ("nginx", "-t") in calls and ("systemctl", "reload", "nginx") in calls)
check("and the pick is kept for the next upgrade",
      open(admin.UPSTREAM_FILE).read() == "9.9.9.9 149.112.112.112\n")
before = open(admin.NGINX_CONF).read()


def run_refuse(args, **kw):
    return type("R", (), {"returncode": 1 if tuple(args) == ("nginx", "-t") else 0,
                          "stdout": "", "stderr": "bad"})()


admin.subprocess.run = run_refuse
check("nginx refusing it puts the old file back",
      admin.set_exit_resolvers(["1.1.1.1"]) != "" and open(admin.NGINX_CONF).read() == before)
admin.subprocess.run = run


class Rec:
    def __init__(self):
        self.to = None

    def redirect(self, where, headers=None):
        self.to = where
        return where


# The form reader, as the real handler has it.
Rec.one = admin.Admin.__dict__["one"]


def act(**form):
    r = Rec()
    r.body = form
    admin.Admin.action(r, "dns-upstream", {k: [v] for k, v in form.items()})
    return r.to or ""


if hasattr(admin.Admin, "action"):
    admin.dns_probe = lambda ip: {"1.1.1.3": 9, "1.0.0.3": 11, "8.8.8.8": 7}.get(ip)
    try:
        got = act(primary="1.1.1.1", primary_custom="10.1.2.3", backup="")
        check("a private address is refused", "عمومی نیست" in got, got)
        got = act(primary="9.9.9.9", backup="1.0.0.3")
        check("one that does not answer from the exit is refused, nothing changed",
              "!" in got and "9.9.9.9" in got
              and not admin.STORE.one("SELECT 1 FROM settings WHERE key = 'dns_upstream'"), got)
        got = act(primary="1.1.1.3", backup="1.0.0.3")
        row = admin.STORE.one("SELECT value FROM settings WHERE key = 'dns_upstream'")
        check("a pick that answers is saved and named",
              row and row["value"] == "1.1.1.3 1.0.0.3" and "Cloudflare" in got, got)
        got = act(primary="1.1.1.3", backup="", backup_custom="8.8.8.8")
        row = admin.STORE.one("SELECT value FROM settings WHERE key = 'dns_upstream'")
        check("a typed address wins over the list", row["value"] == "1.1.1.3 8.8.8.8", got)
    except Exception as e:
        check("the action runs", False, repr(e))

store.set_setting("upstream_state:198.51.100.7", json.dumps(
    {"want": ["1.1.1.3", "8.8.8.8"], "applied": ["1.1.1.3", "8.8.8.8"],
     "tested": {"1.1.1.3": 12, "8.8.8.8": 30}, "error": ""}))
store.set_setting("upstream_state:203.0.113.9", json.dumps(
    {"want": ["1.1.1.3", "8.8.8.8"], "applied": ["1.1.1.1", "9.9.9.9"],
     "tested": {"1.1.1.3": None}, "error": "1.1.1.3 از این رله جواب نداد؛ همان قبلی ماند"}))
card = admin.upstream_card()
check("the settings page shows the pick selected, every provider, and the warnings",
      "value='1.1.1.3' selected" in card and "value='8.8.8.8' selected" in card
      and "AdGuard" in card and "ECS" in card and "149.112.112.112" in card)
check("and each relay: what it uses, and why not the pick",
      "اعمال شد" in card and "جواب نداد" in card and "12ms" in card)
check("Google, OpenDNS and AdGuard are each said to break PUBG Mobile",
      all("PUBG" in w for n, a, b, w in admin.RESOLVERS if n in ("Google", "OpenDNS", "AdGuard"))
      and admin.ECS_RESOLVERS == {ip for n, a, b, w in admin.RESOLVERS
                                  if n in ("Google", "OpenDNS", "AdGuard") for ip in (a, b)})
check("  and a pick of one is a red warning at the top of the card",
      "<div class='msg err'>⚠️ 8.8.8.8 " in card)
admin.STORE.run("UPDATE settings SET value = '1.1.1.1 9.9.9.9' WHERE key = 'dns_upstream'")
check("  none for Cloudflare and Quad9", "msg err" not in admin.upstream_card())

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
