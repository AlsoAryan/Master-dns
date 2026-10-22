#!/usr/bin/env python3
"""Split WireGuard beside the DNS.

What has to hold: a config reaches one relay, and through it only that relay
- its DNS, and the services it routes - so it is never a VPN: the relay
locks forwarding in and out of wg0 whatever ip_forward says, and refuses to
run WireGuard at all when the lock does not load. A config's keys are real
X25519 keys, made here without WireGuard or a library. Each config takes one
of the plan's devices and has an address of its own, registered beside the
addresses customers type, so the allowed list, the template, the counters and
the gate treat it the same; the two rows come and go together, and a typed
address never pushes a config out. The relay is told its configs, adds and
drops them without touching the others, and says when and from where each
connected - from which the admin's limit on addresses a day is kept. The
admin turns it on, makes, shows and deletes configs; the installer puts
wireguard-tools on relays and uninstall takes everything away.
"""
import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

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
admin = load("templates/smartdns-admin", "admin")
panel.log = sync.log = admin.log = lambda *a: None

RELAY, OTHER = "198.51.100.1", "198.51.100.2"

print("the keys")
k = bytes.fromhex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
u = bytes.fromhex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
check("X25519 as RFC 7748 has it", panel.x25519(k, u).hex()
      == "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")
alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
check("  and a public key from a private one",
      panel.x25519(alice, (9).to_bytes(32, "little")).hex()
      == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
check("  the admin panel's copy agrees", admin.x25519(k, u) == panel.x25519(k, u))
priv, pub = panel.wg_keypair()
check("a new pair is two WireGuard keys, the public one the private one's",
      bool(panel.WG_KEY_RE.fullmatch(priv)) and bool(panel.WG_KEY_RE.fullmatch(pub))
      and panel.x25519(base64.b64decode(priv), (9).to_bytes(32, "little"))
      == base64.b64decode(pub) and panel.wg_keypair()[0] != priv)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
now = panel.now()
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)", (now,))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('بازی', 0, ?)", (now,))
store.run("INSERT INTO users (username, created_at, status, max_ips, expires_at, template_id)"
          " VALUES ('ali', ?, 'active', 2, '2099-01-01T00:00:00+00:00', 2)", (now,))
store.run("INSERT INTO users (username, created_at, status, max_ips) VALUES ('sara', ?,"
          " 'active', 1)", (now,))
ALI, SARA = 1, 2
relays = [RELAY]

print("making a config")
r = panel.create_wg_device(store, ALI, relays)
check("refused while WireGuard is off", r.get("error") == "wg_off", str(r))
store.set_setting("wg_on", "1")
r = panel.create_wg_device(store, ALI, relays)
check("refused while no relay has said its key", r.get("error") == "wg_not_ready", str(r))
SERVER_PRIV, SERVER_PUB = panel.wg_keypair()
store.set_setting("wg_pub:" + RELAY, SERVER_PUB)
r = panel.create_wg_device(store, ALI, relays)
check("made once a relay is ready", r.get("ok") and r.get("device_id") == 1, str(r))
dev = store.one("SELECT * FROM wg_devices WHERE id = 1")
check("  on that relay, at the first address after the relay's own",
      dev["relay"] == RELAY and dev["address"] == "10.66.0.2")
row = store.one("SELECT * FROM ips WHERE ip = '10.66.0.2'")
check("  its address registered beside typed ones, marked",
      row is not None and row["user_id"] == ALI and row["wg"] == 1)
conf = r["config"]
check("the config: its own key and address, the relay's DNS",
      "PrivateKey = " in conf and "Address = 10.66.0.2/32" in conf
      and "DNS = %s" % RELAY in conf, conf)
check("  only the relay through the tunnel - not a VPN",
      "AllowedIPs = %s/32" % RELAY in conf and "0.0.0.0/0" not in conf
      and "Endpoint = %s:51820" % RELAY in conf and "PublicKey = " + SERVER_PUB in conf)
