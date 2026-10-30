#!/usr/bin/env python3
"""BackPack's layer-3 direct tunnel, between a relay and its exit.

What has to hold: the relay dials, as in direct, but the two machines get a
small network of their own - a /30 of 10.10.0.0/16 picked by the relay's
address, worked out the same by the installer, the admin panel, the relay's
sync, the exit's sync API and its firewall, with nothing passed between
them, so each relay of an exit has its own. The relay keeps the same five
ports on its loopback as every other tunnel, each going to the exit's own
port at the far end of that network; the exit listens, its port answering
the relay alone. The exit lets the relay in from its tunnel address -
nginx, the sync API and the port 8443 rule alike. The pairing token carries
it ("-l"), the installer asks for it, and the admin panel offers it for the
main exit and the nodes. To a node, the block is picked by the relay's
address and the node's, so the relay's tunnels to its exit and its nodes
each have their own; the node builds its end and lets the relay in from it,
and while that tunnel carries nothing the relay goes straight to the node.
"""
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

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


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


sync = load("templates/smartdns-sync", "sync")
admin = load("templates/smartdns-admin", "admin")
admin.exit_address = lambda: "203.0.113.9"
panel = load("templates/smartdns-panel", "panel")
LOGIC = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
RELAY, EXIT, SECRET = "198.51.100.7", "203.0.113.9", "s3cret"

print("one block for each relay, the same everywhere")
relays = ["198.51.100.%d" % i for i in range(1, 60)] + ["192.0.2.44", "203.0.113.20"]
blocks = [sync.l3_block(r) for r in relays]
check("the relay's end and the exit's are the two addresses of one /30",
      all(a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]
          and int(a.rsplit(".", 1)[1]) % 4 == 1 and int(b.rsplit(".", 1)[1]) == int(a.rsplit(".", 1)[1]) + 1
          and a.startswith("10.10.") for a, b, _ in blocks))
check("  the admin panel and the exit's API work out the same",
      [admin.l3_block(r) for r in relays] == blocks
      and [panel.l3_relay(r) for r in relays] == [b[0] for b in blocks])
check("  an interface name a kernel takes", all(re.fullmatch(r"ddl[0-9a-f]{4}", i) for *_, i in blocks))
check("  sixty relays, sixty blocks", len({b[0] for b in blocks}) == len(relays))

BASH = shutil.which("bash")
tmp = tempfile.mkdtemp()


def function(name, text=LOGIC):
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), text, re.S | re.M)
    if not m:       # a one-line function
        m = re.search(r"^%s\(\) \{[^\n]*\}\n" % re.escape(name), text, re.M)
    return m.group(0) if m else ""


if BASH:
    variables = "\n".join(re.findall(
        r"^(?:TUNNEL_(?:LOCAL_HTTPS|LOCAL_HTTP|LOCAL_API|LOCAL_SPOTIFY|LOCAL_BLIZZARD"
        r"|REVERSE_TRANSPORTS|DIRECT_TRANSPORTS|L3_TRANSPORTS))=.*$", LOGIC, re.M))
    harness = os.path.join(tmp, "h.sh")
    with open(harness, "w", newline="\n", encoding="utf-8") as fh:
        fh.write('set -u\nB=""; N=""\ninfo() { :; }\nwarn() { :; }\ndie() { echo "DIE $*"; exit 1; }\n')
        fh.write(variables + "\n")
        for n in ("l3_block", "tunnel_transport_ok", "tunnel_preset_ok", "tunnel_tuning",
                  "tunnel_port_problem", "parse_tunnel_spec",
                  "tunnel_token", "tunnel_pair_token", "tunnel_toml", "ask_tunnel"):
            fh.write(function(n) + "\n")
        fh.write('eval "$1"\n')

    def sh(code, answers=None):
        r = subprocess.run([BASH, posix(harness), code], capture_output=True, timeout=60,
                           input=answers.encode() if answers is not None else None)
        return r.stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()

    got = sh('for r in %s; do l3_block "$r"; echo "$L3_RELAY $L3_EXIT $L3_IFACE"; done'
             % " ".join(relays)).splitlines()
    check("the installer's bash, the same blocks", got == [" ".join(b) for b in blocks],
          str(got[:3]))
    guard = open(os.path.join(ROOT, "templates", "smartdns-api-guard"), encoding="utf-8").read()
    got = sh(function("l3_relay", guard) + 'for r in %s; do l3_relay "$r"; done' % " ".join(relays))
    check("  and the 8443 rule's", got.splitlines() == [b[0] for b in blocks])

    print("the installer")
    check("the pairing token says l3 with an l",
          sh('parse_tunnel_spec bp-xdi-8477-l && echo "$TUNNEL_DIRECTION $TUNNEL_TRANSPORT $TUNNEL_PORT"')
          == "l3 xdi 8477"
          and "1" in sh('parse_tunnel_spec bp-kcp-8477-l || echo 1'))
    got = sh('ask_tunnel >/dev/null; echo "$TUNNEL_DIRECTION $TUNNEL_TRANSPORT $TUNNEL_PORT"',
             "3\n2\n2\n8477\n")
    check("  asked for as direct, the second choice, xdi among its carriers", got == "l3 xdi 8477", got)
    menu = sh("ask_tunnel", "3\n2\n\n\n")
    check("  its five carriers, and only those",
          all(re.search(r"\) %s " % t, menu) for t in ("pck", "xdi", "sni", "quic", "udp"))
          and not re.search(r"\) (stealth|wss|kcp|tcp) ", menu), menu[-400:])
    env = ('TUNNEL_DIRECTION=l3; TUNNEL_TRANSPORT=xdi; TUNNEL_PORT=8477; RELAY_IP=%s; EXIT_IP=%s; '
           % (RELAY, EXIT))
    relay_end = sh(env + 'ROLE=relay; tunnel_toml %s' % SECRET)
    exit_end = sh(env + 'ROLE=exit; tunnel_toml %s' % SECRET)
