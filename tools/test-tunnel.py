#!/usr/bin/env python3
"""The optional BackPack tunnel between the relay and the exit.

The installer's own tunnel functions are lifted out and run in bash here - the
port check, the spec the pairing token carries, the token both ends derive, the
config each end writes - and the templates are rendered the way install_payload
renders them. What cannot run off a server is held to the source.
"""
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
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


BASH = shutil.which("bash")
if BASH is None:
    print("bash not available - skipping")
    sys.exit(0)


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


def function(name):
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), LOGIC, re.S | re.M)
    if not m:       # a one-line function
        m = re.search(r"^%s\(\) \{[^\n]*\}\n" % re.escape(name), LOGIC, re.M)
    return m.group(0) if m else ""


tmp = tempfile.mkdtemp()
variables = "\n".join(re.findall(
    r"^(?:BACKPACK_\w+|TUNNEL_(?:DIR|NFT|LOCAL_HTTPS|LOCAL_HTTP|LOCAL_API|LOCAL_SPOTIFY|LOCAL_BLIZZARD"
    r"|REVERSE_TRANSPORTS|DIRECT_TRANSPORTS|L3_TRANSPORTS))=.*$",
    LOGIC, re.M))
names = ["tunnel_transport_ok", "tunnel_preset_ok", "tunnel_tuning", "tunnel_port_problem",
         "parse_tunnel_spec",
         "tunnel_token", "tunnel_pair_token", "tunnel_toml", "ask_tunnel"]
bodies = [function(n) for n in names]
check("the tunnel's functions are all in the installer", all(bodies),
      str([n for n, b in zip(names, bodies) if not b]))
HARNESS = os.path.join(tmp, "harness.sh")
with open(HARNESS, "w", newline="\n", encoding="utf-8") as fh:
    fh.write('set -u\nB=""; N=""\ninfo() { :; }\nwarn() { echo "WARN $*" >&2; }\n'
             'die() { echo "DIE $*"; exit 1; }\n')
    fh.write(variables.replace("TUNNEL_DIR=/etc/smart-dns/tunnel",
                               "TUNNEL_DIR=%s" % posix(os.path.join(tmp, "tun"))) + "\n")
    fh.write("\n".join(bodies) + "\n")
    fh.write('mkdir -p "$TUNNEL_DIR"\neval "$1"\n')


def sh(code, answers=None):
    # Bytes, not text: in text mode Windows turns every "\n" typed at the
    # script into "\r\n", and `read` hands the "\r" to the answer.
    r = subprocess.run([BASH, posix(HARNESS), code], capture_output=True, timeout=60,
                       input=answers.encode() if answers is not None else None)
    return r.stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()


print("the questions on the exit")


def ask(answers):
    return sh('ask_tunnel >/dev/null; echo "$TUNNEL ${TUNNEL_DIRECTION:-} ${TUNNEL_TRANSPORT:-} ${TUNNEL_PORT:-}"',
              "\n".join(answers) + "\n").strip()


check("enter at the first question keeps the direct path", ask([""]) == "off", ask([""]))
check("  and so does 1", ask(["1"]) == "off", ask(["1"]))
got = ask(["2", "", "", ""])
check("every other default: a reverse stealth tunnel on 8444", got == "backpack reverse stealth 8444", got)
got = ask(["2", "2", "2", "9443"])
check("direct is BackPack's direct as its menu makes it now, so its second is xdi",
      got == "backpack l3 xdi 9443", got)
check("  and the old direct engine is not offered", "answer 1 or 2" in function("ask_tunnel")
      and "2) TUNNEL_DIRECTION=l3 ;;" in function("ask_tunnel")
      and "TUNNEL_DIRECTION=direct" not in function("ask_tunnel"))
got = ask(["2", "1", "12", ""])
check("the twelfth reverse transport is udp", got == "backpack reverse udp 8444", got)
got = ask(["2", "1", "1", "443", "8447"])
check("a port that is taken is refused and asked again", got == "backpack reverse stealth 8447", got)
check("a number past the list is refused",
      "DIE" in sh("ask_tunnel", "2\n1\n13\n"), sh("ask_tunnel", "2\n1\n13\n")[-120:])
