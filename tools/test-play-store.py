#!/usr/bin/env python3
"""Google Play, through the relay.

The store itself, its API and its images were routed already; its app
downloads (gvt1.com) and its icons and screenshots (ggpht.com) were not, so
the store opened and then could not show or install anything. Both are now in
Google's group.

What stays out: Google's notification service. mtalk.google.com runs on port
5228, and the relay carries 80 and 443 only - routed, notifications would
stop. So google.com is never routed whole, only its named hosts.
"""
import importlib.util
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
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


PLAY = {"play.google.com", "googleapis.com", "googleusercontent.com",
        "gstatic.com", "dl.google.com", "gvt1.com", "ggpht.com"}

cat = json.load(open(os.path.join(ROOT, "domains", "services.json"),
                     encoding="utf-8"))["services"]
google = {d for s in cat if s["key"] == "google"
          for g in s["groups"] for d in g["domains"]}
listed = [l.strip() for l in open(os.path.join(ROOT, "domains", "domains.txt"),
                                  encoding="utf-8")
          if l.strip() and not l.startswith("#")]
everything = set(listed) | {d for s in cat for g in s["groups"] for d in g["domains"]}

print("Play Store's names")
check("are all in Google's group", PLAY <= google, str(sorted(PLAY - google)))
check("and on the relay's own list", PLAY <= set(listed), str(sorted(PLAY - set(listed))))
check("which stays sorted", listed == sorted(listed))
check("routed by default", all(not g.get("opt_in") for s in cat if s["key"] == "google"
                               for g in s["groups"]))

print("notifications stay direct")
check("mtalk.google.com is not routed",
      not any("mtalk.google.com" == d or "mtalk.google.com".endswith("." + d)
              for d in everything),
      str(sorted(d for d in everything if "mtalk.google.com".endswith("." + d))))

print("the classifier agrees")
spec = importlib.util.spec_from_file_location(
    "classify", os.path.join(HERE, "classify-services.py"))
classify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classify)
for d in ("gvt1.com", "ggpht.com"):
    check("%s would be filed under Google" % d,
          classify.find(d) == ("google", "main"), str(classify.find(d)))

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
