#!/usr/bin/env python3
"""The KCP transports' drive, and the installer's checks on a tunnel-only route.

What has to hold: a tunnel on xdi, kcp or pck carries BackPack's Turbo
numbers - fast retransmit, no congestion window of its own, a 10 ms tick -
at both ends, written the same by the installer, the admin panel, the relay
and a node, and still parses as BackPack reads it: the keys inside [server]
or [client]. Error correction stays off at both ends, since it must match
and the two ends are upgraded minutes apart. Other transports get none of
it. And the installer counts the sync API as answering when it answers
through the tunnel, as the sync itself uses it, rather than failing a relay
whose direct path is closed.
"""
import importlib.machinery
import importlib.util
import os
import re
import sys
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


sync = load("templates/smartdns-sync", "sync")
admin = load("templates/smartdns-admin", "admin")
# wss makes a certificate under /etc/smart-dns; nothing here may touch disk.
sync.tunnel_cert = lambda: ("/tmp/c.pem", "/tmp/k.pem")
WANT = {"kcp_mtu": 1250, "kcp_interval": 10, "kcp_resend": 2, "kcp_nodelay": 1,
        "kcp_nocongestion": 1, "kcp_sndwnd": 1024, "kcp_rcvwnd": 1024, "kcp_acknodelay": True}


def spec(t):
    return {"transport": t, "direction": "reverse", "port": 8477}


def kcp_keys(text, table):
    parsed = tomllib.loads(text)[table]
    return {k: v for k, v in parsed.items() if k.startswith("kcp_")}


print("the four writers")
for t in ("xdi", "kcp", "pck"):
    relay = sync.tunnel_toml_text(spec(t), "198.51.100.9", "s3cret")
    exit_end = admin.exit_tunnel_toml("198.51.100.1", spec(t), "s3cret", "/tmp/x")
    node_end = sync.exit_side_toml("198.51.100.1", spec(t), "s3cret", "/tmp/x")
    check("%s: the relay, the exit and a node all drive it at Turbo" % t,
          kcp_keys(relay, "server") == WANT and kcp_keys(exit_end, "client") == WANT
          and kcp_keys(node_end, "client") == WANT)
for t in ("tcp", "stealth", "wss", "ws", "quic", "udp"):
    relay = sync.tunnel_toml_text(spec(t), "198.51.100.9", "s3cret")
    exit_end = admin.exit_tunnel_toml("198.51.100.1", spec(t), "s3cret", "/tmp/x")
    check("%s: none of it" % t, "kcp_" not in relay and "kcp_" not in exit_end)
check("no error correction at either end: it must match, and the ends upgrade apart",
      "datashards" not in sync.KCP_TUNING and "parityshards" not in sync.KCP_TUNING
      and admin.KCP_TUNING == sync.KCP_TUNING)

print("the installer")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
m = re.search(r"case \"\$side:\$t\" in server:kcp\|server:xdi\|server:pck\|client:kcp\|"
              r"client:xdi\|client:pck\)\n((?:\s+printf '[^']*'[^\n]*\n?)+)", logic)
lines = "".join(re.findall(r"printf '([^']*)'", m.group(1))).replace("\\n", "\n") if m else ""
check("its own tunnel gets the very same lines", lines == sync.KCP_TUNING, lines)
check("the full-chain check no longer prints 000000",
      "https://github.com/ 2>/dev/null || true)\" \"200\"" in logic
      and "|| echo 000)" not in logic)
check("the sync API counts as answering through the tunnel, and says the direct path is closed",
      'api_code 127.0.0.1 "$TUNNEL_LOCAL_API"' in logic
      and "answers through the tunnel only" in logic
      and 'check "the exit\'s sync API answers this relay" "$api_got" "501"' in logic)

print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
