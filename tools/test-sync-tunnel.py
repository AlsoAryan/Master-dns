#!/usr/bin/env python3
"""The sync API goes through the tunnel when there is one.

Filtering between Iran and an exit kills a large upload on the direct path
while small ones pass: a customer's receipt timed out every time at 25
seconds, the exit logging a read that never finished, and the same bytes went
through the tunnel untouched. So the relay reaches the API through the tunnel
when it can, and straight to the exit when that end is not up.

Two things have to hold and neither is obvious. Only the way in may be
retried - a request that was already sent may have been acted on, and a second
"add this much quota" is worse than an error. And through the tunnel the
request arrives at the exit from its own loopback, so the relay has to name
itself, or every relay's usage would be counted under one name.
"""
import hashlib
import importlib.machinery
import importlib.util
import os
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


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(ROOT, "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def text(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


sync = load("smartdns-sync", "sync")
panel = load("smartdns-panel", "panel")

print("the tunnel carries a third port")
logic = text("tools/installer-logic.sh")
check("the relay's end of it is named once", "TUNNEL_LOCAL_API=18843" in logic)
ports = [l for l in logic.splitlines() if "ports = [" in l]
check("every tunnel shape carries it to the exit's 8443 - reverse, direct and l3",
      len(ports) == 3 and all('=8443' in l for l in ports), str(ports))
check("and nobody can pick it as the tunnel's own port",
      any('"$TUNNEL_LOCAL_API"' in l and 'echo "the tunnel' in l for l in logic.splitlines()))

print("which way the relay goes")
sync.CFG = {"PANEL_HOST": "198.51.100.7", "SYNC_SECRET": "s", "SELF_IP": "203.0.113.4",
            "SYNC_FINGERPRINT": "", "TUNNEL": "backpack"}
check("through the tunnel first, the exit second",
      sync.api_endpoints() == [("127.0.0.1", 18843), ("198.51.100.7", 8443)],
      str(sync.api_endpoints()))
sync.CFG["TUNNEL"] = "off"
check("with no tunnel, straight to the exit",
      sync.api_endpoints() == [("198.51.100.7", 8443)])

CERT = b"pretend certificate"
FINGER = hashlib.sha256(CERT).hexdigest()
tried = []


class FakeSock:
    def getpeercert(self, binary_form=False):
        return CERT


class FakeConn:
    """One attempt at one endpoint, behaving as the test asks."""
    behaviour = {}

    def __init__(self, host, port, sni, timeout=None, context=None):
        self.where = (host, port)
        self.sock = FakeSock()
        self.sent = None
        tried.append(self.where)

    def connect(self):
        if self.behaviour.get(self.where) == "refuse":
            raise ConnectionRefusedError("nothing listening")

    def request(self, method, path, body, headers):
        self.sent = (path, body)
        if self.behaviour.get(self.where) == "die after sending":
            raise ConnectionResetError("reset while sending")

    def getresponse(self):
        class Res:
            status = 200

            def read(self):
                return b'{"ok": true}'
        return Res()

    def close(self):
        pass


sync.NamedHTTPS = FakeConn
sync.sync_sni = lambda: "panel.example.com"
sync.CFG = {"PANEL_HOST": "198.51.100.7", "SYNC_SECRET": "s", "SELF_IP": "203.0.113.4",
            "SYNC_FINGERPRINT": FINGER, "TUNNEL": "backpack"}

print("when the tunnel is up")
tried[:] = []
FakeConn.behaviour = {}
res = sync.post("/sync", {"hello": 1})
check("it is used", tried == [("127.0.0.1", 18843)], str(tried))
check("and the answer comes back", res == {"ok": True})

print("when the tunnel's end is not up")
tried[:] = []
FakeConn.behaviour = {("127.0.0.1", 18843): "refuse"}
res = sync.post("/sync", {"hello": 1})
check("it goes straight to the exit",
      tried == [("127.0.0.1", 18843), ("198.51.100.7", 8443)], str(tried))
check("and still works", res == {"ok": True})

print("but a request that was already sent is never sent twice")
tried[:] = []
FakeConn.behaviour = {("127.0.0.1", 18843): "die after sending"}
try:
    sync.post("/user-receipt", {"data": "..."})
    died = False
except OSError:
    died = True
check("the failure is reported", died)
check("and the exit is not asked again", tried == [("127.0.0.1", 18843)], str(tried))

print("a certificate that is not the exit's stops everything")
tried[:] = []
FakeConn.behaviour = {}
sync.CFG["SYNC_FINGERPRINT"] = "00" * 32
try:
    sync.post("/sync", {})
    refused = False
except RuntimeError as e:
    refused = "fingerprint" in str(e)
check("it is refused", refused)
check("and not retried anywhere else", tried == [("127.0.0.1", 18843)], str(tried))
sync.CFG["SYNC_FINGERPRINT"] = FINGER

print("the relay names itself")
sent = {}
sync.post = lambda path, payload: sent.setdefault(path, payload) and None or {
    "allowed": [], "profiles": {}, "extra_domains": [], "templates": {}, "support": ""}
for name in ("save_template_names", "save_user_names",
             "apply_custom_domains", "apply_speeds", "close_relay_when_ready"):
    setattr(sync, name, lambda *a, **k: False)
sync.apply_profiles = lambda *a, **k: None
sync.current_state = lambda: []
sync.HEALTH = type("H", (), {"sample": staticmethod(lambda: {})})()
sync.sync_once()
check("every sync says which relay it is",
      sent.get("/sync", {}).get("relay") == "203.0.113.4", str(sent.get("/sync")))

print("and the exit takes that name only from a relay it knows")


class FakeApi:
    relays = ("203.0.113.4", "203.0.113.5")
    nodes = ()
    secret = "s"

    def __init__(self, came_from, token="Bearer s"):
        self.client_address = (came_from, 40000)
        self.headers = {"Authorization": token}


for name in ("relay_name", "authorised"):
    setattr(FakeApi, name, getattr(panel.API, name))

api = FakeApi("127.0.0.1")
check("a paired relay's own name is used",
      api.relay_name({"relay": "203.0.113.5"}) == "203.0.113.5")
check("a name this exit does not know is ignored",
      api.relay_name({"relay": "198.51.100.9"}) == "127.0.0.1")
check("and so is no name at all", api.relay_name({}) == "127.0.0.1")
direct = FakeApi("203.0.113.4")
check("a relay connecting directly is itself",
      direct.relay_name({}) == "203.0.113.4")

print("who may talk to the API")
check("a paired relay, with the secret", FakeApi("203.0.113.4").authorised())
check("the tunnel, arriving from this machine itself",
      FakeApi("127.0.0.1").authorised())
check("but not with the wrong secret",
      not FakeApi("127.0.0.1", "Bearer nope").authorised())
check("and not a stranger", not FakeApi("198.51.100.200").authorised())

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