key_in_conf = conf.split("PrivateKey = ")[1].split("\n")[0]
check("  its private key matches the public one the relay is given",
      panel.x25519(base64.b64decode(key_in_conf), (9).to_bytes(32, "little"))
      == base64.b64decode(dev["public_key"]))
store.set_setting("wg_port", "443")
check("  the admin's port", "Endpoint = %s:443" % RELAY in panel.wg_config(store, 1))
store.set_setting("wg_port", "51820")

store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (2, '10.66.0.3', ?)", (now,))
r = panel.create_wg_device(store, SARA, relays)
check("an address already in ips, however it got there, is not given again",
      r.get("ok") and store.one("SELECT address FROM wg_devices WHERE id = ?",
                                (r["device_id"],))["address"] == "10.66.0.4", str(r))
panel.delete_wg_device(store, SARA, r["device_id"])
store.run("DELETE FROM ips WHERE ip = '10.66.0.3'")

print("devices and relays")
check("a typed address list leaves configs out",
      [x["ip"] for x in store.user_ips(ALI)] == []
      and [x["ip"] for x in store.user_ips(ALI, wg=True)] == ["10.66.0.2"])
panel.register_ip(store, ALI, "203.0.113.5")
check("an address for the DNS and a config both: neither takes the other's place",
      sorted(x["ip"] for x in store.user_ips(ALI, wg=True)) == ["10.66.0.2", "203.0.113.5"])
r = panel.create_wg_device(store, ALI, relays)
check("one config for each relay, however many devices: a second here is refused",
      r.get("error") == "wg_have" and "همهٔ سرورها" in r["message"], str(r))
check("  asked for by name, too", panel.create_wg_device(store, ALI, relays, relay=RELAY)
      .get("error") == "wg_have")
panel.register_ip(store, ALI, "203.0.113.6")
panel.register_ip(store, ALI, "203.0.113.7")
check("addresses up to the devices, the oldest replaced, never a config",
      sorted(x["ip"] for x in store.user_ips(ALI)) == ["203.0.113.6", "203.0.113.7"]
      and len(panel.wg_devices_of(store, ALI)) == 1)
with store.lock:
    panel.set_devices(store.db, ALI, 1)
    store.db.commit()
check("fewer devices: fewer addresses; the config stays - it is no device",
      [x["ip"] for x in store.user_ips(ALI)] == ["203.0.113.7"]
      and [d["address"] for d in panel.wg_devices_of(store, ALI)] == ["10.66.0.2"])
store.run("UPDATE users SET max_ips = 2 WHERE id = ?", (ALI,))
r = panel.create_wg_device(store, SARA, relays)
check("one device: its address and the relay's config", r.get("ok") and panel.register_ip(
    store, SARA, "203.0.113.9").get("ok"), str(r))
store.run("UPDATE users SET status = 'suspended' WHERE id = ?", (SARA,))
check("a blocked account gets none",
      panel.create_wg_device(store, SARA, relays).get("error") == "suspended")
store.run("UPDATE users SET status = 'active' WHERE id = ?", (SARA,))

print("going together")
store.run("DELETE FROM ips WHERE ip = ?", (store.one(
    "SELECT address FROM wg_devices WHERE user_id = ?", (SARA,))["address"],))
check("its address gone, the config goes", store.one(
    "SELECT 1 FROM wg_devices WHERE user_id = ?", (SARA,)) is None)
panel.create_wg_device(store, SARA, relays)
sid = store.one("SELECT id, address FROM wg_devices WHERE user_id = ?", (SARA,))
check("  a freed address is given again", sid["address"] == "10.66.0.3")
panel.delete_wg_device(store, SARA, sid["id"])
check("the config deleted, its address goes", store.one(
    "SELECT 1 FROM ips WHERE ip = ?", (sid["address"],)) is None)
