#!/usr/bin/env python3
"""One machine doing both ends: ROLE=single.

What has to hold: the exit's nginx is rendered the way install_payload renders
it - for an exit exactly as before, and for a single machine without the
"only the relay" lines, with the relay's usage log, and with DoH on its own
name once there is a certificate; nothing of it leaks into an exit. The
installer treats a single machine as a relay and an exit at once, pairs it
with itself and runs no tunnel. Its sync API moves to loopback on another
port, because 8443 is its customer panel - the panel listens there, the sync
agent goes there, and the guard closes that port and leaves 8443 open.
"""
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
LOGIC = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:400]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


def function(name):
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), LOGIC, re.S | re.M)
    return m.group(0) if m else ""


BASH = shutil.which("bash")
tmp = tempfile.mkdtemp()

# ---------------------------------------------------------------- nginx, as installed
print("the exit's nginx, rendered by install_payload")
if BASH:
    harness = os.path.join(tmp, "h.sh")
    with open(harness, "w", newline="\n", encoding="utf-8") as fh:
        fh.write('set -eu\ninfo() { :; }\nnote_file() { :; }\nbackup_file() { :; }\n'
                 'die() { echo "DIE $*" >&2; exit 1; }\n')
        fh.write('payload() { cat "%s"; }\n' % posix(os.path.join(ROOT, "templates", "exit-nginx.conf")))
        fh.write(function("install_payload"))
        fh.write('install_payload EXIT_NGINX "$1" || true\n')

    def render(**env):
        out = os.path.join(tmp, "nginx-%d.conf" % len(os.listdir(tmp)))
        base = {"RELAY_IP": "198.51.100.1", "EXIT_IP": "203.0.113.2", "MOD": "",
                "NO_GOOGLE_V6": "1", "NO_TUNNEL": "1", "NO_DOH": "1", "DOH_ON": "",
                "SINGLE": "", "HTTPS_TARGET": "$upstream", "RESOLVERS": "1.1.1.1 9.9.9.9",
                "EXIT_HTTPS": "", "EXIT_SPOTIFY": "", "EXIT_HTTP": "", "EXIT_BLIZZARD": "",
                "DOH_HOST": "", "DOH_CERT": "", "DOH_KEY": ""}
        base.update(env)
        r = subprocess.run([BASH, posix(harness), posix(out)], capture_output=True,
                           env=dict(os.environ, **base), timeout=60)
        return open(out, encoding="utf-8").read() if os.path.exists(out) else r.stderr.decode()

    exit_conf = render()
    check("an exit's nginx has none of the single machine's parts",
          "single begin" not in exit_conf and "/run/smartdns-usage" not in exit_conf
          and "8453" not in exit_conf and "listen 853" not in exit_conf)
    check("and lets only its relays and the tunnel in, from the file the admin panel keeps",
          exit_conf.count("include /etc/nginx/smartdns-relays.conf;") == 4 and exit_conf.count("deny all;") == 4)
    check("and passes 443 on to $upstream", "proxy_pass $upstream;" in exit_conf)
    solo = render(SINGLE="1", RELAY_IP="203.0.113.2")
    check("a single machine's nginx lets in anybody the gate lets through",
          not re.search(r"^\s*(allow|deny) ", solo, re.M) and "smartdns-relays" not in solo)
    check("keeps the usage log a relay keeps", "access_log /run/smartdns-usage usage" in solo)
    check("and without a certificate, no DoH", "8453" not in solo and "listen 853" not in solo
          and "proxy_pass $upstream;" in solo)
    doh = render(SINGLE="1", RELAY_IP="203.0.113.2", NO_DOH="", DOH_ON="1",
                 DOH_HOST="solo.example.com", HTTPS_TARGET="$https_target",
                 DOH_CERT="/etc/letsencrypt/live/solo.example.com/fullchain.pem",
                 DOH_KEY="/etc/letsencrypt/live/solo.example.com/privkey.pem")
    check("with one, its own name on 443 goes to the DoH server and the rest on",
          "include /etc/nginx/smartdns-doh-names.map;" in doh and "default       $upstream;" in doh
          and "proxy_pass $https_target;" in doh)
    check("DoT on 853 and the DoH server on loopback, with the certificate",
          "listen 853 ssl;" in doh and "listen 127.0.0.1:8453 ssl http2;" in doh
          and doh.count("include /etc/nginx/smartdns-doh-cert.conf;") == 3)
    check("  and the gate's doors for DoT and DoH, as on a relay",
          "listen 8853 ssl;" in doh and "listen 5297;" in doh
          and "listen 127.0.0.1:8454 ssl http2 proxy_protocol;" in doh)
    for name, text in (("exit", exit_conf), ("single", solo), ("single with DoH", doh)):
        check("%s: no placeholder left, braces balanced" % name,
              "__" not in text and text.count("{") == text.count("}"), text[:300])
