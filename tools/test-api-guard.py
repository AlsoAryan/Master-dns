#!/usr/bin/env python3
"""The exit's sync API answers the relays, and nobody else.

The panel refused strangers before this, but only after the TLS handshake, so
a stranger could still hold a connection open on it. The rule that drops them
first is built from the same RELAY_IP the panel reads, and loaded before every
start of the panel - so the two lists can never disagree.
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
TOOL = os.path.join(ROOT, "templates", "smartdns-api-guard")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


BASH = shutil.which("bash")
if BASH is None:
    print("bash not available - skipping")
    sys.exit(0)


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


tmp = tempfile.mkdtemp()


def run(relay_ip, *args, nft=None):
    etc = tempfile.mkdtemp(dir=tmp)
    with open(os.path.join(etc, "panel.env"), "w", newline="\n") as fh:
        fh.write("SYNC_SECRET=x\n" + ("RELAY_IP=%s\n" % relay_ip if relay_ip is not None else ""))
    env = dict(os.environ, SMARTDNS_ETC=posix(etc))
    if nft:
        env["SMARTDNS_NFT"] = posix(nft)
    return subprocess.run([BASH, posix(TOOL)] + list(args), capture_output=True,
                          text=True, timeout=60, env=env)


print("the rule")
check("it parses", subprocess.run([BASH, "-n", posix(TOOL)], capture_output=True).returncode == 0)
r = run("198.51.100.1,198.51.100.2", "--print")
check("every relay, and this machine itself, may reach 8443",
      "tcp dport 8443 ip saddr { 127.0.0.1, 198.51.100.1, 10.10.184.29, 198.51.100.2, 10.10.234.101 } accept" in r.stdout, r.stdout)
check("and anybody else is dropped, IPv6 included",
      r.stdout.index("accept") < r.stdout.index("tcp dport 8443 drop"), r.stdout)
check("it replaces itself rather than piling up",
      r.stdout.startswith("table inet smartdns_api\ndelete table inet smartdns_api\n"), r.stdout[:80])
inbound = r.stdout[r.stdout.index("chain input"):r.stdout.index("chain nginx_out")]
check("inbound, it touches no other port", set(re.findall(r"dport (\d+)", inbound)) == {"8443"})
out = r.stdout[r.stdout.index("chain nginx_out"):]
check("nginx may not proxy to this machine - a name resolving to it made nginx connect "
      "to itself until it ran out of memory",
      "type filter hook output" in out and "fib daddr type local reject with tcp reset" in out
      and "meta skuid { 33, 65534 }" in out, out)
check("  nor to a private address, the cloud's metadata service among them",
      "169.254.0.0/16" in out and "10.0.0.0/8" in out and "192.168.0.0/16" in out
      and "0.0.0.0/8" in out)
check("  on the ports it proxies only - its own upstreams, DoH and the rest, are elsewhere",
      "tcp dport { 80, 443, 1119, 4070 }" in out and "8453" not in out and "18119" not in out)
r = run("198.51.100.1, 1.2.3,abc,300.1.1.1", "--print")
check("a mistake in RELAY_IP is left out, and said",
      "{ 127.0.0.1, 198.51.100.1, 10.10.184.29 }" in r.stdout and "ignoring '1.2.3'" in r.stderr
      and "ignoring 'abc'" in r.stderr and "300.1.1.1" not in r.stdout, r.stdout + r.stderr)
r = run(None, "--print")
check("no relays at all leaves this machine only, and says so",
      "{ 127.0.0.1 }" in r.stdout and "no relays" in r.stderr, r.stdout + r.stderr)

print("loading it")
fake = os.path.join(tmp, "nft")
log = os.path.join(tmp, "nft.log")
with open(fake, "w", newline="\n") as fh:
    fh.write('#!/bin/bash\necho "nft $*" >> "%s"; cat >> "%s"\nexit ${FAKE_RC:-0}\n' % (posix(log), posix(log)))
os.chmod(fake, 0o755)
r = run("198.51.100.1", nft=fake)
got = open(log).read() if os.path.exists(log) else ""
check("it hands the rule to nft on stdin", r.returncode == 0 and "nft -f -" in got
      and "198.51.100.1" in got, got)
check("and says which addresses reach 8443", "port 8443 answers: 127.0.0.1, 198.51.100.1" in r.stdout, r.stdout)
r = run("198.51.100.1", nft=os.path.join(tmp, "no-such-nft"))
check("without nft it says so and does not fail - the panel must still start",
      r.returncode == 0 and "not installed" in r.stderr, r.stderr)

print("where it runs")
unit = open(os.path.join(ROOT, "templates", "smartdns-panel.service"), encoding="utf-8").read()
check("before every start of the panel, outside its sandbox, never stopping it",
      "ExecStartPre=-+/usr/local/bin/smartdns-api-guard" in unit)
check("  and before the panel itself", unit.index("ExecStartPre") < unit.index("ExecStart="))
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("the installer puts it on the exit",
      "payload SMARTDNS_API_GUARD > /usr/local/bin/smartdns-api-guard" in logic
      and logic.index("payload SMARTDNS_API_GUARD") < logic.index("install_payload PANEL_SERVICE"))
check("the exit gets nftables for it",
      re.search(r'WANT="nginx libnginx-mod-stream dnsutils curl python3 openssl nftables[ "]', logic) is not None)
check("the installer says whether it holds", "port 8443 answers the relays only" in logic)
check("uninstall takes the rule away", "nft delete table inet smartdns_api;" in logic)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
