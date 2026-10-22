#!/usr/bin/env python3
"""No ceiling on templates, only what they cost.

What has to hold: the admin can make as many templates as they like; the
templates page says how many are in use and what each costs every relay; the
relay gives each template in use its own port in 5300-5999 and never one
past it; and the installer keeps a tunnel off that range.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
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
for m in (panel, admin, sync):
    m.log = lambda *a: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/templates"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "template_list"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


print("making templates")
for i in range(12):
    act("template-new", name="قالب %d" % i)
check("far more than the old eight", store.one("SELECT count(*) c FROM templates")["c"] == 13)
for i, tid in enumerate((2, 3, 4)):
    store.run("INSERT INTO users (username, created_at, status, template_id)"
              " VALUES (?, ?, 'active', ?)", ("u%d" % i, panel.now(), tid))
store.run("INSERT INTO users (username, created_at, status, template_id)"
          " VALUES ('d', ?, 'active', NULL)", (panel.now(),))
store.run("INSERT INTO users (username, created_at, status, template_id)"
          " VALUES ('x', ?, 'pending', 5)", (panel.now(),))
page = Rec().template_list()
check("the page says how many are in use, and what that costs each relay",
      "الان 3 قالب مشتری دارند" in page and "حدود 24 مگابایت" in page, page[-600:])

print("the relay")
d_main, d_base, d_prof = (os.path.join(tmp, x) for x in ("main", "base", "prof"))
for d in (d_main, d_base, d_prof):
    os.makedirs(d)
open(os.path.join(d_main, "smart-dns.conf"), "w").write("no-resolv\n")
sync.DNSMASQ_D, sync.BASE_DIR, sync.PROFILE_DIR = d_main, d_base, d_prof
sync.HIJACK_CONF = os.path.join(d_main, "smart-dns.conf")
sync.BYPASS_CONF = os.path.join(d_main, "none.conf")
sync.EPIC_PINS = os.path.join(d_main, "none2.conf")
sync.CFG = {"SELF_IP": "198.51.100.1"}
sync.sync_base_dir = lambda: False


class R:
    stdout, returncode, stderr = "", 0, ""


sync.sh = lambda *a: R()
sync.nft = lambda *a: R()
sync.PROFILE_PORTS = 5
with contextlib.redirect_stdout(io.StringIO()):
    ports = sync.apply_profiles({str(i): {"routed": []} for i in range(1, 8)}, {})
check("each template in use gets a port of its own from 5300",
      sorted(ports.values()) == [5300, 5301, 5302, 5303, 5304])
check("  and never one past the range", max(ports.values()) < 5300 + sync.PROFILE_PORTS
      and len(os.listdir(d_prof)) == 5)
sync.PROFILE_PORTS = 700
check("the range is 5300-5999", sync.PROFILE_BASE_PORT + 700 - 1 == 5999)
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("the installer keeps a tunnel off it",
      '[ "$p" -ge 5299 ] && [ "$p" -le 5999 ]' in logic)
check("  and so does the admin panel",
      "۵۹۹۹" in admin.tunnel_port_problem(5800, "198.51.100.1", "reverse"))

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