else:
    print("  (bash not available - rendering skipped)")

# ---------------------------------------------------------------- the installer
print("the installer")
check("a single machine is a relay and an exit at once",
      'is_relay() { [ "$ROLE" = relay ] || [ "$ROLE" = single ]; }' in LOGIC
      and 'is_exit()  { [ "$ROLE" = exit ] || [ "$ROLE" = single ] || [ "$ROLE" = node ]; }' in LOGIC)
check("offered as the third choice", "3) single - both on one server abroad" in LOGIC
      and "3|single) ROLE=single; break ;;" in LOGIC)
check("with the trade said plainly", "customers reach it directly from Iran" in LOGIC)
check("one address, both ends", 'RELAY_IP="$SELF_IP"; EXIT_IP="$SELF_IP"; PEER_IP="$SELF_IP"' in LOGIC
      and '[ -z "$PEER_IP" ] && [ "$ROLE" != single ]' in LOGIC)
check("no tunnel", '{ [ "$ROLE" = single ] || [ "$ROLE" = node ]; } && TUNNEL=off' in LOGIC)
check("both halves run: the relay's DNS and gate, and the exit's panel",
      "# ---------------------------------------------------------------- relay only\nif is_relay; then" in LOGIC
      and 'if is_exit && [ "$ROLE" != node ] && [ -z "$JOINED" ]; then\n    step "Panel: database and sync API"' in LOGIC
      and 'if is_relay && [ -n "${SYNC_TOKEN:-}" ]; then' in LOGIC)
check("it pairs with itself, and prints no token for anybody",
      'SYNC_TOKEN="$SYNC_TOKEN_OUT"; SYNC_TOKEN_OUT=""; PANEL_IP=127.0.0.1' in LOGIC)
check("its sync API goes to loopback, and the agent goes there",
      'set_env_key /etc/smart-dns/panel.env API_PORT "$SINGLE_API_PORT"' in LOGIC
      and "set_env_key /etc/smart-dns/panel.env API_BIND 127.0.0.1" in LOGIC
      and 'set_env_key /etc/smart-dns/sync.env PANEL_PORT "$SINGLE_API_PORT"' in LOGIC)
check("the exit's nginx is the one it runs, DoH included",
      'install_payload EXIT_NGINX /etc/nginx/nginx.conf' in function("relay_doh"))
check("an upgrade knows it by having both files",
      '[ -f /etc/smart-dns/sync.env ] && [ -f /etc/smart-dns/panel.env ]; then ROLE=single' in LOGIC)
check("behind NAT, dnsmasq and coturn listen on the private address, and coturn hands out the public",
      'grep -qw "inet $RELAY_IP"' in LOGIC
      and 'LISTEN_IP="$local_ip"; TURN_EXTERNAL="$RELAY_IP/$local_ip"' in LOGIC
      and "listen-address=127.0.0.1,%s\\n\\n' \"$LISTEN_IP\"" in LOGIC)
turn = open(os.path.join(ROOT, "templates", "turnserver.conf"), encoding="utf-8").read()
check("which the coturn config takes from the installer - the public address when it is on the machine",
      "listening-ip=__LISTEN_IP__" in turn and "external-ip=__TURN_EXTERNAL__" in turn
      and 's#__LISTEN_IP__#${LISTEN_IP:-$RELAY_IP}#g' in LOGIC
      and 's#__TURN_EXTERNAL__#${TURN_EXTERNAL:-$RELAY_IP}#g' in LOGIC)