check("so is an answer that is not a number", "DIE" in sh("ask_tunnel", "2\n1\nstealth\n"))
menu = sh("ask_tunnel", "2\n1\n\n\n")
# How fast a transport is depends on the route, so the menu does not say -
# neither a figure nor fast or slow. Only which did not connect at all.
check("the menu says which did not connect, and nothing about speed",
      "did not connect" in menu and not re.search(r"MB/s|\bslow|\bfast", menu, re.I), menu[-400:])

print("asking again with --tunnel")
NOW = "CUR_TUNNEL=backpack CUR_TRANSPORT=wss CUR_DIRECTION=reverse CUR_PORT=9443; "


def ask_now(answers):
    return sh(NOW + 'ask_tunnel >/dev/null; echo "$TUNNEL ${TUNNEL_DIRECTION:-} ${TUNNEL_TRANSPORT:-} ${TUNNEL_PORT:-}"',
              "\n".join(answers) + "\n").strip()


got = ask_now(["", "", "", ""])
check("enter at every question keeps what the machine has", got == "backpack reverse wss 9443", got)
got = ask_now(["", "", "", "8447"])
check("changing only the port is one line typed", got == "backpack reverse wss 8447", got)
got = ask_now(["1"])
check("and 1 at the first question goes back to plain TCP", got == "off", got)
check("it says what the machine has now", "now: BackPack, wss, reverse, port 9443" in sh(NOW + "ask_tunnel", "\n\n\n\n"))
check("--tunnel is an argument the installer takes", "--tunnel|tunnel) ASK_TUNNEL=1 ;;" in LOGIC)
check("and --help names it", "--tunnel       choose the tunnel" in LOGIC)
check("on the exit it asks with what it has as the defaults",
      re.search(r'if \[ -n "\$\{ASK_TUNNEL:-\}" \].*?CUR_TUNNEL=.*?ask_tunnel', LOGIC, re.S) is not None)
check("on the relay it asks for the exit's new pairing token",
      re.search(r'ASK_TUNNEL.*?read -r -p "  pairing token: " SYNC_TOKEN', LOGIC, re.S) is not None)
check("and the exit tells the operator to do that next",
      "Now the relay%s: run the installer there with --tunnel" in LOGIC)
check("turning it off takes BackPack's metrics away too, not only the config",
      'rm -rf "$TUNNEL_DIR"' in function("apply_tunnel"))
check("the installer puts smartdns-tunnel and smartdns-menu on both machines",
      "payload SMARTDNS_TUNNEL > /usr/local/bin/smartdns-tunnel" in LOGIC
      and "payload SMARTDNS_MENU > /usr/local/bin/smartdns-menu" in LOGIC
      and LOGIC.index("payload SMARTDNS_MENU") < LOGIC.index('if [ -n "${PANEL_DOMAIN:-}" ]; then\n    step "HTTPS'))


print("which ports may carry it")
for p in ("8444", "2083", "9443", "31337"):
    check("%s is fine" % p, sh('tunnel_port_problem %s' % p) == "", sh('tunnel_port_problem %s' % p))
for p, why in (("22", "ssh"), ("53", "dns"), ("80", "proxy"), ("443", "proxy"),
               ("8443", "sync"), ("8446", "Google"), ("8402", "certificates"),
               ("3478", "STUN"), ("18443", "tunnel"), ("18080", "tunnel"),
               ("18843", "tunnel"), ("14070", "tunnel"), ("11119", "tunnel"),
               ("1119", "Battle.net"), ("4070", "Spotify"), ("18119", "Battle.net"),
               ("5300", "resolvers"), ("5350", "resolvers"),
               ("abc", "number"), ("0", "port"), ("70000", "port")):
    got = sh('tunnel_port_problem %s' % p)
    check("%s is refused, and says why" % p, why in got, got)

