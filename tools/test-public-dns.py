#!/usr/bin/env python3
"""Public DNS: one button that opens every relay to everyone.

What has to hold: the owner turns it on and off from the settings page; the
panel tells the relays; a relay that was closed opens while it is on and
closes again when it goes off - but one an operator opened by hand stays
open, and a fresh relay waiting to close does not close while it is on.
"""
import importlib.machinery
import importlib.util
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
sync = load("templates/smartdns-sync", "sync")
admin.log = panel.log = sync.log = lambda *a: None

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/settings"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    admin.REQ.admin = None
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


print("the admin panel")
check("the settings card offers to turn it on", "روشن کردن DNS عمومی" in admin.public_dns_card("p"))
act("public-dns", to="1")
check("turned on", store.setting("public_dns") == "1"
      and "خاموش کردن DNS عمومی" in admin.public_dns_card("p"))
check("only the owner's: not a seller's to press", admin.ACTION_SECTION["public-dns"] == "settings"
      and "settings" not in admin.RESELLER_SECTIONS)
check("the update from GitHub is on the settings page now, not the nodes page",
      "github_card" not in admin.nodes_page.__code__.co_names)

print("the relay")
sync.ENFORCE_FILE = os.path.join(tmp, "enforce.conf")
sync.PUBLIC_MARK = os.path.join(tmp, "public-dns")
sync.AUTO_ENFORCE = os.path.join(tmp, "auto-enforce")
ran = []


class R:
    returncode, stdout, stderr = 0, "", ""


def fake_run(args, **k):
    ran.append(args[1:])
    if args[1:3] == ["enforce", "off"]:
        if os.path.exists(sync.ENFORCE_FILE):
            os.remove(sync.ENFORCE_FILE)
    elif args[1:3] == ["enforce", "on"]:
        open(sync.ENFORCE_FILE, "w").close()
    elif args[1:3] == ["enforce", "status"]:
        R.stdout = "enforcing" if os.path.exists(sync.ENFORCE_FILE) else "open"
    return R()


sync.subprocess = type("S", (), {"run": staticmethod(fake_run)})
open(sync.ENFORCE_FILE, "w").close()
check("a closed relay opens while it is on", sync.follow_public(True, 5)
      and not os.path.exists(sync.ENFORCE_FILE) and os.path.exists(sync.PUBLIC_MARK))
check("  once", not sync.follow_public(True, 5))
check("and closes again when it goes off", sync.follow_public(False, 5)
      and os.path.exists(sync.ENFORCE_FILE) and not os.path.exists(sync.PUBLIC_MARK))
os.remove(sync.ENFORCE_FILE)
ran.clear()
sync.follow_public(True, 5)
sync.follow_public(False, 5)
check("a relay opened by hand stays open either way", not os.path.exists(sync.ENFORCE_FILE)
      and not any(a[:2] == ["enforce", "on"] for a in ran))
open(sync.AUTO_ENFORCE, "w").close()
sync.follow_public(True, 5)
check("a fresh relay waiting to close does not close while it is on",
      not os.path.exists(sync.ENFORCE_FILE) and os.path.exists(sync.AUTO_ENFORCE))
sync.follow_public(False, 5)
check("  and closes once it is off", os.path.exists(sync.ENFORCE_FILE))

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
