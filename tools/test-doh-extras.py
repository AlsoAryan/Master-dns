#!/usr/bin/env python3
"""The smaller DoH pieces: a new address, the rate, the counts, and "your DNS
reaches us".

What has to hold: a customer can replace their personal address, from the
page, the bot or the API, and the old one stops; each customer is held to a
rate on DoH and on DoT, and one customer's flood does not stop another's;
smartdns-doh counts queries per customer and never names, hands the counts
over in finished files and loses none that were not delivered; the panel
turns them into daily figures for the admin; the relay counts each address's
DNS on port 53, and the customer's page says whether their DNS reaches us -
from what the relay saw, since a browser cannot test DNS itself.
"""
import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
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


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


doh = load("templates/smartdns-doh", "doh")
sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (doh, sync, panel, admin):
    m.log = lambda *a: None
panel.print = lambda *a, **k: None
tmp = tempfile.mkdtemp()

# ---------------------------------------------------------------- the rate
print("the rate")
clock = [1000.0]
doh.time.monotonic = lambda: clock[0]
lim = doh.Limiter(rate=10, burst=5)
got = [lim.allow("a") for _ in range(7)]
check("a burst up to the limit is let through, the rest refused",
      got == [True] * 5 + [False] * 2, repr(got))
check("somebody else is not held up by it", lim.allow("b"))
clock[0] += 0.35
check("and it refills at the rate", [lim.allow("a") for _ in range(4)] == [True, True, True, False])
doh.time.monotonic = time.monotonic


# ---------------------------------------------------------------- DoH and DoT, for real
print("DoH and DoT, answering")
resolver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
resolver.bind(("127.0.0.1", 0))


def answer_all():
    while True:
        try:
            data, addr = resolver.recvfrom(512)
        except OSError:
            return
        resolver.sendto(data[:2] + bytes([0x81, 0x80, 0, 1, 0, 1, 0, 0, 0, 0]) + data[12:], addr)


threading.Thread(target=answer_all, daemon=True).start()
RPORT = resolver.getsockname()[1]
TOKEN = "Tk3v9QpZr2LmXw8bYc4dNa"
state = os.path.join(tmp, "doh.json")
import hashlib
with open(state, "w") as fh:
    json.dump({"tokens": {hashlib.sha256(TOKEN.encode()).hexdigest(): {"uid": 7, "port": RPORT}},
               "ips": {"203.0.113.5": RPORT}, "allowed": ["203.0.113.5"], "enforcing": True}, fh)
doh.STATE_NOW = doh.State(state)
doh.LIMIT = doh.Limiter(rate=0.001, burst=3)
doh.STATS = doh.Stats()
server = doh.DoHServer(("127.0.0.1", 0), doh.DoH)
threading.Thread(target=server.serve_forever, daemon=True).start()
QUERY = os.urandom(2) + bytes([1, 0, 0, 1, 0, 0, 0, 0, 0, 0]) + b"\x07example\x03org\x00\x00\x01\x00\x01"


def post(token=TOKEN):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    c.request("POST", "/dns-query/" + token, QUERY,
              {"Content-Type": "application/dns-message", "X-Real-IP": "127.0.0.1"})
    r = c.getresponse()
    r.read()
    return r.status


logged = []
doh.log = lambda level, msg: logged.append(msg)
codes = [post() for _ in range(5)]
check("DoH answers up to the customer's limit, then 429",
      codes == [200, 200, 200, 429, 429], repr(codes))
check("the log names the account, never the token",
      any("account 7" in m for m in logged) and not any(TOKEN in m for m in logged), repr(logged))
check("and counts each answered query for that account, by number",
      doh.STATS.doh == {"7": 3} and doh.STATS.dot == {}, repr(doh.STATS.doh))

dot = doh.DoTServer(("127.0.0.1", 0), doh.DoT)
threading.Thread(target=dot.serve_forever, daemon=True).start()


def dot_queries(ip, n):
    s = socket.create_connection(dot.server_address, timeout=5)
    s.sendall(b"PROXY TCP4 %s 198.51.100.1 5555 853\r\n" % ip.encode())
    rcodes = []
    for _ in range(n):
        s.sendall(struct.pack("!H", len(QUERY)) + QUERY)
        size = struct.unpack("!H", doh.read_exact(s, 2))[0]
        rcodes.append(doh.read_exact(s, size)[3] & 0x0F)
    s.close()
    return rcodes


rc = dot_queries("203.0.113.5", 5)
check("DoT answers up to the address's limit, then REFUSED", rc == [0, 0, 0, 5, 5], repr(rc))
check("and counts by address", doh.STATS.dot == {"203.0.113.5": 3}, repr(doh.STATS.dot))

# The gate's resolver, told apart by its answer: NXDOMAIN, where the
# customers' says NOERROR.
gate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
gate.bind(("127.0.0.1", 0))


