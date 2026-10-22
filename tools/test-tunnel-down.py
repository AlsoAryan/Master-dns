#!/usr/bin/env python3
"""A tunnel that is up but carries nothing.

What has to hold: when the relay's call to the exit's API fails through the
tunnel and answers straight to the exit, the tunnel is taken for down and
the relay's nginx is sent straight to the exit - a layer-3 tunnel's
forwarder keeps its port open while the tunnel is down, taking each
connection and closing it, so nginx never turns to the exit by itself and
every customer of the relay was cut until the tunnel came back. As soon as
a call goes through the tunnel again, nginx goes back through it. When the
exit answers neither way, nothing is concluded about the tunnel.
"""
import importlib.machinery
import importlib.util
import os
import ssl
import sys

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


spec = importlib.util.spec_from_loader("sync", importlib.machinery.SourceFileLoader(
    "sync", os.path.join(ROOT, "templates", "smartdns-sync")))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
said = []
sync.log = lambda level, text: said.append(text)
sync.CFG = {"SYNC_FINGERPRINT": "ab" * 32, "SYNC_SECRET": "s", "PANEL_HOST": "203.0.113.9",
            "TUNNEL": "backpack"}
sync.sync_sni = lambda: "sync.example.com"
broken = set()


class Res:
    status = 200

    def read(self):
        return b'{"ok": true}'


class Sock:
    def getpeercert(self, binary_form=False):
        return b"cert"


class FakeHTTPS:
    def __init__(self, host, port, sni, **kw):
        self.where = (host, port)

    def connect(self):
        if self.where in broken:
            raise ssl.SSLEOFError("EOF occurred in violation of protocol")
        self.sock = Sock()

    def request(self, *a):
        pass

    def getresponse(self):
        return Res()

    def close(self):
        pass


sync.NamedHTTPS = FakeHTTPS
sync.hashlib.sha256 = (lambda real: lambda b=b"": real(b) if b != b"cert" else type(
    "H", (), {"hexdigest": lambda self: "ab" * 32})())(sync.hashlib.sha256)
TUNNEL, DIRECT = ("127.0.0.1", sync.API_TUNNEL_PORT), ("203.0.113.9", 8443)
check("the tunnel is tried first, the exit second", sync.api_endpoints()[:2] == [TUNNEL, DIRECT])

print("what a call finds")
sync.post("/sync", {})
check("through the tunnel: up", sync.TUNNEL_DOWN["down"] is False)
broken.add(TUNNEL)
sync.post("/sync", {})
check("the tunnel refuses and the exit answers: down, and said",
      sync.TUNNEL_DOWN["down"] is True and any("carries nothing" in s for s in said))
broken.add(DIRECT)
try:
    sync.post("/sync", {})
except OSError:
    pass
check("neither answers: nothing concluded", sync.TUNNEL_DOWN["down"] is True)
broken.clear()
sync.post("/sync", {})
check("through the tunnel again: up, and said",
      sync.TUNNEL_DOWN["down"] is False and any("carries again" in s for s in said))
broken.add(DIRECT)
sync.post("/sync", {})
check("only the exit straight refusing says nothing of the tunnel", sync.TUNNEL_DOWN["down"] is False)

print("where nginx is sent")
src = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("the panel's tunnel: straight to the exit while it is down, and the panel told",
      'point_exit_nginx(exit_ip, not TUNNEL_DOWN["down"])' in src
      and "تونل وصل است ولی چیزی رد نمی‌کند" in src)
check("the installer's tunnel likewise",
      '== "backpack"\n                            and not TUNNEL_DOWN["down"]):' in src)

print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