check("  somebody else's is not theirs to delete",
      panel.delete_wg_device(store, SARA, 1).get("error") == "wg_not_found")
panel.create_wg_device(store, SARA, relays)

print("like any registered address")
by_ip, profiles = store.profiles({}, 1)
check("on the allowed list, with its customer's template",
      by_ip.get("10.66.0.2", {}).get("uid") == ALI and by_ip["10.66.0.2"]["tid"] == 2)
before = store.one("SELECT used_bytes FROM users WHERE id = ?", (ALI,))["used_bytes"]
store.fold_counters(RELAY, {"10.66.0.2": 500000})
check("its traffic counted to its customer",
      store.one("SELECT used_bytes FROM users WHERE id = ?", (ALI,))["used_bytes"]
      == before + 500000)

print("what a relay is told")
spec = panel.wg_spec(store, RELAY)
check("on, the port, and the configs that are its",
      spec["on"] and spec["port"] == 51820
      and {p["addr"] for p in spec["peers"]} == {"10.66.0.2", "10.66.0.3"})
check("  none for another relay", panel.wg_spec(store, OTHER)["peers"] == [])
store.run("UPDATE users SET status = 'expired' WHERE id = ?", (ALI,))
check("  a customer whose plan ended stays on it, to be shown the gate",
      any(p["addr"] == "10.66.0.2" for p in panel.wg_spec(store, RELAY)["peers"])
      and "10.66.0.2" not in store.profiles({}, 1)[0])
store.run("UPDATE users SET status = 'active' WHERE id = ?", (ALI,))
store.set_setting("wg_on", "0")
check("  off, and nothing else", panel.wg_spec(store, RELAY) == {"on": False})
store.set_setting("wg_on", "1")
src = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("every relay's sync carries it, and records what the relay says",
      '"wg": wg_spec(self.store, who)' in src
      and 'record_wg_state(self.store, who, body.get("wg_state"))' in src)

print("what a relay says")
ali_pub = store.one("SELECT public_key FROM wg_devices WHERE id = 1")["public_key"]
hs = int(datetime.now(timezone.utc).timestamp())
panel.record_wg_state(store, RELAY, {"on": True, "pub": SERVER_PUB, "port": 51820,
                                     "peers": {ali_pub: {"hs": hs, "ep": "5.209.1.1"}}})