print("the spec the pairing token carries")
for spec, want in (("bp-stealth-8444-r", "backpack stealth 8444 reverse"),
                   ("bp-wss-9443-d", "backpack wss 9443 direct"),
                   ("bp-wssmux-2083-r", "backpack wssmux 2083 reverse"),
                   ("bp-xdi-7443-r", "backpack xdi 7443 reverse")):
    got = sh('parse_tunnel_spec %s && echo "$TUNNEL $TUNNEL_TRANSPORT $TUNNEL_PORT $TUNNEL_DIRECTION"' % spec)
    check("%s is read back" % spec, got == want, got)
for spec, why in (("bp-kcp-8444-d", "kcp is reverse-only"),
                  ("bp-stealth-443-r", "443 is the proxy"),
                  ("bp-nosuch-8444-r", "no such transport"),
                  ("bp-stealth-8444-x", "no such direction"),
                  ("stealth-8444-r", "not a spec"), ("", "empty")):
    check("%r is refused (%s)" % (spec, why),
          sh('parse_tunnel_spec "%s" && echo YES || echo NO' % spec) == "NO")

print("the token both ends derive")
a1, a2, b = sh("tunnel_token abc"), sh("tunnel_token abc"), sh("tunnel_token abd")
check("the same secret gives the same token", a1 == a2 and len(a1) == 48, a1)
check("a different secret a different one", a1 != b)
check("and it is not the secret itself", "abc" not in a1)

print("the config each end writes")
common = 'TUNNEL_TRANSPORT=%s TUNNEL_PORT=8444 RELAY_IP=198.51.100.1 EXIT_IP=203.0.113.2 PANEL_DOMAIN="" '
confs = {}
for role in ("relay", "exit"):
    for d in ("reverse", "direct"):
        confs[role, d] = sh(common % "stealth" + 'ROLE=%s TUNNEL_DIRECTION=%s tunnel_toml s3cret' % (role, d))
rr, xr, rd, xd = confs["relay", "reverse"], confs["exit", "reverse"], confs["relay", "direct"], confs["exit", "direct"]
check("reverse: the relay listens on the tunnel port",
      "[server]" in rr and 'bind_addr = "0.0.0.0:8444"' in rr, rr)
check("  and hands the exit's 443, 80, sync API, Spotify's 4070 and Battle.net's 1119 to loopback only",
      'ports = ["127.0.0.1:18443=443", "127.0.0.1:18080=80",'
      ' "127.0.0.1:18843=8443", "127.0.0.1:14070=4070", "127.0.0.1:11119=1119"]' in rr, rr)
check("reverse: the exit dials the relay",
      "[client]" in xr and 'remote_addr = "198.51.100.1:8444"' in xr, xr)
check("direct: the relay dials the exit, with the same local ports",
      'role = "iran"' in rd and 'addr = "203.0.113.2:8444"' in rd
      and "127.0.0.1:18443=443" in rd and "127.0.0.1:18843=8443" in rd
      and "127.0.0.1:14070=4070" in rd and "127.0.0.1:11119=1119" in rd, rd)
check("direct: the exit listens", 'role = "kharej"' in xd and 'addr = "0.0.0.0:8444"' in xd, xd)
tokens = {re.search(r'token = "(\w+)"', c).group(1) for c in confs.values()}
check("every end carries the same token", len(tokens) == 1, str(tokens))
check("and not the secret it came from", "s3cret" not in "".join(confs.values()))
check("the reverse ends keep BackPack's web panel and kernel tuning off",
      all("web_port = 0" in c and "skip_optz = true" in c for c in (rr, xr)))
wss = sh(common % "wss" + "ROLE=relay TUNNEL_DIRECTION=reverse tunnel_toml s3cret")
check("wss on the listening end names a certificate, self-signed without a domain",
      "tls_cert" in wss and "tls_key" in wss, wss)
check("the dialling end needs none", "tls_cert" not in sh(common % "wss" + "ROLE=exit TUNNEL_DIRECTION=reverse tunnel_toml s3cret"))

print("the relay's nginx, both ways")
nginx = read("templates", "relay-nginx.conf")


import importlib.machinery as _mach
import importlib.util as _util
_spec = _util.spec_from_loader("sync", _mach.SourceFileLoader(
    "sync", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates",
                         "smartdns-sync")))
