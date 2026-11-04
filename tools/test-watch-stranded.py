#!/usr/bin/env python3
"""0.1.2: smartdns-watch notices a game that knocks on a port the relay does not carry.

What has to hold: the kernel filter lets through a connection being asked for on a
port other than 80 and 443, and nothing else; a customer's SYN on such a port is
blamed on the names the relay was just asked for, the likeliest first and sharpened by
each further attempt; ports the relay does answer on, a relay's own traffic and
customers not being watched are left alone; and the end of the session lists the
names with the command that makes each direct.
"""
import importlib.machinery
import importlib.util
import os
import socket
import struct
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" - " + str(detail)[:400]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


spec = importlib.util.spec_from_loader("watch", importlib.machinery.SourceFileLoader(
    "watch", os.path.join(HERE, "..", "templates", "smartdns-watch")))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
RELAY, CUSTOMER, OTHER, SERVER = "45.12.34.56", "5.200.1.2", "5.200.9.9", "93.184.216.34"
SYN, ACK, RST = 0x02, 0x10, 0x04


def tcp_packet(src, dst, sport, dport, flags, frag=0, ihl=5):
    options = b"\x00" * ((ihl - 5) * 4)
    header = struct.pack("!HHIIBBHHH", sport, dport, 0, 0, 5 << 4, flags, 65535, 0, 0)
    body = header + b"\x00" * 8
    return struct.pack("!BBHHHBBH4s4s", 0x40 | ihl, 0, 20 + len(options) + len(body), 0, frag, 64, 6, 0,
                       socket.inet_aton(src), socket.inet_aton(dst)) + options + body


def udp_packet(src, dst, sport, dport, payload):
    body = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 0, 0, 64, 17, 0,
                       socket.inet_aton(src), socket.inet_aton(dst)) + body


def qname(name):
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def answer(qid, name, addrs):
    records = b"".join(b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(a) for a in addrs)
    return struct.pack("!HHHHHH", qid, 0x8180, 1, len(addrs), 0, 0) + qname(name) + struct.pack("!HH", 1, 1) + records


def bpf(prog, pkt, proto=0x0800):
    """A classic BPF program run over a packet, as the kernel would."""
    a = x = pc = 0
    while True:
        code, jt, jf, k = prog[pc]
        try:
            if code == 0x28 and k == 0xFFFFF000:
                a = proto
            elif code == 0x30:
                a = pkt[k]
            elif code == 0x28:
                a = struct.unpack("!H", pkt[k:k + 2])[0]
            elif code == 0xB1:
                x = 4 * (pkt[k] & 0x0F)
            elif code == 0x50:
                a = pkt[x + k]
            elif code == 0x48:
                a = struct.unpack("!H", pkt[x + k:x + k + 2])[0]
            elif code == 0x54:
                a &= k
            elif code == 0x15:
                pc += jt if a == k else jf
            elif code == 0x45:
                pc += jt if a & k else jf
            elif code == 0x06:
                return k
            else:
                raise AssertionError("opcode %#x" % code)
        except (IndexError, struct.error):
            return 0
        pc += 1


print("the kernel filter for connections being asked for")
f = watch.BPF_SYN
check("a SYN to port 20443 passes", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 20443, SYN)) > 0)
check("and one to 8090", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 8090, SYN)) > 0)
check("a SYN to 443 does not", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 443, SYN)) == 0)
check("nor one to 80", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 80, SYN)) == 0)
check("a SYN-ACK, which is an answer, does not", bpf(f, tcp_packet(RELAY, CUSTOMER, 20443, 50000, SYN | ACK)) == 0)
check("nor does a connection already going (ACK)", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 20443, ACK)) == 0)
check("nor a reset", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 20443, RST)) == 0)
check("nor UDP, which is DNS and is the other filter's", bpf(f, udp_packet(CUSTOMER, RELAY, 40000, 20443, b"x")) == 0)
check("nor a later fragment", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 20443, SYN, frag=0x0010)) == 0)
check("a SYN with IP options is read past them", bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 20443, SYN, ihl=6)) > 0
      and bpf(f, tcp_packet(CUSTOMER, RELAY, 50000, 443, SYN, ihl=6)) == 0)
check("nor IPv6", bpf(f, b"\x60" + b"\x00" * 60, proto=0x86DD) == 0)
check("nor a runt", bpf(f, b"\x45\x00") == 0)
check("every instruction is eight bytes", all(len(struct.pack("HBBI", *i)) == 8 for i in f))
check("every jump lands inside the program",
      all(i + 1 + jt < len(f) and i + 1 + jf < len(f) for i, (code, jt, jf, k) in enumerate(f)
          if code in (0x15, 0x45)))