d = store.one("SELECT * FROM wg_devices WHERE id = 1")
check("when and from where it last connected", d["last_endpoint"] == "5.209.1.1"
      and d["last_handshake"].startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d")))
panel.record_wg_state(store, OTHER, {"pub": "AAAA" * 11})
check("a relay's key kept only when it is one",
      store.setting("wg_pub:" + OTHER) == "" and store.setting("wg_pub:" + RELAY) == SERVER_PUB)
store.set_setting("wg_ips_per_day", "2")
for ep in ("5.209.1.2", "5.209.1.2"):
    panel.record_wg_state(store, RELAY, {"pub": SERVER_PUB,
                                         "peers": {ali_pub: {"hs": hs, "ep": ep}}})
check("two addresses today, the admin's limit: still on", store.one(
    "SELECT blocked_day FROM wg_devices WHERE id = 1")["blocked_day"] is None)
panel.record_wg_state(store, RELAY, {"pub": SERVER_PUB,
                                     "peers": {ali_pub: {"hs": hs, "ep": "151.234.1.1"}}})
check("a third: off until tomorrow", store.one(
    "SELECT blocked_day FROM wg_devices WHERE id = 1")["blocked_day"] == panel.wg_day()
      and all(p["addr"] != "10.66.0.2" for p in panel.wg_spec(store, RELAY)["peers"]))
store.run("UPDATE wg_devices SET blocked_day = '2000-01-01' WHERE id = 1")
check("  tomorrow, back", any(p["addr"] == "10.66.0.2"
                              for p in panel.wg_spec(store, RELAY)["peers"]))
store.set_setting("wg_ips_per_day", "")

print("on the relay")
work = tempfile.mkdtemp()
sync.WG_BIN = sys.executable          # something that exists, for os.access
sync.WG_DIR = work
sync.WG_CONF = os.path.join(work, "wg0.conf")
sync.WG_NFT = os.path.join(work, "42-smartdns-wg.conf")
sync.wg_server_key = lambda: (SERVER_PRIV, SERVER_PUB)
sync.CFG = {"SELF_IP": RELAY}
sync.is_single = lambda: False


class R:
    def __init__(self, out="", rc=0, err=""):
        self.stdout, self.returncode, self.stderr = out, rc, err


calls, state = [], {"link": False, "nft_rc": 0}
DUMP = "%s\t%s\t51820\toff\n%s\t(none)\t5.209.9.9:4242\t10.66.0.2/32\t%d\t100\t200\t25\n" % (
    SERVER_PRIV, SERVER_PUB, ali_pub, hs)


def fake_sh(*args):
    calls.append(args)
    if args[:3] == ("ip", "link", "show"):
        return R(rc=0 if state["link"] else 1)
    if args[:3] == ("ip", "link", "add"):
        state["link"] = True
    if args[:3] == ("ip", "link", "del"):
        state["link"] = False
    if args[:1] == ("/usr/sbin/nft",) and args[1:2] == ("-f",):
        return R(rc=state["nft_rc"], err="nft broke" if state["nft_rc"] else "")
    if args[1:3] == ("show", "wg0"):
        return R(DUMP)
    return R()


sync.sh = fake_sh
q = contextlib.redirect_stdout(io.StringIO())
peers = panel.wg_spec(store, RELAY)
with q:
    sync.apply_wg(peers)
ran = [" ".join(map(str, c)) for c in calls]
check("on: wg0 made, its configs given, its address up",
      any(c.startswith("ip link add wg0 type wireguard") for c in ran)
      and any(c.endswith("syncconf wg0 " + sync.WG_CONF) for c in ran)
      and any(c.startswith("ip addr replace 10.66.0.1/16 dev wg0") for c in ran))
text = open(sync.WG_CONF, encoding="utf-8").read()
check("  each config by its key, at its own address only",
      "PublicKey = %s\nAllowedIPs = 10.66.0.2/32" % ali_pub in text
      and "ListenPort = 51820" in text and text.count("[Peer]") == 2)
nft_text = open(sync.WG_NFT, encoding="utf-8").read()
check("  and the lock: nothing forwarded into or out of wg0, whatever ip_forward says",
      'iifname "wg0" drop' in nft_text and 'oifname "wg0" drop' in nft_text
      and "hook forward" in nft_text and any(c.endswith("-f " + sync.WG_NFT) for c in ran))
check("  what it says back: its key, its port, who connected from where",
      sync.WG_REPORT["state"] == {"on": True, "pub": SERVER_PUB, "port": 51820,
                                  "peers": {ali_pub: {"hs": hs, "ep": "5.209.9.9"}}},
      str(sync.WG_REPORT["state"]))
calls.clear()
with q:
    sync.apply_wg(peers)
check("nothing new: the configs left alone, nobody cut off",
      not any("syncconf" in " ".join(map(str, c)) for c in calls))
calls.clear()
with q:
    sync.apply_wg(dict(peers, peers=peers["peers"][:1]))
check("one fewer: given again, by syncconf, which keeps the others connected",
      any("syncconf" in " ".join(map(str, c)) for c in calls)
      and open(sync.WG_CONF, encoding="utf-8").read().count("[Peer]") == 1)
check("bad entries from the panel are left out", sync.clean_wg_peers([
    {"pub": "x", "addr": "10.66.0.9"}, {"pub": ali_pub, "addr": "8.8.8.8"},
    {"pub": ali_pub, "addr": "10.66.0.1"}, "junk", {"pub": ali_pub, "addr": "10.66.0.9"}])
      == {ali_pub: "10.66.0.9"})
os.unlink(sync.WG_NFT)
state["nft_rc"] = 1
with q:
    sync.apply_wg(peers)
check("the lock not loaded: no WireGuard at all, and the panel told why",
      state["link"] is False and sync.WG_REPORT["state"]["on"] is False
      and "قفل" in sync.WG_REPORT["state"]["error"])
state["nft_rc"] = 0
with q:
    sync.apply_wg(peers)
calls.clear()
with q:
    sync.apply_wg({"on": False})
ran = [" ".join(map(str, c)) for c in calls]
check("off: wg0, its lock and its config gone",
      "ip link del wg0" in ran and any(c.endswith("delete table inet smartdns_wg") for c in ran)
      and not os.path.exists(sync.WG_CONF) and sync.WG_REPORT["state"] == {"on": False})
calls.clear()
with q:
    sync.apply_wg(None)
check("a panel too old to say: left as it is", calls == [])
sync.is_single = lambda: True
with q:
    sync.apply_wg(peers)
check("a single machine has no WireGuard", state["link"] is False)
sync.is_single = lambda: False
ssrc = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("every sync applies it and reports it",
      'apply_wg(answer.get("wg"))' in ssrc and 'payload["wg_state"] = WG_REPORT["state"]' in ssrc)

print("the admin panel")
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
open(admin.PANEL_ENV, "w").write("RELAY_IP=%s\n" % RELAY)
admin.REQ.admin = None


class Rec:
    path = "/p/settings"
    headers = {}

    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]
