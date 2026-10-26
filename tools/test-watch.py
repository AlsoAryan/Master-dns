#!/usr/bin/env python3
"""smartdns-watch: the names a customer asks for, and where each went.

The packet socket and its kernel filter only exist on Linux, so the filter is
checked here by running its program through a small interpreter, and the rest
by feeding the watcher packets built by hand - the same bytes the kernel would
hand it.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import socket
import sqlite3
import struct
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, fname):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(
            name, os.path.join(HERE, "..", "templates", fname)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


watch = load("watch", "smartdns-watch")
RELAY, CUSTOMER, OTHER, UPSTREAM = "45.12.34.56", "5.200.1.2", "5.200.9.9", "1.1.1.1"


# ------------------------------------------------------------ packets by hand
def ip_packet(src, dst, sport, dport, payload, proto=17, frag=0):
    body = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 0, frag, 64,
                       proto, 0, socket.inet_aton(src), socket.inet_aton(dst)) + body


def qname(name):
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def question(qid, name, qtype=1):
    return struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname(name) + \
        struct.pack("!HH", qtype, 1)


def answer(qid, name, addrs, rcode=0, qtype=1, cname=None):
    records = b""
    count = len(addrs)
    if cname:
        target = qname(cname)
        records += b"\xc0\x0c" + struct.pack("!HHIH", 5, 1, 60, len(target)) + target
        count += 1
    for a in addrs:
        records += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(a)
    return struct.pack("!HHHHHH", qid, 0x8180 | rcode, 1, count, 0, 0) + \
        qname(name) + struct.pack("!HH", qtype, 1) + records


def asks(qid, name, client=CUSTOMER, port=40000, qtype=1):
    return ip_packet(client, RELAY, port, 53, question(qid, name, qtype))


def told(qid, name, addrs, client=CUSTOMER, port=40000, **kw):
    return ip_packet(RELAY, client, 53, port, answer(qid, name, addrs, **kw))


# ------------------------------------------------------------ the kernel filter
def bpf(prog, pkt, proto=0x0800):
    """Run a classic BPF program over a packet, as the kernel would. `proto`
    is the ethertype the kernel keeps beside the packet."""
    a = x = pc = 0
    while True:
        code, jt, jf, k = prog[pc]
        try:
            if code == 0x28 and k == 0xFFFFF000:
                a = proto                       # SKF_AD_OFF + SKF_AD_PROTOCOL
            elif code == 0x30:
                a = pkt[k]
            elif code == 0x28:
                a = struct.unpack("!H", pkt[k:k + 2])[0]
            elif code == 0xB1:
                x = 4 * (pkt[k] & 0x0F)
            elif code == 0x48:
                a = struct.unpack("!H", pkt[x + k:x + k + 2])[0]
            elif code == 0x15:
                pc += jt if a == k else jf
            elif code == 0x45:
                pc += jt if a & k else jf
            elif code == 0x06:
                return k
            else:
                raise AssertionError("opcode %#x" % code)
        except (IndexError, struct.error):
            return 0          # a load past the end drops the packet
        pc += 1


print("the kernel filter")
check("a customer's question passes", bpf(watch.BPF, asks(1, "a.com")) > 0)
check("the relay's answer passes", bpf(watch.BPF, told(1, "a.com", [RELAY])) > 0)
check("HTTPS traffic does not", bpf(watch.BPF, ip_packet(CUSTOMER, RELAY, 50000, 443, b"x" * 100)) == 0)
check("nor does a download on port 80", bpf(watch.BPF, ip_packet(RELAY, CUSTOMER, 80, 50000, b"x" * 100)) == 0)
check("nor DNS over TCP", bpf(watch.BPF, ip_packet(CUSTOMER, RELAY, 40000, 53, b"x" * 30, proto=6)) == 0)
check("nor a later fragment, whose first bytes are not a UDP header",
      bpf(watch.BPF, ip_packet(CUSTOMER, RELAY, 40000, 53, b"x", frag=0x0010)) == 0)
check("nor a runt", bpf(watch.BPF, b"\x45\x00") == 0)
# The socket takes every protocol, so that the relay's own answers reach it.
# The filter is then all that keeps the rest out.
v6 = b"\x60" + b"\x00" * 5 + b"\x11\x40" + b"\x00" * 32 + struct.pack("!HHHH", 40000, 53, 8, 0)
check("nor IPv6, even DNS over it", bpf(watch.BPF, v6, proto=0x86DD) == 0)
check("nor ARP", bpf(watch.BPF, b"\x00\x01\x08\x00\x06\x04" + b"\x00" * 22, proto=0x0806) == 0)
src_watch = open(os.path.join(HERE, "..", "templates", "smartdns-watch"), encoding="utf-8").read()
check("the socket takes every protocol, or it would never see an answer leave",
      "socket.htons(ETH_P_ALL)" in src_watch)
check("every instruction is eight bytes, as the kernel expects",
      all(len(struct.pack("HBBI", *i)) == 8 for i in watch.BPF))

print("reading DNS")
d = watch.parse_dns(answer(7, "Auth.Game-X.com", ["1.2.3.4"], cname="edge.cdn.net"))
check("an answer with a CNAME in front of its address",
      d == (7, True, 0, "auth.game-x.com", 1, ["1.2.3.4"]), str(d))
check("a question", watch.parse_dns(question(9, "a.com")) == (9, False, 0, "a.com", 1, []))
loop = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0) + b"\xc0\x0c" + b"\x00\x01\x00\x01"
check("a name that points at itself is refused, not followed forever",
      watch.parse_dns(loop) is None)
for junk in (b"", b"\x00" * 5, b"\xff" * 40, answer(1, "a.com", ["1.2.3.4"])[:30]):
    try:
        watch.parse_dns(junk)
        watch.parse_ip_udp(junk)
        ok = True
    except Exception as e:
        ok = False
    check("junk of %d bytes does not crash it" % len(junk), ok)

print("watching one customer")
lines, now = [], [1000.0]
w = watch.Watcher({RELAY, "127.0.0.1"}, {CUSTOMER: "ali"}, {CUSTOMER},
                  out=lines.append, clock=lambda: now[0])
w.feed(asks(1, "cdn.game-x.com")); w.feed(told(1, "cdn.game-x.com", [RELAY]))
w.feed(asks(2, "auth.game-x.com")); w.feed(told(2, "auth.game-x.com", ["104.18.2.3"]))
w.feed(asks(3, "youtube.com")); w.feed(told(3, "youtube.com", ["10.10.34.35"]))
w.feed(asks(4, "nope.invalid")); w.feed(told(4, "nope.invalid", [], rcode=3))
text = "\n".join(lines)
check("a routed name says so", "cdn.game-x.com" in text and "via relay" in text, text)
check("a name that went around the relay says where",
      "auth.game-x.com" in text and "direct 104.18.2.3" in text, text)
check("Iran's block is named as such", "filtered in Iran" in text, text)
check("and a name that does not exist", "no such name" in text, text)
check("one customer: lines are not prefixed with who asked",
      all(l.split()[1] != "ali" for l in lines), text)

before = len(lines)
w.feed(asks(5, "cdn.game-x.com")); w.feed(told(5, "cdn.game-x.com", [RELAY]))
w.feed(asks(6, "auth.game-x.com")); w.feed(told(6, "auth.game-x.com", ["104.18.9.9"]))
check("a name is shown once, even when a CDN hands out another address",
      len(lines) == before, "\n".join(lines[before:]))

w.feed(asks(7, "slow.example.com"))
w.tick()
check("an unanswered question is not called unanswered at once",
      "slow.example.com" not in "\n".join(lines))
now[0] += watch.WAIT + 0.1
w.tick()
check("but is after a few seconds - which is how a blocked address shows",
      "slow.example.com" in lines[-1] and "no answer" in lines[-1], lines[-1])

before = len(lines)
w.feed(asks(8, "ipv6.game-x.com", qtype=28)); w.feed(told(8, "ipv6.game-x.com", [], qtype=28))
w.feed(asks(9, "else.com", client=OTHER)); w.feed(told(9, "else.com", ["9.9.9.9"], client=OTHER))
w.feed(ip_packet(RELAY, UPSTREAM, 33333, 53, question(10, "up.com")))
w.feed(ip_packet(UPSTREAM, RELAY, 53, 33333, answer(10, "up.com", ["8.8.8.8"])))
w.feed(ip_packet(CUSTOMER, RELAY, 50000, 443, b"x" * 60))
check("AAAA, other customers, the relay's own upstream and non-DNS are all left out",
      len(lines) == before, "\n".join(lines[before:]))
check("the summary counts what was shown",
      w.summary().startswith("5 names") and "1 via relay" in w.summary(), w.summary())

print("watching everybody")
lines2 = []
w2 = watch.Watcher({RELAY}, {CUSTOMER: "ali"}, None, out=lines2.append)
w2.feed(asks(1, "a.com")); w2.feed(told(1, "a.com", [RELAY]))
w2.feed(asks(2, "b.com", client=OTHER)); w2.feed(told(2, "b.com", [RELAY], client=OTHER))
check("each line names who asked, by username where known",
      "ali" in lines2[0] and OTHER in lines2[1], "\n".join(lines2))

print("who is who")
users = {CUSTOMER: {"label": "u12", "user": "Ali"}, OTHER: {"label": "u13", "user": ""}}
check("an address is taken as itself", watch.resolve(" 9.8.7.6 ", users) == {"9.8.7.6"})
check("a username finds that customer's address, whatever the case",
      watch.resolve("ali", users) == {CUSTOMER})
check("so does the label smartdns-acl list shows", watch.resolve("u13", users) == {OTHER})
check("a name nobody has finds nothing", watch.resolve("zahra", users) == set())
check("nor does an empty one", watch.resolve("  ", users) == set())
check("the display prefers the username, then the label",
      watch.display(users) == {CUSTOMER: "Ali", OTHER: "u13"})

print("the relay learns usernames from the panel")
sync = load("sync", "smartdns-sync")
tmp = tempfile.mkdtemp()
sync.USER_NAMES = os.path.join(tmp, "sub", "users.json")
allowed = [{"ip": CUSTOMER, "name": "u12", "uid": 12, "user": "ali"},
           {"ip": OTHER, "name": "u13", "uid": 13}]
check("the first save writes the file", sync.save_user_names(allowed))
import json as _json
saved = _json.load(open(sync.USER_NAMES, encoding="utf-8"))
check("with each address's label and username",
      saved == {CUSTOMER: {"label": "u12", "user": "ali"}, OTHER: {"label": "u13", "user": ""}},
      str(saved))
check("an unchanged list is not written again", not sync.save_user_names(allowed))
check("an older panel that sends none is no trouble",
      sync.save_user_names([{"ip": CUSTOMER, "name": "u12"}]))
src = open(os.path.join(HERE, "..", "templates", "smartdns-sync"), encoding="utf-8").read()
check("the file is made readable by root only", "0o600" in src)
check("and saved on every sync", "save_user_names(answer.get(\"allowed\"))" in src)

panel = load("panel", "smartdns-panel")
store = panel.Store(os.path.join(tmp, "panel.db"))
catalogue = panel.load_catalogue() + [panel.CUSTOM_SERVICE]
default = store.ensure_default_template(catalogue)["id"]
store.run("INSERT INTO users (username, first_name, created_at, status) VALUES (?, ?, ?, 'active')",
          ("ali", "علی", panel.now()))
uid = store.one("SELECT id FROM users WHERE username = 'ali'")["id"]
store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)", (uid, CUSTOMER, panel.now()))
by_ip, _ = store.profiles(catalogue, default)
check("the panel's view of each address carries the username",
      by_ip.get(CUSTOMER, {}).get("user") == "ali", str(by_ip))
psrc = open(os.path.join(HERE, "..", "templates", "smartdns-panel"), encoding="utf-8").read()
check("and it goes out with the allowlist", '"user": v.get("user", "")' in psrc)
store.db.close()
shutil.rmtree(tmp, ignore_errors=True)

print("DoH and DoT, from the DoH server")
import json
import threading
import time
doh = load("doh", "smartdns-doh")
feed_dir = tempfile.mkdtemp()
server = doh.WatchFeed(feed_dir)


class State:
    def self_ips(self):
        return {RELAY}


def reply_for(query, addr):
    """An answer to `query` with one A record, the way the resolver sends it."""
    head = query[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
    record = struct.pack("!HHHIH", 0xC00C, 1, 1, 60, 4) + socket.inet_aton(addr)
    return head + query[12:] + record


q = question(7, "chat.openai.com")
server.note("doh", "12", q, reply_for(q, RELAY), State())
check("with nobody watching, the DoH server writes nothing",
      not os.path.exists(os.path.join(feed_dir, "encrypted.log")))
feed = watch.EncryptedFeed(feed_dir)
check("a watch asks for them, with a file of its own", os.listdir(feed_dir) == ["%d.want" % os.getpid()])
server.checked = 0
server.note("doh", "12", q, reply_for(q, RELAY), State())
q2 = question(8, "steamcommunity.com")
server.note("dot", OTHER, q2, reply_for(q2, "23.1.2.3"), State())
got = feed.read()
check("and then each encrypted answer comes through, with where it sent them",
      [(e["kind"], e["key"], e["name"], e["v"]) for e in got]
      == [("doh", "12", "chat.openai.com", "via relay"),
          ("dot", OTHER, "steamcommunity.com", "direct 23.1.2.3")], repr(got))
check("each once", feed.read() == [])
lines3 = []
w3 = watch.Watcher({RELAY}, {CUSTOMER: "ali", OTHER: "sara"}, None, out=lines3.append)
users = {CUSTOMER: {"label": "u12", "user": "ali"}, OTHER: {"label": "u13", "user": "sara"}}
for e in got:
    w3.encrypted(e, users)
check("the watcher shows them as it shows plain DNS, marked DoH or DoT",
      len(lines3) == 2 and "ali" in lines3[0] and "chat.openai.com" in lines3[0]
      and "via relay" in lines3[0] and "(DOH)" in lines3[0]
      and "sara" in lines3[1] and "(DOT)" in lines3[1], repr(lines3))
lines4 = []
w4 = watch.Watcher({RELAY}, {CUSTOMER: "ali"}, {CUSTOMER}, out=lines4.append)
for e in got:
    w4.encrypted(e, users)
check("watching one customer shows only theirs - DoH found by the account's label",
      len(lines4) == 1 and "chat.openai.com" in lines4[0], repr(lines4))
feed.close()
check("stopping takes the request away", os.listdir(feed_dir) == ["encrypted.log"])
server.checked = 0
server.active()
check("and the DoH server empties its log once nobody is watching",
      os.path.getsize(os.path.join(feed_dir, "encrypted.log")) == 0)
old = os.path.join(feed_dir, "999.want")
open(old, "w").close()
os.utime(old, (time.time() - 60, time.time() - 60))
server.checked = 0
check("a watcher that died without saying goes stale", server.active() is False)
dsrc = open(os.path.join(HERE, "..", "templates", "smartdns-doh"), encoding="utf-8").read()
check("the DoH server passes on both kinds",
      'WATCH.note("doh", uid, query, reply, state)' in dsrc
      and 'WATCH.note("dot", ip, query, reply, state)' in dsrc)
wsrc = open(os.path.join(HERE, "..", "templates", "smartdns-watch"), encoding="utf-8").read()
check("and --json, which keeps the support report, does not ask - the DoH server keeps that itself",
      "feed = None if as_json else EncryptedFeed()" in wsrc)
shutil.rmtree(feed_dir, ignore_errors=True)

print("the command itself")
out = []
watch.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
check("-h explains it", watch.main(["-h"]) == 0 and "smartdns-watch ali" in "\n".join(out))
check("two arguments are refused", watch.main(["a", "b"]) == 2)
check("so is an option it does not know", watch.main(["--bogus"]) == 2)
had = getattr(watch.os, "geteuid", None)
watch.os.geteuid = lambda: 1000
check("it wants root", watch.main(["ali"]) == 1 and any("sudo" in l for l in out))
if had:
    watch.os.geteuid = had
else:
    del watch.os.geteuid
del watch.print

print("the installer puts it on the relay")
built = open(os.path.join(HERE, "..", "master-dns.sh"), encoding="utf-8").read()
at = built.find("payload SMARTDNS_WATCH > /usr/local/bin/smartdns-watch")
check("the installer writes it", at > 0)
check("in the relay's part, beside smartdns-rules",
      0 < built.find("payload SMARTDNS_RULES > /usr/local/bin/smartdns-rules") < at)
check("and uninstall knows it is ours", "note_file /usr/local/bin/smartdns-watch" in built)

# A name a console asked for that the lobby does not need, and went direct.
print("hints")
hl = []
hw = watch.Watcher({RELAY}, {CUSTOMER: "ali"}, {CUSTOMER}, out=hl.append, clock=lambda: 1000.0)
check("nothing seen, nothing to say", hw.hints() == [])
for name in ("lsg.prod.demonware.net", "genesis.stun.us.demonware.net", "loginservice.prod.demonware.net"):
    hw.report(CUSTOMER, name, "direct 20.1.2.3")
check("the lobby, STUN and login names going direct are as they should be", hw.hints() == [], hw.hints())
hw.report(CUSTOMER, "rex-gvs.prod.demonware.net", "via relay")
check("a web name that already goes through the relay is fine", hw.hints() == [], hw.hints())
hw.report(CUSTOMER, "user-consent.prod.demonware.net", "direct 20.1.2.4")
hw.report(CUSTOMER, "example.com", "direct 93.184.216.34")
text = "\n".join(hw.hints())
check("a web name going direct is listed, with what to do",
      "user-consent.prod.demonware.net" in text and "smartdns add" in text and "Checking for update" in text, text)
check("and only Demonware names are", "example.com" not in text and "lsg." not in text)

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
