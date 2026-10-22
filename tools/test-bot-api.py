#!/usr/bin/env python3
"""The bot API, over a real socket.

A key speaks for every customer at once, so the door is most of what matters:
no key, a wrong key, a revoked key and a key used from the wrong address all
get nothing, and guessing is slowed down. Behind the door the rules are the
web panel's own - the same function records a receipt either way - and a
request sent twice with the same Idempotency-Key is acted on once.
"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []
GB = 1024 ** 3


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(HERE, "..", "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("smartdns-panel", "panel")
admin = load("smartdns-admin", "admin")
panel.log = lambda *a: None
panel.print = lambda *a, **k: None
admin.log = lambda *a: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p", "ADMIN_CERT": "/etc/letsencrypt/live/panel.example.com/x"}
admin.STORE = admin.Store(db_path)

store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ماهانه', 1, 30, ?, 200000, ?)", (50 * GB, panel.now()))


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "api_keys"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def make_key(name, allow=""):
    r = Rec()
    r.action("api-key-new", {"name": [name], "allow_ips": [allow]})
    found = re.findall(r"dd_[A-Za-z0-9_-]{20,}", r.written.decode("utf-8"))
    return found[0] if found else None


panel.BotAPI.store = store
panel.BotAPI.relays = ("198.51.100.4",)
server = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]


def call(method, path, body=None, key=None, headers=None, raw=None):
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        res = urllib.request.urlopen(req, timeout=10)
        return res.status, json.loads(res.read()), dict(res.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")).decode()

print("making a key in the admin panel")
key = make_key("ربات فروش")
check("the key is shown once, on the page", bool(key))
row = store.one("SELECT * FROM api_tokens")
check("only its hash is kept", row and key not in json.dumps(dict(row))
      and row["token_hash"] == panel.token_hash(key))
page = Rec().api_keys()
check("the page lists it, and where the API is",
      "ربات فروش" in page and "https://panel.example.com:8445/api/v1/" in page, page[:300])

print("the door")
code, res, _ = call("GET", "/api/v1/")
check("no key: 401", code == 401 and res.get("error") == "unauthorized", str(res))
code, res, _ = call("GET", "/api/v1/", key="dd_nonsense")
check("a wrong key: 401", code == 401)
code, res, _ = call("GET", "/api/v1/", key=key)
check("the right key: in", code == 200 and res["ok"], str(res))
code, res, _ = call("GET", "/nothing-here", key=key)
check("outside /api/v1: 404", code == 404)
code, res, _ = call("DELETE", "/api/v1/plans", key=key)
check("a known path with the wrong method: 405", code == 405, str(code))
check("every refusal carries a code and a message", res.get("error")
      and res.get("message"))
check("last use is recorded",
      store.one("SELECT last_used_at FROM api_tokens")["last_used_at"])

print("plans")
code, res, _ = call("GET", "/api/v1/plans", key=key)
check("what is on sale", code == 200 and [p["name"] for p in res["plans"]] == ["ماهانه"]
      and res["plans"][0]["price"] == 200000, str(res))

print("customers")
code, res, _ = call("POST", "/api/v1/users", {"name": "علی"}, key=key)
check("a telegram id is required", code == 400 and res["error"] == "bad_telegram_id")
code, res, _ = call("POST", "/api/v1/users", {"telegram_id": 111, "name": "علی"}, key=key)
check("a new customer: 201", code == 201 and res["created"], str(res))
u = res["user"]
check("pending, with nothing", u["status"] == "pending" and u["plan"] is None)
check("told where to point their DNS", u["dns"] == ["198.51.100.4"])
check("no web username is taken for them",
      store.one("SELECT username FROM users WHERE telegram_id = 111")["username"] is None)
code, res, _ = call("POST", "/api/v1/users", {"telegram_id": 111}, key=key)
check("the same id again is the same customer, not a second",
      code == 200 and not res["created"] and res["user"]["id"] == u["id"])
code, res, _ = call("GET", "/api/v1/users/999", key=key)
check("an unknown customer: 404", code == 404 and res["error"] == "user_not_found")
code, res, _ = call("GET", "/api/v1/users/111", key=key)
check("a known one", code == 200 and res["user"]["name"] == "علی")

print("addresses")
code, res, _ = call("POST", "/api/v1/users/111/ips", {"ip": "192.168.1.5"}, key=key)
check("a LAN address is refused", code == 400 and res["error"] == "bad_ip")
code, res, _ = call("POST", "/api/v1/users/111/ips", {"ip": "5.120.1.2"}, key=key)
check("a real one is registered", code == 200 and res["user"]["ips"] == ["5.120.1.2"],
      str(res))
call("POST", "/api/v1/users", {"telegram_id": 222}, key=key)
code, res, _ = call("POST", "/api/v1/users/222/ips", {"ip": "5.120.1.2"}, key=key)
check("somebody else's address: 409", code == 409 and res["error"] == "ip_taken")
code, res, _ = call("POST", "/api/v1/users/111/ips", {"ip": "5.120.9.9"}, key=key)
check("a new address replaces the old one", res["user"]["ips"] == ["5.120.9.9"])
code, res, _ = call("DELETE", "/api/v1/users/111/ips/5.120.9.9", key=key)
check("an address can be taken off", code == 200 and res["user"]["ips"] == [])
code, res, _ = call("DELETE", "/api/v1/users/111/ips/5.120.9.9", key=key)
check("taking it off twice: 404", code == 404)

print("receipts")
code, res, _ = call("POST", "/api/v1/users/111/receipts",
                    {"content_type": "image/png", "data": PNG}, key=key)
check("with plans on sale, one must be named", code == 400
      and res["error"] == "plan_required", str(res))
code, res, _ = call("POST", "/api/v1/users/111/receipts",
                    {"plan_id": 1, "content_type": "text/html", "data": PNG}, key=key)
check("the web panel's own checks apply", code == 400 and res["error"] == "bad_type")
body = {"plan_id": 1, "content_type": "image/png", "data": PNG, "amount": 5}
code, res, hdr = call("POST", "/api/v1/users/111/receipts", body, key=key,
                      headers={"Idempotency-Key": "pay-111-1"})
check("a receipt: 201, pending", code == 201 and res["receipt"]["status"] == "pending",
      str(res))
check("priced by the plan", res["receipt"]["amount"] == 200000)
first = res["receipt"]["id"]
code, res, hdr = call("POST", "/api/v1/users/111/receipts", body, key=key,
                      headers={"Idempotency-Key": "pay-111-1"})
check("the same request again gets the same answer",
      code == 201 and res["receipt"]["id"] == first
      and hdr.get("Idempotent-Replayed") == "true", str(hdr))
check("and nothing was done twice",
      store.one("SELECT count(*) c FROM transactions")["c"] == 1
      and store.one("SELECT id FROM transactions")["id"] == first)
code, res, _ = call("POST", "/api/v1/users/111/receipts", dict(body, note="x"),
                    key=key, headers={"Idempotency-Key": "pay-111-1"})
check("the same key for a different request: 422",
      code == 422 and res["error"] == "idempotency_key_reused")
code, res, _ = call("GET", "/api/v1/users/111", key=key)
check("the customer shows it waiting", res["user"]["receipt_waiting"]
      and res["user"]["receipt_waiting"]["plan"] == "ماهانه")

r = Rec()
r.action("receipt-decide", {"id": [str(first)], "to": ["approved"]})
code, res, _ = call("GET", "/api/v1/users/111", key=key)
u = res["user"]
check("once approved, the bot sees the plan on the account",
      u["status"] == "active" and u["plan"]["name"] == "ماهانه"
      and u["quota_bytes"] == 50 * GB and u["remaining_bytes"] == 50 * GB, str(u))
code, res, _ = call("GET", "/api/v1/users/111/receipts", key=key)
check("and the receipt as approved", res["receipts"][0]["status"] == "approved")

print("what a request may be")
code, res, _ = call("POST", "/api/v1/users", raw=b"{not json", key=key)
check("broken JSON: 400", code == 400 and res["error"] == "bad_json")
code, res, _ = call("POST", "/api/v1/users", raw=b"[1,2]", key=key)
check("JSON that is not an object: 400", code == 400)
code, res, _ = call("POST", "/api/v1/users/111/receipts",
                    raw=b"x" * (panel.BOT_BODY_MAX + 1), key=key)
check("far too big: 413", code == 413)

print("keys that are limited, or revoked")
near = make_key("از جای دیگر", "203.0.113.9")
code, res, _ = call("GET", "/api/v1/", key=near)
check("a key limited to another address is refused here",
      code == 403 and res["error"] == "ip_not_allowed")
r = Rec()
r.action("api-key-revoke", {"id": [str(store.one(
    "SELECT id FROM api_tokens WHERE name = 'ربات فروش'")["id"])]})
code, res, _ = call("GET", "/api/v1/", key=key)
check("a revoked key is no key", code == 401)

print("guessing is slowed down")
panel.BOT_THROTTLE.clear("bad:127.0.0.1")
codes = [call("GET", "/api/v1/", key="dd_guess%d" % i)[0] for i in range(22)]
check("after twenty wrong keys, 429", codes[-1] == 429 and codes[0] == 401, str(codes))

print("the port is kept for it")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("the installer will not give 8445 to the admin panel or the tunnel",
      '8445) die "port 8445 is the bot API"' in logic
      and '8445) echo "the bot API"' in logic)
check("nor will the admin panel move onto it", 8445 in admin.RESERVED_PORTS)

server.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
