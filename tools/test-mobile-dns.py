#!/usr/bin/env python3
"""0.1.3: DNS for phones on mobile data.

What has to hold: the main resolver answers its own names with a TTL of a minute, not
zero, and never in an answer too big to travel without fragments; every template's
resolver gets the same two settings; the resolver of addresses not let in keeps its
TTL of zero, because its answers must not outlive the registration they wait for.
"""
import importlib.machinery
import importlib.util
import os
import re
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" - " + str(detail)[:300]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def read(rel):
    with open(os.path.join(HERE, "..", rel), encoding="utf-8") as fh:
        return fh.read()


spec = importlib.util.spec_from_loader("sync", importlib.machinery.SourceFileLoader(
    "sync", os.path.join(HERE, "..", "templates", "smartdns-sync")))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
logic = read("tools/installer-logic.sh")

print("the main resolver")
check("a name answered from its own file lives a minute, not zero", "local-ttl=60" in logic)
check("and no answer is larger than 1232 bytes, which is what fragments are made of",
      "edns-packet-max=1232" in logic)
check("the two lines are written with the rest of the settings, before any rule",
      logic.index("local-ttl=60") < logic.index("listen-address=127.0.0.1,%s"))
check("and say why, so nobody 'fixes' them back", "radio link" in logic and "fragments" in logic)

print("the other resolvers")
d = tempfile.mkdtemp()
conf = os.path.join(d, "smart-dns.conf")
with open(conf, "w") as fh:
    fh.write("no-resolv\nserver=1.1.1.1\ncache-size=10000\nlocal-ttl=60\nedns-packet-max=1232\n"
             "listen-address=127.0.0.1,45.12.34.56\naddress=/a.com/45.12.34.56\n")
sync.HIJACK_CONF = conf
shared = sync.base_settings()
check("every template's resolver is given both, with the cache and the upstreams",
      "local-ttl=60" in shared and "edns-packet-max=1232" in shared and "cache-size=10000" in shared, shared)
check("and none of the rules", not any(l.startswith("address=") for l in shared))
gate = sync.gate_conf_text(["panel.example.com"], ["1.1.1.1"], here="45.12.34.56")
check("the resolver of addresses not let in is not given them: its answers must not be kept",
      "local-ttl=0" in gate and "local-ttl=60" not in gate and "edns-packet-max" not in gate, gate)
check("it still hands out the relay for everything, with a TTL of zero",
      "address=/#/45.12.34.56" in gate)

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
