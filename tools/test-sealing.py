#!/usr/bin/env python3
"""The pictures customers send are kept sealed.

What has to hold: a receipt or a ticket picture is sealed with AES-256-GCM as
it is written, and the ones from before are sealed where they lie at the
panel's start - after which their bytes are nowhere in the database file;
both panels hand the picture back as it was; a sealed picture that was
tampered with, or sealed with another machine's key, does not open, and
says so instead of serving noise; a key is never made while sealed data is
already there, since that data's key is only missing, not replaced; the
key is not in the database or its backup, the backup shows whether its
pictures open here, and the admin panel offers the key for keeping.
"""
import base64
import importlib.machinery
import importlib.util
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
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
panel.log = lambda *a: None
panel.print = lambda *a, **k: None
admin.log = lambda *a: None

if panel.libcrypto() is None:
    print("no libcrypto here - skipping")
    sys.exit(0)

tmp = tempfile.mkdtemp()
KEY = os.path.join(tmp, "db.key")
for m in (panel, admin):
    m.KEY_FILE = KEY
DB = os.path.join(tmp, "panel.db")
store = panel.Store(DB)
panel.SEALED_CHECK.append(lambda: panel.count_sealed(store.db))
admin.DB = DB
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(DB)
admin.SEALED_CHECK.append(lambda: admin.count_sealed(admin.STORE.db))

MARK = b"BANK-SLIP-CARD-6037-9911-2233"
PNG = b"\x89PNG\r\n\x1a\n" + MARK + os.urandom(2000)
u = store.create_user(111, "ali", "علی")

print("from before sealing")
# As the panel wrote them before this version: the bytes as they came.
store.run("INSERT INTO transactions (user_id, amount, kind, receipt_blob, receipt_type,"
          " status, created_at) VALUES (?, 200000, 'card', ?, 'image/png', 'pending', ?)",
          (u["id"], PNG, panel.now()))
store.run("INSERT INTO tickets (user_id, subject, status, created_at, updated_at)"
          " VALUES (?, 's', 'open', ?, ?)", (u["id"], panel.now(), panel.now()))
tid = store.one("SELECT id FROM tickets")["id"]
store.run("INSERT INTO ticket_messages (ticket_id, from_admin, body, image_blob, image_type,"
          " created_at) VALUES (?, 0, 'b', ?, 'image/png', ?)", (tid, PNG, panel.now()))
store.run("INSERT INTO ticket_messages (ticket_id, from_admin, body, created_at)"
          " VALUES (?, 0, 'no picture', ?)", (tid, panel.now()))
check("no key before anything needed one", not os.path.exists(KEY))
done = panel.seal_existing(store)
check("the panel's start seals both pictures where they lie", done == 2, str(done))
check("and makes the key for it, readable by root alone",
      os.path.exists(KEY) and (os.name == "nt" or (os.stat(KEY).st_mode & 0o777) == 0o600))
raw = store.one("SELECT receipt_blob FROM transactions")["receipt_blob"]
check("what is kept is sealed, and only a little bigger",
      bytes(raw).startswith(panel.SEALED) and len(raw) == len(PNG) + 32)
check("a message with no picture is left alone",
      store.one("SELECT image_blob FROM ticket_messages WHERE body = 'no picture'")["image_blob"] is None)
check("a second start seals nothing twice", panel.seal_existing(store) == 0)
store.q("PRAGMA wal_checkpoint(TRUNCATE)")
blob = b""
for name in os.listdir(tmp):
    if name.startswith("panel.db"):
        with open(os.path.join(tmp, name), "rb") as fh:
            blob += fh.read()
check("the slip's bytes are nowhere in the database files any more", MARK not in blob)
key_hex = open(KEY).read().strip()
check("and neither is the key", key_hex.encode() not in blob and bytes.fromhex(key_hex) not in blob)

print("reading them back")
got = panel.unseal(raw)
check("sealed and opened is the picture as it was", got == PNG)
api = panel.BotAPI.__new__(panel.BotAPI)
api.store, api.relays = store, ()
rid = store.one("SELECT id FROM transactions")["id"]
code, res = api.api_admin_receipt_image({}, str(rid))
check("the bot API hands the receipt back as it was",
      code == 200 and base64.b64decode(res["data"]) == PNG)
mid = store.one("SELECT id FROM ticket_messages WHERE image_blob IS NOT NULL")["id"]


class Rec:
    def __init__(self):
        self.sent = None

    def send(self, body, code=200, headers=None):
        self.sent = (code, body, headers or {})


r = Rec()
admin.Admin.send_receipt(r, str(rid))
check("so does the admin panel's receipt page", r.sent[0] == 200 and r.sent[1] == PNG)
r = Rec()
admin.Admin.send_ticket_image(r, str(mid))
check("and its ticket picture", r.sent[0] == 200 and r.sent[1] == PNG)
check("everything that writes a picture seals it",
      open(os.path.join(ROOT, "templates/smartdns-panel"), encoding="utf-8").read().count("seal(blob)") == 4
      and "seal(blob) if blob else blob" in open(os.path.join(ROOT, "templates/smartdns-admin"),
                                                 encoding="utf-8").read())
