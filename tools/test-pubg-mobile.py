#!/usr/bin/env python3
"""PUBG Mobile in the templates: there to choose, off until chosen.

The game's own names answer an Iranian address directly, so on most lines
routing them buys nothing and costs the game's updates in transit. Some
operators may block them, though. So the category is opt-in: the default plan
leaves it alone, and an operator ticks it in a template for the customers of
an operator that needs it.

Two things make that true. The names are not in domains.txt - the shared list
the relay's main resolver, and so the default plan, is served from. And the
group is opt-in, which every template but a ticking one honours.

Only names the game uses on 443 are there. The relay carries 80 and 443, so
its telemetry (8013) and login (8085/8086) would break if routed.
"""
import importlib.util
import json
import os
import shutil
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
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


import importlib.machinery  # noqa: E402

# The three the game itself talks to, and three more from the platform around
# it - the shop and Tencent's international API - each checked on 443 with a
# certificate of its own before being let in. The group is off by default
# either way, so nothing here routes until an operator asks for it.
GAME = {"gcloudcs.com", "igamecj.com", "pubgmobile.com",
        "cloudpvp.com", "igamebuy.com", "intlgame.com"}
NOT_443 = {"tdatamaster.com": "telemetry, port 8013",
           "proximabeta.com": "login, ports 8085/8086"}

cat = json.load(open(os.path.join(ROOT, "domains", "services.json"),
                     encoding="utf-8"))["services"]
listed = [l.strip() for l in open(os.path.join(ROOT, "domains", "domains.txt"),
                                  encoding="utf-8")
          if l.strip() and not l.startswith("#")]
by_key = {s["key"]: s for s in cat}

print("the category")
svc = by_key.get("pubgmobile")
check("PUBG Mobile is a service of its own", svc is not None)
groups = (svc or {}).get("groups", [])
check("with the game's names", {d for g in groups for d in g["domains"]} == GAME)
check("off until an operator turns it on", groups and all(g.get("opt_in") is True for g in groups))
check("saying why, and when to turn it on", all(g.get("note") for g in groups))
check("not locked - turning it on is a real choice", not any(g.get("locked") for g in groups))
order = [s["key"] for s in cat]
check("listed with the games", order.index("pubgmobile") < order.index("othergames"))
check("PUBG on PC stays where it was",
      "pubg.com" in {d for g in by_key["othergames"]["groups"] for d in g["domains"]})

print("kept out of the shared list")
check("none of its names is in domains.txt - the default plan would route them",
      not (GAME & set(listed)), str(sorted(GAME & set(listed))))

print("what each template does with it")
panel = load(os.path.join(ROOT, "templates", "smartdns-panel"), "panel")
tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
default = store.ensure_default_template(cat)["id"]
check("the default plan does not route it",
      not (GAME & set(store.routed_for(default, cat))))
check("and resolves it normally", GAME <= set(store.bypass_for(default, cat)))
plain = store.run("INSERT INTO templates (name, is_default, created_at)"
                  " VALUES ('بدون پابجی', 0, ?)", (panel.now(),)).lastrowid
check("a template that has not ticked it leaves it direct",
      GAME <= set(store.bypass_for(plain, cat))
      and not (GAME & set(store.routed_for(plain, cat))))
ticked = store.run("INSERT INTO templates (name, is_default, created_at)"
                   " VALUES ('با پابجی', 0, ?)", (panel.now(),)).lastrowid
store.run("INSERT INTO template_services (template_id, service_key, group_key)"
          " VALUES (?, 'pubgmobile', 'main')", (ticked,))
check("a template that ticks it routes it",
      GAME <= set(store.routed_for(ticked, cat))
      and not (GAME & set(store.bypass_for(ticked, cat))))
store.run("INSERT INTO template_domains_off (template_id, domain)"
          " VALUES (?, 'gcloudcs.com')", (ticked,))
check("and one name can still be left out of it - the downloads, say",
      "gcloudcs.com" not in store.routed_for(ticked, cat)
      and {"igamecj.com", "pubgmobile.com"} <= set(store.routed_for(ticked, cat)))
shutil.rmtree(tmp, ignore_errors=True)

print("left out on purpose")
everything = set(listed) | {d for s in cat for g in s["groups"] for d in g["domains"]}
for name, why in NOT_443.items():
    check("%s is not in the catalogue (%s)" % (name, why),
          not any(d == name or d.endswith("." + name) for d in everything))

print("the classifier agrees")
classify = load(os.path.join(HERE, "classify-services.py"), "classify")
check("it lists the names", set(classify.EXPLICIT.get("pubgmobile.main", [])) == GAME)
check("as opt-in", "pubgmobile.main" in classify.OPT_IN)
check("under PUBG Mobile, ahead of the other games",
      [b[0] for b in classify.BRANDS].index("pubgmobile")
      < [b[0] for b in classify.BRANDS].index("othergames"))

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
