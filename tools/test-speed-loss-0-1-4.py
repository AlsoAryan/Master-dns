#!/usr/bin/env python3
"""0.1.4: DNS speed and packet loss on the Iran side.

What has to hold: the resolver asks every public resolver at once, allows enough queries in
flight and keeps short answers for a minute, and asks for stale answers only where dnsmasq knows
the option; a registered customer is not held to the limit meant for strangers; both nginx
configs ask for keepalives on every stream port they carry; the kernel file sets the loss and
keepalive settings; the catalogue audit finds no error.
"""
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" - " + str(detail)[:300]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as fh:
        return fh.read()


inst = read("tools/installer-logic.sh")
print("the resolver")
check("version is 0.1.4", 'VERSION="0.1.4"' in inst)
check("it asks every resolver at once", "all-servers" in inst)
check("it allows more than 150 queries in flight", re.search(r"dns-forward-max=(\d+)", inst) and
      int(re.search(r"dns-forward-max=(\d+)", inst).group(1)) >= 500)
check("it keeps an answer at least a minute", "min-cache-ttl=60" in inst)
check("a name of ours still has a TTL of a minute", "local-ttl=60" in inst)
check("stale answers only when dnsmasq has the option",
      re.search(r"if dnsmasq --help .*grep -q -- '--use-stale-cache'.*\n\s+printf 'use-stale-cache=3600", inst) is not None)
check("the built installer carries them", all(x in read("master-dns.sh") for x in ("all-servers", "dns-forward-max", "use-stale-cache")))

print("the firewall")
nf = read("templates/nftables-smartdns.conf")
check("a registered address has a limit of its own, higher",
      re.search(r"@allowed udp dport 53 meter dnsflood_customers \{ ip saddr limit rate over (\d+)/second", nf) and
      int(re.search(r"dnsflood_customers \{ ip saddr limit rate over (\d+)/second", nf).group(1)) > 40)
check("and is then let through, ahead of the strangers' rule",
      nf.index("@allowed udp dport 53 accept") < nf.index('iifname != "lo" udp dport 53 meter dnsflood {'))
check("a stranger is still held to 40 a second", "limit rate over 40/second burst 80 packets" in nf)

print("the kernel")
sy = read("common/sysctl-tuning.conf")
for key in ("net.ipv4.tcp_thin_linear_timeouts = 1", "net.ipv4.tcp_keepalive_time = 60",
            "net.ipv4.tcp_keepalive_intvl = 20", "net.ipv4.tcp_keepalive_probes = 4",
            "net.core.rmem_default = 1048576"):
    check(key, re.search(r"^" + re.escape(key) + r"$", sy, re.M) is not None)

print("nginx")
for name, ports in (("relay", (443, 80, 4070, 1119)), ("exit", (443, 4070, 1119))):
    conf = read("templates/%s-nginx.conf" % name)
    for port in ports:
        m = re.search(r"listen %d so_keepalive=60s:20s:4;(.*?)\n    \}" % port, conf, re.S)
        check("%s: port %d asks for keepalives on both sides" % (name, port),
              m is not None and "proxy_socket_keepalive on;" in m.group(1))

print("the catalogue")
r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "audit-catalogue.py"), "--quiet"],
                   capture_output=True, text=True)
check("tools/audit-catalogue.py finds no error", r.returncode == 0, r.stdout)

if fails:
    print("\n%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("\nall passed")
