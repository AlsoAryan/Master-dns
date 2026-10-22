#!/usr/bin/env python3
"""A renewed certificate is served without restarting anything.

Renewal writes new files and moves the live/ links; nothing tells the Python
servers. They loaded the certificate once at start, so after a renewal the
admin panel, the customer panel and the bot API went on serving the old one
from memory until it expired in every visitor's browser - some thirty days
later, unless an upgrade happened to restart them first.

Each is checked the same way, over a real TLS connection: the next connection
after the files change gets the new certificate. And the moment between the
two links moving - a new certificate beside the old key - neither breaks the
server nor loses the certificate it was serving.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

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


if shutil.which("openssl") is None:
    print("openssl not available - skipping")
    sys.exit(0)

tmp = tempfile.mkdtemp()


def make_pair(name):
    cert, key = os.path.join(tmp, name + ".crt"), os.path.join(tmp, name + ".key")
    # MSYS_NO_PATHCONV: on git-bash for Windows a leading slash in -subj is
    # taken for a path and rewritten.
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=" + name],
                   capture_output=True, env=dict(os.environ, MSYS_NO_PATHCONV="1"))
    if not os.path.exists(cert):
        print("could not make a test certificate - skipping")
        sys.exit(0)
    return cert, key


PAIRS = {n: make_pair(n) for n in ("old", "new", "newer")}
DER = {n: ssl.PEM_cert_to_DER_cert(open(c).read()) for n, (c, k) in PAIRS.items()}


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(HERE, "..", "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


warnings = []
admin = load("smartdns-admin", "admin")
sync = load("smartdns-sync", "sync")
panel = load("smartdns-panel", "panel")
for m in (admin, sync, panel):
    m.log = lambda level, msg: warnings.append(msg) if level == m.WARN else None
    m.HANDSHAKE_TIMEOUT = 3
    m.IO_TIMEOUT = 2


def put(live, which, part):
    """Swap one of the live files to `which`, as a renewal moves one link."""
    src = PAIRS[which][0 if part == "cert" else 1]
    dst = os.path.join(live, "fullchain.pem" if part == "cert" else "privkey.pem")
    shutil.copyfile(src, dst)
    # A clearly later time: some filesystems keep mtimes to the second.
    bump = time.time() + len(warnings) + 10 + hash((which, part)) % 50
    os.utime(dst, (bump, bump))


def served(port):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
    return next((n for n, d in DER.items() if d == der), "unknown")


def start(kind, live):
    cert, key = os.path.join(live, "fullchain.pem"), os.path.join(live, "privkey.pem")
    if kind == "bot":
        server = panel.BotServer(("127.0.0.1", 0), cert, key)
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server = (admin.make_admin_server(ctx, 0) if kind == "admin"
                  else sync.make_panel_server(ctx, port=0))
        server.follow(cert, key)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


for kind, label in (("admin", "the admin panel"), ("sync", "the customer panel"),
                    ("bot", "the bot API")):
    print(label)
    live = os.path.join(tmp, kind)
    os.makedirs(live)
    put(live, "old", "cert")
    put(live, "old", "key")
    server = start(kind, live)
    port = server.server_address[1]
    check("serves the certificate it started with", served(port) == "old")

    put(live, "new", "cert")
    put(live, "new", "key")
    check("the next connection after a renewal gets the new one",
          served(port) == "new")

    before = len(warnings)
    put(live, "newer", "cert")          # the first link has moved, not the second
    first = served(port)
    second = served(port)
    check("half-way through a renewal it keeps serving the one it had",
          first == "new" and second == "new", "%s, %s" % (first, second))
    check("and says so once, not once per visitor", len(warnings) - before == 1,
          str(warnings[before:]))
    put(live, "newer", "key")           # and now the second
    check("once both files have moved, the new pair is served",
          served(port) == "newer")

    os.unlink(os.path.join(live, "privkey.pem"))
    check("a file gone missing does not take the server down",
          served(port) == "newer")
    server.shutdown()

print("where they are started")
src_admin = open(os.path.join(HERE, "..", "templates", "smartdns-admin"), encoding="utf-8").read()
src_sync = open(os.path.join(HERE, "..", "templates", "smartdns-sync"), encoding="utf-8").read()
check("the admin panel follows its certificate", "httpd.follow(cert, key)" in src_admin)
check("so does the customer panel", "httpd.follow(cert, key)" in src_sync)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
