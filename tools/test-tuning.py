#!/usr/bin/env python3
"""The kernel settings an install writes, read as a file and as the installer uses them.

What is checked is what can go wrong without anyone noticing: a key spelled
wrong is silently ignored by sysctl, a setting that needs a module is skipped
at boot if nothing loads the module first, and a file left behind after an
uninstall keeps changing a machine that is no longer ours.
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
tuning = open(os.path.join(HERE, "..", "common", "sysctl-tuning.conf"), encoding="utf-8").read()
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
fails = []


def check(name, ok, detail=""):
    print("  %s %s" % ("ok  " if ok else "FAIL", name))
    if not ok:
        fails.append(name + (" - " + str(detail)[:200] if detail else ""))


settings = {}
for line in tuning.splitlines():
    m = re.match(r"^\s*([a-z0-9_.]+)\s*=\s*(\S.*?)\s*$", line)
    if m:
        settings[m.group(1)] = m.group(2)

print("the file")
check("every line is key = value or a comment",
      all(re.match(r"^\s*([a-z0-9_.]+\s*=\s*\S.*|#.*|)\s*$", l) for l in tuning.splitlines()))
check("no key is set twice", len(settings) == len(re.findall(r"^\s*[a-z0-9_.]+\s*=", tuning, re.M)))
check("the notsent limit keeps the queue short (64 KB to 256 KB)",
      65536 <= int(settings.get("net.ipv4.tcp_notsent_lowat", 0)) <= 262144)
check("the conntrack table is bigger than the usual 65536 and not unreasonable",
      65536 < int(settings.get("net.netfilter.nf_conntrack_max", 0)) <= 1048576)
check("an established entry lives hours, not the default five days",
      3600 <= int(settings.get("net.netfilter.nf_conntrack_tcp_timeout_established", 0)) <= 86400)
check("the buffers it had are still there", int(settings["net.core.rmem_max"]) >= 33554432)
check("it still leaves congestion control alone (that is asked for, not assumed)",
      "tcp_congestion_control =" not in tuning.replace("# Congestion", ""))

print("the installer")
check("the module is named for early loading", "/etc/modules-load.d/smartdns-conntrack.conf" in logic
      and "nf_conntrack" in logic)
check("and loaded before the settings that need it",
      logic.index("modprobe nf_conntrack") < logic.index("sysctl -p /etc/sysctl.d/99-smartdns-tuning.conf"))
check("an uninstall takes the module file away",
      "rm -f /etc/modules-load.d/smartdns-conntrack.conf" in logic)

print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
