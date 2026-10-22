#!/usr/bin/env python3
"""The panel's backup, sent by the operator's bot every few days.

What has to hold: the bundle carries the database, the pictures' sealing key
and what a new machine needs to be this panel for the relays; it is
encrypted with the operator's password and opens with it, and with nothing
else; it goes to every admin the bot knows, not at all past Telegram's size,
and it says why when it cannot; it is off until the operator turns it on,
due every so many days, and a failed one is tried again within the hour; and
a bundle uploaded with its password restores like any backup, its sealing
key with it.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
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
          ((" - " + str(detail)[:500]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, rel):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(
        name, os.path.join(ROOT, rel)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if not shutil.which("openssl"):
    print("  --   no openssl here - the backup is left to the live test")
    sys.exit(0)

tmp = tempfile.mkdtemp()
panel = load("panel", "templates/smartdns-panel")
panel.print = lambda *a, **k: None
admin = load("admin", "templates/smartdns-admin")
admin.log = admin.log_exception = lambda *a, **k: None
admin.DB = os.path.join(tmp, "panel.db")
panel.Store(admin.DB)
admin.STORE = admin.Store(admin.DB)
admin.STORE.run("INSERT INTO users (telegram_id, username, status, created_at)"
                " VALUES (1, 'ali', 'active', '2026-01-01')")
admin.CFG = {"ADMIN_PATH": "p"}
files = {}
for name, _ in admin.BUNDLE_FILES:
    path = os.path.join(tmp, name)
    with open(path, "w", newline="\n") as fh:
        fh.write("contents of %s\n" % name)
    files[name] = path
admin.BUNDLE_FILES = tuple((n, files[n]) for n, _ in admin.BUNDLE_FILES)
admin.BOT_ENV = files["master-dns-bot.env"]
with open(admin.BOT_ENV, "w") as fh:
    fh.write("BOT_TOKEN=123:secret\nADMIN_IDS=111,222\n")
admin.BACKUP_PASS_FILE = os.path.join(tmp, "backup.pass")
admin.KEY_FILE = files["db.key"]

print("the bundle")
blob = admin.make_bundle("correct horse")
check("encrypted, openssl's way", blob.startswith(b"Salted__") and b"contents of" not in blob)
opened = admin.open_bundle(blob, "correct horse")
check("the password opens it: the database and what a new panel needs",
      set(opened) == {"panel.db", "panel.env", "sync.key", "sync.crt", "admin.env",
                      "master-dns-bot.env", "db.key"}
      and opened["sync.key"] == b"contents of sync.key\n")
db = os.path.join(tmp, "check.db")
with open(db, "wb") as fh:
    fh.write(opened["panel.db"])
check("  and the database is whole",
      sqlite3.connect(db).execute("SELECT username FROM users").fetchone()[0] == "ali")
try:
    admin.open_bundle(blob, "wrong")
    wrong = False
except ValueError:
    wrong = True
check("nothing else opens it", wrong)

print("sent by the bot")
sent = []


class Res:
    def __init__(self, ok=True):
        self.ok = ok

    def read(self):
        return json.dumps({"ok": self.ok}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    sent.append((req.full_url, req.data))
    return Res()


admin.urllib.request.urlopen = fake_urlopen
why = admin.telegram_send_document(b"FILEBYTES", "b.enc", "caption")
check("to every admin the bot knows, as a document",
      why == "" and len(sent) == 2 and all("/bot123:secret/sendDocument" in u for u, _ in sent)
      and b'name="chat_id"\r\n\r\n111' in sent[0][1] and b'name="chat_id"\r\n\r\n222' in sent[1][1]
      and b'filename="b.enc"' in sent[0][1] and b"FILEBYTES" in sent[0][1])
sent.clear()
admin.TELEGRAM_MAX = 4
check("not past what Telegram takes, and it says so",
      "۵۰ مگ" in admin.telegram_send_document(b"FILEBYTES", "b.enc", "c") and not sent)
admin.TELEGRAM_MAX = 49 * 1024 * 1024
with open(admin.BOT_ENV, "w") as fh:
    fh.write("BOT_TOKEN=123:secret\n")
check("  nor without an admin to send it to",
      "ادمین" in admin.telegram_send_document(b"x", "b.enc", "c"))
with open(admin.BOT_ENV, "w") as fh:
    fh.write("BOT_TOKEN=123:secret\nADMIN_IDS=111\n")

print("every so many days")
check("off until the operator turns it on", not admin.backup_due())


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, **form):
    r = Rec()
    admin.Admin.action(r, what, {k: [v] for k, v in form.items()})
    return r.to


check("turning it on wants a password", act("backup-auto", on="1", days="3").startswith(
    "settings?m=!"))
check("  of eight or more", act("backup-auto", on="1", days="3", password="short").startswith(
    "settings?m=!"))
act("backup-auto", on="1", days="3", password="correct horse")
check("  kept apart from the database, the days in the settings",
      admin.backup_password() == "correct horse"
      and admin.STORE.one("SELECT value FROM settings WHERE key = 'backup_every_days'")["value"]
      == "3")
check("then it is due at once", admin.backup_due())
sent.clear()
got = act("backup-now")
last = json.loads(admin.STORE.one("SELECT value FROM settings WHERE key = 'backup_last'")["value"])
check("sent now on request, and what happened kept",
      not got.startswith("settings?m=!") and last["ok"] and len(sent) == 1, got)
check("  not due again for three days", not admin.backup_due())
admin.STORE.run("UPDATE settings SET value = ? WHERE key = 'backup_last'",
                (json.dumps({"at": (admin.datetime.now(admin.timezone.utc)
                                    - admin.timedelta(hours=2)).isoformat(timespec="seconds"),
                             "ok": False, "error": "x", "size": 0}),))
check("  a failed one is tried again within the hour", admin.backup_due())
card = admin.backup_card("p")
check("the settings page: turned on, when the last went, the openssl line",
      "backup-auto" in card and " checked" in card and "openssl enc -d" in card
      and "correct horse" not in card)
act("backup-auto", days="3")
check("  and off", not admin.backup_due())

print("restored from a bundle")
restored = admin.make_bundle("correct horse")
boundary = "XyZ"
body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"b.enc\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n" % boundary).encode() + restored + (
    "\r\n--%s\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\ncorrect horse\r\n"
    "--%s--\r\n" % (boundary, boundary)).encode()


class Up(Rec):
    raw_body = body
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}


up = Up()
admin.Admin.take_upload(up)
check("a bundle and its password stage its database for the usual confirmation",
      up.to == "restore" and admin.PENDING.get("counts", {}).get("users") == 1
      and admin.PENDING.get("key") == b"contents of db.key\n", up.to)
Up.raw_body = body.replace(b"correct horse\r\n", b"nope\r\n")
up = Up()
admin.Admin.take_upload(up)
check("  a wrong password is said so", up.to.startswith("settings?m=!") and "رمز" in up.to)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