check("and the admin panel may not take its sync API's port",
      '"$SINGLE_API_PORT") if [ "$ROLE" = single ]' in LOGIC)

# ---------------------------------------------------------------- the programs
print("the programs")
panel = load("templates/smartdns-panel", "panel")
check("the panel's sync API is on 8443 everywhere by default",
      panel.api_address({}) == ("0.0.0.0", 8443))
check("and where it is told, on a single machine",
      panel.api_address({"API_PORT": "8449", "API_BIND": "127.0.0.1"}) == ("127.0.0.1", 8449))
sync = load("templates/smartdns-sync", "sync")
sync.CFG = {"PANEL_HOST": "203.0.113.2"}
check("the sync agent goes to 8443 by default", sync.api_endpoints() == [("203.0.113.2", 8443)])
sync.CFG = {"PANEL_HOST": "127.0.0.1", "PANEL_PORT": "8449"}
check("and to the single machine's own port when told", sync.api_endpoints() == [("127.0.0.1", 8449)])
if BASH:
    etc = os.path.join(tmp, "etc")
    os.makedirs(etc)
    with open(os.path.join(etc, "panel.env"), "w", newline="\n") as fh:
        fh.write("RELAY_IP=203.0.113.2\nAPI_PORT=8449\nAPI_BIND=127.0.0.1\n")
    r = subprocess.run([BASH, posix(os.path.join(ROOT, "templates", "smartdns-api-guard")), "--print"],
                       capture_output=True, env=dict(os.environ, SMARTDNS_ETC=posix(etc)), timeout=30)
    rules = r.stdout.decode()
    check("the guard closes the sync API's port and leaves 8443 - the customer panel - open",
          "tcp dport 8449 drop" in rules and "8443" not in rules, rules)
for tool in ("smartdns-menu", "smartdns-logs", "smartdns-restart"):
    src = open(os.path.join(ROOT, "templates", tool), encoding="utf-8").read()
    check("%s knows a single machine by its two files" % tool,
          re.search(r'sync\.env.*panel\.env.*single|role=single', src, re.S) is not None
          and "single" in src)
logs = open(os.path.join(ROOT, "templates", "smartdns-logs"), encoding="utf-8").read()
check("and the logs show the DoH service too", "smartdns-doh" in logs)

print("the pages' words")
admin = load("templates/smartdns-admin", "admin")
admin.tickets_waiting = lambda: 0
cfg = {"ADMIN_PATH": "p"}
body = ("<h2>سرورهای ایران</h2><p>سرور ایران 1.2.3.4 — پنل و API — سرور خارج، "
        "هیچ رله‌ای هنوز، به آی‌پی رله، رله‌ها اعمال می‌کنند</p>")
admin.SYNC_ENV_HERE = os.path.join(tmp, "no-such-file")
two = admin.page("t", body, cfg, msg="روی رله‌ها اعمال می‌شود")
check("with two servers, the pages keep naming them", "سرورهای ایران" in two and "رله‌ها" in two)
admin.SYNC_ENV_HERE = os.path.join(tmp, "sync.env")
open(admin.SYNC_ENV_HERE, "w").close()
one = admin.page("t", body, cfg, msg="روی رله‌ها اعمال می‌شود")
check("on one, no server in Iran, none abroad, and no relay - on the page or in its message",
      all(w not in one for w in ("ایران", "خارج", "رله")) and "هیچ سروری هنوز" in one
      and "به آی‌پی سرور" in one, one[one.index("<h2>"):][:300])
sync.PANEL_ENV_HERE = os.path.join(tmp, "no-such-file")
check("the customer's page the same way: as it was with two servers",
      "از رله" in sync.user_page("<p>✅ از رله</p>"))
sync.PANEL_ENV_HERE = admin.SYNC_ENV_HERE
check("and in its own words on one", "از سرور" in sync.user_page("<p>✅ از رله</p>")
      and "رله" not in sync.user_page("<p>✅ از رله</p>"))

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
