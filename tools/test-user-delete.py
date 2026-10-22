#!/usr/bin/env python3
"""Deleting a customer from the admin panel.

What has to hold: the account goes, and so does everything that is only its -
addresses, receipts, sign-ins, and the usage readings kept by address, so the
next owner of one of those addresses is not billed from where this one left
off. Nobody else's anything is touched. The relays stop serving it at their
next sync, because it is no longer in what the panel sends them. And the
button asks first, naming the account - safely, whatever that name is.
"""
import html
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

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
cat = json.load(open(os.path.join(ROOT, "domains", "services.json"),
                     encoding="utf-8"))["services"]
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
default = store.ensure_default_template(cat)["id"]


def add_user(phone, name, ips):
    store.run("INSERT INTO users (phone, first_name, created_at, status)"
              " VALUES (?, ?, ?, 'active')", (phone, name, panel.now()))
    uid = store.one("SELECT id FROM users WHERE phone = ?", (phone,))["id"]
    for ip in ips:
        store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)",
                  (uid, ip, panel.now()))
        store.run("INSERT INTO ip_counters (ip, relay, last_counter)"
                  " VALUES (?, 'relay-1', 123456)", (ip,))
    store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at)"
              " VALUES (?, 100000, 'topup', 'pending', ?)", (uid, panel.now()))
    store.run("INSERT INTO panel_sessions (token, user_id, created_at, expires_at)"
              " VALUES (?, ?, ?, ?)", ("tok-" + phone, uid, panel.now(), panel.now()))
    return uid


gone = add_user("09120000001", "رفتنی", ["5.52.1.1", "5.52.1.2"])
kept = add_user("09120000002", "ماندنی", ["2.176.4.9"])


def count(sql, args=()):
    return store.one(sql, args)[0]


admin.STORE = admin.Store(db)
admin.CATALOGUE = cat
admin.CFG = {"ADMIN_PATH": "p"}


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        ("%s: %s\r\n" % (k, v)).encode("latin-1")
        self.sent[k] = v

    def end_headers(self):
        pass

    class _W:
        def write(self, b):
            pass
    wfile = _W()


for n in ("action", "redirect", "send"):
    setattr(Rec, n, getattr(admin.Admin, n))
Rec.one = staticmethod(admin.Admin.one)

print("before")
by_ip, _ = store.profiles(cat, default)
check("the relays are told about both accounts' addresses",
      {"5.52.1.1", "5.52.1.2", "2.176.4.9"} <= set(by_ip), str(sorted(by_ip)))

print("deleting one account")
r = Rec()
r.action("user-delete", {"id": [str(gone)]})
where = r.sent.get("Location", "")
check("the panel says it is done", r.sent.get("code") == 303 and "m=" in where
      and "%21" not in where.split("m=", 1)[1][:3], where)
check("the account is gone",
      count("SELECT count(*) FROM users WHERE id = ?", (gone,)) == 0)
check("its addresses with it",
      count("SELECT count(*) FROM ips WHERE user_id = ?", (gone,)) == 0)
check("its receipts",
      count("SELECT count(*) FROM transactions WHERE user_id = ?", (gone,)) == 0)
check("its sign-ins",
      count("SELECT count(*) FROM panel_sessions WHERE user_id = ?", (gone,)) == 0)
check("and the usage readings of its addresses",
      count("SELECT count(*) FROM ip_counters WHERE ip IN ('5.52.1.1', '5.52.1.2')") == 0)

print("nobody else is touched")
check("the other account is there",
      count("SELECT count(*) FROM users WHERE id = ?", (kept,)) == 1)
check("with its address, receipt and sign-in",
      count("SELECT count(*) FROM ips WHERE user_id = ?", (kept,)) == 1
      and count("SELECT count(*) FROM transactions WHERE user_id = ?", (kept,)) == 1
      and count("SELECT count(*) FROM panel_sessions WHERE user_id = ?", (kept,)) == 1)
check("and its usage reading",
      count("SELECT count(*) FROM ip_counters WHERE ip = '2.176.4.9'") == 1)

print("so the relays stop serving it")
by_ip, _ = store.profiles(cat, default)
check("its addresses are no longer sent to the relays",
      not ({"5.52.1.1", "5.52.1.2"} & set(by_ip)), str(sorted(by_ip)))
check("the other account's still are", "2.176.4.9" in by_ip)

print("an account that is not there")
before = count("SELECT count(*) FROM users")
r = Rec()
r.action("user-delete", {"id": ["99999"]})
check("is reported as not found", "m=%21" in r.sent.get("Location", ""),
      r.sent.get("Location", ""))
check("and nothing is deleted", count("SELECT count(*) FROM users") == before)
r = Rec()
r.action("user-delete", {"id": [""]})
check("nor with no id at all", count("SELECT count(*) FROM users") == before)

print("the button asks first, naming the account")
nasty = "x'\"<b>"
store.run("UPDATE users SET username = ? WHERE id = ?", (nasty, kept))
page = admin.Admin.users(None)
form = re.search(r"<form method='post' action='/p/user-delete' onsubmit='([^']*)'>", page)
check("every row has one", form is not None and page.count("/p/user-delete") == 1,
      str(page.count("/p/user-delete")))
attr = form.group(1) if form else ""
check("the question stays inside its attribute, whatever the name",
      "<" not in attr and '"' not in attr and "&#x27;" in attr, attr)
asked = html.unescape(attr)
check("and is the question it looks like",
      asked.startswith("return confirm(") and
      json.loads(asked[len("return confirm("):-1]).startswith("«%s»" % nasty), asked)
check("the button is not the same red as the block beside it",
      "<button class='del'" in page and "button.del{" in admin.CSS)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
