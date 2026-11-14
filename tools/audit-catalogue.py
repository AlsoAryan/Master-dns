#!/usr/bin/env python3
"""audit-catalogue.py - an offline check of the services, the games and the bypass rules.

It needs no network and no root, and it never changes a file. It reads domains/services.json,
domains/games.json, domains/domains.txt and common/bypass.conf, and says what would go wrong on
a relay before a customer finds it:

  errors    the catalogue is broken: a bad name, a game that needs a group that does not exist,
            a routed name that a game cannot work through, a bypass that dnsmasq will ignore.
  warnings  odd but harmless: the same name in two services, a bypass with nothing above it.

Exit status 1 when there is an error, so it can sit in the test loop:

    python3 tools/audit-catalogue.py            # the report
    python3 tools/audit-catalogue.py --quiet    # only errors, for a script

What it cannot know: whether a name answers from Iran, and on which port. That is what
`smartdns-watch` on a live relay is for (docs/troubleshooting.md). So the list of names that
must never be routed below is the record of what real captures found, and it grows with them.
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
QUIET = "--quiet" in sys.argv

# Names that were found, in a real capture, not to survive the relay: a port other than 443, or
# a sign-in that has to come from the address the game plays from. Each must resolve directly.
# (name, why)
MUST_BE_DIRECT = [
    ("gosredirector.ea.com", "EA redirector, TCP 42130/42230"),
    ("blazeredirector.ea.com", "EA redirector for FC 26; sign-in address must match the servers'"),
    ("blaze.ea.com", "EA game servers, TCP 15000-15100"),
    ("gameservices.ea.com", "EA QoS and match stats, TCP 10010/11000"),
    ("tnt-ea.com", "EA realtime messaging, TCP 8095"),
    ("lsg.prod.demonware.net", "Call of Duty lobby, not on 443"),
    ("genesis.stun.prod.demonware.net", "Call of Duty STUN, not on 443"),
    ("loginservice.prod.demonware.net", "Call of Duty sign-in, not on 443"),
    ("us.np.stun.playstation.net", "PlayStation STUN: coturn answers 420 to a PS5"),
    ("ros.rockstargames.com", "GTA Online sign-in, must come from the player's address"),
    ("cloud.rockstargames.com", "GTA Online cloud saves, same reason"),
    ("prod-locator-cloud.rockstargames.com", "GTA Online locator, same reason"),
    ("steamserver.net", "Steam connection managers, ports 27018-27024"),
]
# Names that must go through the relay: plain HTTPS on 443 that answers an Iranian address 403.
MUST_BE_ROUTED = [
    ("user-consent.prod.demonware.net", "Call of Duty: 'Checking for update' (HILLCAT)"),
    ("objectstore-publishers-cod.prod.demonware.net", "Call of Duty: 'Checking for update'"),
    ("rex-gvs.prod.demonware.net", "Call of Duty: 'Checking for update'"),
]

HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$")
errors, warnings = [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


services = json.loads(load("domains/services.json"))["services"]
games = json.loads(load("domains/games.json"))["games"]
master = [l.strip() for l in load("domains/domains.txt").splitlines() if l.strip() and not l.startswith("#")]

# ---------------------------------------------------------------- the services
routed, bypassed, where, optin = set(), set(), {}, set()
seen_service = set()
for svc in services:
    if svc["key"] in seen_service:
        err("service key twice: %s" % svc["key"])
    seen_service.add(svc["key"])
    seen_group = set()
    for grp in svc["groups"]:
        gk = "%s/%s" % (svc["key"], grp["key"])
        if grp["key"] in seen_group:
            err("group key twice in %s: %s" % (svc["key"], grp["key"]))
        seen_group.add(grp["key"])
        if not grp.get("domains"):
            warn("%s has no domains" % gk)
        for d in grp.get("domains", []):
            if d != d.strip().lower() or d.endswith("."):
                err("%s: %r is not lower-case, trimmed and without a trailing dot" % (gk, d))
                continue
            if not HOST.match(d):
                err("%s: %r is not a host name" % (gk, d))
                continue
            (bypassed if svc["key"] == "bypass" else routed).add(d)
            if grp.get("opt_in"):
                optin.add(d)
            where.setdefault(d, []).append(gk)

for d, groups in sorted(where.items()):
    if len(groups) > 1 and len({g.split("/")[0] for g in groups}) > 1:
        warn("%s is in more than one service: %s" % (d, ", ".join(groups)))
    elif len(groups) > 1:
        warn("%s is twice in %s" % (d, groups[0].split("/")[0]))

# ---------------------------------------------------------------- the master list
mset = set(master)
if len(mset) != len(master):
    dup = sorted({d for d in master if master.count(d) > 1})
    err("domains.txt has a name twice: %s" % ", ".join(dup[:10]))
for d in master:
    if not HOST.match(d):
        err("domains.txt: %r is not a host name" % d)
if master != sorted(master):
    warn("domains.txt is not sorted")
# The master list is what the default template routes; an opt-in group is off by design.
for d in sorted(routed - mset - optin):
    warn("routed in services.json but not in domains.txt: %s" % d)

# ---------------------------------------------------------------- the games
group_keys = {"%s/%s" % (s["key"], g["key"]) for s in services for g in s["groups"]}
seen_game = set()
for g in games:
    if g["key"] in seen_game:
        err("game key twice: %s" % g["key"])
    seen_game.add(g["key"])
    for need in g.get("needs", []):
        if need not in group_keys:
            err("game %s needs %s, which does not exist" % (g["key"], need))
    if not g.get("needs") and not g.get("names_row"):
        warn("game %s needs nothing, so ticking it does nothing" % g["key"])

# ---------------------------------------------------------------- the bypass file
conf = load("common/bypass.conf")
for line in conf.splitlines():
    m = re.match(r"^server=/([^/]+)/#\s*$", line.strip())
    if m:
        bypassed.add(m.group(1))
    elif line.strip().startswith("server=/") or line.strip().startswith("address=/"):
        if not re.match(r"^(address|server)=/[^/]+/[^/]*$", line.strip()):
            err("bypass.conf: a line dnsmasq may not read: %r" % line.strip())


def verdict(name):
    """What dnsmasq does with `name`: the longest rule wins, and on a tie address= beats server=."""
    parts = name.split(".")
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        if suffix in routed:
            return "routed", suffix
        if suffix in bypassed:
            return "direct", suffix
    return "direct", None


for d in sorted(routed & bypassed):
    err("%s is routed and bypassed at once; address= wins, so the bypass does nothing" % d)
for name, why in MUST_BE_DIRECT:
    how, rule = verdict(name)
    if how == "routed":
        err("%s would be routed (by %s); it must resolve directly - %s" % (name, rule, why))
for name, why in MUST_BE_ROUTED:
    how, rule = verdict(name)
    if how != "routed":
        err("%s would go direct; it must be routed - %s" % (name, why))

# ---------------------------------------------------------------- the report
if not QUIET:
    print("services %d, groups %d, routed names %d, bypassed names %d, games %d, master list %d" % (
        len(services), len(group_keys), len(routed), len(bypassed), len(games), len(master)))
    for w in warnings:
        print("  warn  " + w)
for e in errors:
    print("  ERROR " + e)
if not errors and not QUIET:
    print("no errors" + (" (%d warning%s)" % (len(warnings), "" if len(warnings) == 1 else "s") if warnings else ""))
sys.exit(1 if errors else 0)