rec = Rec()
SAVE = {"on": ["1"], "port": ["443"], "ips": ["3"], "mtu": [""], "keepalive": ["25"],
        "unused": [""], "name": ["master-dns"], "endpoint": ["ip"], "relay": [RELAY],
        "plans_mode": ["all"]}
admin.Admin.action(rec, "wg-save", SAVE)
check("turned on, its port and its limit saved",
      store.setting("wg_on") == "1" and store.setting("wg_port") == "443"
      and store.setting("wg_ips_per_day") == "3" and rec.to.startswith("wireguard?m=")
      and "m=!" not in rec.to, rec.to)
check("  every relay ticked is all of them, one added later too; all plans is all",
      store.setting("wg_relays") == "" and store.setting("wg_plans") == "")
for bad, field in ((dict(SAVE, port=["0"]), "port"), (dict(SAVE, mtu=["900"]), "mtu"),
                   (dict(SAVE, keepalive=["500"]), "keepalive"),
                   (dict(SAVE, name=["my tunnel name too long"]), "name"),
                   (dict(SAVE, unused=["soon"]), "unused"), (dict(SAVE, ips=["x"]), "ips")):
    admin.Admin.action(rec, "wg-save", bad)
    check("  a %s that is none refused, nothing changed" % field,
          rec.to.startswith("wireguard?m=!") and store.setting("wg_port") == "443"
          and store.setting("wg_name") == "master-dns", rec.to)
tab = admin.wg_admin_page("p")
check("its own tab: the switch, every choice, each relay's state",
      "action='/p/wg-save'" in tab and "value='443'" in tab and "value='3'" in tab
      and RELAY in tab and "آماده" in tab and "name='mtu'" in tab and "name='keepalive'" in tab
      and "name='endpoint'" in tab and "name='unused'" in tab and "name='plans_mode'" in tab)
check("  and how many configs, how many online now",
      "<div class='l'>کانفیگ</div>" in tab and "آنلاین الان" in tab)
settings_page = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("  a tab in the menu, the settings section's, not on the settings page any more",
      '("wireguard", "وایرگارد")' in settings_page and admin.PAGE_SECTION["wireguard"] == "settings"
      and "wg_card(" not in settings_page)