src = open(os.path.join(HERE, "..", "templates", "smartdns-watch"), encoding="utf-8").read()
check("it is a second socket, with its own filter, and not the one that reads DNS",
      "attach_filter(syn, BPF_SYN)" in src and "select.select(" in src)
check("and not opened for the report mode, which only reads answers", "if not as_json:\n            syn" in src)

print("reading a SYN")
check("addresses, ports and flags come out",
      watch.parse_ip_tcp(tcp_packet(CUSTOMER, RELAY, 50000, 20443, SYN)) == (CUSTOMER, RELAY, 50000, 20443, SYN))
check("UDP is not TCP", watch.parse_ip_tcp(udp_packet(CUSTOMER, RELAY, 1, 2, b"x" * 30)) is None)
check("a runt is nothing", watch.parse_ip_tcp(b"\x45" * 10) is None)

print("blaming a name")
clock = {"t": 1000.0}
lines = []
w = watch.Watcher({RELAY}, {CUSTOMER: "ali"}, out=lines.append, clock=lambda: clock["t"], listening={8443})


def routed(name, qid=1, client=CUSTOMER):
    w.feed(udp_packet(client, RELAY, 40000, 53, struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname(name)
                      + struct.pack("!HH", 1, 1)))
    w.feed(udp_packet(RELAY, client, 53, 40000, answer(qid, name, [RELAY])))


def knock(port, client=CUSTOMER, flags=SYN, dst=RELAY):
    w.feed(tcp_packet(client, dst, 50000, port, flags))


routed("login.game.net", 1)
clock["t"] += 1
routed("matchmaker.game.net", 2)
clock["t"] += 1
knock(20443)
check("a SYN to a port the relay does not carry is blamed on the names just before it",
      len(lines) == 3 and "port 20443 on the relay" in lines[-1] and "login.game.net" in lines[-1]
      and "matchmaker.game.net" in lines[-1], lines)
check("with the customer's name", "ali" in lines[-1])
before = len(lines)
clock["t"] += 3
knock(20443)
check("the same again, with nothing new to say, is not said again", len(lines) == before)
clock["t"] += 1
routed("cdn.game.net", 3)
clock["t"] += 1
knock(20443)
check("a name asked after the first try was not blamed for it, so it is not as likely as the others",
      set(w.stranded()) == {"login.game.net", "matchmaker.game.net"}, w.stranded())
check("and the guess did not change, so nothing more is said about it",
      not any("cdn.game.net" in l and "port" in l for l in lines))

print("what is left alone")
n = len(lines)
knock(443)
knock(80)
check("443 and 80, which the relay carries", len(lines) == n)
knock(53)
knock(853)
knock(22)
check("DNS, DNS over TLS and ssh, which the relay answers on", len(lines) == n)
knock(8443)
check("a port something on the relay is listening on", len(lines) == n)
knock(20443, flags=SYN | ACK)
knock(20443, flags=ACK)
check("anything that is not a connection being asked for", len(lines) == n)
knock(20443, client=RELAY)
check("the relay's own connections", len(lines) == n)
knock(20443, dst=SERVER)
check("a SYN that is not to the relay", len(lines) == n)
knock(9999, client=OTHER)
check("a customer the relay gave no name to has nothing to blame", len(lines) == n)
clock["t"] += 3600
knock(9999)
check("and names an hour old are not blamed", len(lines) == n)

print("watching one customer")
watched = watch.Watcher({RELAY}, {}, targets={OTHER}, out=lines.append, clock=lambda: clock["t"])
routed2 = lambda name: (watched.feed(udp_packet(CUSTOMER, RELAY, 40000, 53, struct.pack("!HHHHHH", 9, 0x0100, 1, 0, 0, 0)
                                                + qname(name) + struct.pack("!HH", 1, 1))),
                        watched.feed(udp_packet(RELAY, CUSTOMER, 53, 40000, answer(9, name, [RELAY]))))
n = len(lines)
routed2("x.game.net")
watched.feed(tcp_packet(CUSTOMER, RELAY, 50000, 20443, SYN))
check("a customer not being watched is not blamed", len(lines) == n)

print("the end of the session")
hints = w.hints()
text = "\n".join(hints)
check("names are listed with the command that makes each direct and the port",
      "smartdns bypass login.game.net" in text and "# port 20443" in text, text)
check("and it says it is a guess from timing", "guess from timing" in text)
check("a name that was not blamed is not listed", "cdn.game.net" not in text)
check("a session with nothing stranded says nothing about it",
      watch.Watcher({RELAY}, {}, out=lines.append).hints() == [])
quiet = watch.Watcher({RELAY}, {}, out=lines.append, as_json=True)
quiet.feed(tcp_packet(CUSTOMER, RELAY, 50000, 20443, SYN))
check("the report mode leaves it alone", quiet.hints() == [])
check("the ports something listens on are read from ss", callable(watch.listening_ports))

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
