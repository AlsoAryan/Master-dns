#!/usr/bin/env python3
"""WireGuard bound to the customer's registered addresses.

What has to hold: with the admin's tick on - the default - a config works
only from its customer's registered addresses, as the DNS does. The panel
tells each relay the addresses of every config's customer; the relay lets
only those reach WireGuard's port, so a config handed to somebody on another
connection never connects; and a config used from another customer's
registered address is caught at the next sync - a handshake of the last
few minutes, not the old address wg still holds - and that address kept off
WireGuard for ten minutes, put back if the table is made anew. Ticked off,
WireGuard is open to any address, as before.
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
sync = load("templates/smartdns-sync", "sync")
panel.log = sync.log = lambda *a: None
RELAY = "198.51.100.1"

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
now = panel.now()
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)", (now,))
for name in ("ali", "sara"):
    store.run("INSERT INTO users (username, created_at, status, max_ips) VALUES (?, ?, 'active', 2)",
              (name, now))
store.set_setting("wg_on", "1")
store.set_setting("wg_pub:" + RELAY, panel.wg_keypair()[1])
panel.create_wg_device(store, 1, [RELAY])
panel.create_wg_device(store, 2, [RELAY])
panel.register_ip(store, 1, "5.209.1.1")
panel.register_ip(store, 1, "80.191.1.1")
panel.register_ip(store, 2, "151.234.1.1")
keys = {r["user_id"]: r["public_key"] for r in store.q("SELECT * FROM wg_devices")}

print("what a relay is told")
spec = panel.wg_spec(store, RELAY)
by = {p["pub"]: p for p in spec["peers"]}
check("bound by default, each config with its customer's registered addresses",
      spec["bind"] is True and by[keys[1]]["ips"] == ["5.209.1.1", "80.191.1.1"]
      and by[keys[2]]["ips"] == ["151.234.1.1"])
check("  and only the addresses they typed, never a config's own",
      all(not ip.startswith("10.66.") for p in spec["peers"] for ip in p["ips"]))
store.set_setting("wg_bind_ip", "0")
spec_off = panel.wg_spec(store, RELAY)
check("ticked off: not bound, no addresses sent", spec_off["bind"] is False
      and all("ips" not in p for p in spec_off["peers"]))
store.set_setting("wg_bind_ip", "")

print("on the relay")
text = sync.wg_nft_text(51820, True, {"5.209.1.1", "80.191.1.1", "151.234.1.1"})
check("only the registered addresses reach WireGuard's port",
      "udp dport 51820 ip saddr != @ok drop" in text
      and "elements = { 151.234.1.1, 5.209.1.1, 80.191.1.1 }" in text)
check("  and none kept off", "udp dport 51820 ip saddr @deny drop" in text
      and "flags timeout" in text)
check("  the forwarding lock as always", 'iifname "wg0" drop' in text
      and 'oifname "wg0" drop' in text)
empty = sync.wg_nft_text(51820, True, set())
check("  no config's customer has an address: nobody reaches it",
      "ip saddr != @ok drop" in empty and "elements" not in empty)
check("not bound: the port open to all, the lock still there",
      "@ok" not in sync.wg_nft_text(51820, False) and "hook forward" in sync.wg_nft_text(51820))
peer_ips = sync.wg_peer_ips(spec["peers"] + [{"pub": "x", "ips": ["1.1.1.1"]},
                                             {"pub": keys[2], "ips": ["junk"]}])
check("the addresses read back by config, junk left out",
      peer_ips[keys[1]] == {"5.209.1.1", "80.191.1.1"} and "x" not in peer_ips)

calls = []


class R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


sync.sh = lambda *a: calls.append(a) or R()
sync.WG_DENY.clear()
peer_ips = sync.wg_peer_ips(spec["peers"])
seen = {keys[1]: {"hs": 990, "ep": "80.191.1.1"}, keys[2]: {"hs": 990, "ep": "151.234.1.1"}}
check("each on its customer's address: nothing done",
      sync.wg_police(peer_ips, seen, 1000.0) == [] and calls == [])
seen[keys[2]] = {"hs": 600, "ep": "5.209.1.1"}
check("an old handshake from somebody else's address - the address wg still holds after"
      " the config is gone: nobody kept off", sync.wg_police(peer_ips, seen, 1000.0) == []
      and calls == [] and not sync.WG_DENY)
seen[keys[2]] = {"hs": 990, "ep": "5.209.1.1"}
caught = sync.wg_police(peer_ips, seen, 1000.0)
ran = [" ".join(map(str, c)) for c in calls]
check("sara's config used from ali's address: that address kept off for ten minutes",
      caught == ["5.209.1.1"] and sync.WG_DENY["5.209.1.1"] == 1600.0
      and any("add element inet smartdns_wg deny { 5.209.1.1 timeout 600s }" in c for c in ran))
calls.clear()
check("  not twice while it lasts", sync.wg_police(peer_ips, seen, 1100.0) == [] and calls == [])
check("  and forgotten after, not kept off again for the handshake now old",
      sync.wg_police(peer_ips, seen, 1700.0) == []
      and "5.209.1.1" not in sync.WG_DENY)
ssrc = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("every sync polices, and puts the kept-off back when the table is made anew",
      "wg_police(peer_ips, seen)" in ssrc
      and 'nft("add", "element", "inet", WG_TABLE, "deny",' in ssrc
      and ssrc.count('"{ %s timeout %ds }"') == 2)

print("the admin panel and the customer")
asrc = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("the admin's tick, on by default",
      "name='bind' value='1'" in asrc and '"wg_bind_ip": "1" if one("bind") == "1" else "0",' in asrc
      and 'wg_setting("wg_bind_ip") != "0"' in asrc)
view = panel.wg_view(store, store.one("SELECT * FROM users WHERE id = 1"), [RELAY])
check("the customer is told whether it is bound", view["bind"] is True)

store.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
