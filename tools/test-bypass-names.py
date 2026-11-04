#!/usr/bin/env python3
"""0.1.2: two names a console needs direct, from reports on Irancell.

What has to hold: the PS5's STUN server (us.np.stun.playstation.net, which is not
under np.playstation.net) is bypassed, and the three Rockstar zones its sign-in
uses are too - while rockstargames.com itself stays routed; the dnsmasq lines and the
catalogue say the same thing; the new Rockstar group is locked like EA's and Call of
Duty's, so no tick can route it; and no template routes any of the four.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import tempfile

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


spec = importlib.util.spec_from_loader("panel", importlib.machinery.SourceFileLoader(
    "panel", os.path.join(ROOT, "templates", "smartdns-panel")))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
panel.log = lambda *a, **k: None

STUN = "stun.playstation.net"
ROCKSTAR = ["ros.rockstargames.com", "cloud.rockstargames.com", "prod-locator-cloud.rockstargames.com"]
conf = read("common/bypass.conf")
lines = set(re.findall(r"^server=/([^/]+)/#$", conf, re.M))
cat = json.loads(read("domains/services.json"))["services"]
bypass = next(s for s in cat if s["key"] == "bypass")
groups = {g["key"]: g for g in bypass["groups"]}

print("the PlayStation's STUN server")
check("it is bypassed in dnsmasq", STUN in lines)
check("and named in the catalogue, in the PlayStation group", STUN in groups["playstation"]["domains"])
check("the console's own name is under it", "us.np.stun.playstation.net".endswith("." + STUN))
check("and not under the line that was there, which is why it was missed",
      not "us.np.stun.playstation.net".endswith(".np.playstation.net"))
check("the note says what a tick would break", "NAT" in groups["playstation"]["note"])
check("the comment no longer calls ps5.np the STUN server",
      "ps5.np - STUN" not in conf and "us.np.stun.playstation.net" in conf)

print("Rockstar's sign-in")
check("the three zones are bypassed in dnsmasq", all(d in lines for d in ROCKSTAR), sorted(lines))
check("and are one group in the catalogue", sorted(groups["rockstar"]["domains"]) == sorted(ROCKSTAR))
check("the site itself is not bypassed", "rockstargames.com" not in lines
      and not any(d == "rockstargames.com" for d in groups["rockstar"]["domains"]))
check("it is still in the routed Rockstar group, so the site and Social Club go through the exit",
      "rockstargames.com" in next(s for s in cat if s["key"] == "rockstar")["groups"][0]["domains"])
check("the group is locked and opt-in, with its reason",
      groups["rockstar"].get("locked") is True and groups["rockstar"].get("opt_in") is True
      and "0x50060190" in groups["rockstar"]["note"])
check("the three names are longer matches than the zone they sit under, so dnsmasq picks them",
      all(d.endswith(".rockstargames.com") for d in ROCKSTAR))

print("what a template does with them")
store = panel.Store(os.path.join(tempfile.mkdtemp(), "panel.db"))
default = store.ensure_default_template(cat)["id"]
bypassed = set(store.bypass_for(default, cat))
routed = set(store.routed_for(default, cat))
check("the default template bypasses all four", {STUN, *ROCKSTAR} <= bypassed, sorted({STUN, *ROCKSTAR} - bypassed))
check("and routes none of them", not ({STUN, *ROCKSTAR} & routed))
check("while rockstargames.com is still routed", "rockstargames.com" in routed)
tid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('t', 0, ?)", (panel.now(),)).lastrowid
store.run("INSERT INTO template_services (template_id, service_key, group_key) VALUES (?, 'bypass', 'rockstar')", (tid,))
check("a tick on the locked group does not route it",
      set(ROCKSTAR) <= set(store.bypass_for(tid, cat)) and not (set(ROCKSTAR) & set(store.routed_for(tid, cat))))

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
