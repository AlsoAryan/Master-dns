#!/usr/bin/env python3
"""Files the admin panel hands over save as files.

What has to hold: a download - a receipt's picture, a backup, a config -
goes out with its own Content-Type and only that one. send() used to write
text/html first and the file's type after it; a phone's browser took the
first, nosniff held it there, and the file opened as a page instead of
saving. A page is still sent as a page.
"""
import importlib.machinery
import importlib.util
import io
import os
import sys

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


spec = importlib.util.spec_from_loader("admin", importlib.machinery.SourceFileLoader(
    "admin", os.path.join(HERE, "..", "templates", "smartdns-admin")))
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class Wire:
    def __init__(self):
        self.sent, self.wfile = [], io.BytesIO()

    def send_response(self, code):
        self.sent.append(("status", code))

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass


def types(wire):
    return [v for k, v in wire.sent if k == "Content-Type"]


w = Wire()
admin.Admin.send(w, b"\x89PNG...", 200, {"Content-Type": "image/png",
                                         "Content-Disposition": "inline; filename=receipt-7"})
check("a file goes out with its own type, once", types(w) == ["image/png"], str(types(w)))
check("  and the rest of what it was given", ("Content-Disposition",
                                              "inline; filename=receipt-7") in w.sent)
check("  the panel's own guards still on it", ("X-Content-Type-Options", "nosniff") in w.sent
      and ("Cache-Control", "no-store") in w.sent)
w = Wire()
admin.Admin.send(w, "[Interface]\n", 200, {"Content-Type": "text/plain; charset=utf-8",
                                           "Content-Disposition": "attachment; filename=a.conf"})
check("a text file to save, the same", types(w) == ["text/plain; charset=utf-8"]
      and w.wfile.getvalue() == b"[Interface]\n")
w = Wire()
admin.Admin.send(w, "<p>hi</p>")
check("a page is still a page", types(w) == ["text/html; charset=utf-8"])
headers = {"Content-Type": "image/png"}
admin.Admin.send(Wire(), b"x", 200, headers)
check("the caller's headers are left as they were", headers == {"Content-Type": "image/png"})

print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
