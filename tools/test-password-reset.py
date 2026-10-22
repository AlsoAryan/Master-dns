#!/usr/bin/env python3
"""A customer who forgot their password, and the operator who gives a new one.

The operator presses one button and gets a temporary password, shown once and
nowhere else. The customer is signed out everywhere, but not cut off. Signing
in with the temporary password leads to one page only - choosing their own -
and the panel refuses everything else until they have. After that the
temporary password is worth nothing. The sign-in form says whom to ask.
"""
import html
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

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


panel = load("smartdns-panel", "panel")
admin = load("smartdns-admin", "admin")
sync = load("smartdns-sync", "sync")

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
cat = json.load(open(os.path.join(ROOT, "domains", "services.json"),
                     encoding="utf-8"))["services"]
store.ensure_default_template(cat)
store.create_web_user("forgetful", "علی", "the-old-password")
ali = store.user_by_username("forgetful")
store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, '203.0.113.20', ?)",
          (ali["id"], panel.now()))
store.run("UPDATE users SET status = 'active' WHERE id = ?", (ali["id"],))
phone_only = store.run("INSERT INTO users (phone, first_name, created_at)"
                       " VALUES ('09120000009', 'قدیمی', ?)", (panel.now(),)).lastrowid
old_session = store.open_session(ali["id"])
other_device = store.open_session(ali["id"])

admin.STORE = admin.Store(db)
admin.CATALOGUE = cat
admin.CFG = {"ADMIN_PATH": "p"}

print("the two panels hash passwords the same way")
check("one algorithm, written twice on purpose",
      admin.hash_password("x-y-z", "ab" * 16) == panel.hash_password("x-y-z", "ab" * 16))

print("the temporary password")
made = {admin.temp_password() for _ in range(200)}
check("never the same twice in 200", len(made) == 200)
check("three groups of four", all(re.fullmatch(r"[a-z2-9]{4}(-[a-z2-9]{4}){2}", p)
                                  for p in made))
check("with nothing that reads as something else",
      not any(c in p for p in made for c in "01ilo"))


class Out:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.wfile = Out()

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        ("%s: %s\r\n" % (k, v)).encode("latin-1")
        self.sent[k] = v

    def end_headers(self):
        pass


for n in ("action", "redirect", "send", "session_token"):
    setattr(Rec, n, getattr(admin.Admin, n))
Rec.one = staticmethod(admin.Admin.one)
Rec.headers = {}

print("the operator gives one")
r = Rec()
r.action("user-password-reset", {"id": [str(ali["id"])]})
body = r.sent and r.wfile.data.decode("utf-8")
shown = re.search(r"<code>([a-z2-9-]{14})</code>", body)
check("the page shows it", r.sent.get("code") == 200 and shown is not None, body[:200])
temp = shown.group(1) if shown else ""
check("and says for whom", "«forgetful»" in body)
check("not in any address", "Location" not in r.sent and temp not in json.dumps(r.sent))
check("and the page is not kept", r.sent.get("Cache-Control") == "no-store")
after = store.user_by_username("forgetful")
check("it works", panel.check_password(after, temp))
check("the old one does not", not panel.check_password(after, "the-old-password"))
check("the account must choose its own", after["must_change_password"] == 1)
check("every place it was signed in is closed",
      store.one("SELECT count(*) c FROM panel_sessions WHERE user_id = ?",
                (ali["id"],))["c"] == 0)
check("but its connection is untouched",
      after["status"] == "active" and
      store.one("SELECT count(*) c FROM ips WHERE user_id = ?", (ali["id"],))["c"] == 1)

print("and cannot give one where it means nothing")
before = store.one("SELECT password_hash FROM users WHERE id = ?", (phone_only,))
r = Rec()
r.action("user-password-reset", {"id": [str(phone_only)]})
check("an account with no username is refused",
      "m=%21" in r.sent.get("Location", ""), str(r.sent))
check("and left as it was",
      store.one("SELECT password_hash FROM users WHERE id = ?", (phone_only,))
      ["password_hash"] == before["password_hash"])
r = Rec()
r.action("user-password-reset", {"id": ["99999"]})
check("nor one that is not there", "m=%21" in r.sent.get("Location", ""))


class Api:
    relays = ("198.51.100.1",)

    def __init__(self, store):
        self.store = store


for name in ("do_user_password_login", "do_user_password_first", "do_user_password",
             "do_user_info", "do_user_claim", "do_user_receipt", "do_claim_register",
             "_session_user", "_must_choose"):
    setattr(Api, name, getattr(panel.API, name))
api = Api(store)

print("the customer signs in with it")
panel.THROTTLE.clear("login:198.51.100.4")
res = api.do_user_password_login({"username": "forgetful", "password": temp,
                                  "ip": "198.51.100.4"})
