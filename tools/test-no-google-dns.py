#!/usr/bin/env python3
"""The relay does not ask Google.

8.8.8.8 passes the asker's subnet on (ECS). A service that refuses Iran in its
DNS then sees the relay's Iranian subnet and refuses it - Tencent's games get
0.0.0.1, an address that leads nowhere. dnsmasq takes turns between its
upstreams, so with Google among them the refusal came and went, which is the
worst kind of fault to chase. 1.1.1.1 and 9.9.9.9 send no subnet.

epic-pin is the exception, on purpose: it asks for addresses that answer from
here, and the subnet helps it find them. It does not answer customers.
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []
GOOGLE = re.compile(r"\b8\.8\.(8\.8|4\.4)\b")


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def text(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


logic = text("tools/installer-logic.sh")
line = next((l for l in logic.splitlines() if "no-resolv" in l and "server=" in l), "")
print("the relay's upstreams")
check("the installer writes them", bool(line))
check("without Google", not GOOGLE.search(line), line.strip())
check("with two that send no subnet",
      "server=1.1.1.1" in line and "server=9.9.9.9" in line, line.strip())

print("the names that are left alone")
bypass = text("common/bypass.conf")
rules = re.findall(r"^server=/([^/]+)/(\S+)$", bypass, re.M)
check("none of them is asked of Google", not any(GOOGLE.search(r) for _, r in rules),
      str([n for n, r in rules if GOOGLE.search(r)]))
# "#" is dnsmasq's "the usual resolvers": upstream.conf, which the admin
# panel picks (test-upstream.py) and which defaults to the two above.
check("each asks the usual resolvers",
      rules and all(r == "#" for _, r in rules),
      str([n for n, r in rules if r != "#"]))
cli = text("templates/smartdns")
check("and smartdns bypass adds new ones the same way",
      "server=/%s/#" in cli
      and not GOOGLE.search(cli[cli.index("  bypass)"):cli.index("  unbypass)")]))

print("the exception")
check("epic-pin still asks Google, on purpose",
      GOOGLE.search(text("templates/epic-pin")) is not None)

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