else:
    print("  (bash not available - the installer's part skipped)")
    relay_end = exit_end = ""

check("  taken from the environment too, as TUNNEL_DIRECTION=l3",
      'case "$TUNNEL_DIRECTION" in reverse|direct|l3) ;;' in LOGIC)

print("the relay's end and the exit's")
sync.CFG = {"SELF_IP": RELAY}
spec = {"direction": "l3", "transport": "xdi", "port": 8477}
mine, theirs, iface = sync.l3_block(RELAY)
r = tomllib.loads(sync.tunnel_toml_text(spec, EXIT, SECRET))["l3"]
e = tomllib.loads(admin.exit_tunnel_toml(RELAY, spec, SECRET, "/tmp/x"))["l3"]
check("the relay dials the exit on the tunnel's port", r["mode"] == "dial"
      and r["addr"] == "%s:8477" % EXIT)
check("  and keeps the five ports on its loopback, each to the exit's own across the tunnel",
      r["ports"] == ["127.0.0.1:18443=443", "127.0.0.1:18080=80", "127.0.0.1:18843=8443",
                     "127.0.0.1:14070=4070", "127.0.0.1:11119=1119"])
check("the exit listens, nothing forwarded at its end",
      e["mode"] == "listen" and e["addr"] == "0.0.0.0:8477" and "ports" not in e)
check("the two ends' addresses, crossed", r["local_ip"] == mine + "/30" and r["peer_ip"] == theirs
      and e["local_ip"] == theirs + "/30" and e["peer_ip"] == mine)
check("  the same carrier, interface and token at both",
      (r["carrier"], r["iface"], r["token"]) == (e["carrier"], e["iface"], e["token"]) == (
          "xdi", iface, sync.tunnel_token(SECRET, spec, RELAY, EXIT)))
check("  no reverse or direct table beside it, and no KCP knobs - they are not this kind's",
      set(tomllib.loads(sync.tunnel_toml_text(spec, EXIT, SECRET))) == {"l3"}
      and not any(k.startswith("kcp_") for k in r))
if relay_end:
    check("the installer writes the very same two",
          tomllib.loads(relay_end)["l3"] == r and tomllib.loads(exit_end)["l3"] == e,
          relay_end + "\n" + exit_end)
check("the panel's word on it is taken by the relay",
      sync.clean_tunnel({"on": True, "direction": "l3", "transport": "xdi", "port": 8477})
      == spec and sync.clean_tunnel({"on": True, "direction": "l3", "transport": "kcp",
                                     "port": 8477}) is None)

print("the exit lets the relay in from its tunnel address")
admin_src = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
panel_src = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("nginx's list: every relay, and each one's tunnel address",
      '"".join("allow %s;\\n" % l3_block(ip)[0] for ip in ips)' in admin_src
      and 'l3_block "$ip"; printf \'allow %s;\\n\' "$L3_RELAY"' in LOGIC)
check("the sync API", 'not in getattr(self, "l3_relays", ())' in panel_src
      and "API.l3_relays = frozenset(l3_relay(r) for r in API.relays)" in panel_src)
check("the exit's port answers the relay alone, as for direct",
      'if spec["direction"] != "reverse":' in admin_src
      and '[ "$TUNNEL_DIRECTION" != reverse ]; }; then' in LOGIC)

print("a relay's tunnel to a node")
NODE = "192.0.2.80"
sync_src = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
base = sync.NODE_TUNNEL_BASE + 10
carried = [(name, base + i, port) for i, (name, _, port) in enumerate(sync.TUNNEL_PORTS)]
sync.CFG = {"SELF_IP": RELAY}
r = tomllib.loads(sync.tunnel_toml_text(spec, NODE, SECRET, carried))["l3"]
sync.CFG = {"SELF_IP": NODE}
n = tomllib.loads(sync.exit_side_toml(RELAY, spec, SECRET, "/tmp/x"))["l3"]
nmine, ntheirs, niface = sync.l3_block(RELAY, NODE)
check("the relay dials the node, the proxy's ports on its own loopback ports for it",
      r["mode"] == "dial" and r["addr"] == "%s:8477" % NODE
      and r["ports"] == ["127.0.0.1:%d=%d" % (base + i, p)
                         for i, p in enumerate((443, 80, 4070, 1119))])