check("and is let in", res.get("ok"), str(res))
check("told to choose a password first", res.get("must_change") is True)
session = res.get("session", "")
check("the account page says the same",
      api.do_user_info({"session": session, "ip": "198.51.100.4"}).get("must_change") is True)
check("registering an address waits",
      not api.do_user_claim({"session": session, "ip": "198.51.100.4"}).get("ok"))
check("so does a receipt",
      not api.do_user_receipt({"session": session, "content_type": "image/png",
                               "data": "aGk="}).get("ok"))

print("and chooses their own")
check("too short is refused",
      not api.do_user_password_first({"session": session, "new": "short"}).get("ok"))
check("the temporary one again is refused",
      not api.do_user_password_first({"session": session, "new": temp}).get("ok"))
elsewhere = store.open_session(ali["id"])
res = api.do_user_password_first({"session": session, "new": "all-my-own-now"})
check("a proper one is kept", res.get("ok"), str(res))
mine = store.user_by_username("forgetful")
check("it works", panel.check_password(mine, "all-my-own-now"))
check("the temporary one is worth nothing now", not panel.check_password(mine, temp))
check("the account is free again", mine["must_change_password"] == 0)
check("this session stays, any other goes",
      [r["token"] for r in store.q("SELECT token FROM panel_sessions WHERE user_id = ?",
                                   (ali["id"],))] == [session])
check("and the door is shut behind them",
      not api.do_user_password_first({"session": session, "new": "sneaky-second"}).get("ok"))
check("the address can be registered now",
      api.do_user_claim({"session": session, "ip": "203.0.113.20"}).get("ok"))
check("and nobody else had the flag",
      store.one("SELECT count(*) c FROM users WHERE must_change_password = 1")["c"] == 0)

print("the relay's pages")
check("the sign-in page points to the Telegram reset, and names no contact",
      "/forgot" in sync.login_form() and "به پشتیبانی پیام دهید" not in sync.login_form())


def request(method, path, cookie="", form=None, answer=None):
    h = sync.UserPanel.__new__(sync.UserPanel)
    raw = "&".join("%s=%s" % kv for kv in (form or {}).items()).encode()
    h.path, h.command = path, method
    h.headers = {"Cookie": cookie, "Content-Length": str(len(raw)),
                 "Content-Type": "application/x-www-form-urlencoded"}
    h.request_version, h.requestline = "HTTP/1.1", "%s %s HTTP/1.1" % (method, path)
    h.client_address = ("198.51.100.4", 40000)
    h.close_connection = False
    h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
    h._t0 = time.monotonic()
    calls = []

    def fake_post(p, payload):
        calls.append((p, payload))
        return answer(p, payload)
    sync.post = fake_post
    (h.do_POST if method == "POST" else h.do_GET)()
    head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
    return head.decode("latin-1"), body.decode("utf-8", "replace"), calls


head, _, _ = request("POST", "/login", form={"username": "forgetful", "password": "x"},
                     answer=lambda p, b: {"ok": True, "session": "s1", "must_change": True})
check("signing in with a temporary password goes to the account page",
      "Location: /\r" in head + "\r", head)
head, _, _ = request("POST", "/login", form={"username": "forgetful", "password": "x"},
                     answer=lambda p, b: {"ok": True, "session": "s1"})
check("an ordinary sign-in still goes to the address page",
      "Location: /register-ip" in head, head)
info = {"ok": True, "must_change": True, "used": 0, "quota": 0, "ip": None,
        "status": "active"}
_, page, _ = request("GET", "/", cookie="sdu=s1", answer=lambda p, b: info)
check("which shows only the form to choose one",
      "رمز خودتان را انتخاب کنید" in page and "action='/password-first'" in page
      and "/register-ip" not in page and "/receipt" not in page)
head, _, calls = request("POST", "/password-first", cookie="sdu=s1",
                         form={"new": "all-my-own", "again": "all-my-own"},
                         answer=lambda p, b: {"ok": True, "message": "ok"})
check("which the relay passes on to the panel",
      calls and calls[0][0] == "/user-password-first"
      and calls[0][1] == {"session": "s1", "new": "all-my-own"}, str(calls))
head, _, calls = request("POST", "/password-first", cookie="sdu=s1",
                         form={"new": "all-my-own", "again": "all-my-0wn"},
                         answer=lambda p, b: {"ok": True})
check("a typo in the second box never reaches it", not calls and "e=1" in head)

print("the admin panel")
page = admin.Admin.users(None)
check("an account with a username has the button",
      page.count("/p/user-password-reset") == 1, str(page.count("/p/user-password-reset")))
question = re.search(r"action='/p/user-password-reset' onsubmit='([^']*)'", page)
check("which asks first, naming it",
      question and "«forgetful»" in html.unescape(question.group(1)))
admin.CFG = {"ADMIN_PATH": "p", "ADMIN_PORT": "9443"}
check("the settings page has no support contact any more",
      "support-save" not in admin.Admin.settings(None))

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