def gate_answers():
    while True:
        try:
            data, addr = gate.recvfrom(512)
        except OSError:
            return
        gate.sendto(data[:2] + bytes([0x81, 0x83, 0, 1, 0, 0, 0, 0, 0, 0]) + data[12:], addr)


threading.Thread(target=gate_answers, daemon=True).start()
doh.GATE_PORT = gate.getsockname()[1]
check("an address not let in, come by the gate's door, gets the gate's answers, "
      "not counted", dot_queries("198.51.100.99", 2) == [3, 3]
      and "198.51.100.99" not in doh.STATS.dot)
doh.GATE_LIMIT = doh.Limiter(rate=0.001, burst=2)
check("  held to the gate's own rate", dot_queries("198.51.100.98", 3) == [3, 3, 5])


def post_gate(token, client="198.51.100.97", gate_mark="1"):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    c.request("POST", "/dns-query/" + token, QUERY,
              {"Content-Type": "application/dns-message", "X-Real-IP": "127.0.0.1",
               "X-Gate": gate_mark, "X-Client-IP": client})
    r = c.getresponse()
    return r.status, r.read()


status, body = post_gate("NotAnyonesToken12345678")
check("DoH from the gate's server: the gate's answer, whatever address is in the path",
      status == 200 and body[3] & 0x0F == 3, repr((status, body[:4])))
status, body = post_gate(TOKEN, client="198.51.100.96")
check("  a customer's own address too: a token is no way round registering",
      status == 200 and body[3] & 0x0F == 3 and doh.STATS.doh == {"7": 3})
status, _ = post_gate("NotAnyonesToken12345678", gate_mark="")
check("  and without the mark, an unknown address is refused as before", status == 403)

print("the counts, handed over")
sdir = os.path.join(tmp, "stats")
doh.flush_stats(sdir)
check("a flush writes one finished file and empties the counts",
      len([n for n in os.listdir(sdir) if n.endswith(".json")]) == 1
      and doh.STATS.doh == {} and not any(n.endswith(".tmp") for n in os.listdir(sdir)))
doh.flush_stats(sdir)
check("an empty flush writes nothing", len(os.listdir(sdir)) == 1)
kept = open(os.path.join(sdir, os.listdir(sdir)[0])).read()
check("the file has counts and no names", "example" not in kept and '"7": 3' in kept, kept)
with open(os.path.join(sdir, "half.tmp"), "w") as fh:
    fh.write("{")
sync.DOH_STATS_DIR = sdir
got = sync.take_doh_stats()
check("the relay reads the finished files and deletes them, not a half-written one",
      got == {"doh": {"7": 3}, "dot": {"203.0.113.5": 3}}
      and os.listdir(sdir) == ["half.tmp"], repr(got))
check("what was not delivered is still there next time", sync.take_doh_stats() == got)

print("the relay's DNS counters")
sync.nft = lambda *a: type("R", (), {"returncode": 0, "stdout": json.dumps({"nftables": [
    {"metainfo": {}}, {"set": {"name": "dns", "elem": [
        {"elem": {"val": "203.0.113.5", "counter": {"packets": 0, "bytes": 1234}}}]}}]})})()
check("the relay reads each address's DNS bytes", sync.dns_seen() == {"203.0.113.5": 1234})
nf = read("templates/nftables-smartdns.conf")
check("the kernel counts port 53 per registered address, UDP and TCP",
      "set dns {" in nf and "ip saddr @allowed udp dport 53 update @dns" in nf
      and "ip saddr @allowed tcp dport 53 update @dns" in nf)
check("the flood guard leaves loopback alone, where smartdns-doh asks for everybody",
      'iifname != "lo" udp dport 53 meter dnsflood' in nf
      and "\n        udp dport 53 meter dnsflood" not in nf)
check("and forgets an address when it is removed",
      'nft delete element $TABLE dns  "{ $ip }"' in read("templates/smartdns-acl"))
src = read("templates/smartdns-sync")
check("both go to the panel with the sync, and are dropped once delivered",
      'payload["dns_seen"] = dns_seen()' in src and 'payload["doh_stats"] = doh_stats' in src
      and 'DOH_PENDING["doh"].clear()' in src)

# ---------------------------------------------------------------- panel
print("the panel")
store = panel.Store(os.path.join(tmp, "panel.db"))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
u = store.create_user(111, "ali", "علی")
v = store.create_user(222, "sara", "سارا")
panel.register_ip(store, u["id"], "203.0.113.5")
panel.register_ip(store, v["id"], "203.0.113.6")
store.fold_counters("r1", {"203.0.113.5": 10, "203.0.113.6": 10})
seen = lambda ip: store.one("SELECT dns_seen_at FROM ips WHERE ip = ?", (ip,))["dns_seen_at"]
panel.record_dns(store, "r1", {"203.0.113.5": 500, "203.0.113.6": 0})
check("an address whose DNS count moved is marked as reaching us",
      seen("203.0.113.5") is not None and seen("203.0.113.6") is None)