check("the node listens, the two ends' addresses crossed, the same carrier, interface and token",
      n["mode"] == "listen" and n["addr"] == "0.0.0.0:8477" and "ports" not in n
      and r["local_ip"] == nmine + "/30" and r["peer_ip"] == ntheirs
      and n["local_ip"] == ntheirs + "/30" and n["peer_ip"] == nmine
      and (r["carrier"], r["iface"], r["token"]) == (n["carrier"], n["iface"], n["token"])
      == ("xdi", niface, sync.tunnel_token(SECRET, spec, RELAY, NODE)))
check("an xdi tunnel's token is its pair's own: the relay's to its exit and to a node differ,"
      " and another relay's to the same exit too - xdi tags its pings with it",
      len({sync.tunnel_token(SECRET, spec, RELAY, EXIT), sync.tunnel_token(SECRET, spec, RELAY, NODE),
           sync.tunnel_token(SECRET, spec, "192.0.2.45", EXIT)}) == 3
      and sync.tunnel_token(SECRET, dict(spec, transport="pck"), RELAY, EXIT)
      == sync.tunnel_token(SECRET))
check("  its own block and interface on the relay, beside the one to the main exit",
      sync.l3_block(RELAY, NODE)[0] != sync.l3_block(RELAY)[0] and niface != iface
      and sync.l3_block(RELAY, NODE) != sync.l3_block(RELAY, "192.0.2.81"))
check("  the node's port answers the relay alone",
      'spec["port"] if spec["direction"] != "reverse" else None)\n    NODE_STATE' in sync_src)
written = []
sync.nginx_swap = lambda path, text: written.append(text) or True
sync.CFG = {"SELF_IP": NODE}
sync.apply_node_relays([RELAY, "192.0.2.90"], {RELAY: dict(spec, slot=1),
                                                "192.0.2.90": {"direction": "reverse",
                                                               "transport": "tcp", "port": 9000}})
check("the node lets the relay in from its tunnel address too, and only that relay's",
      written and written[-1].count("allow ") == 3 and "allow %s;" % nmine in written[-1])
sync.NODE_TUNNELS.clear()
sync.NODE_TUNNELS[NODE] = base
sync.note_node_tunnel(NODE, False)
first = dict(sync.node_tunnel_ways())
sync.note_node_tunnel(NODE, False)
down = dict(sync.node_tunnel_ways())
sync.note_node_tunnel(NODE, True)
sync.note_node_tunnel(NODE, True)
check("a node tunnel that carries nothing twice running sends nginx straight to the node, "
      "and back through it once it carries twice",
      first == {NODE: base} and down == {} and sync.node_tunnel_ways() == {NODE: base}
      and "node_tunnel_ways())" in sync_src)
sync.NODE_TUNNELS.clear()

print("the admin panel")


class Versions:
    def __init__(self, have):
        self.have, self.saved = have, []

    def one(self, sql, args):
        key = args[0]
        if key.startswith("version:") and key[8:] in self.have:
            return {"value": self.have[key[8:]]}
        return None

    def run(self, sql, args=()):
        if sql.startswith("INSERT"):
            self.saved.append(args)

    def q(self, *a):
        return []


admin.node_list = lambda: [NODE]
admin.node_slot = lambda n: 1
admin.all_tunnels = lambda: []
admin.relay_tunnel = lambda ip, to: {}
form = {"on": "1", "direction": "l3", "transport": "xdi", "port": "8477", "preset": ""}
admin.STORE = Versions({RELAY: "0.10.5", NODE: "0.10.4"})
old = admin.save_node_tunnel(RELAY, NODE, lambda k: form.get(k, ""))
admin.STORE, kept_old = Versions({RELAY: "0.10.5", NODE: "0.10.5"}), admin.STORE.saved
new = admin.save_node_tunnel(RELAY, NODE, lambda k: form.get(k, ""))
check("a node takes it once both ends are of the version that builds it",
      old.startswith("nodes?m=!") and NODE in old and not kept_old
      and not new.startswith("nodes?m=!") and len(admin.STORE.saved) == 1
      and '"direction": "l3"' in admin.STORE.saved[0][1], old + " | " + new)
check("offered for the main exit and the nodes alike",
      "<option value='l3'%s>" in admin_src and "if to == exit_address() else" not in admin_src
      and 'if direction == "l3":\n        return "nodes?m=!' not in admin_src)
check("what each direction can carry is said when something else is picked",
      set(admin.TRANSPORT_LIMIT) == set(admin.TUNNEL_TRANSPORTS) == {"reverse", "direct", "l3"}
      and admin.TUNNEL_TRANSPORTS["l3"] == sync.TUNNEL_TRANSPORTS["l3"]
      and "sni" in admin.TRANSPORT_WORDS)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