SYNC = _util.module_from_spec(_spec)
_spec.loader.exec_module(SYNC)


def render(tunnel, doh=False):
    """The relay's nginx the way install_payload fills it, with the way to the
    exit the installer and smartdns-sync write in place of its include, and
    either the DoH blocks or the plain 443 block."""
    out = []
    skip = False
    cut = [("# nodoh begin", "# nodoh end") if doh else ("# doh begin", "# doh end")]
    for line in nginx.splitlines():
        if any(a in line and not ("nodoh" in line and "nodoh" not in a)
               for a, _ in cut):
            skip = True
        if not skip:
            out.append(line)
        if any(b in line and not ("nodoh" in line and "nodoh" not in b)
               for _, b in cut):
            skip = False
    text = "\n".join(out).replace("include /etc/nginx/smartdns-exit.conf;",
                                   SYNC.exit_conf_text("203.0.113.2", tunnel))
    if doh:
        text = (text.replace("__DOH_HOST__", "users.example.com")
                    .replace("__DOH_CERT__", "/etc/letsencrypt/live/users.example.com/fullchain.pem")
                    .replace("__DOH_KEY__", "/etc/letsencrypt/live/users.example.com/privkey.pem"))
    return text


on, off = render(True), render(False)
check("with a tunnel, every port goes through it",
      "proxy_pass ${to_exit}_https;" in on and "proxy_pass ${to_exit}_http;" in on
      and "proxy_pass ${to_exit}_spotify;" in on and "proxy_pass ${to_exit}_blizzard;" in on, on[-600:])
check("  with the exit itself as the fallback",
      "server 203.0.113.2:443 backup;" in on and "server 203.0.113.2:80 backup;" in on)
check("  and the tunnel's end first", on.index("127.0.0.1:18443") < on.index("203.0.113.2:443 backup"))
check("without one, straight to the exit as before",
      "server 203.0.113.2:443;" in off and "server 203.0.113.2:80;" in off
      and "server 203.0.113.2:4070;" in off and "server 203.0.113.2:1119;" in off
      and "proxy_pass ${to_exit}_https;" in off)
check("  and no trace of the tunnel", "backup;" not in off and "18443" not in off)
print("the relay's nginx with DNS over HTTPS")
don, doff = render(True, doh=True), render(False, doh=True)
check("443 reads the name and nothing more",
      "ssl_preread on;" in don and "proxy_pass $https_target;" in don)
check("  the DoH names go to the DoH server - the file the installer and smartdns-sync keep",
      "include /etc/nginx/smartdns-doh-names.map;" in don)
check("  every other name goes where it always went, through the tunnel",
      "default       ${to_exit}_https;" in don)
check("  or straight to the exit without one",
      "default       ${to_exit}_https;" in doff and "server 203.0.113.2:443;" in doff
      and "backup;" not in doff)
check("  and the plain 443 block is gone, so 443 is not listened on twice",
      don.count("listen 443;") == 1 and doff.count("listen 443;") == 1)
check("853 ends TLS and says who connected",
      "listen 853 ssl;" in don and "proxy_protocol on;" in don
      and "proxy_pass 127.0.0.1:8054;" in don)
check("the DoH server speaks HTTP/2, on loopback only - the gate is the way in",
      "listen 127.0.0.1:8453 ssl http2;" in don and "X-Real-IP $remote_addr" in don
      and "proxy_pass http://127.0.0.1:8055;" in don)
check("without a certificate there is no trace of DoH",
      "8453" not in on and "853" not in on and "https_target" not in on
      and "\nhttp {" not in on and on.count("listen 443;") == 1)
for name, text in (("with DoH", don), ("with DoH, no tunnel", doff)):
    check("%s: no placeholder left, braces balanced" % name,
          "__" not in text.replace("__MODULE_PATH__", "") and text.count("{") == text.count("}"))
check("the filler cuts one DoH shape or the other",
      "${NO_DOH:+/# doh begin/,/# doh end/d}" in LOGIC
      and "${DOH_ON:+/# nodoh begin/,/# nodoh end/d}" in LOGIC)

