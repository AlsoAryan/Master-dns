#!/usr/bin/env python3
"""A group added to a service later reaches the templates that already route the service.

Templates name the groups they route, by key. Call of Duty gained a group
(cod-https) in an upgrade; a template that routed Activision's names the day
before - a PS5 one has no use for Battle.net's downloads, so it ticks only the
main group - has to get it, once, or its customers stay on "Checking for update"
while the editor shows nothing wrong. An operator who then unticks it must not
have it put back at the next start.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_loader(
    "panel", importlib.machinery.SourceFileLoader(
        "panel", os.path.join(HERE, "..", "templates", "smartdns-panel")))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


CATALOGUE = [
    {"key": "blizzard", "label": "Blizzard", "groups": [
        {"key": "main", "label": "همه", "domains": ["battle.net", "callofduty.com"]},
        {"key": "cod-https", "label": "COD", "join": True, "domains": ["rex-gvs.prod.demonware.net"]},
        {"key": "download", "label": "دانلود", "domains": ["cdn.blizzard.com"]}]},
    {"key": "custom", "label": "دامنه‌های دلخواه", "groups": [
        {"key": "main", "label": "همه", "domains": []}]},
]

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
default = store.ensure_default_template(CATALOGUE)["id"]


def template(name, groups):
    tid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES (?, 0, ?)",
                    (name, panel.now())).lastrowid
    for g in groups:
        store.run("INSERT INTO template_services (template_id, service_key, group_key) VALUES (?, 'blizzard', ?)", (tid, g))
    return tid


covers = template("everything of it", ["main", "download"])
ps5 = template("a PS5 one: the main group only", ["main"])
nothing = template("none of Blizzard", [])
before = store.template_groups(covers)

print("a template that routes part of the service")
check("it did not have the new group", ("blizzard", "cod-https") not in before)
n = panel.follow_catalogue_joins(store, CATALOGUE)
check("it gets the group", ("blizzard", "cod-https") in store.template_groups(covers))
check("what it already had is untouched", before <= store.template_groups(covers))
check("so does the one with only the main group", ("blizzard", "cod-https") in store.template_groups(ps5))
check("two templates joined", n == 2, str(n))

print("a template that routes none of it")
check("is left alone", not store.template_groups(nothing))
check("the default already routes everything that is not opt-in, new group included",
      ("blizzard", "cod-https") in store.template_groups(default))

print("once")
store.run("DELETE FROM template_services WHERE template_id = ? AND group_key = 'cod-https'", (covers,))
check("the operator unticks it", ("blizzard", "cod-https") not in store.template_groups(covers))
n = panel.follow_catalogue_joins(store, CATALOGUE)
check("the next start does not put it back", n == 0 and ("blizzard", "cod-https") not in store.template_groups(covers))

print("not marked, not joined")
CATALOGUE[0]["groups"][1].pop("join")
store.run("DELETE FROM catalogue_joined")
check("a group that is not marked `join` is never added behind anybody's back",
      panel.follow_catalogue_joins(store, CATALOGUE) == 0)

print("the catalogue")
import json
cat = json.load(open(os.path.join(HERE, "..", "domains", "services.json"), encoding="utf-8"))["services"]
grp = next(g for s in cat if s["key"] == "blizzard" for g in s["groups"] if g["key"] == "cod-https")
check("cod-https is marked to join", grp.get("join") is True)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