fresh = panel.seal(PNG)
check("a new one is sealed with a fresh nonce each time",
      fresh.startswith(panel.SEALED) and fresh != raw and panel.unseal(fresh) == PNG)

print("what does not open")
bad = bytearray(raw)
bad[40] ^= 1
check("a tampered picture does not open", panel.unseal(bytes(bad)) is None)
store.run("UPDATE transactions SET receipt_blob = ? WHERE id = ?", (bytes(bad), rid))
r = Rec()
admin.Admin.send_receipt(r, str(rid))
check("and the admin panel says so rather than serving noise", r.sent[0] == 409)
code, res = api.api_admin_receipt_image({}, str(rid))
check("so does the bot API", code == 409 and res["error"] == "image_sealed")
store.run("UPDATE transactions SET receipt_blob = ? WHERE id = ?", (raw, rid))

print("the key")
out = os.path.join(tmp, "backup.db")
admin.STORE.snapshot(out)
counts = admin.inspect_backup(out)
check("a backup says how many pictures are sealed, and that they open here",
      counts["sealed"] == 2 and counts["sealed_opens"] is True, repr(counts))
os.rename(KEY, KEY + ".away")
check("with the key gone, nothing sealed opens", panel.unseal(raw) is None)
check("and no new key is made over it", panel.db_key() is None and not os.path.exists(KEY))
check("new pictures are kept as they are until it is back", panel.seal(PNG) == PNG)
note = admin.sealing_note("p")
check("the admin panel says the key is missing, and how many pictures need it",
      "روی این سرور نیست" in note and "2 عکس" in note, note[:200])
with open(KEY, "w") as fh:
    fh.write(os.urandom(32).hex() + "\n")
counts = admin.inspect_backup(out)
check("a backup from under another key says its pictures do not open here",
      counts["sealed_opens"] is False)
admin.PENDING.update({"path": out, "counts": counts, "size": 1})
page = admin.Admin.restore_page(Rec())
check("and the restore page names the key file to bring", "db.key" in page and "باز نمی‌شود" in page)
os.remove(KEY)
os.rename(KEY + ".away", KEY)
check("with the right key back, it all opens again", panel.unseal(raw) == PNG)
note = admin.sealing_note("p")
check("the settings page explains the key and offers it for keeping",
      "/p/db.key" in note and "دانلود کلید" in note)
r = Rec()
r.redirect = lambda *a, **k: None
admin.Admin.send_key(r)
check("which hands back the key itself", r.sent[0] == 200 and r.sent[1].decode().strip() == key_hex)
import threading
race = os.path.join(tmp, "race.key")
panel.KEY_FILE = race
# A machine with nothing sealed yet, as on a first start.
saved_check = list(panel.SEALED_CHECK)
panel.SEALED_CHECK[:] = [lambda: 0]
keys = []
threads = [threading.Thread(target=lambda: keys.append(panel.db_key())) for _ in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("twenty starting at once all get the same whole key, and no scraps are left",
      len(keys) == 20 and None not in keys and len(set(keys)) == 1
      and not [n for n in os.listdir(tmp) if n.endswith(".tmp")], repr(set(keys))[:80])
panel.KEY_FILE = KEY
panel.SEALED_CHECK[:] = saved_check
print("made by the installer")
fresh_dir = tempfile.mkdtemp()
panel.KEY_FILE = os.path.join(fresh_dir, "db.key")
panel.DB = os.path.join(fresh_dir, "panel.db")
panel.SEALED_CHECK[:] = []
check("--make-key makes the key on a machine with nothing sealed",
      panel.make_key() == 0 and os.path.exists(panel.KEY_FILE))
os.remove(panel.KEY_FILE)
panel.SEALED_CHECK[:] = []
s2 = panel.Store(panel.DB)
someone = s2.create_user(333, "reza", "رضا")
s2.run("INSERT INTO transactions (user_id, amount, kind, receipt_blob, status, created_at)"
       " VALUES (?, 1, 'card', ?, 'pending', ?)", (someone["id"], raw, panel.now()))
panel.SEALED_CHECK[:] = []
check("but not over a lost one, and says so by its exit",
      panel.make_key() == 1 and not os.path.exists(panel.KEY_FILE))
logic = open(os.path.join(ROOT, "tools/installer-logic.sh"), encoding="utf-8").read()
check("the installer makes it before the panel starts, as root",
      logic.index("/usr/local/bin/smartdns-panel --make-key")
      < logic.index("systemctl restart smartdns-panel.service"))
check("and uninstall leaves it be", "note_file /etc/smart-dns/db.key" not in logic)
panel.KEY_FILE = KEY
psrc = open(os.path.join(ROOT, "templates/smartdns-panel"), encoding="utf-8").read()
check("deleted data is zeroed, not left in free pages",
      'PRAGMA secure_delete = ON' in psrc
      and 'PRAGMA secure_delete = ON' in open(os.path.join(ROOT, "templates/smartdns-admin"),
                                               encoding="utf-8").read())

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