for name, text in (("with", on), ("without", off)):
    check("%s: no placeholder left, braces balanced" % name,
          "__" not in text.replace("__MODULE_PATH__", "") and text.count("{") == text.count("}"))
check("the way to the exit is the file the installer and smartdns-sync keep",
      nginx.count("include /etc/nginx/smartdns-exit.conf;") == 1 and "__EXIT_" not in nginx
      and "exit_upstreams() {" in LOGIC)

print("the exit lets the tunnel's own end in")
exitng = read("templates", "exit-nginx.conf")
check("the relays are allowed into every block that listens outward",
      exitng.count("allow 127.0.0.1;") == 4 and exitng.count("include /etc/nginx/smartdns-relays.conf;") == 4)
check("and Spotify's access point is where 4070 goes",
      "listen 4070;" in exitng and "proxy_pass ap.spotify.com:4070;" in exitng)
check("and Battle.net's 1119: the sign-in by its TLS name, Blizzard's names only",
      "listen 1119;" in exitng and "proxy_pass $blizzard_upstream;" in exitng
      and "\\.(battle\\.net|blizzard\\.com)$  $ssl_preread_server_name:1119;" in exitng
      and 'default  "";' in exitng)
check("  the version check, which is plain HTTP, by its Host on a loopback server",
      '""       127.0.0.1:18119;' in exitng and "listen 127.0.0.1:18119;" in exitng
      and "proxy_pass http://$host:1119$request_uri;" in exitng
      and "listen 127.0.0.1:18119 default_server;" in exitng)
for m in re.finditer(r"include /etc/nginx/smartdns-relays\.conf;(.*?)deny all;", exitng, re.S):
    check("  loopback sits between the relay and the deny", "allow 127.0.0.1;" in m.group(1))

print("the installer")
check("it pins a version, and a hash for each architecture",
      re.search(r'BACKPACK_VERSION="v\d+\.\d+\.\d+"', LOGIC) is not None
      and re.search(r'BACKPACK_SHA_amd64="[0-9a-f]{64}"', LOGIC) is not None
      and re.search(r'BACKPACK_SHA_arm64="[0-9a-f]{64}"', LOGIC) is not None)
inst = function("install_backpack")
check("it fetches BackPack from BackPack's own releases",
      "github.com/AminMGMT/BackPack/releases/download/$BACKPACK_VERSION" in inst)
check("and checks the hash before installing anything",
      0 < inst.index("sha256sum") < inst.index("install -m 755"))
check("a download that fails leaves the run on the direct path",
      "! install_backpack" in LOGIC and 'TUNNEL=off; TUNNEL_SPEC=""' in LOGIC)
block = LOGIC[LOGIC.index('if [ "$TUNNEL" = backpack ] && ! install_backpack; then'):]
block = block[:block.index("\nfi\n") + 4]


