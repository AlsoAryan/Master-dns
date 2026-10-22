#!/usr/bin/env python3
"""A message to many customers, through the bot.

What has to hold: the admin picks who - everyone, the active, those whose
period ends soon, the expired, those with no plan, a plan's customers - and
sees how many each is before sending; only customers with a Telegram are
sent it, one message each to the bot, which passes it on at a pace Telegram
accepts; and the page says how many have reached the bot.
"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

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
botmod = load("examples/telegram-bot/bot.py", "bot")
for m in (panel, admin, botmod):
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
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, created_at)"
          " VALUES ('ماهانه', 1, 30, 1, 1000, ?)", (panel.now(),))
soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds")
later = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(timespec="seconds")
for i, (status, tg, plan, ends) in enumerate([
        ("active", 101, 1, soon), ("active", 102, 1, later), ("active", None, 1, later),
        ("expired", 103, 1, None), ("pending", 104, None, None), ("pending", None, None, None)]):
    store.run("INSERT INTO users (telegram_id, username, created_at, status, plan_id, expires_at)"
              " VALUES (?, ?, ?, ?, ?, ?)", (tg, "u%d" % i, panel.now(), status, plan, ends))


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/bot"
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
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


print("who gets it")
aud = lambda t: admin.broadcast_audience(t)
check("everyone with a Telegram, and a count of those without",
      len(aud("all")[0]) == 4 and aud("all")[1] == 2)
check("the active", len(aud("active")[0]) == 2 and aud("active")[1] == 1)
check("those whose period ends within 3 days", len(aud("expiring3")[0]) == 1)
check("the expired, and those with no plan",
      len(aud("expired")[0]) == 1 and len(aud("noplan")[0]) == 1)
check("a plan's customers", len(aud("plan:1")[0]) == 2)
card = admin.broadcast_card("p")
check("the bot page shows each choice with its count",
      "همهٔ مشتری‌ها — 4 نفر (2 نفر دیگر تلگرام ندارند)" in card
      and "مشتری‌های پلن «ماهانه»" in card)

print("sending")
check("not without a bot", "!" in act("broadcast-send", text="سلام", target="all"))
store.run("INSERT INTO api_tokens (name, token_hash, scope, webhook_url, webhook_secret,"
          " created_at) VALUES ('b', 'h', 'admin', 'http://127.0.0.1:1/', 'w', ?)",
          (panel.now(),))
check("not with no text", "!" in act("broadcast-send", text="  ", target="all"))
where = act("broadcast-send", text="تعطیلات: پشتیبانی تا شنبه جواب نمی‌دهد", target="active")
rows = store.q("SELECT * FROM webhook_outbox WHERE event = 'broadcast'")
check("one message to the bot per customer with a Telegram",
      len(rows) == 2 and "2 نفر" in where, where)
ev = json.loads(rows[0]["payload"])
check("  each for that customer, with the text", ev["telegram_id"] in (101, 102)
      and "تعطیلات" in ev["data"]["text"])
store.run("UPDATE webhook_outbox SET delivered_at = ? WHERE id = ?", (panel.now(), rows[0]["id"]))
check("the page says how many reached the bot", "1 از 2" in admin.broadcast_card("p"))

print("the bot")


class FakeTelegram:
    def __init__(self):
        self.out = []

    def send(self, chat, text, markup=None):
        self.out.append((chat, text))


tg = FakeTelegram()
bot = botmod.Bot(botmod.settings({"BOT_TOKEN": "t", "API_URL": "x", "API_KEY": "k"}),
                 telegram=tg)
slept = []
botmod.time.sleep = lambda s: slept.append(s)
bot.on_event(dict(ev, id=1))
check("passes it on to that customer, pausing between them",
      tg.out == [(ev["telegram_id"], ev["data"]["text"])] and slept)

print("with photos and videos")
admin.BROADCAST_DIR = panel.BROADCAST_DIR = os.path.join(tmp, "broadcast")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def act_upload(fields, *media, empty=0):
    """The form as a browser sends it: multipart, one part per file box -
    `empty` boxes left with nothing chosen, as the last one always is."""
    boundary = "b0undary"
    parts = [('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
              % (boundary, k, v)).encode() for k, v in fields.items()]
    for blob in list(media) + [b""] * empty:
        parts.append(('--%s\r\nContent-Disposition: form-data; name="media"; '
                      'filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n'
                      % (boundary, "x" if blob else "")).encode() + blob + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    r = Rec()
    r.headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
    r.raw_body = b"".join(parts)
    r.action("broadcast-send", {})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def latest():
    return store.one("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 1")


def read(bid, n):
    with open(admin.broadcast_file(bid, n), "rb") as fh:
        return fh.read()


card = admin.broadcast_card("p")
check("the form has one file box, and opens the next as each is chosen",
      card.count("type='file' name='media'") == 1 and "bc-media" in card
      and "appendChild" in card and "multipart/form-data" in card)
store.run("UPDATE api_tokens SET token_hash = ?", (panel.token_hash("dd_k"),))
where = act_upload({"text": "جشنواره تخفیف 🎉", "target": "active"}, PNG, empty=1)
b = latest()
check("a photo with words is sent, as a form with a file",
      "2 نفر" in where and b["media"] == "photo" and b["text"] == "جشنواره تخفیف 🎉", where)
check("the photo is kept on disk, not in the database", read(b["id"], 0) == PNG)
pev = json.loads(store.q("SELECT payload FROM webhook_outbox WHERE event = 'broadcast'"
                         " ORDER BY id DESC LIMIT 1")[0]["payload"])
check("the bot is told there is a photo", pev["data"]["media"] == ["photo"]
      and pev["data"]["broadcast_id"] == b["id"])
where = act_upload({"text": "", "target": "all"}, MP4)
v = latest()
check("a video alone, with no words, is sent too", "m=!" not in where and v["media"] == "video")
where = act_upload({"text": "آلبوم", "target": "all"}, PNG, MP4, JPG, empty=1)
al = latest()
check("several at once, in the order chosen",
      "m=!" not in where and al["media"] == "photo,video,photo"
      and [read(al["id"], n) for n in range(3)] == [PNG, MP4, JPG], where)
check("a file that is neither is refused",
      "فقط عکس" in act_upload({"text": "x", "target": "all"}, PNG, b"hello, not a picture"))
admin.BROADCAST_PHOTO_MAX = 10
check("so is a photo too big", "۱۰ مگابایت" in act_upload({"text": "x", "target": "all"}, PNG))
admin.BROADCAST_PHOTO_MAX = 10 * 1024 * 1024
check("and more than ten", "حداکثر 10" in act_upload({"text": "x", "target": "all"},
                                                    *[PNG] * 11))
admin.BROADCAST_TOTAL_MAX = 100
check("or too much together", "۵۰ مگابایت" in act_upload({"text": "x", "target": "all"},
                                                        PNG, PNG))
admin.BROADCAST_TOTAL_MAX = 50 * 1024 * 1024
check("neither words nor a file is nothing to send",
      "m=!" in act_upload({"text": "  ", "target": "all"}, empty=1))
card = admin.broadcast_card("p")
check("the page marks the ones with photos or videos, and how many",
      "🖼 عکس" in card and "🎬 فیلم" in card and "🖼 2 عکس 🎬 فیلم" in card)

# The panel's API hands the files to a bot they were sent to, and to no other.
panel.BotAPI.store = store
api = panel.BotServer(("127.0.0.1", 0), None, None)
threading.Thread(target=api.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d/api/v1" % api.server_address[1]
res = botmod.Panel({"api": base, "key": "dd_k"}).call("GET", "/broadcasts/%d/media/1" % al["id"])
check("the panel gives the bot each file by its place", res["kind"] == "video"
      and base64.b64decode(res["data"]) == MP4)


def refused_with(key, path):
    try:
        botmod.Panel({"api": base, "key": key}).call("GET", path)
        return 200
    except botmod.ApiError as e:
        return e.status


check("and nothing past the last", refused_with("dd_k", "/broadcasts/%d/media/3" % al["id"])
      == 404)
store.run("INSERT INTO api_tokens (name, token_hash, scope, created_at) VALUES"
          " ('other', ?, 'admin', ?)", (panel.token_hash("dd_other"), panel.now()))
check("nor to a bot that was never sent it",
      refused_with("dd_other", "/broadcasts/%d/media/0" % b["id"]) == 404)


class MediaTelegram(FakeTelegram):
    """Takes uploads, and gives back file ids as Telegram does."""

    def upload(self, method, files, **fields):
        self.out.append((fields["chat_id"], method, sorted(files), fields.get("caption"),
                         fields.get("media")))
        if method == "sendMediaGroup":
            return [({"photo": [{"file_id": "S"}, {"file_id": "TG-%d" % n}]} if m["type"] ==
                     "photo" else {"video": {"file_id": "TG-%d" % n}})
                    for n, m in enumerate(fields["media"])]
        kind = next(iter(files))
        return ({"photo": [{"file_id": "S"}, {"file_id": "TG-photo"}]} if kind == "photo"
                else {"video": {"file_id": "TG-video"}})

    def call(self, method, http_timeout=30, **params):
        self.out.append((params["chat_id"], method, params.get("photo") or params.get("video"),
                         params.get("caption"), params.get("media")))


mtg = MediaTelegram()
mbot = botmod.Bot(botmod.settings({"BOT_TOKEN": "t", "API_URL": base, "API_KEY": "dd_k"}),
                  telegram=mtg)
pdata = {"broadcast_id": b["id"], "text": "جشنواره تخفیف 🎉", "media": ["photo"]}
mbot.on_event({"id": 50, "event": "broadcast", "telegram_id": 101, "data": pdata})
mbot.on_event({"id": 51, "event": "broadcast", "telegram_id": 102, "data": pdata})
check("the first customer's photo is uploaded, with the words under it",
      mtg.out[0] == (101, "sendPhoto", ["photo"], "جشنواره تخفیف 🎉", None), mtg.out)
check("everybody after gets Telegram's own copy, not another upload",
      mtg.out[1] == (102, "sendPhoto", "TG-photo", "جشنواره تخفیف 🎉", None), mtg.out)
mtg.out.clear()
adata = {"broadcast_id": al["id"], "text": "آلبوم", "media": ["photo", "video", "photo"]}
mbot.on_event({"id": 54, "event": "broadcast", "telegram_id": 101, "data": adata})
mbot.on_event({"id": 55, "event": "broadcast", "telegram_id": 102, "data": adata})
first, second = mtg.out
check("several go as one album, all uploaded with it, the words under the first",
      first[1] == "sendMediaGroup" and first[2] == ["f0", "f1", "f2"]
      and first[4] == [{"type": "photo", "media": "attach://f0", "caption": "آلبوم"},
                       {"type": "video", "media": "attach://f1"},
                       {"type": "photo", "media": "attach://f2"}], first)
check("  and the next customer gets the album from Telegram's copies",
      second[1] == "sendMediaGroup" and [m["media"] for m in second[4]]
      == ["TG-0", "TG-1", "TG-2"], second)
mtg.out.clear()
long_words = "خبر " * 400
mbot.on_event({"id": 52, "event": "broadcast", "telegram_id": 101,
               "data": {"broadcast_id": v["id"], "text": long_words, "media": ["video"]}})
check("words too long for a caption go after the video, on their own",
      mtg.out == [(101, "sendVideo", ["video"], None, None), (101, long_words)], mtg.out[:2])
mtg.out.clear()
os.remove(admin.broadcast_file(b["id"], 0))
mbot.media.clear()
mbot.on_event({"id": 53, "event": "broadcast", "telegram_id": 101, "data": pdata})
check("a photo no longer there: the words still go", mtg.out == [(101, "جشنواره تخفیف 🎉")],
      mtg.out)

old = time.time() - 8 * 86400
os.utime(admin.broadcast_file(v["id"], 0), (old, old))
admin.sweep_broadcast_media()
check("files older than a week are cleared out",
      not os.path.exists(admin.broadcast_file(v["id"], 0))
      and os.path.exists(admin.broadcast_file(al["id"], 0)))
api.shutdown()

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
