#!/usr/bin/env python3
"""The panels' font: Vazirmatn, carried in the installer, served by the panels.

Pages opened from Iran cannot count on any font host, so the font travels in
the installer and each panel serves it itself. Checked here: that the file is
the one its makers published, that it survives the trip through the installer
byte for byte, and that both panels serve it - the admin panel before anyone
has signed in, since the sign-in page is drawn in it, and only under its
secret path.
"""
import base64
import hashlib
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

FONT = os.path.join(ROOT, "common", "fonts", "Vazirmatn[wght].woff2")
OFL = os.path.join(ROOT, "common", "fonts", "OFL.txt")
# The vazirmatn 33.0.3 package's own hashes, as jsdelivr publishes them.
FONT_SHA256 = "Tj+iF9OP2vwf6kQUzrWMpeZizwq1+nNajIwg6LQsrZI="
OFL_SHA256 = "F+NVBnyChPR3Q6HuOx73/2hP8GAe2jV/k1OxCzAWqzE="


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha(blob):
    return base64.b64encode(hashlib.sha256(blob).digest()).decode()


def payload(built, name):
    body = built[built.index("#__BEGIN_%s__\n" % name) + len("#__BEGIN_%s__\n" % name):
                 built.index("\n#__END_%s__" % name)]
    return "\n".join(line[1:] for line in body.split("\n"))


def get(handler, path):
    """One GET through a panel's handler, without a socket."""
    h = handler.__new__(handler)
    h.path = path
    h.headers = {}
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.requestline = "GET %s HTTP/1.1" % path
    h.client_address = ("198.51.100.7", 40000)
    h.close_connection = False
    h.wfile = io.BytesIO()
    h._t0 = time.monotonic()
    h.do_GET()
    head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    hdrs = dict((k.lower(), v) for k, v in
                (line.split(": ", 1) for line in lines[1:] if ": " in line))
    return int(lines[0].split()[1]), hdrs, body


blob = open(FONT, "rb").read()
licence = open(OFL, encoding="utf-8").read()

print("the file")
check("the font is the one its makers published", sha(blob) == FONT_SHA256, sha(blob))
check("and so is its licence", sha(open(OFL, "rb").read()) == OFL_SHA256)
check("which is the OFL, that lets it be carried", "SIL Open Font License" in licence)
check("a woff2 font", blob[:4] == b"wOF2")

print("through the installer")
build = load(os.path.join(HERE, "build-installer.py"), "build")
build.main()
built = open(os.path.join(ROOT, "master-dns.sh"), encoding="utf-8").read()
check("the font comes out byte for byte",
      base64.b64decode("".join(payload(built, "FONT").split())) == blob)
check("its licence goes with it", payload(built, "FONT_LICENSE") == licence.rstrip("\n"))
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("the installer unpacks it", "payload FONT | base64 -d" in logic)
start = logic.index('step "Admin web panel"')
exit_block = logic[start:logic.index("SYNC_TOKEN_OUT=", start)]
relay_at = logic.index("payload SYNC > /usr/local/bin/smartdns-sync",
                       logic.index('step "Panel: sync agent and claim page"'))
check("on the exit, with the admin panel", "install_font" in exit_block)
check("and on the relay, with the users panel",
      "install_font" in logic[relay_at:relay_at + 200])

panel = load(os.path.join(ROOT, "templates", "smartdns-panel"), "panel")
admin = load(os.path.join(ROOT, "templates", "smartdns-admin"), "admin")
sync = load(os.path.join(ROOT, "templates", "smartdns-sync"), "sync")
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
panel.Store(db)
admin.STORE = admin.Store(db)
admin.CFG = {"ADMIN_PATH": "p"}
admin.CATALOGUE = json.load(open(os.path.join(ROOT, "domains", "services.json"),
                                 encoding="utf-8"))["services"]

print("the admin panel serves it")
admin.FONT_FILE = FONT
code, hdrs, body = get(admin.Admin, "/p/vazirmatn.woff2")
check("under its secret path, before anyone signs in", code == 200, str(code))
check("as a font", hdrs.get("content-type") == "font/woff2")
check("for the browser to keep", "immutable" in hdrs.get("cache-control", ""))
check("the whole file", body == blob)
code, _, body = get(admin.Admin, "/vazirmatn.woff2")
check("but not to someone without the path", code == 404 and body != blob)
admin._FONT[:] = []
admin.FONT_FILE = os.path.join(tmp, "missing.woff2")
code, _, _ = get(admin.Admin, "/p/vazirmatn.woff2")
check("an install without the file answers 404, and the system font is used",
      code == 404)
page = admin.page("x", "", admin.CFG)
want = "url('/p/vazirmatn.woff2?v=%s')" % admin.FONT_VERSION
check("every page asks for it at that address", want in page)
check("the sign-in page too", want in admin.login_page(admin.CFG))
check("and draws its text in it",
      re.search(r"body\{[^}]*font:[^;]*\bVazirmatn,", admin.CSS) is not None)

print("the users panel serves it")
sync.FONT_FILE = FONT
code, hdrs, body = get(sync.UserPanel, "/vazirmatn.woff2")
check("at its own address", code == 200 and body == blob, str(code))
check("for the browser to keep", "immutable" in hdrs.get("cache-control", ""))
check("its pages ask for it",
      "url('/vazirmatn.woff2?v=%s')" % sync.FONT_VERSION in sync.user_page("x"))
check("and draw their text in it",
      re.search(r"body\{[^}]*font:[^;]*\bVazirmatn,", sync.USER_CSS) is not None)
check("both panels name the same version", admin.FONT_VERSION == sync.FONT_VERSION)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
