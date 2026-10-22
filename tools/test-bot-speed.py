#!/usr/bin/env python3
"""The bot answers without making everybody wait.

What has to hold: the bot keeps one connection open to Telegram and one to
the panel for each thread that asks, instead of opening a new one - a new
TLS handshake - for every call; a connection the other end closed while it
sat idle is noticed and opened again, with nothing for the customer to see;
a server that closes after every answer is still served. Through a proxy from
the environment it stands aside for urllib. Updates are handled several at a
time: a chat's own always down the same lane, in their order, so a photo and
the text after it cannot race, and one slow answer holds up nobody else's.
A lost long poll is tried again at once.
"""
import http.server
import importlib.util
import json
import os
import socket
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(HERE, "..", "examples", "telegram-bot", "bot.py")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
spec = importlib.util.spec_from_file_location("botmod", BOT)
botmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(botmod)
botmod.log = lambda *a: None
SRC = open(BOT, encoding="utf-8").read()


def server(protocol, reply=None):
    """A local server counting the connections it is given."""
    seen = {"connections": 0, "requests": []}

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = protocol

        def setup(self):
            seen["connections"] += 1
            super().setup()

        def answer(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            seen["requests"].append((self.command, self.path, body,
                                     self.headers.get("Authorization")))
            code, out = (reply or (lambda path: (200, {"ok": True, "result": path})))(self.path)
            raw = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = answer

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen, "http://127.0.0.1:%d" % srv.server_address[1]


print("one connection, kept")
srv, seen, base = server("HTTP/1.1")
tg = botmod.Telegram({"telegram": base, "token": "T"})
got = [tg.call("sendMessage", chat_id=1, text="x") for _ in range(5)]
check("five calls to Telegram", got == ["/botT/sendMessage"] * 5 and len(seen["requests"]) == 5)
check("  over one connection, not five", seen["connections"] == 1, str(seen["connections"]))
check("  each carrying what was asked",
      json.loads(seen["requests"][0][2]) == {"chat_id": 1, "text": "x"})
other = []
t = threading.Thread(target=lambda: other.append(tg.call("getMe")))
t.start()
t.join()
check("another thread has a connection of its own", seen["connections"] == 2 and other)
panel = botmod.Panel({"api": base + "/api/v1", "key": "dd_k"})
before = seen["connections"]
for _ in range(3):
    panel.call("GET", "/users/5")
check("the panel's calls too, with the key",
      seen["connections"] == before + 1 and seen["requests"][-1][1] == "/api/v1/users/5"
      and seen["requests"][-1][3] == "Bearer dd_k")
srv.shutdown()

print("a connection closed while idle")
srv, seen, base = server("HTTP/1.1")
tg = botmod.Telegram({"telegram": base, "token": "T"})
tg.call("getMe")
tg.wire.local.conn.sock.shutdown(socket.SHUT_RDWR)      # as if Telegram had hung up
time.sleep(0.1)
try:
    again = tg.call("getMe")
except Exception as e:
    again = repr(e)
check("is opened again, and the customer sees nothing", again == "/botT/getMe", str(again))
check("  on a new one", seen["connections"] == 2)
srv.shutdown()

print("a server that closes after every answer")
srv, seen, base = server("HTTP/1.0")
panel = botmod.Panel({"api": base, "key": "k"})
check("is still served, call after call",
      [panel.call("GET", "/a")["result"], panel.call("GET", "/b")["result"]] == ["/a", "/b"])
srv.shutdown()

print("what the other end says")
srv, seen, base = server("HTTP/1.1", lambda path: (409, {"ok": False, "error": "has_username",
                                                         "message": "گرفته شده"})
                         if "panel" in path else (400, {"ok": False, "description": "bad chat"}))
try:
    botmod.Panel({"api": base + "/panel", "key": "k"}).call("POST", "/x", {"a": 1})
    err = None
except botmod.ApiError as e:
    err = e
check("the panel's refusal is an ApiError with its body",
      err is not None and err.status == 409 and err.body["error"] == "has_username")
try:
    botmod.Telegram({"telegram": base, "token": "T"}).call("sendMessage", chat_id=1)
    said = ""
except RuntimeError as e:
    said = str(e)
check("Telegram's is raised with its reason", "bad chat" in said, said)
srv.shutdown()

print("a file under its own name")
srv, seen, base = server("HTTP/1.1")
botmod.Telegram({"telegram": base, "token": "T"}).document(7, "dns.mobileconfig", b"<plist/>",
                                                           "how to install")
method, path, body, _ = seen["requests"][-1]
check("a document goes to sendDocument, named as given - an iPhone knows a profile by it",
      path == "/botT/sendDocument" and b'name="document"; filename="dns.mobileconfig"' in body
      and b"<plist/>" in body and b'name="chat_id"\r\n\r\n7' in body
      and b"how to install" in body, body[:300].decode("utf-8", "replace"))
srv.shutdown()

print("a proxy in the environment")
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
check("the wire stands aside for urllib", botmod.Wire("https://api.telegram.org/botT/").proxied)
del os.environ["HTTPS_PROXY"]
check("  and not without one", not botmod.Wire("https://api.telegram.org/botT/").proxied)

print("several customers at once")
msg = lambda chat, n=0: {"update_id": n, "message": {"chat": {"id": chat}, "text": str(n)}}
tap = lambda chat: {"update_id": 1, "callback_query": {"from": {"id": chat}, "data": "x",
                                                       "message": {"chat": {"id": chat}}}}
check("a chat's messages and its buttons go down one lane",
      botmod.lane_of(msg(12345)) == botmod.lane_of(tap(12345)))
check("  different chats, different lanes",
      len({botmod.lane_of(msg(c)) for c in range(100, 100 + botmod.LANES)}) == botmod.LANES)
check("  an update with no chat still has one", botmod.lane_of({"update_id": 1}) == 0)

done, lock = [], threading.Lock()


def handle(update):
    chat, n = update["message"]["chat"]["id"], update["update_id"]
    if chat == 1 and n == 0:
        time.sleep(0.6)                     # a receipt being fetched
    if n == 99:
        raise RuntimeError("boom")
    with lock:
        done.append((chat, n, time.time()))


hand = botmod.start_lanes(handle)
t0 = time.time()
hand(msg(1, 0))
hand(msg(1, 1))
hand(msg(2, 0))
hand(msg(3, 99))
hand(msg(3, 1))
time.sleep(1.0)
when = {(c, n): at - t0 for c, n, at in done}
check("one slow answer holds up nobody else's", when.get((2, 0), 9) < 0.3, str(when))
check("a chat's own stay in order", when.get((1, 0), 9) < when.get((1, 1), 0), str(when))
check("an update that fails does not stop its lane", (3, 1) in when)

print("the long poll")
main = SRC[SRC.index("def main():"):]
check("updates are handed to the lanes, not handled in the poll",
      "hand = start_lanes(bot.handle)" in main and "hand(u)" in main
      and "bot.handle(u)" not in main)
check("a lost poll is noticed within a minute, and tried again at once",
      "http_timeout=40, timeout=25" in main
      and 'time.sleep(1 if "timed out" in str(e) else 5)' in main)

print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
