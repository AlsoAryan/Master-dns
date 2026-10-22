#!/usr/bin/env python3
"""When the resolver behind smartdns-doh does not answer.

What has to hold: DoH answers with a DNS SERVFAIL, not an HTTP 502 - a
browser that gets an HTTP error from its DoH server can give up on it and go
back to the operator's plain DNS for a while, which is exactly what the
customer turned DoH on to avoid. DoT answers SERVFAIL too and keeps the
connection for the next question. Both keep the question in the answer, so
the client can tell which question it is; REFUSED does the same.
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


doh = load("templates/smartdns-doh", "doh")
doh.log = lambda *a: None
doh.UPSTREAM_TIMEOUT = 0.5
tmp = tempfile.mkdtemp()

QUESTION = b"\x07example\x03org\x00\x00\x01\x00\x01"
QUERY = b"\x12\x34" + bytes([1, 0, 0, 1, 0, 0, 0, 0, 0, 0]) + QUESTION


def parse(answer):
    qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", answer[:12])
    return {"id": qid, "qr": bool(flags & 0x8000), "rcode": flags & 0x0F,
            "counts": (qd, an, ns, ar), "question": answer[12:]}


print("the answers themselves")
s = parse(doh.servfail(QUERY))
check("SERVFAIL is an answer to the same question, with it kept and nothing else",
      s == {"id": 0x1234, "qr": True, "rcode": 2, "counts": (1, 0, 0, 0), "question": QUESTION}, repr(s))
r = parse(doh.refused(QUERY))
check("REFUSED keeps the question too", r["rcode"] == 5 and r["question"] == QUESTION
      and r["counts"] == (1, 0, 0, 0))
edns = QUERY[:10] + b"\x00\x01" + QUESTION + b"\x00\x00\x29\x04\xd0\x00\x00\x00\x00\x00\x00"
check("a query's EDNS record is not copied into the answer",
      parse(doh.servfail(edns))["counts"] == (1, 0, 0, 0)
      and parse(doh.servfail(edns))["question"] == QUESTION)
check("a query too short to be one gets nothing", doh.servfail(b"\x00" * 5) == b"")
check("and one whose question does not parse gets the header alone",
      parse(doh.servfail(QUERY[:12] + b"\x40bad"))["counts"] == (0, 0, 0, 0))

print("DoH and DoT, with a resolver that says nothing")
silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
silent.bind(("127.0.0.1", 0))
TOKEN = "Tk3v9QpZr2LmXw8bYc4dNa"
import hashlib
state = os.path.join(tmp, "doh.json")
with open(state, "w") as fh:
    json.dump({"tokens": {hashlib.sha256(TOKEN.encode()).hexdigest():
                          {"uid": 7, "port": silent.getsockname()[1]}},
               "ips": {"203.0.113.5": silent.getsockname()[1]},
               "allowed": ["203.0.113.5"], "enforcing": True}, fh)
doh.STATE_NOW = doh.State(state)
doh.STATS = doh.Stats()
server = doh.DoHServer(("127.0.0.1", 0), doh.DoH)
threading.Thread(target=server.serve_forever, daemon=True).start()
c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
c.request("POST", "/dns-query/" + TOKEN, QUERY,
          {"Content-Type": "application/dns-message", "X-Real-IP": "127.0.0.1"})
res = c.getresponse()
body = res.read()
check("DoH answers 200 with a DNS message, not 502",
      res.status == 200 and res.getheader("Content-Type") == "application/dns-message", str(res.status))
check("and the message is SERVFAIL for that question", parse(body)["rcode"] == 2
      and parse(body)["question"] == QUESTION)
check("not kept by any cache on the way", res.getheader("Cache-Control") == "no-store")

dot = doh.DoTServer(("127.0.0.1", 0), doh.DoT)
threading.Thread(target=dot.serve_forever, daemon=True).start()
sk = socket.create_connection(dot.server_address, timeout=10)
sk.sendall(b"PROXY TCP4 203.0.113.5 198.51.100.1 5555 853\r\n")
rcodes = []
for _ in range(2):
    sk.sendall(struct.pack("!H", len(QUERY)) + QUERY)
    size = struct.unpack("!H", doh.read_exact(sk, 2))[0]
    rcodes.append(parse(doh.read_exact(sk, size))["rcode"])
sk.close()
check("DoT answers SERVFAIL, and the same connection takes the next question",
      rcodes == [2, 2], repr(rcodes))

server.shutdown()
dot.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