store.run("INSERT INTO users (username, created_at, status, max_ips) VALUES ('reza', ?,"
          " 'active', 1)", (now,))
admin.Admin.action(rec, "user-wg-new", {"id": ["3"]})
made = store.one("SELECT * FROM wg_devices WHERE user_id = 3")
check("a config made for a customer", made is not None and "m=" in rec.to
      and store.one("SELECT wg FROM ips WHERE ip = ?", (made["address"],))["wg"] == 1)
admin.Admin.action(rec, "user-wg-new", {"id": ["3"]})
check("  a second refused: one device", "m=!" in rec.to and store.one(
    "SELECT count(*) c FROM wg_devices WHERE user_id = 3")["c"] == 1)
page = admin.wg_page("/p/wg?u=3")
check("its page: the config to copy and download, and delete",
      "Address = %s/32" % made["address"] in page and "Endpoint = %s:443" % RELAY in page
      and "/p/wg-conf/%d" % made["id"] in page and "user-wg-del" in page)
cell = admin.devices_cell("p", store.one("SELECT * FROM users WHERE id = 1"))
check("the users table: addresses counted against devices, configs apart on their page",
      "📱 1/2" in cell and "203.0.113.7" in cell and "10.66.0.2" not in cell
      and "/p/wg?u=1" in cell and "🛡 کانفیگ وایرگارد: 1 از 1" in cell, cell)


class Wire:
    def __init__(self):
        self.sent, self.wfile = [], io.BytesIO()

    def send_response(self, code):
        self.sent.append(("status", code))

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass


wire = Wire()
admin.Admin.send(wire, admin.wg_config(made), 200, {
    "Content-Type": "text/plain; charset=utf-8",
    "Content-Disposition": "attachment; filename=master-dns-%d.conf" % made["id"]})
types = [v for k, v in wire.sent if k == "Content-Type"]
check("the file downloads as a file: its own type, once, not a page's beside it",
      types == ["text/plain; charset=utf-8"]
      and ("Content-Disposition", "attachment; filename=master-dns-%d.conf" % made["id"])
      in wire.sent and wire.wfile.getvalue().startswith(b"[Interface]"), str(types))
wire = Wire()
admin.Admin.send(wire, "<p>hi</p>")
check("  a page is still a page", [v for k, v in wire.sent if k == "Content-Type"]
      == ["text/html; charset=utf-8"])
admin.Admin.action(rec, "user-wg-del", {"id": ["3"], "dev": [str(made["id"])]})
check("deleted, its address with it", store.one(
    "SELECT 1 FROM wg_devices WHERE user_id = 3") is None and store.one(
    "SELECT 1 FROM ips WHERE ip = ?", (made["address"],)) is None)
check("the page and its actions are the users section's; the switch the settings'",
      admin.PAGE_SECTION["wg"] == "users" and admin.ACTION_SECTION["user-wg-new"] == "users"
      and admin.ACTION_SECTION["user-wg-del"] == "users"
      and admin.ACTION_SECTION["wg-save"] == "settings")

print("the admin's choices")
store.run("UPDATE users SET max_ips = 3 WHERE id = 3")
admin.Admin.action(rec, "user-wg-new", {"id": ["3"]})
dev = store.one("SELECT * FROM wg_devices WHERE user_id = 3 ORDER BY id DESC LIMIT 1")
store.set_setting("panel_url:" + RELAY, "https://relay.example.com:8443/")
admin.Admin.action(rec, "wg-save", dict(SAVE, endpoint=["domain"], mtu=["1280"],
                                         keepalive=["0"], name=["MyBrand"]))
conf = panel.wg_config(store, dev["id"])
check("the relay's domain in place of its address, the MTU, no keepalive",
      "Endpoint = relay.example.com:443" in conf and "MTU = 1280" in conf
      and "PersistentKeepalive" not in conf and "AllowedIPs = %s/32" % RELAY in conf
      and "DNS = %s" % RELAY in conf, conf)
