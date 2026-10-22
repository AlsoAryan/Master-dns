#!/usr/bin/env python3
"""A relay that reports but never hears back.

What has to hold: when a relay's reports reach the panel but the panel's
answers are lost on the way back - the road between them lets a little
through one way and not the other - new customers and settings never reach
it, and nothing else shows it: its reports keep coming. The relay says in
each report how long it has gone without an answer, counted from its own
start; past five minutes the operator is told once, and told again when
answers come through. A relay from before, which does not say, is left be.
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
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("templates/smartdns-panel", "panel")
panel.log = lambda *a: None
told = []
panel.emit_admin = lambda store, kind, data: told.append((kind, data["text"]))
RELAY = "198.51.100.1"

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))

print("on the panel")
panel.record_unanswered(store, RELAY, 40)
check("answered: nothing said", told == [])
panel.record_unanswered(store, RELAY, 290)
check("  nor just under five minutes", told == [])
panel.record_unanswered(store, RELAY, 420)
check("past five minutes: the operator told, with the relay and how long",
      len(told) == 1 and told[0][0] == "server.down" and RELAY in told[0][1]
      and "7 دقیقه" in told[0][1] and "جواب پنل" in told[0][1])
panel.record_unanswered(store, RELAY, 900)
check("  once, not at every report", len(told) == 1)
panel.record_unanswered(store, RELAY, 30)
check("answers through again: told so", len(told) == 2 and told[1][0] == "server.up")
panel.record_unanswered(store, "198.51.100.2", None)
panel.record_unanswered(store, "198.51.100.2", True)
check("a relay from before, which does not say: left be",
      len(told) == 2 and not store.setting("alert_state:unanswered:198.51.100.2"))
store.set_setting("single:198.51.100.3", "1")
panel.record_unanswered(store, "198.51.100.3", 600)
check("a single server named as one", "تک‌سرور" in told[-1][1])
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("every report is looked at",
      'record_unanswered(self.store, who, body.get("unanswered"))' in psrc)

print("on the relay")
ssrc = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
at = ssrc.index('payload["unanswered"]')
check("the report says how long, counted from the start, and the clock reset on an answer",
      'SYNC_HEARD = {"at": time.time()}' in ssrc
      and ssrc.index('answer = post("/sync", payload)', at)
      < ssrc.index('SYNC_HEARD["at"] = time.time()', at))

store.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