store.run("UPDATE ips SET dns_seen_at = '2020-01-01T00:00:00+00:00'")
panel.record_dns(store, "r1", {"203.0.113.5": 500})
check("an unchanged count is not new DNS", seen("203.0.113.5").startswith("2020"))
panel.record_dns(store, "r1", {"203.0.113.5": 40})
check("a smaller one, from a rebuilt set, is", not seen("203.0.113.5").startswith("2020"))
panel.record_dns(store, "r1", {"198.51.100.9": 99, "x": "bad"})
check("addresses nobody has are ignored", True)

panel.record_doh(store, {"doh": {str(u["id"]): 30, "0": 2}, "dot": {"203.0.113.6": 5, "198.51.100.9": 1}})
panel.record_doh(store, {"doh": {str(u["id"]): 10}})
day = panel.usage_buckets()["1d"]
daily = {r["kind"]: r["queries"] for r in store.q("SELECT * FROM doh_daily WHERE day = ?", (day,))}
check("daily figures add up, DoH and DoT apart", daily == {"doh": 42, "dot": 6}, repr(daily))
who = sorted(r["user_id"] for r in store.q("SELECT user_id FROM doh_users WHERE day = ?", (day,)))
check("and each customer who used either counts once, DoT by their address",
      who == sorted([u["id"], v["id"]]), repr(who))
check("and the account is marked as using encrypted DNS",
      store.one("SELECT doh_seen_at FROM users WHERE id = ?", (v["id"],))["doh_seen_at"])
old = (datetime.now(panel.TEHRAN) - timedelta(days=100)).strftime("%Y-%m-%d")
store.run("INSERT INTO doh_daily (day, kind, queries) VALUES (?, 'doh', 1)", (old,))
panel.prune_usage(store)
check("figures older than 90 days are dropped",
      not store.one("SELECT 1 FROM doh_daily WHERE day = ?", (old,)))

store.run("UPDATE users SET status = 'active'")
before = panel.doh_token(store, store.one("SELECT * FROM users WHERE id = ?", (u["id"],)))
api = panel.BotAPI.__new__(panel.BotAPI)
api.store, api.relays = store, ()
store.set_setting("doh_host", "users.example.com")
code, res = api.api_doh_reset({}, "111")
after = store.one("SELECT doh_token FROM users WHERE id = ?", (u["id"],))["doh_token"]
check("a bot can give the customer a new address, and gets it back",
      code == 200 and after != before and after in res["user"]["doh"]["url"])
check("the old one is gone from what the relays are sent",
      panel.doh_hash(before) not in panel.doh_tokens(store, {}, 1)
      and panel.doh_hash(after) in panel.doh_tokens(store, {}, 1))
psrc = read("templates/smartdns-panel")
check("the customer's page has its own way to it, behind the session",
      'if self.path == "/user-doh-reset":' in psrc
      and "new_doh_token(self.store, user)" in psrc)
check("and the page is told whether the DNS reaches us",
      '"dns_seen_at": ips[0]["dns_seen_at"] if ips else None' in psrc
      and '"doh_seen_at": user["doh_seen_at"]' in psrc)

# ---------------------------------------------------------------- the pages
print("the customer's page")
iso = lambda s: (datetime.now(timezone.utc) - timedelta(seconds=s)).isoformat(timespec="seconds")
check("no line about whether the DNS reaches us - it only alarmed people",
      "def dns_check(" not in read("templates/smartdns-sync"))
sync.CFG = {"PANEL_DOMAIN": "users.example.com"}
sync.DOH_FLAG = os.path.join(tmp, "flag")
open(sync.DOH_FLAG, "w").close()
box = sync.doh_box({"doh_token": TOKEN})
check("the DoH section has the button for a new address, asking first",
      "action='/doh-reset'" in box and "confirm(" in box)
setup = sync.doh_box({"doh_token": TOKEN}, setup=True)
check("but not the setup page, which the address itself opens", "/doh-reset" not in setup)
check("the button is wired", 'if path == "/doh-reset":' in src and '"/user-doh-reset"' in src)

print("the admin panel")
admin.DB = os.path.join(tmp, "panel.db")
admin.STORE = admin.Store(admin.DB)
card = admin.doh_card()
check("the home page shows the week: customers, DoH and DoT queries",
      "DNS امن" in card and ">2<" in card and ">42<" in card and ">6<" in card, card[:300])
store.run("DELETE FROM doh_daily")
check("and says so when nobody used it", "کسی از DNS امن استفاده نکرده" in admin.doh_card())
check("the bot has the button", '"callback_data": "dohnew"' in read("examples/telegram-bot/bot.py"))

server.shutdown()
dot.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
