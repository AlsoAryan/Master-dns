#!/usr/bin/env python3
"""Call of Duty through the service.

What has to hold: Demonware - Call of Duty's network layer, whose lobby and
STUN are not on 443 - resolves directly, on the main resolver and in every
template, never routed: routed, MW III and Warzone stop at "Networking failed
to start" (HUENEME - NEGEV). And `smartdns bypass` on a name that is routed
itself says it cannot work, rather than claiming it did.
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:400]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


print("Demonware")
check("the main resolver asks for it directly",
      "server=/demonware.net/#" in read("common/bypass.conf").splitlines())
listed = [l.strip() for l in read("domains/domains.txt").splitlines()]
check("it is not on the shared routed list, nor any name under it",
      not [l for l in listed if l == "demonware.net" or l.endswith(".demonware.net")])
services = json.loads(read("domains/services.json"))["services"]
routed = [d for s in services if s["key"] != "bypass" for g in s["groups"] for d in g["domains"]
          if d == "demonware.net" or d.endswith(".demonware.net")]
check("nor in any service a template can route", not routed, routed)
group = next(g for s in services if s["key"] == "bypass" for g in s["groups"]
             if g["key"] == "demonware")
check("every template bypasses it, the group being locked",
      group["domains"] == ["demonware.net"] and group.get("locked"))
cod = next(g for s in services for g in s["groups"] if "callofduty.com" in g["domains"])
check("while Call of Duty's sites and accounts stay routed",
      "activision.com" in cod["domains"] and "callofduty.com" in cod["domains"])

print("smartdns bypass")
cli = read("templates/smartdns")
check("a routed name is refused, with what to do instead",
      'elif grep -qxF "address=/$d/$IP" "$CONF"; then' in cli
      and "is routed itself" in cli)
body = cli[cli.index("  bypass)"):cli.index("  unbypass)")]
check("  and dnsmasq is restarted only when a bypass was added",
      '[ -n "$changed" ] || exit 0' in body and body.count("changed=1") == 1
      and body.index('[ -n "$changed" ] || exit 0') < body.index("systemctl restart dnsmasq"))

print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
