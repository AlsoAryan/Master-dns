#!/usr/bin/env python3
"""Usage back to zero every so many days, for a customer or a seller.

What has to hold: the admin sets the number of days for a customer from
their row, and for a seller in their form; empty turns it off; the same
number again keeps the date it was counting to, a new one counts from today;
when the day comes the usage is zero again, a customer cut off for their
quota is back, the warnings about it are due again - and not the one about
the term ending - and the next reset is that many days on from the one that
was due, even when the server was off for a while.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone

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


panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
admin.log = panel.log = lambda *a: None
panel.print = lambda *a, **k: None
panel.emit = panel.emit_admin = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
GB = 1024 ** 3


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/users"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "users"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    admin.REQ.admin = None
    r = Rec()
    r.action(rest, {k: v if isinstance(v, list) else [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def ts(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat(timespec="seconds")


store.ensure_default_template([])
uid = store.run("INSERT INTO users (username, created_at, status, quota_bytes, used_bytes,"
                " quota_mode, warned) VALUES ('ali', ?, 'active', ?, ?, 'oneoff', 0)",
                (panel.now(), 10 * GB, 3 * GB)).lastrowid
row = lambda: store.one("SELECT * FROM users WHERE id = ?", (uid,))

print("a customer")
check("the box is in their row's menu", "user-reset-days" in Rec().users())
act("user-reset-days", id=str(uid), days="30")
first = row()["reset_next"]
check("set: every 30 days, the first 30 days from today",
      row()["reset_days"] == 30 and first[:10] == ts(days=30)[:10])
act("user-reset-days", id=str(uid), days="30")
check("  the same number again keeps its date", row()["reset_next"] == first)
act("user-reset-days", id=str(uid), days="7")
check("  a new one counts from today", row()["reset_next"][:10] == ts(days=7)[:10])
check("not a number is refused", "m=!" in act("user-reset-days", id=str(uid), days="x"))
panel.enforce_quotas(store)
check("before the day, nothing happens", row()["used_bytes"] == 3 * GB)

store.run("UPDATE users SET used_bytes = ?, status = 'over_quota', warned = ?,"
          " reset_next = ? WHERE id = ?",
          (10 * GB, 1 | 2 | panel.WARNED_EXPIRING, ts(days=-15), uid))
panel.enforce_quotas(store)
u = row()
check("on the day, the usage is zero and a customer cut off for it is back",
      u["used_bytes"] == 0 and u["status"] == "active")
check("  the warnings about it are due again, not the one about the term",
      u["warned"] == panel.WARNED_EXPIRING)
check("  the next is counted on from the one that was due, past today",
      u["reset_next"][:10] == ts(days=-15 + 7 * 3)[:10], u["reset_next"])
act("user-reset-days", id=str(uid), days="")
check("empty turns it off", row()["reset_days"] is None and row()["reset_next"] is None)
store.run("UPDATE users SET used_bytes = 5 WHERE id = ?", (uid,))
panel.enforce_quotas(store)
check("  and nothing is reset", row()["used_bytes"] == 5)

print("a seller")
admin.CONFIG = os.path.join(tmp, "admin.env")
act("admin-save", id="0", username="sara", password="sara-pw-123", own_only="1",
    cap_gb="100", reset_days="30", templates_mode="pick")
s = store.one("SELECT * FROM admins WHERE username = 'sara'")
check("set in their form", s["reset_days"] == 30 and s["reset_next"][:10] == ts(days=30)[:10])
act("admin-save", id=str(s["id"]), username="sara", password="", own_only="1",
    cap_gb="100", reset_days="30", templates_mode="pick")
check("  saving again keeps its date",
      store.one("SELECT reset_next FROM admins WHERE id = ?", (s["id"],))[0] == s["reset_next"])
store.run("UPDATE admins SET used_bytes = cap_bytes, warned = 7 | 8, reset_next = ?"
          " WHERE id = ?", (ts(hours=-1), s["id"]))
panel.check_sellers(store)
s2 = store.one("SELECT * FROM admins WHERE id = ?", (s["id"],))
check("on the day, their usage is zero and their customers served again",
      s2["used_bytes"] == 0 and s2["warned"] == 8 and s2["reset_next"] > panel.now())
act("admin-save", id=str(s["id"]), username="sara", password="", own_only="1",
    cap_gb="100", reset_days="", templates_mode="pick")
check("  empty turns it off",
      store.one("SELECT reset_days FROM admins WHERE id = ?", (s["id"],))[0] is None)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
