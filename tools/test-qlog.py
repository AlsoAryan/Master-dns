#!/usr/bin/env python3
"""The DNS report a customer switches on for support.

What has to hold: nothing is kept for anybody who did not switch it on;
switched on, it lasts an hour and each name an hour from its last use, and
switching it off throws away what was kept at once. Plain DNS comes from
smartdns-watch run for exactly those addresses, DoH and DoT from
smartdns-doh for exactly those accounts - both saying where each answer sent
the customer the same way. The panel says why, from the customer's own
template, and both the customer and the operator see it.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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


watch = load("templates/smartdns-watch", "watch")
doh = load("templates/smartdns-doh", "doh")
sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (doh, sync, panel, admin):
    m.log = lambda *a: None
panel.print = lambda *a, **k: None
tmp = tempfile.mkdtemp()
RELAY = "198.51.100.7"


def dns(qid, name, answer=None, rcode=0):
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack("!HH", 1, 1)
    if answer is None:
        return struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q
    an = (b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(answer)) if answer else b""
    return struct.pack("!HHHHHH", qid, 0x8180 | rcode, 1, 1 if answer else 0, 0, 0) + q + an


def udp(src, dst, sport, dport, payload):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28 + len(payload), 0, 0, 64, 17, 0,
                     socket.inet_aton(src), socket.inet_aton(dst))
    return ip + struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


# ---------------------------------------------------------------- smartdns-watch --json
print("plain DNS, off the wire")
lines = []
w = watch.Watcher({RELAY}, {}, {"203.0.113.5"}, out=lines.append, clock=lambda: 1000.0,
                  as_json=True)
for i in range(3):
    w.feed(udp("203.0.113.5", RELAY, 5000 + i, 53, dns(i, "store.steampowered.com")))
    w.feed(udp(RELAY, "203.0.113.5", 53, 5000 + i, dns(i, "store.steampowered.com", RELAY)))
w.feed(udp("203.0.113.9", RELAY, 6000, 53, dns(9, "example.org")))
w.feed(udp(RELAY, "203.0.113.9", 53, 6000, dns(9, "example.org", "93.184.216.34")))
got = [json.loads(l) for l in lines]
check("every answer to the addresses asked for is a JSON line, repeats included",
      len(got) == 3 and all(g == {"t": 1000, "ip": "203.0.113.5",
                                  "name": "store.steampowered.com", "v": "via relay"}
                            for g in got), repr(lines))
check("and nobody else's", not any("example.org" in l for l in lines))
check("--json wants addresses", watch.main(["--json"]) == 2 and watch.main(["--json", "ali"]) == 2)

# ---------------------------------------------------------------- smartdns-doh
print("DoH and DoT")
state = os.path.join(tmp, "doh.json")
with open(state, "w") as fh:
    json.dump({"tokens": {}, "qlog_uids": [7], "qlog_ips": ["203.0.113.5"], "self": [RELAY]}, fh)
doh.STATE_NOW = doh.State(state)
doh.STATS = doh.Stats()
check("the answer is read the same way smartdns-watch reads it",
      doh.verdict(dns(1, "a.b", RELAY), {RELAY}) == "via relay"
      and doh.verdict(dns(1, "a.b", "93.184.216.34"), {RELAY}) == "direct 93.184.216.34"
      and doh.verdict(dns(1, "a.b", "10.10.34.35"), {RELAY}) == "filtered in Iran"
      and doh.verdict(dns(1, "a.b", "", 3), {RELAY}) == "no such name")
check("only the accounts and addresses that asked are kept",
      doh.STATE_NOW.logging("doh", "7") and not doh.STATE_NOW.logging("doh", "8")
      and doh.STATE_NOW.logging("dot", "203.0.113.5") and not doh.STATE_NOW.logging("dot", "203.0.113.6"))
doh.keep_for_report("doh:7", dns(1, "www.ea.com"), dns(1, "www.ea.com", RELAY), doh.STATE_NOW)
doh.keep_for_report("doh:7", dns(2, "www.ea.com"), dns(2, "www.ea.com", RELAY), doh.STATE_NOW)
got = doh.STATS.take()["qlog"]
check("and counted by name and where it went",
      list(got) == ["doh:7"] and list(got["doh:7"]) == ["www.ea.com\tvia relay"]
      and got["doh:7"]["www.ea.com\tvia relay"][2] == 2, repr(got))

# ---------------------------------------------------------------- the relay agent
print("the relay agent")
sync.qlog_add("203.0.113.5", "store.steampowered.com", "via relay", 1000)
sync.qlog_add("203.0.113.5", "store.steampowered.com", "via relay", 1060)
sdir = os.path.join(tmp, "stats")
os.makedirs(sdir)
with open(os.path.join(sdir, "1.json"), "w") as fh:
    json.dump({"doh": {}, "dot": {}, "qlog": {"doh:7": {"www.ea.com\tvia relay": [900, 950, 2]}}}, fh)
sync.DOH_STATS_DIR = sdir
sync.take_doh_stats()
sent = sync.take_qlog()
check("plain and encrypted come together, counted",
      sent == {"203.0.113.5": [["store.steampowered.com", "via relay", 1000, 1060, 2]],
               "doh:7": [["www.ea.com", "via relay", 900, 950, 2]]}, repr(sent))
sync.qlog_add("203.0.113.5", "store.steampowered.com", "via relay", 1100)
again = sync.take_qlog()
check("what was not delivered is sent again, with what came since",
      again["203.0.113.5"] == [["store.steampowered.com", "via relay", 1000, 1100, 3]])
sync.QLOG["unsent"] = {}
check("and once delivered, nothing is left", sync.take_qlog() == {})

started = []


class Proc:
    def __init__(self, cmd, **kw):
        started.append(cmd)
        self.stdout = iter(())
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, t=None):
        return 0


sync.subprocess.Popen = Proc
sync.WATCH = __file__          # anything that exists
sync.qlog_watch(["203.0.113.6", "203.0.113.5"])
check("smartdns-watch is run for exactly those addresses",
      started == [[sync.WATCH, "--json", "203.0.113.5", "203.0.113.6"]], repr(started))
sync.qlog_watch(["203.0.113.5", "203.0.113.6"])
check("and not again while the same are wanted", len(started) == 1)
old = sync.QLOG["proc"]
sync.qlog_watch([])
check("and stopped when nobody wants it", not old.alive and sync.QLOG["proc"] is None)

# ---------------------------------------------------------------- the panel
print("the panel")
store = panel.Store(os.path.join(tmp, "panel.db"))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)", (panel.now(),))
panel.DEFAULT_TEMPLATE[0] = 1
catalogue = [{"key": "steam", "label": "Steam", "groups": [{"key": "main", "domains": ["steampowered.com"]}]},
             {"key": "pubg", "label": "PUBG", "groups": [{"key": "shop", "opt_in": True,
                                                         "domains": ["igamebuy.com"]}]}]
store.run("INSERT INTO custom_domains (domain, added_at) VALUES ('mygame.example', ?)", (panel.now(),)) \
    if "added_at" in [r[1] for r in store.db.execute("PRAGMA table_info(custom_domains)")] else \
    store.run("INSERT INTO custom_domains (domain) VALUES ('mygame.example')")
u = store.create_user(111, "ali", "علی")
v = store.create_user(222, "sara", "سارا")
panel.register_ip(store, u["id"], "203.0.113.5")
panel.register_ip(store, v["id"], "203.0.113.6")
import time
T = int(time.time()) - 120
report = {"203.0.113.5": [["store.steampowered.com", "via relay", T, T + 60, 2],
                          ["cdn.igamebuy.com", "direct 1.2.3.4", T, T, 1],
                          ["example.org", "direct 93.184.216.34", T, T, 1],
                          ["x.mygame.example", "via relay", T, T, 1]],
          "203.0.113.6": [["example.org", "direct 93.184.216.34", T, T, 1]]}
panel.record_qlog(store, report, catalogue)
check("nothing is kept for somebody who did not switch it on",
      store.one("SELECT count(*) c FROM query_log")["c"] == 0)
panel.qlog_on(store, u["id"], True)
want = panel.qlog_wanted(store)
check("switched on, the relays are told which address and which account",
      want == {"uids": [u["id"]], "ips": ["203.0.113.5"]}, repr(want))
panel.record_qlog(store, report, catalogue)
rows = {r["name"]: r for r in panel.qlog_rows(store, u["id"])}
check("then it is kept, for that customer alone",
      set(rows) == {"store.steampowered.com", "cdn.igamebuy.com", "example.org", "x.mygame.example"}
      and not panel.qlog_rows(store, v["id"]))
check("with why, from their template",
      rows["store.steampowered.com"]["reason"] == "routed:Steam"
      and rows["cdn.igamebuy.com"]["reason"].startswith(("bypass:PUBG", "unticked:PUBG"))
      and rows["example.org"]["reason"] == "outside:"
      and rows["x.mygame.example"]["reason"].startswith("routed:"), repr({k: r["reason"] for k, r in rows.items()}))
panel.record_qlog(store, {"doh:%d" % u["id"]: [["store.steampowered.com", "via relay", T + 90, T + 90, 5]]}, catalogue)
r = [x for x in panel.qlog_rows(store, u["id"]) if x["name"] == "store.steampowered.com"][0]
check("DoH adds to the same name, and says it came that way", r["hits"] == 7 and r["via"] == "doh")
store.run("UPDATE query_log SET last_at = ? WHERE name = 'example.org'",
          ((datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat(timespec="seconds"),))
panel.prune_usage(store)
left = {x["name"] for x in panel.qlog_rows(store, u["id"])}
check("a name not asked for an hour is gone, the rest stay",
      "example.org" not in left and "store.steampowered.com" in left, repr(left))
store.run("UPDATE users SET qlog_until = ? WHERE id = ?",
          ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"), u["id"]))
panel.prune_usage(store)
check("the switch goes off by itself after an hour",
      store.one("SELECT qlog_until FROM users WHERE id = ?", (u["id"],))["qlog_until"] is None
      and panel.qlog_wanted(store) == {"uids": [], "ips": []})
before = [(x["name"], x["hits"]) for x in panel.qlog_rows(store, u["id"])]
panel.record_qlog(store, report, catalogue)
check("and nothing new is kept after",
      [(x["name"], x["hits"]) for x in panel.qlog_rows(store, u["id"])] == before)
panel.qlog_on(store, u["id"], True)
panel.qlog_on(store, u["id"], False)
check("switching it off throws away what was kept, at once",
      not panel.qlog_rows(store, u["id"]))

# ---------------------------------------------------------------- the pages
print("the pages")
panel.qlog_on(store, u["id"], True)
panel.record_qlog(store, report, catalogue)
rows = panel.qlog_rows(store, u["id"])
info = {"ip": "203.0.113.5", "qlog_until": store.one("SELECT qlog_until FROM users WHERE id = ?",
                                                     (u["id"],))["qlog_until"], "qlog": rows}
box = sync.qlog_box(info)
check("the customer's page shows what was kept, where it went and why",
      "store.steampowered.com" in box and "✅ از رله" in box and "Steam" in box
      and "↪ مستقیم" in box and "در فهرست سرویس‌ها نیست" in box, box[-600:])
check("with the switch to turn it off", "value='0'" in box and "خاموش کردن" in box)
off = sync.qlog_box({"ip": "203.0.113.5", "qlog_until": None, "qlog": []})
check("off, it offers to turn it on and explains who sees it and for how long",
      "value='1'" in off and "یک ساعت" in off and "۲۴ ساعت" not in off
      and "پشتیبانی هم همین را می‌بیند" in off)
check("an hour, in the panel too", panel.QLOG_HOURS == 1)
psrc = open(os.path.join(ROOT, "templates/smartdns-panel"), encoding="utf-8").read()
sync_part = psrc[psrc.index('if self.path == "/sync":'):psrc.index("PRUNED[0] = time.time()")]
check("and it is thrown away on every sync, not with the hourly pruning",
      "prune_qlog(self.store)" in sync_part)
check("no address registered, no switch", sync.qlog_box({"ip": None}) == "")
admin.DB = os.path.join(tmp, "panel.db")
admin.STORE = admin.Store(admin.DB)
card = admin.qlog_card(admin.STORE.one("SELECT * FROM users WHERE id = ?", (u["id"],)))
check("the operator sees the same, on the customer's page",
      "store.steampowered.com" in card and "روشن تا" in card and "DNS" in card)
card = admin.qlog_card(admin.STORE.one("SELECT * FROM users WHERE id = ?", (v["id"],)))
check("and for somebody who did not switch it on, how to ask them to",
      "خاموش است" in card and "store.steampowered.com" not in card)
psrc = open(os.path.join(ROOT, "templates/smartdns-panel"), encoding="utf-8").read()
check("the switch is behind the customer's session",
      'if self.path == "/user-qlog":' in psrc and "qlog_on(self.store, user[\"id\"], on)" in psrc)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
