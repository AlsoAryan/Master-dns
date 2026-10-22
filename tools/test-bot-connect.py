#!/usr/bin/env python3
"""The bot past an address that never answers.

What has to hold: when one of Telegram's addresses takes no connection - an
IPv6 route that comes and goes - the bot moves to the next within a few
seconds, not after the call's whole timeout (five minutes for a QR or a
file), and tries the family that failed last for a while. Pictures, files
and downloads go the same way as messages, on a connection kept open, and a
refusal from Telegram is still an HTTPError, so a receipt that is not a
picture is still said in words.
"""
import http.server
import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.error

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


spec = importlib.util.spec_from_loader("bot", importlib.machinery.SourceFileLoader(
    "bot", os.path.join(ROOT, "examples", "telegram-bot", "bot.py")))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
seen = []


class Fake(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        blob = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        seen.append((self.path, self.headers.get("Content-Type", ""), blob))
        if self.path.endswith("/sendPhoto") and b"not-a-picture" in blob:
            return self.reply(400, b'{"ok":false,"description":"bad photo"}')
        result = {"file_path": "photos/a.jpg", "file_size": 3} if self.path.endswith(
            "/getFile") else {"message_id": 1}
        self.reply(200, json.dumps({"ok": True, "result": result}).encode())

    def do_GET(self):
        seen.append((self.path, "", b""))
        self.reply(200, b"JPG")


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]

# The first address, IPv6, takes nothing - an address into nowhere - the
# second, IPv4, is the server.
real = socket.getaddrinfo
DEAD = ("100::1", port, 0, 0)
tried = []


def addresses(host, p, *a, **k):
    if host == "telegram.test":
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", DEAD),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p))]
    return real(host, p, *a, **k)


bot.socket.getaddrinfo = addresses
plain = socket.socket


class Counted(plain):
    def connect(self, addr):
        tried.append(self.family)
        return super().connect(addr)


bot.socket.socket = Counted
bot.CONNECT_WAIT = 1

print("an address that never answers")
tg = bot.Telegram({"telegram": "http://telegram.test:%d" % port, "token": "1:x"})
start = time.time()
tg.send(5, "hi")
took = time.time() - start
check("the next address within seconds, not the call's whole timeout",
      took < 4 and seen[-1][0] == "/bot1:x/sendMessage", "%.1fs" % took)
check("  the family that failed is tried last for a while",
      bot.FAMILY_DOWN.get(socket.AF_INET6, 0) > time.time() + 500)
tried.clear()
start = time.time()
bot.Telegram({"telegram": "http://telegram.test:%d" % port, "token": "1:x"}).send(5, "again")
check("  so the next new connection goes straight to the one that works",
      tried == [socket.AF_INET] and time.time() - start < 1)
bot.FAMILY_DOWN[socket.AF_INET6] = time.time() - 1
tried.clear()
bot.Telegram({"telegram": "http://telegram.test:%d" % port, "token": "1:x"}).send(5, "later")
check("  and after that while, it is tried first again",
      tried[:1] == [socket.AF_INET6] and socket.AF_INET not in bot.FAMILY_DOWN)

print("pictures and files")
seen.clear()
start = time.time()
tg.photo(5, b"PNGDATA", "سرور 1")
tg.document(5, "master-dns-1.conf", b"[Interface]\n", "")
check("a QR and a file go on the connection kept open, as multipart",
      [p for p, _, _ in seen] == ["/bot1:x/sendPhoto", "/bot1:x/sendDocument"]
      and all(t.startswith("multipart/form-data; boundary=") for _, t, _ in seen)
      and b'filename="master-dns-1.conf"' in seen[1][2] and time.time() - start < 2)
said = []
tg.send = lambda chat, text, markup=None: said.append(text)
tg.photo(5, b"not-a-picture", "رسید")
check("  a picture Telegram refuses: said in words, as before",
      said and "رسید" in said[0])
try:
    tg.upload("sendPhoto", {"photo": b"not-a-picture"}, chat_id=5)
    refused = False
except urllib.error.HTTPError as e:
    refused = e.code == 400 and "1:x" not in str(e)
check("  the refusal an HTTPError, the token not in it", refused)
check("a download the same way", tg.download("abc") == b"JPG"
      and seen[-1][0] == "/file/bot1:x/photos/a.jpg")
src = open(os.path.join(ROOT, "examples", "telegram-bot", "bot.py"), encoding="utf-8").read()
check("nothing opens a connection of its own any more but through a proxy",
      src.count("urllib.request.urlopen(") == 1)

server.shutdown()
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
