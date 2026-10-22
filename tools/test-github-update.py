#!/usr/bin/env python3
"""The main server upgraded from GitHub, then the others.

What has to hold: the panel finds the latest release and says so; the
installer is taken only when its hash is the one GitHub published for it -
or, where a release carries none, when it is the main branch's byte for byte
- and says the version it claims; an older or the same version is never
installed; the upgrade runs as a unit of its own; and when this panel is back
on the new version, the other servers are upgraded one by one, once.
"""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

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

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
admin.VERSION_FILE = os.path.join(tmp, "version")
admin.SELF_UPGRADE_DIR = os.path.join(tmp, "self-upgrade")
admin.INSTALLER_COPY = os.path.join(tmp, "installer.sh")
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
with open(admin.VERSION_FILE, "w") as fh:
    fh.write("0.9.0\n")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1,198.51.100.2\n")

NEW = b'#!/bin/bash\nVERSION="0.9.1"\n'
SHA = hashlib.sha256(NEW).hexdigest()
release = {"tag_name": "v0.9.1", "html_url": "https://github.com/x/releases/v0.9.1",
           "assets": [{"name": "master-dns.sh", "browser_download_url": "https://dl/master-dns.sh",
                       "digest": "sha256:" + SHA}]}
files = {"https://dl/master-dns.sh": NEW, admin.GITHUB_RAW: NEW}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout=0):
    url = req.full_url
    if url == admin.GITHUB_API:
        return Resp(json.dumps(release).encode())
    return Resp(files[url])


print("the latest release")
rel = admin.github_latest(opener)
check("its version, its installer and the hash GitHub published",
      rel["version"] == "0.9.1" and rel["url"] == "https://dl/master-dns.sh" and rel["sha"] == SHA)
check("its installer, checked", admin.fetch_release(rel, opener) == NEW)
files["https://dl/master-dns.sh"] = NEW + b"# tampered\n"
try:
    admin.fetch_release(rel, opener)
    ok = False
except RuntimeError:
    ok = True
check("not one with another hash", ok)
nohash = dict(rel, sha=None)
try:
    admin.fetch_release(nohash, opener)
    ok = False
except RuntimeError:
    ok = True
check("with no hash published, not one unlike the main branch's", ok)
files["https://dl/master-dns.sh"] = NEW
check("  and taken when it is the same", admin.fetch_release(nohash, opener) == NEW)
try:
    admin.fetch_release(dict(rel, version="0.9.2"), opener)
    ok = False
except RuntimeError:
    ok = True
check("not one that says another version", ok)

print("the nodes page")
admin.check_github(opener)
card = admin.github_card("p")
check("says a newer one is out, with the button", "0.9.1" in card and "self-upgrade" in card)
with open(admin.VERSION_FILE, "w") as fh:
    fh.write("0.9.1\n")
check("  and nothing to do when this server has it", "self-upgrade" not in admin.github_card("p"))
with open(admin.VERSION_FILE, "w") as fh:
    fh.write("0.9.0\n")

print("upgrading")
ran = []


class R:
    returncode, stdout, stderr = 0, "", ""


admin.subprocess = type("S", (), {"run": staticmethod(lambda args, **k: (ran.append(args), R())[1])})
r = admin.start_self_upgrade(NEW, "0.9.1")
check("the installer kept, and run as a unit of its own",
      open(os.path.join(admin.SELF_UPGRADE_DIR, "master-dns.sh"), "rb").read() == NEW
      and any(a[:2] == ["systemd-run", "--unit=smartdns-self-upgrade"] for a in ran))
check("  the page says it is under way", "در حال آپدیت همین سرور" in admin.github_card("p"))
check("before the new version is here, the others wait",
      admin.continue_after_self_upgrade() is None)
with open(admin.VERSION_FILE, "w") as fh:
    fh.write("0.9.1\n")
with open(admin.INSTALLER_COPY, "wb") as fh:
    fh.write(NEW)
store.set_setting("version:198.51.100.1", "0.9.0")
store.set_setting("version:198.51.100.2", "0.9.1")
msg = admin.continue_after_self_upgrade()
job = json.loads(store.setting("upgrade_job"))
check("back on it, the servers behind are upgraded one by one",
      job["current"] == "198.51.100.1" and job["version"] == "0.9.1" and "198.51.100.1" in msg)
check("  once", admin.continue_after_self_upgrade() is None)
with open(os.path.join(admin.SELF_UPGRADE_DIR, "rc"), "w") as fh:
    fh.write("1\n")
with open(os.path.join(admin.SELF_UPGRADE_DIR, "run.log"), "w") as fh:
    fh.write("ERROR: something\n")
check("a failed upgrade shows its log", "ERROR: something" in admin.github_card("p"))

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
