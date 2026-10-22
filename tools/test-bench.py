#!/usr/bin/env python3
"""Timing the public resolvers the admin panel offers.

What has to hold: a resolver's time is the median of a few real DNS
questions, and one that does not answer says so; each relay times the list -
plus the operator's own pick - every ten minutes, in a thread of its own,
and again at once when the operator asks; the panel hands the list out and
keeps each relay's figures; the admin panel's card shows them per relay and
for the exit, the fastest in each column marked, and the times beside the
choices. The panel's list and the admin panel's are the same list.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:300]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (sync, panel, admin):
    m.log = lambda *a: None
panel.print = lambda *a, **k: None

print("the timing")
answers = {"1.1.1.1": [30, 12, 14], "9.9.9.9": [40, None, 44], "10.9.9.9": [None, None, None]}
seq = {}


def fake_probe(ip, **kw):
    i = seq.get(ip, 0)
    seq[ip] = i + 1
    return answers[ip][i % 3]


sync.dns_probe = fake_probe
got = sync.bench(["1.1.1.1", "9.9.9.9", "10.9.9.9"])
check("the median of three real questions, in milliseconds", got["1.1.1.1"] == 14, repr(got))
check("a lost question does not count against it", got["9.9.9.9"] == 44, repr(got))
check("and one that never answers says so", got["10.9.9.9"] is None)

print("the relay")


class Now:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


sync.threading.Thread = Now
timed = []
sync.bench = lambda ips: timed.append(list(ips)) or {ip: 20 for ip in ips}
want = {"ips": ["1.1.1.1", "9.9.9.9", "bad!"], "now": ""}
check("the list the panel sends is timed", sync.maybe_bench(want) and timed == [["1.1.1.1", "9.9.9.9"]],
      repr(timed))
check("its figures go up with the next sync",
      sync.BENCH_STATE["ms"] == {"1.1.1.1": 20, "9.9.9.9": 20})
check("not again within ten minutes", sync.maybe_bench(want) is False and len(timed) == 1)
check("unless the operator asks", sync.maybe_bench(dict(want, now="2026-09-25T10:00")) and len(timed) == 2)
sync.BENCH_STATE["at"] -= sync.BENCH_EVERY + 1
check("and again when ten minutes are up", sync.maybe_bench(dict(want, now="2026-09-25T10:00"))
      and len(timed) == 3)
src = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("the figures ride on the sync, the list comes back with its answer",
      'payload["resolver_bench"] = {"at": int(BENCH_STATE["at"]), "ms": BENCH_STATE["ms"]}' in src
      and 'maybe_bench(answer.get("bench"))' in src)

print("the panel")
tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
check("its list is the admin panel's list",
      panel.BENCH_RESOLVERS == [ip for _, a, b, _ in admin.RESOLVERS for ip in (a, b)])
store.set_setting("dns_upstream", "1.1.1.1 203.0.113.53")
w = panel.bench_wanted(store)
check("with the operator's own pick added when it is not on it",
      w["ips"][-1] == "203.0.113.53" and w["ips"].count("1.1.1.1") == 1 and w["now"] == "")
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("each relay's figures are kept, and the list goes out with every sync",
      'note_bench(self.store, who, body.get("resolver_bench"))' in psrc
      and psrc.count('"bench": bench_wanted(self.store),') == 2)
panel.note_bench(store, "198.51.100.9", {"at": 5, "ms": {"1.1.1.1": 7, "x": "junk"}})
check("  a node's the same way, junk dropped",
      json.loads(store.setting("resolver_bench:198.51.100.9"))
      == {"at": 5, "ms": {"1.1.1.1": 7, "x": None}})
panel.note_bench(store, "198.51.100.9", {"ms": "nonsense"})
check("  and nonsense leaves what was there",
      json.loads(store.setting("resolver_bench:198.51.100.9"))["at"] == 5)
store.run("DELETE FROM settings WHERE key = 'resolver_bench:198.51.100.9'")
check("a node times them and sends its figures, as a relay does",
      src.count('payload["resolver_bench"] = {"at": int(BENCH_STATE["at"]), "ms": '
                'BENCH_STATE["ms"]}') == 2 and src.count('maybe_bench(answer.get("bench"))') == 2)

print("the admin panel")
admin.DB = os.path.join(tmp, "panel.db")
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(admin.DB)
card = admin.upstream_card()
check("before anything is timed, it says so", "هنوز اندازه گرفته نشده" in card)
now = int(time.time())
store.set_setting("resolver_bench:198.51.100.7", json.dumps(
    {"at": now - 120, "ms": {"1.1.1.1": 38, "9.9.9.9": 21, "8.8.8.8": None, "203.0.113.53": 90}}))
admin.EXIT_BENCH.update(ms={"1.1.1.1": 2, "9.9.9.9": 5}, at=now)
card = admin.upstream_card()
check("a column for each relay and one for this machine",
      "رله 198.51.100.7" in card and "این سرور" in card)
check("the fastest in each column marked",
      "21 ms ✓" in card and "2 ms ✓" in card and "38 ms ✓" not in card)
check("one that did not answer said so", "جواب نداد" in card)
check("the operator's own pick has a row of its own", "203.0.113.53" in card and "90 ms" in card)
check("the relays' times beside the choices", "9.9.9.9 — 21 ms" in card and "1.1.1.1 — 38 ms" in card)
check("and a way to time them again", "action='/p/dns-bench'" in card)
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.7\nNODE_IP=198.51.100.2\n")
store.set_setting("resolver_bench:198.51.100.2", json.dumps(
    {"at": now - 60, "ms": {"1.1.1.1": 4, "9.9.9.9": 9}}))
store.set_setting("upstream_state:198.51.100.7", json.dumps(
    {"applied": ["1.1.1.1", "203.0.113.53"], "tested": {"1.1.1.1": 38}}))
store.set_setting("node_resolvers:198.51.100.2", json.dumps(
    {"using": "1.1.1.1", "error": "203.0.113.53 از این سرور جواب نداد"}))
card = admin.upstream_card()
check("a column for each node too, after the relays",
      "نود 198.51.100.2" in card
      and card.index("رله 198.51.100.7") < card.index("نود 198.51.100.2") < card.index("این سرور"))
check("  the choices still labelled with the relays' times", "1.1.1.1 — 38 ms" in card)
check("each node in the table of what each server uses, with what went wrong",
      "نود <code>198.51.100.2</code>" in card and "رله <code>198.51.100.7</code>" in card
      and "203.0.113.53 از این سرور جواب نداد" in card)
store.set_setting("node_resolvers:198.51.100.5", json.dumps({"using": "1.1.1.1", "error": ""}))
check("  but not one no longer joined", "198.51.100.5" not in admin.upstream_card())


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]
r = Rec()
admin.EXIT_BENCH["wake"].clear()
admin.Admin.action(r, "dns-bench", {})
check("asking again tells the relays and wakes this machine's timing",
      admin.STORE.one("SELECT value FROM settings WHERE key = 'bench_now'")
      and admin.EXIT_BENCH["wake"].is_set() and "settings" in r.to)
asrc = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("this machine times them in a thread of its own",
      "threading.Thread(target=bench_loop, daemon=True).start()" in asrc)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
