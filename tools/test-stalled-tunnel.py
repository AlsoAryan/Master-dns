#!/usr/bin/env python3
"""A tunnel that connects and then carries nothing, and an old BackPack left
running.

What has to hold: a relay fetching the installer for an upgrade - bytes that
change nothing to ask for - goes on to the direct path when the tunnel took
the request and went quiet, instead of timing out on every upgrade; a wrong
certificate still stops it, and a panel that says no is still a no. And a
new BackPack restarts the tunnels already running, which would otherwise go
on running the binary replaced under them.
"""
import hashlib
import importlib.machinery
import importlib.util
import os
import socket
import sys

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


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(HERE, "..", path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = load("templates/smartdns-sync", "sync")
sync.log = lambda *a: None
CERT = b"the exit's certificate"
sync.CFG = {"PANEL_HOST": "203.0.113.1", "TUNNEL": "backpack", "SYNC_SECRET": "s",
            "SYNC_FINGERPRINT": hashlib.sha256(CERT).hexdigest()}
tried = []
BEHAVIOUR = {}


class Res:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def getheaders(self):
        return [("X-Version", "0.9.25")]


class Sock:
    def __init__(self, cert):
        self.cert = cert

    def getpeercert(self, binary_form=False):
        return self.cert


class FakeConn:
    def __init__(self, host, port, sni, **kw):
        self.host, self.port = host, port

    def connect(self):
        tried.append((self.host, self.port))
        how = BEHAVIOUR.get(self.host, "ok")
        if how == "refused":
            raise ConnectionRefusedError(111, "Connection refused")
        self.sock = Sock(b"another certificate" if how == "wrong cert" else CERT)

    def request(self, *a, **kw):
        if BEHAVIOUR.get(self.host) == "stalls":
            raise socket.timeout("The read operation timed out")

    def getresponse(self):
        if BEHAVIOUR.get(self.host) == "no":
            return Res(403, b"")
        return Res(200, b"#!/bin/bash installer")

    def close(self):
        pass


sync.NamedHTTPS = FakeConn


def fetch():
    del tried[:]
    try:
        return sync.fetch("/installer")
    except Exception as e:
        return e


print("fetching the installer")
BEHAVIOUR.clear()
body = fetch()
check("through the tunnel when it carries", isinstance(body, tuple)
      and tried == [("127.0.0.1", sync.API_TUNNEL_PORT)])
BEHAVIOUR["127.0.0.1"] = "stalls"
body = fetch()
check("the tunnel connects and goes quiet: on to the direct path, and it arrives",
      isinstance(body, tuple) and body[0] == b"#!/bin/bash installer"
      and tried == [("127.0.0.1", sync.API_TUNNEL_PORT), ("203.0.113.1", sync.API_PORT)],
      repr(body))
BEHAVIOUR["127.0.0.1"] = "refused"
check("the tunnel refuses: direct, as before", isinstance(fetch(), tuple)
      and tried[-1] == ("203.0.113.1", sync.API_PORT))
BEHAVIOUR.update({"127.0.0.1": "stalls", "203.0.113.1": "stalls"})
err = fetch()
check("both quiet: said plainly", isinstance(err, RuntimeError)
      and "not reachable" in str(err) and "timed out" in str(err), repr(err))
BEHAVIOUR.clear()
BEHAVIOUR["127.0.0.1"] = "wrong cert"
err = fetch()
check("a certificate that is not the exit's still stops it, nothing sent elsewhere",
      isinstance(err, RuntimeError) and "fingerprint" in str(err) and len(tried) == 1)
BEHAVIOUR.clear()
BEHAVIOUR["127.0.0.1"] = "no"
err = fetch()
check("the panel saying no is a no, not a reason to ask again",
      isinstance(err, RuntimeError) and "403" in str(err) and len(tried) == 1)

print("a new BackPack")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
fn = logic[logic.index("install_backpack() {"):]
fn = fn[:fn.index("\n}\n")]
check("restarts every tunnel already running, after the binary is in place",
      fn.index('install -m 755 "$tmp/backpack" "$BACKPACK_BIN"')
      < fn.index("--state=active 'smartdns-tunnel*'") < fn.index('systemctl restart "$unit"'))
check("  and only when it really installed one - not when it was already here",
      fn.index('info "BackPack $BACKPACK_VERSION already here"')
      < fn.index('install -m 755') and "return 0" in fn[:fn.index('install -m 755')])

print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