check("  the admin panel's config is the panel's, word for word",
      admin.wg_config(store.one("SELECT * FROM wg_devices WHERE id = ?", (dev["id"],))) == conf)
check("  the file named for the brand, a valid tunnel name",
      admin.wg_file_name(dev["id"]) == "MyBrand-%d.conf" % dev["id"]
      and panel.wg_file_name("master-dns-long", 1234) == "master-dns-1234.conf"
      and len(panel.wg_file_name("abcdefghijklmno", 99999)) - 5 <= 15)
store.set_setting("panel_url:" + RELAY, "")
check("  a relay with no domain keeps its address",
      "Endpoint = %s:443" % RELAY in panel.wg_config(store, dev["id"]))
store.set_setting("panel_url:" + RELAY, "https://relay.example.com:8443/")
admin.Admin.action(rec, "wg-save", dict(SAVE, relay=[]))
check("a relay left unticked: off there, and no new config on it",
      store.setting("wg_relays") == "[]" and panel.wg_spec(store, RELAY) == {"on": False}
      and panel.create_wg_device(store, 3, [RELAY]).get("error") == "wg_not_ready")
admin.Admin.action(rec, "wg-save", SAVE)
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ویژه', 1, 30, 0, 9, ?)", (now,))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ارزان', 1, 30, 0, 1, ?)", (now,))
admin.Admin.action(rec, "wg-save", dict(SAVE, plans_mode=["some"], plan=["1"]))
store.run("UPDATE users SET plan_id = 2 WHERE id = 3")
check("only some plans: refused on another, by the panel and the admin panel alike",
      panel.create_wg_device(store, 3, [RELAY]).get("error") == "wg_plan"
      and admin.wg_create(3).startswith("!"))
store.run("UPDATE users SET plan_id = 1 WHERE id = 3")
store.set_setting("wg_pub:" + OTHER, panel.wg_keypair()[1])
check("  and made on one of them, on another relay", panel.create_wg_device(
    store, 3, [RELAY, OTHER]).get("ok"))
admin.Admin.action(rec, "wg-save", SAVE)
store.set_setting("wg_unused_days", "30")
old = "2000-01-01T00:00:00+00:00"
store.run("UPDATE wg_devices SET created_at = ?, last_handshake = NULL WHERE id = ?",
          (old, dev["id"]))
fresh = store.one("SELECT id FROM wg_devices WHERE user_id = 3 AND id != ? ORDER BY id DESC",
                  (dev["id"],))["id"]
store.run("UPDATE wg_devices SET created_at = ?, last_handshake = ? WHERE id = ?",
          (old, panel.now(), fresh))
n = panel.wg_prune_unused(store)
check("unused for the admin's days: deleted, its device free; one that connected kept",
      n == 1 and store.one("SELECT 1 FROM wg_devices WHERE id = ?", (dev["id"],)) is None
      and store.one("SELECT 1 FROM ips WHERE ip = ?", (dev["address"],)) is None
      and store.one("SELECT 1 FROM wg_devices WHERE id = ?", (fresh,)) is not None)
store.set_setting("wg_unused_days", "")
check("  off: nothing deleted", panel.wg_prune_unused(store) == 0)
check("  every sync prunes", "wg_prune_unused(self.store)" in src)

print("the installer")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("relays get wireguard-tools",
      'WANT="nginx libnginx-mod-stream dnsmasq coturn nftables dnsutils python3 curl '
      'wireguard-tools"' in logic)
check("uninstall takes wg0, its lock and its key away",
      "ip link del wg0" in logic and "nft delete table inet smartdns_wg" in logic
      and "rm -rf /etc/smart-dns/wg" in logic and "42-smartdns-wg.conf" in logic)
check("  and offers wireguard-tools among what it can remove",
      "python3-certbot-dns-cloudflare wireguard-tools qrencode $packages" in logic)

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(work, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