def after_failed_download(have_old):
    d = tempfile.mkdtemp()
    try:
        binary = os.path.join(d, "backpack").replace("\\", "/")
        if have_old:
            with open(binary, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(binary, 0o755)
            with open(binary + ".version", "w") as f:
                f.write("v1.8.0 abc\n")
        script = os.path.join(d, "t.sh")
        with open(script, "w", newline="\n") as f:
            f.write('warn() { echo "warn: $*"; }\ninstall_backpack() { return 1; }\n'
                    'BACKPACK_BIN="%s"; TUNNEL=backpack; TUNNEL_SPEC=bp-stealth-8444-r; ROLE=relay\n'
                    '%s\necho "TUNNEL=$TUNNEL SPEC=$TUNNEL_SPEC"\n' % (binary, block))
        return subprocess.run(["bash", script], capture_output=True, text=True).stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


out = after_failed_download(True)
check("an update BackPack cannot download keeps the one already here, and the tunnel",
      "TUNNEL=backpack SPEC=bp-stealth-8444-r" in out and "keeping BackPack v1.8.0" in out, out)
out = after_failed_download(False)
check("  with none here, the direct path", "TUNNEL=off SPEC=" in out, out)
check("that happens before nginx is written",
      LOGIC.index("! install_backpack")
      < LOGIC.index("install_payload RELAY_NGINX /etc/nginx/nginx.conf && NGINX_CHANGED=1"))
check("only the exit is asked, and never on an upgrade or with ASSUME_YES",
      '[ "$ROLE" = exit ] && [ -z "$TUNNEL" ] && [ -z "${ASSUME_YES:-}" ] && [ -z "$UPGRADE" ]' in LOGIC)
check("the exit's pairing token carries the spec",
      'SYNC_TOKEN_OUT="$SYNC_SECRET.$FP${TUNNEL_SPEC:+.$TUNNEL_SPEC}"' in LOGIC)
check("the relay reads the secret and fingerprint by position, not from the end",
      "cut -d. -f1" in LOGIC and "cut -s -d. -f2" in LOGIC)
check("and the spec from the third part", "cut -s -d. -f3" in LOGIC)
check("both ends keep the choice for the next run",
      LOGIC.count("set_env_key /etc/smart-dns/panel.env TUNNEL") >= 4
      and LOGIC.count("set_env_key /etc/smart-dns/sync.env TUNNEL") >= 4)
check("the admin panel cannot take the tunnel's port",
      '"${TUNNEL_PORT:-none}") die' in LOGIC)
ask = function("ask_tunnel")
check("the menu offers every transport",
      all(re.search(r"^%s\|" % t, ask, re.M) for t in
          "stealth wss wssmux wsmux ws tcp tcpmux kcp pck xdi quic udp".split()))
check("and says which did not connect in our test",
      re.search(r"^quic\|.*did not connect", ask, re.M) and re.search(r"^udp\|.*did not connect", ask, re.M))
apply_ = function("apply_tunnel")
check("the listening end's port answers the other machine only",
      "ip saddr != $peer drop" in apply_ and "meta nfproto ipv6" in apply_)
check("turning it off takes the service and the rule away",
      "systemctl disable --now smartdns-tunnel.service" in apply_ and 'rm -f "$TUNNEL_NFT"' in apply_)
check("the checks load a site through the tunnel's own end, not through nginx",
      '--connect-to "github.com:443:127.0.0.1:$TUNNEL_LOCAL_HTTPS"' in LOGIC)
check("uninstall removes the tunnel's firewall table", "nft delete table inet smartdns_tunnel;" in LOGIC)

print("credit where it is due")
check("the installer names BackPack's author when it installs it",
      'info "BackPack is the work of Amin Mohammadi' in inst)
check("and so does smartdns-tunnel's help",
      "Amin Mohammadi" in read("templates", "smartdns-tunnel")
      and "github.com/AminMGMT/BackPack" in read("templates", "smartdns-tunnel"))
if all(os.path.exists(os.path.join(ROOT, n)) for n in ("README.md", "README.fa.md")):
    for n, heading in (("README.md", "## Credits"), ("README.fa.md", "## سپاس")):
        txt = read(n)
        check("%s credits him, with the licence" % n,
              heading in txt and ("Amin Mohammadi" in txt or "امین محمدی" in txt)
              and "AGPL-3.0" in txt and "github.com/AminMGMT/BackPack" in txt)
else:
    print("  --   not the published tree - README credits skipped")

print("its service, and the tools that know it")
unit = read("templates", "smartdns-tunnel.service")
check("the service runs BackPack from its own config",
      "ExecStart=/usr/local/lib/smart-dns/backpack -c /etc/smart-dns/tunnel/tunnel.toml" in unit)
check("and loads the firewall rule first, a missing file being no failure",
      "ExecStartPre=-/usr/sbin/nft -f /etc/nftables.d/40-smartdns-tunnel.conf" in unit)
check("smartdns-restart restarts it", "restart smartdns-tunnel" in read("templates", "smartdns-restart"))
check("smartdns-logs shows it", "smartdns-tunnel" in read("templates", "smartdns-logs"))
built_path = os.path.join(ROOT, "master-dns.sh")
if os.path.exists(built_path):
    built = open(built_path, encoding="utf-8").read()
    check("the built installer carries the service", "#__BEGIN_TUNNEL_SERVICE__" in built)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
