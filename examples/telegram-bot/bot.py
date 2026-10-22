#!/usr/bin/env python3
"""master-dns sample Telegram bot.

A working bot on top of the panel's bot API - for customers, and for the
operator when their Telegram id is in ADMIN_IDS. Plain Python 3, nothing to
install. It talks to Telegram by long polling, so it needs no public address
of its own, and it listens on 127.0.0.1 for what the panel tells it.

Run it where Telegram is reachable - the exit server is the natural place.
Settings come from the environment (see bot.env.example):

    BOT_TOKEN        from @BotFather
    API_URL          https://<panel domain>:8445/api/v1
    API_KEY          a key from the admin panel's API page; with admin rights
                     if ADMIN_IDS is set
    WEBHOOK_SECRET   the key's webhook signing secret (whsec_...)
    LISTEN           where the panel's messages arrive, default 127.0.0.1:18990
    ADMIN_IDS        Telegram ids of the operator, comma separated (optional)
    PAY_TEXT         how to pay, only if the admin panel's Payment page is
                     empty - that one wins
    SUPPORT_TEXT     shown under "help" (optional)
    JOIN_CHANNEL     a channel to join before anything else, @name or -100...
                     (optional; the bot must be an admin there)
    JOIN_LINK        its invitation link, for a private channel
"""
import base64
import hashlib
import hmac
import html
import http.client
import http.server
import json
import os
import queue
import re
import secrets
import select
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- settings
def settings(env=os.environ):
    cfg = {
        "token": env.get("BOT_TOKEN", "").strip(),
        "api": env.get("API_URL", "").strip().rstrip("/"),
        "key": env.get("API_KEY", "").strip(),
        "secret": env.get("WEBHOOK_SECRET", "").strip(),
        "listen": env.get("LISTEN", "127.0.0.1:18990").strip(),
        "admins": {int(x) for x in re.findall(r"\d+", env.get("ADMIN_IDS", ""))},
        "pay": env.get("PAY_TEXT", "").strip().replace("\\n", "\n"),
        "support": env.get("SUPPORT_TEXT", "").strip().replace("\\n", "\n"),
        "telegram": env.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/"),
        # The language the bot speaks, set in the admin panel's bot page.
        "lang": "en" if env.get("BOT_LANG", "").strip() == "en" else "fa",
        "channel": env.get("JOIN_CHANNEL", "").strip(),
        "channel_title": env.get("JOIN_TITLE", "").strip(),
    }
    cfg["channel_link"] = env.get("JOIN_LINK", "").strip() or (
        "https://t.me/" + cfg["channel"][1:] if cfg["channel"].startswith("@") else "")
    if cfg["channel"] and not cfg["channel_link"]:
        # Nobody could get in: better no gate than one without a door.
        log("JOIN_CHANNEL %s has no JOIN_LINK; not asking anybody to join" % cfg["channel"])
        cfg["channel"] = ""
    missing = [k for k in ("token", "api", "key") if not cfg[k]]
    if missing:
        sys.exit("missing settings: %s (see bot.env.example)"
                 % ", ".join({"token": "BOT_TOKEN", "api": "API_URL", "key": "API_KEY"}[m]
                             for m in missing))
    return cfg


# The buttons of the customer's keyboard.
B_ACCOUNT = "📊 حساب من"
B_BUY = "🛒 خرید / تمدید"
B_IP = "🌐 ثبت آی‌پی"
B_SUPPORT = "🎫 پشتیبانی"
B_HELP = "❓ راهنما"
B_WEB = "🔑 پنل وب"
B_DNS = "📡 DNSها"
B_WALLET = "💰 کیف پول"
B_INVITE = "🎁 دعوت از دوستان"
B_CANCEL = "انصراف"
MENU = {"keyboard": [[B_ACCOUNT, B_BUY], [B_WALLET, B_INVITE], [B_IP, B_DNS],
                     [B_SUPPORT, B_WEB], [B_HELP]],
        "resize_keyboard": True}
CANCEL = {"keyboard": [[B_CANCEL]], "resize_keyboard": True}
STATUS = {"pending": "در انتظار خرید پلن", "active": "فعال ✅",
          "over_quota": "حجم تمام شده ⛔", "expired": "دوره تمام شده ⛔",
          "suspended": "مسدود ⛔"}
MAX_FILE = 4 * 1024 * 1024
# What a ticket's picture may be - the panel's TICKET_IMAGE_TYPES.
TICKET_IMAGES = ("image/jpeg", "image/png", "image/webp")
# Telegram's longest caption under a photo or video.
CAPTION_MAX = 1024
# How long "is in the channel" is believed before Telegram is asked again.
MEMBER_FRESH = 600


def size_fa(n):
    n = float(n or 0)
    for unit in ("بایت", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت"):
        if n < 1024 or unit == "ترابایت":
            return ("%d %s" if unit == "بایت" else "%.1f %s") % (n, unit)
        n /= 1024


def money(n):
    return format(n or 0, ",")


def time_left_fa(ts, now=None):
    """How long until a plan's end: days, or hours on the last day; "" when
    it cannot be read. The same words as the customer's page."""
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    left = (t - (now or datetime.now(timezone.utc))).total_seconds()
    if left <= 0:
        return "تمام شده"
    if left < 86400:
        return "%d ساعت" % max(1, int(left // 3600))
    return "%d روز" % int(left // 86400)


FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def typed_toman(text):
    """A sum as somebody typed it - Persian digits, commas - or None."""
    raw = re.sub(r"[\s,٬،]", "", (text or "").translate(FA_DIGITS))
    for word in ("تومان", "تومن"):
        raw = raw.replace(word, "")
    return int(raw) if raw.isdigit() and len(raw) < 16 else None


def image_type(blob):
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:5] == b"%PDF-":
        return "application/pdf"
    return None


# ---------------------------------------------------------------- English
# The pages and the bot are written in Persian; English is the same text with
# every Persian phrase swapped for its English, from one file the installer
# ships (domains/i18n-en.json). A phrase is a piece of a Persian string in the
# code, cut where a value goes in and at each tag - tools/i18n-extract.py lists
# them. What somebody typed - a name, a note - is left as they wrote it.
I18N_FILE = os.environ.get("I18N_FILE", "/usr/local/share/smart-dns/i18n-en.json")
I18N = {}


def english_index():
    """The phrases by their first two characters, longest first."""
    if "index" not in I18N:
        try:
            with open(I18N_FILE, encoding="utf-8") as fh:
                pairs = json.load(fh)
        except (OSError, ValueError):
            pairs = {}
        index = {}
        for k, v in pairs.items():
            if len(k) >= 2 and isinstance(v, str):
                # Plain text only: the English goes into attributes and
                # script strings quoted either way.
                v = v.replace("'", "\u2019").replace('"', "\u201d")
                index.setdefault(k[:2], []).append((k, v))
        for bucket in index.values():
            bucket.sort(key=lambda kv: -len(kv[0]))
        I18N["index"] = index
    return I18N["index"]


def fa_letter(c):
    """Part of a Persian word: a letter or a mark on one, not the comma,
    the semicolon or a digit - "نشد؛" ends a word at the "؛"."""
    return "\u0621" <= c <= "\u065f" or "\u066e" <= c <= "\u06d3" or c == "\u200c"


def to_english(text):
    """`text` with every known Persian phrase in English. A phrase is only
    taken whole - never the front of a longer Persian word."""
    index = english_index()
    if not index or not text or not any("\u0600" <= c <= "\u06ff" for c in text):
        return text
    out, last, i, n = [], 0, 0, len(text)
    while i < n:
        bucket = index.get(text[i:i + 2])
        if bucket and not (fa_letter(text[i]) and i and fa_letter(text[i - 1])):
            for k, v in bucket:
                end = i + len(k)
                if text.startswith(k, i) and not (
                        fa_letter(k[-1]) and end < n and fa_letter(text[end])):
                    out.append(text[last:i])
                    out.append(v)
                    i = last = end
                    break
            else:
                i += 1
            continue
        i += 1
    out.append(text[last:])
    # What is left - the quote marks around a name, a digit - in English form.
    return "".join(out).translate(ENGLISH_MARKS)


ENGLISH_MARKS = str.maketrans({"\u00ab": "\u201c", "\u00bb": "\u201d", "\u060c": ",",
                               "\u061b": ";", "\u061f": "?", "\u066a": "%",
                               **{chr(0x06f0 + i): str(i) for i in range(10)},
                               **{chr(0x0660 + i): str(i) for i in range(10)}})




class ApiError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(body.get("message") or body.get("error") or "HTTP %d" % status)


# ------------------------------------------------------------ the two APIs
# How long one of a server's addresses may take to take a new connection
# before the next is tried. Python waits the whole call's timeout on each -
# up to five minutes for an upload - and a machine whose IPv6 route to
# Telegram comes and goes sat that long on every QR and file it sent.
CONNECT_WAIT = 6
# Address families that failed to connect lately, tried last until then:
# {family: until}.
FAMILY_DOWN = {}


def open_socket(host, port, timeout):
    """A connection to host:port - each address given CONNECT_WAIT seconds,
    a family that has just failed tried after the others."""
    stamp = time.time()
    infos = sorted(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM),
                   key=lambda i: FAMILY_DOWN.get(i[0], 0) > stamp)
    err = None
    for family, kind, proto, _, addr in infos:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(min(CONNECT_WAIT, timeout or CONNECT_WAIT))
            sock.connect(addr)
        except OSError as e:
            sock.close()
            err = e
            FAMILY_DOWN[family] = stamp + 600
            continue
        sock.settimeout(timeout)
        FAMILY_DOWN.pop(family, None)
        return sock
    raise err or OSError("no address for %s" % host)


class QuickHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = open_socket(self.host, self.port, self.timeout)


class QuickHTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock = open_socket(self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Wire:
    """A connection to one server, kept open between calls - one for each
    thread that asks.

    Every button a customer presses is three or four calls, and opening a
    new TLS connection to Telegram for each was most of the wait. A
    connection the other end has closed since is noticed before it is used,
    and one that turns out dead as the call goes out is opened again, once.
    Through a proxy from the environment it stands aside for urllib, which
    knows how to use one.
    """

    def __init__(self, base):
        u = urllib.parse.urlsplit(base)
        self.base = base
        self.https = u.scheme == "https"
        self.host, self.port, self.prefix = u.hostname, u.port, u.path
        self.local = threading.local()
        self.proxied = bool(urllib.request.getproxies().get(u.scheme)) and not \
            urllib.request.proxy_bypass(u.hostname or "")

    @staticmethod
    def dropped(conn):
        sock = conn.sock
        if sock is None:
            return True
        try:
            # Nothing was asked, so anything to read is the other end's goodbye.
            return bool(select.select([sock], [], [], 0)[0])
        except (OSError, ValueError):
            return True

    def request(self, method, path, body=None, headers=None, timeout=30):
        """(status, the body's bytes)."""
        if self.proxied:
            req = urllib.request.Request(self.base + path, method=method, data=body,
                                         headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        conn = getattr(self.local, "conn", None)
        if conn is not None and self.dropped(conn):
            conn.close()
            conn = None
        reused = conn is not None
        while True:
            if conn is None:
                kind = QuickHTTPS if self.https else QuickHTTP
                conn = kind(self.host, self.port, timeout=timeout)
            try:
                conn.timeout = timeout
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
                conn.request(method, self.prefix + path, body=body, headers=headers or {})
                r = conn.getresponse()
                data = r.read()
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest,
                    BrokenPipeError, ConnectionResetError):
                conn.close()
                self.local.conn = conn = None
                if not reused:
                    raise
                reused = False              # closed while idle: once more, on a new one
                continue
            except BaseException:
                conn.close()
                self.local.conn = None
                raise
            if r.will_close:
                conn.close()
                conn = None
            self.local.conn = conn
            return r.status, data


class Panel:
    """The panel's bot API."""

    def __init__(self, cfg):
        self.base, self.key = cfg["api"], cfg["key"]
        self.wire = Wire(self.base)

    def call(self, method, path, body=None, idem=None):
        headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        if idem:
            headers["Idempotency-Key"] = idem
        status, raw = self.wire.request(
            method, path, json.dumps(body).encode() if body is not None else None, headers)
        if status >= 400:
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"message": "پنل جواب درستی نداد (HTTP %d)" % status}
            raise ApiError(status, body)
        return json.loads(raw)


class Telegram:
    def __init__(self, cfg):
        self.base = "%s/bot%s/" % (cfg["telegram"], cfg["token"])
        self.files = "%s/file/bot%s/" % (cfg["telegram"], cfg["token"])
        self.wire = Wire(self.base)
        self.file_wire = Wire(self.files)

    def call(self, method, http_timeout=30, **params):
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        _, raw = self.wire.request("POST", method, data,
                                   {"Content-Type": "application/json"}, http_timeout)
        try:
            res = json.loads(raw or b"{}")
        except ValueError:
            res = {"description": raw[:200].decode("utf-8", "replace")}
        if not res.get("ok"):
            raise RuntimeError("telegram %s: %s" % (method, res.get("description")))
        return res.get("result")

    def send(self, chat, text, markup=None):
        return self.call("sendMessage", chat_id=chat, text=text[:4000], reply_markup=markup,
                         link_preview_options={"is_disabled": True})

    def upload(self, method, files, **fields):
        """A call carrying files, which has to be multipart: `files` maps
        each part's name to its bytes, `fields` are the rest (None left out,
        a list or dict sent as JSON). Telegram's answer; HTTPError when it
        refuses."""
        boundary = secrets.token_hex(16)
        parts = []
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (list, dict)):
                value = json.dumps(value)
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                          % (boundary, key, value)).encode())
        for name, blob in files.items():
            # (file name, bytes) where the name matters - a document's does.
            filename, blob = blob if isinstance(blob, tuple) else (name, blob)
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; '
                          'filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n'
                          % (boundary, name, filename)).encode() + blob + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode())
        status, raw = self.wire.request(
            "POST", method, b"".join(parts),
            {"Content-Type": "multipart/form-data; boundary=" + boundary}, 300)
        if status >= 400:
            raise urllib.error.HTTPError(method, status, raw[:200].decode("utf-8", "replace"),
                                         None, None)
        return json.loads(raw).get("result")

    def document(self, chat, filename, blob, caption=""):
        """A file sent as it is, under its own name."""
        return self.upload("sendDocument", {"document": (filename, blob)}, chat_id=chat,
                           caption=caption[:1000] or None)

    def photo(self, chat, blob, caption, markup=None):
        try:
            return self.upload("sendPhoto", {"photo": blob}, chat_id=chat,
                               caption=caption[:1000], reply_markup=markup)
        except urllib.error.HTTPError:
            # A PDF, or something Telegram will not show as a photo: say it in words.
            return self.send(chat, caption + "\n\n(فایل رسید عکس نبود؛ در پنل ببینید)", markup)

    def download(self, file_id):
        info = self.call("getFile", file_id=file_id)
        if (info.get("file_size") or 0) > MAX_FILE:
            raise ValueError("فایل بزرگ‌تر از ۴ مگابایت است")
        status, raw = self.file_wire.request("GET", info["file_path"], timeout=60)
        if status >= 400:
            raise urllib.error.HTTPError("getFile", status, "file not fetched", None, None)
        return raw[:MAX_FILE + 1]


# Dates as the admin picked them shown - the panel says which with every
# account: Shamsi or Gregorian, in Tehran time.
TEHRAN = timezone(timedelta(hours=3, minutes=30))


def to_jalali(gy, gm, gd):
    """A Gregorian day as a Shamsi one: (year, month, day)."""
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def show_date(value, cal="gregorian"):
    """The day of a date or a moment, in `cal`: 1405/07/07 or 2026-09-29.
    Anything else given back as it was."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
        else:
            t = datetime.fromisoformat(text)
            day = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(TEHRAN).date()
    except ValueError:
        return text
    if cal == "jalali":
        return "%04d/%02d/%02d" % to_jalali(day.year, day.month, day.day)
    return day.strftime("%Y-%m-%d")


def ios_profile(url, address=""):
    """An iOS/macOS profile that turns a personal DoH address on for the
    whole device - the same one the customer's web page gives. Its ids come
    from the address, so the file sent again replaces the profile instead of
    adding a second one, and a new address makes a new profile."""
    host = urllib.parse.urlsplit(url).hostname or "dns"
    one = str(uuid.uuid5(uuid.NAMESPACE_URL, "master-dns-payload:" + url)).upper()
    two = str(uuid.uuid5(uuid.NAMESPACE_URL, "master-dns-profile:" + url)).upper()
    name = html.escape("DNS %s" % host)
    addresses = ("\n        <key>ServerAddresses</key>\n        <array><string>%s</string>"
                 "</array>" % html.escape(address)) if address else ""
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>DNSSettings</key>
      <dict>
        <key>DNSProtocol</key>
        <string>HTTPS</string>
        <key>ServerURL</key>
        <string>%s</string>%s
      </dict>
      <key>PayloadDisplayName</key>
      <string>%s</string>
      <key>PayloadIdentifier</key>
      <string>com.masterdns.dns.%s</string>
      <key>PayloadType</key>
      <string>com.apple.dnsSettings.managed</string>
      <key>PayloadUUID</key>
      <string>%s</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
    </dict>
  </array>
  <key>PayloadDisplayName</key>
  <string>%s</string>
  <key>PayloadIdentifier</key>
  <string>com.masterdns.profile.%s</string>
  <key>PayloadRemovalDisallowed</key>
  <false/>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>%s</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>
""" % (html.escape(url), addresses, name, one, one, name, two, two)).encode("utf-8")


B_IOS_PROFILE = "دریافت پروفایل آیفون📱"
IOS_PROFILE_HELP = ("📲 پروفایل آیفون%s\n\nفایل را باز کنید ← دکمهٔ اشتراک‌گذاری ← "
                    "«Save to Files». بعد در برنامهٔ Files روی فایل بزنید، و در تنظیمات ← "
                    "«Profile Downloaded» ← Install را بزنید.")


B_WG = "🛡 کانفیگ وایرگارد"
WG_HELP = ("🛡 وایرگارد\n\n"
           "یک راه دیگر برای استفاده از سرویس، کنار DNS؛ جایی که DNS کار نمی‌کند، این کار "
           "می‌کند.\n\n"
           "۱. برنامهٔ WireGuard را از App Store یا Google Play نصب کنید.\n"
           "۲. در برنامه + را بزنید و QR کد را اسکن کنید، یا فایل کانفیگ را باز کنید.\n"
           "۳. تونل را روشن کنید.\n\n"
           "فقط سرویس‌هایی که این سرویس باز می‌کند از وایرگارد می‌روند؛ بقیهٔ اینترنت مستقیم "
           "است. برای هر سرور یک کانفیگ، مثل یک آدرس DNS برای هر سرور؛ اگر یکی کار نکرد، "
           "دیگری را روشن کنید. در اندروید اگر «Private DNS» روشن است خاموشش کنید. کانفیگ را "
           "به کسی ندهید.")


# ------------------------------------------------------------------ the bot
class Bot:
    def __init__(self, cfg, panel=None, telegram=None):
        self.cfg = cfg
        self.panel = panel or Panel(cfg)
        self.tg = telegram or Telegram(cfg)
        self.state = {}          # chat id -> (what we wait for, details)
        # In English, the keyboard's buttons come back as their English: the
        # way back to the Persian the rest of this file compares with.
        self.en = cfg.get("lang") == "en"
        self.back = {}
        if self.en:
            for label in (B_ACCOUNT, B_BUY, B_WALLET, B_INVITE, B_IP, B_SUPPORT, B_HELP, B_WEB,
                          B_DNS,
                          B_CANCEL):
                self.back[to_english(label)] = label
        self.known = set()       # telegram ids the panel already has an account for
        self.seen = []           # recent webhook ids, so a repeat is dropped
        self.lock = threading.Lock()
        # The photo or video of the broadcast going out: its bytes until
        # Telegram has taken it once, then Telegram's own id for it.
        self.media = {}
        self.members = {}        # telegram id -> when Telegram last said they are in the channel

    # -- helpers ------------------------------------------------------------
    def is_admin(self, uid):
        return uid in self.cfg["admins"]

    def t(self, text):
        return to_english(text) if self.en else text

    def t_markup(self, markup):
        """A keyboard with its buttons' words in the bot's language."""
        if not self.en or not markup:
            return markup
        out = dict(markup)
        for key in ("keyboard", "inline_keyboard"):
            if key in out:
                out[key] = [[dict(b, text=self.t(b["text"])) if isinstance(b, dict)
                             else self.t(b) for b in row] for row in out[key]]
        return out

    def say(self, chat, text, markup=None):
        text, markup = self.t(text), self.t_markup(markup)
        try:
            self.tg.send(chat, text, markup)
        except Exception as e:
            log("could not send to %s: %s" % (chat, e))

    def account(self, sender):
        """The customer's account, opened on first use. Not on /start: a
        customer arriving to link a web account must not get a second one."""
        uid = sender["id"]
        if uid in self.known:
            try:
                return self.panel.call("GET", "/users/%d" % uid)["user"]
            except ApiError as e:
                # Deleted in the admin panel since: open a new one, as for
                # somebody the bot has never seen.
                if e.body.get("error") != "user_not_found":
                    raise
                self.known.discard(uid)
        name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
        self.panel.call("POST", "/users", {"telegram_id": uid, "name": name[:60]})
        self.known.add(uid)
        return self.panel.call("GET", "/users/%d" % uid)["user"]

    # -- updates from Telegram ------------------------------------------------
    def handle(self, update):
        try:
            if "callback_query" in update:
                return self.on_button(update["callback_query"])
            msg = update.get("message")
            if msg and msg.get("chat", {}).get("type") == "private":
                return self.on_message(msg)
        except ApiError as e:
            chat = (update.get("message") or update.get("callback_query", {}).get("message")
                    or {}).get("chat", {}).get("id")
            if chat:
                self.say(chat, "⚠️ " + str(e))
        except Exception:
            log("update failed:\n" + traceback.format_exc())

    def on_message(self, msg):
        chat, sender = msg["chat"]["id"], msg["from"]
        text = (msg.get("text") or "").strip()
        text = self.back.get(text, text)
        if text == B_CANCEL or text == "/cancel":
            self.state.pop(chat, None)
            return self.say(chat, "لغو شد.", MENU)
        if text.startswith("/start"):
            return self.on_start(chat, sender, text)
        if not self.in_channel(sender["id"]):
            return self.ask_to_join(chat)
        if self.is_admin(sender["id"]) and text in ("/stats", "/receipts"):
            return self.admin_command(chat, text)

        waiting, extra = self.state.get(chat, (None, None))
        if waiting == "onb_name":
            return self.got_name(chat, sender, text)
        if waiting == "onb_user":
            return self.got_username(chat, sender, text, extra)
        if text in ("/doh", "/dns"):
            text = B_DNS
        if text in (B_ACCOUNT, B_BUY, B_WALLET, B_INVITE, B_IP, B_DNS, B_SUPPORT, B_WEB) \
                and not self.ready(chat, sender):
            return
        if waiting == "ip":
            return self.got_ip(chat, sender, text)
        if waiting == "receipt":
            return self.got_receipt(chat, sender, msg, extra)
        if waiting == "topup_amount":
            return self.got_topup_amount(chat, sender, text)
        if waiting == "topup_receipt":
            return self.got_receipt(chat, sender, msg, None, topup=extra)
        if waiting == "code":
            self.state.pop(chat, None)
            return self.chose_plan(chat, sender, extra, code=text.strip()[:32])
        if waiting == "device_receipt":
            return self.got_receipt(chat, sender, msg, None, device=True)
        if waiting == "ticket_subject" and text:
            self.state[chat] = ("ticket_body", text[:80])
            return self.say(chat, "متن پیامتان را بنویسید، یا عکس خطا را بفرستید "
                                  "(عکس می‌تواند توضیح هم داشته باشد):", CANCEL)
        if waiting == "ticket_body":
            return self.got_ticket(chat, sender, msg, subject=extra)
        if waiting == "ticket_reply":
            return self.got_ticket(chat, sender, msg, ticket=extra)
        if waiting == "admin_reply" and self.is_admin(sender["id"]):
            return self.admin_reply(chat, msg, extra)

        if text == B_ACCOUNT:
            return self.show_account(chat, sender)
        if text == B_BUY:
            return self.show_plans(chat, sender)
        if text == B_WALLET:
            return self.show_wallet(chat, sender)
        if text == B_INVITE:
            return self.show_invite(chat, sender)
        if text == B_IP:
            return self.ip_help(chat, sender)
        if text == B_WEB:
            return self.show_web(chat, sender)
        if text == B_DNS:
            return self.show_dns(chat, sender)
        if text == B_SUPPORT:
            return self.show_tickets(chat, sender)
        if text == B_HELP:
            return self.say(chat, self.help_text(), MENU)
        return self.say(chat, "از دکمه‌های پایین استفاده کنید.", MENU)

    def on_start(self, chat, sender, text):
        self.state.pop(chat, None)
        arg = text.split(" ", 1)[1].strip() if " " in text else ""
        if arg.startswith("link_"):
            try:
                res = self.panel.call("POST", "/link", {"telegram_id": sender["id"],
                                                        "code": arg[5:]})
                self.known.add(sender["id"])
                return self.say(chat, "✅ " + res["message"] + "\nحالا اگر رمز پنل را فراموش "
                                "کنید، کد بازیابی همین‌جا می‌آید.", MENU)
            except ApiError as e:
                return self.say(chat, "⚠️ " + str(e), MENU)
        if arg.startswith("ref_"):
            # Somebody's invitation link. The account is opened now, with it:
            # the panel writes down the inviter only for an account it opens.
            # Asked even for a Telegram id the bot knows, which may since have
            # been deleted in the admin panel; for one that is still there the
            # panel only answers that it exists.
            name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
            try:
                self.panel.call("POST", "/users", {"telegram_id": sender["id"],
                                                   "name": name[:60], "ref": arg[4:20]})
                self.known.add(sender["id"])
            except ApiError as e:
                log("invitation start failed: %s" % e)
        # After the invitation is written down: somebody who joins the
        # channel first still counts as invited.
        if not self.in_channel(sender["id"]):
            return self.ask_to_join(chat, "سلام! 👋 به ربات خوش آمدید.\n\n")
        return self.say(chat, "سلام! 👋 به ربات خوش آمدید.\n\n" + self.help_text(), MENU)

    # -- the channel a customer must be in first --------------------------------
    def in_channel(self, uid):
        """True when there is no channel to join, for the operator, and for
        somebody Telegram says is in it - remembered for a while, so every
        tap is not a question to Telegram. When Telegram cannot say - the bot
        is no longer an admin there, or Telegram is not answering - nobody
        is kept out for it."""
        channel = self.cfg.get("channel")
        if not channel or self.is_admin(uid):
            return True
        if time.time() - self.members.get(uid, 0) < MEMBER_FRESH:
            return True
        try:
            m = self.tg.call("getChatMember", chat_id=channel, user_id=uid)
        except Exception as e:
            if re.search(r"(?i)user not found|member not found|participant_id_invalid", str(e)):
                return False
            log("could not ask %s whether %s is in it: %s" % (channel, uid, e))
            return True
        status = (m or {}).get("status")
        if status in ("creator", "administrator", "member") or (
                status == "restricted" and m.get("is_member")):
            self.members[uid] = time.time()
            return True
        return False

    def ask_to_join(self, chat, before=""):
        name = self.cfg.get("channel_title") or self.cfg["channel"]
        self.say(chat, before + "برای استفاده از ربات، اول عضو کانال «%s» شوید و بعد "
                 "«✅ عضو شدم» را بزنید." % name,
                 {"inline_keyboard": [
                     [{"text": "📢 عضویت در کانال", "url": self.cfg["channel_link"]}],
                     [{"text": "✅ عضو شدم", "callback_data": "joined"}]]})

    # -- a web sign-in for everybody who comes through the bot ----------------
    def ready(self, chat, sender):
        """True when the account has its web sign-in. Otherwise the two
        questions start, and whatever was pressed waits until they are done."""
        u = self.account(sender)
        if u.get("username"):
            return True
        tg_name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
        self.state[chat] = ("onb_name", None)
        self.say(chat, "اول حسابتان را کامل کنیم 🙂\n\nاسمتان چیست؟",
                 {"keyboard": [[tg_name[:60]]] if tg_name else [[B_CANCEL]],
                  "resize_keyboard": True, "one_time_keyboard": True})
        return False

    def got_name(self, chat, sender, text):
        if not text or text == B_CANCEL:
            self.state.pop(chat, None)
            return self.say(chat, "هر وقت خواستید دوباره یکی از دکمه‌ها را بزنید.", MENU)
        self.state[chat] = ("onb_user", text[:60])
        self.say(chat, "یک نام کاربری انگلیسی برای ورود به پنل وب انتخاب کنید "
                 "(حروف انگلیسی و عدد، مثلاً ali_gamer):", CANCEL)

    def got_username(self, chat, sender, text, name):
        try:
            res = self.panel.call("POST", "/users/%d/credentials" % sender["id"],
                                  {"username": text, "name": name})
        except ApiError as e:
            if e.body.get("error") == "has_username":
                self.state.pop(chat, None)
                return self.show_web(chat, sender)
            return self.say(chat, "⚠️ %s" % e, CANCEL)
        self.state.pop(chat, None)
        self.say(chat, "✅ حساب پنل شما ساخته شد\n\n%sنام کاربری: %s\nرمز: %s\n\nاین را "
                 "نگه دارید؛ رمز را در پنل هر وقت خواستید عوض کنید."
                 % ("آدرس: %s\n" % res["panel_url"] if res.get("panel_url") else "",
                    res["username"], res["password"]), MENU)
        self.ip_help(chat, sender)

    def login_button(self, sender):
        try:
            res = self.panel.call("POST", "/users/%d/login-link" % sender["id"])
        except ApiError:
            return None
        return res

    def ip_help(self, chat, sender):
        """Registering the address is easiest from the web page, which sees it
        by itself: a one-time link signs the customer straight in."""
        link = self.login_button(sender)
        self.state[chat] = ("ip", None)
        if link:
            return self.say(chat, "🌐 ثبت آی‌پی\n\nاین لینک را با همان اینترنتی باز کنید که "
                            "می‌خواهید سرویس رویش کار کند (مثلاً وای‌فای خانه، نه اینترنت "
                            "گوشی)؛ خودکار وارد حسابتان می‌شوید و فقط «ثبت آی‌پی» را بزنید:\n\n"
                            "%s\n\n(%d دقیقه اعتبار دارد و یک بار کار می‌کند)\n\n"
                            "یا آی‌پی را همین‌جا بفرستید." % (link["url"], link["minutes"]),
                            CANCEL)
        return self.say(chat, "آی‌پی اینترنتی را که می‌خواهید سرویس رویش کار کند بفرستید.\n\n"
                        "روی همان اینترنت (مودم یا سیم‌کارت) یک سایت «آی‌پی من چیست» را "
                        "باز کنید تا پیدایش کنید. مثال: 5.123.45.67", CANCEL)

    def show_web(self, chat, sender):
        u = self.account(sender)
        self.say(chat, "🔑 پنل وب\n\n%sنام کاربری: %s\n\nرمز را فراموش کرده‌اید؟ «رمز تازه» "
                 "را بزنید." % ("آدرس: %s\n" % u["panel_url"] if u.get("panel_url") else "",
                              u["username"]),
                 {"inline_keyboard": [[{"text": "🔗 ورود با یک کلیک", "callback_data": "login"},
                                       {"text": "🔄 رمز تازه", "callback_data": "newpw"}]]})

    def profiles_of(self, u):
        """The customer's servers that have a DoH address, for the iPhone
        profile: [(label, DoH address, the server's IP)]."""
        servers = u.get("servers") or []
        if servers:
            return [("%s %d" % (self.t("تک‌سرور") if x.get("single") else self.t("سرور"),
                                x["n"]), x["doh"], x["ip"]) for x in servers if x.get("doh")]
        doh = u.get("doh")
        return [("", doh["url"], (u["dns"] or [""])[0])] if doh else []

    def send_profiles(self, chat, servers):
        """The iPhone profile as a file, when its button is pressed: one for
        each server that has a DoH address. A file that would not go is said,
        not left as silence."""
        if not servers:
            return self.say(chat, "هنوز آدرس DoH ندارید؛ پروفایل آیفون با آن ساخته می‌شود.")
        for i, (label, url, address) in enumerate(servers, 1):
            name = "dns.mobileconfig" if len(servers) == 1 else "dns-%d.mobileconfig" % i
            try:
                self.tg.document(chat, name, ios_profile(url, address),
                                 self.t(IOS_PROFILE_HELP % (" — " + label if label else "")))
            except Exception as e:
                log("iPhone profile not sent to %s: %s" % (chat, e))
                self.say(chat, "⚠️ پروفایل فرستاده نشد؛ کمی بعد دوباره بزنید، یا از پنل وب "
                         "بگیریدش.")

    def show_dns(self, chat, sender):
        """Every way to use the service, in one message: the plain DNS
        addresses, and the personal DoH and DoT ones once a relay has them -
        with a button, then, that sends the iPhone profile as a file."""
        u = self.account(sender)
        lines = ["📡 DNSهای شما", ""]
        servers = u.get("servers") or []
        if servers:
            return self.show_servers(chat, u, servers)
        if u["dns"]:
            lines.append("🌐 DNS معمولی — در کنسول، مودم یا گوشی، هم DNS اول و هم دوم را "
                         "روی یکی از این‌ها بگذارید:")
            lines.extend(u["dns"])
        else:
            lines.append("🌐 آدرس DNS معمولی هنوز آماده نیست؛ کمی بعد دوباره بزنید.")
        doh = u.get("doh")
        buttons = [{"text": "🔗 ورود به پنل وب", "callback_data": "login"}]
        if doh:
            lines += ["",
                      "🔒 DNS امن (رمزگذاری‌شده)",
                      "📱 اندروید — تنظیمات ← شبکه ← DNS خصوصی ← نام میزبان (DoT):",
                      doh["dot_host"],
                      "",
                      "💻 آیفون، ویندوز، کروم و فایرفاکس — آدرس شخصی شما (DoH):",
                      doh["url"],
                      "",
                      "برای آیفون دکمهٔ «دریافت پروفایل آیفون📱» را بزنید. "
                      "آدرس DoH مخصوص حساب شماست؛ آن را به کسی ندهید."]
            buttons.append({"text": "🔄 آدرس DoH تازه", "callback_data": "dohnew"})
            buttons.append({"text": B_IOS_PROFILE, "callback_data": "iosprofile"})
        if u.get("wg_on"):
            buttons.append({"text": B_WG, "callback_data": "wg"})
        lines.append("")
        lines.append("همهٔ این‌ها فقط روی اینترنتی کار می‌کنند که آی‌پی‌اش را ثبت کرده‌اید.")
        if not u["ips"]:
            lines.append("⚠️ هنوز آی‌پی ثبت نکرده‌اید؛ اول «ثبت آی‌پی» را بزنید.")
        self.say(chat, "\n".join(lines), {"inline_keyboard": self.dns_rows(buttons)})

    def show_servers(self, chat, u, servers):
        """One block per server the customer is given: its plain DNS, and
        its DoT and DoH once it has them - so when one is filtered, another
        is right there."""
        lines = ["📡 DNSهای شما", "",
                 "در کنسول، مودم یا گوشی، هم DNS اول و هم دوم را روی آدرس یکی از این "
                 "سرورها بگذارید. اگر یکی کند یا فیلتر شد، سراغ دیگری بروید."]
        with_doh = False
        for s in servers:
            lines += ["", "%s %s %d%s" % ("🌍" if s.get("single") else "🌐",
                                          "تک‌سرور" if s.get("single") else "سرور", s["n"],
                                          " — %s" % s["note"] if s.get("note") else ""),
                      "DNS: %s" % s["ip"]]
            if s.get("dot"):
                with_doh = True
                lines += ["DoT (اندروید ← DNS خصوصی): %s" % s["dot"],
                          "DoH (آیفون، ویندوز، مرورگر): %s" % s["doh"]]
        lines.append("")
        if with_doh:
            lines.append("برای آیفون دکمهٔ «دریافت پروفایل آیفون📱» را بزنید. "
                         "آدرس‌های DoH مخصوص حساب شماست؛ آن‌ها را به کسی ندهید.")
        lines.append("همهٔ این‌ها فقط روی اینترنتی کار می‌کنند که آی‌پی‌اش را ثبت کرده‌اید.")
        if not u["ips"]:
            lines.append("⚠️ هنوز آی‌پی ثبت نکرده‌اید؛ اول «ثبت آی‌پی» را بزنید.")
        buttons = [{"text": "🔗 ورود به پنل وب", "callback_data": "login"}]
        if with_doh:
            buttons.append({"text": "🔄 آدرس DoH تازه", "callback_data": "dohnew"})
            buttons.append({"text": B_IOS_PROFILE, "callback_data": "iosprofile"})
        if u.get("wg_on"):
            buttons.append({"text": B_WG, "callback_data": "wg"})
        self.say(chat, "\n".join(lines), {"inline_keyboard": self.dns_rows(buttons)})

    @staticmethod
    def dns_rows(buttons):
        """The DNS message's buttons: the first two side by side, each after
        them - the iPhone profile, WireGuard - on a row of its own: three do
        not fit one row on a phone."""
        return [buttons[:2]] + [[b] for b in buttons[2:]]

    def show_wg(self, chat, sender, said=""):
        """The customer's WireGuard: how each server's config is doing, one
        button for every config at once, and each to delete."""
        try:
            wg = self.panel.call("GET", "/users/%d/wg" % sender["id"])["wg"]
        except ApiError as e:
            return self.say(chat, "⚠️ " + str(e))
        rows = []
        lines = ([said, ""] if said else []) + [WG_HELP, "", (
            "روی همان اینترنت‌هایی کار می‌کند که آی‌پی‌شان را ثبت کرده‌اید، مثل DNS." if wg.get("bind") else
            "آی‌پی ثبت کردن لازم نیست و روی هر اینترنتی کار می‌کند."), ""]
        devices = wg.get("devices") or []
        for d in devices:
            lines.append("• %s%s — %s" % (d.get("server") or d["address"],
                                          " (%s)" % d["note"] if d.get("note") else "",
                                          "🟢 آنلاین" if d.get("online") else
                                          "⛔ تا فردا قطع (آی‌پی زیاد)" if d.get("blocked") else
                                          "آخرین اتصال: %s" % d["last"] if d.get("last")
                                          else "هنوز وصل نشده"))
        if wg.get("self") and wg.get("room"):
            # Every server still without one gets its config in the same go.
            rows.append([{"text": "📥 کانفیگ سرورهای تازه" if devices
                          else "📥 دریافت کانفیگ وایرگارد", "callback_data": "wgnew"}])
        if devices:
            rows.append([{"text": "📲 همهٔ کانفیگ‌ها و QR کدها", "callback_data": "wgall"}])
        if wg.get("self") and devices:
            dels = [{"text": "🗑 حذف " + (d.get("server") or d["address"]),
                     "callback_data": "wgdel:%d" % d["id"]} for d in devices]
            rows += [dels[i:i + 2] for i in range(0, len(dels), 2)]
        if not wg.get("self") and not devices:
            lines.append("برای گرفتن کانفیگ وایرگارد به پشتیبانی پیام بدهید.")
        return self.say(chat, "\n".join(lines), {"inline_keyboard": rows} if rows else None)

    def send_wg(self, chat, res):
        """One config: its server and the admin's words about it, its QR to
        scan, and its file to open."""
        head = "🛡 %s" % (res.get("server") or "کانفیگ وایرگارد") + (
            "\n%s" % res["note"] if res.get("note") else "")
        caption = head + "\n" + self.t("در برنامهٔ WireGuard: + ← اسکن QR، یا فایل را باز "
                                        "کنید.")
        try:
            if res.get("qr"):
                self.tg.photo(chat, base64.b64decode(res["qr"]), caption)
            self.tg.document(chat, res["file"], res["config"].encode("utf-8"),
                             "" if res.get("qr") else caption)
        except Exception as e:
            log("WireGuard config not sent to %s: %s" % (chat, e))
            self.say(chat, "⚠️ کانفیگ فرستاده نشد؛ کمی بعد دوباره بزنید، یا از پنل وب "
                     "بگیریدش.")

    def send_wg_all(self, chat, configs):
        """Every config, one after another, each under its server's name."""
        if len(configs) > 1:
            self.say(chat, "برای هر سرور یک کانفیگ؛ هر کدام را جدا در برنامهٔ WireGuard اضافه "
                     "کنید. اگر یکی کار نکرد، دیگری را روشن کنید.")
        for c in configs:
            self.send_wg(chat, c)

    def help_text(self):
        return ("📊 حساب من: وضعیت، حجم مانده و روزهای باقی‌مانده\n"
                "🛒 خرید / تمدید: انتخاب پلن و فرستادن رسید\n"
                "💰 کیف پول: موجودی و شارژ\n"
                "🎁 دعوت از دوستان: لینک دعوت و پورسانت\n"
                "🌐 ثبت آی‌پی: سرویس فقط روی آی‌پی ثبت‌شده کار می‌کند\n"
                "🎫 پشتیبانی: تیکت و گفتگو با پشتیبانی\n"
                "📡 DNSها: آدرس DNS معمولی، DoH و DoT\n"
                "🔑 پنل وب: نام کاربری، ورود با یک کلیک و رمز تازه\n\n"
                "حساب پنل وب دارید؟ در پنل «اتصال حساب به تلگرام» را بزنید و کد را "
                "همین‌جا بفرستید." + ("\n\n" + self.cfg["support"] if self.cfg["support"] else ""))

    # -- the customer's screens ---------------------------------------------
    def show_account(self, chat, sender):
        u = self.account(sender)
        lines = ["👤 %s" % (u["name"] or "حساب شما"),
                 "وضعیت: %s" % STATUS.get(u["status"], u["status"])]
        if u["plan"]:
            lines.append("پلن: %s" % u["plan"]["name"])
        if u["status"] != "pending":
            lines.append("حجم مانده: %s" % ("نامحدود" if u["unlimited"]
                                              else size_fa(u["remaining_bytes"])))
            lines.append("مصرف: %s" % size_fa(u["used_bytes"]))
        if u["expires_at"]:
            lines.append("پایان دوره: %s" % show_date(u["expires_at"], u.get("calendar")))
            left = time_left_fa(u["expires_at"])
            if left:
                lines.append("زمان باقی‌مانده: %s" % left)
        lines.append("آی‌پی: %s" % (", ".join(u["ips"]) if u["ips"] else "ثبت نشده ⚠️"))
        if (u.get("max_ips") or 1) > 1:
            lines.append("دستگاه: %d از %d" % (len(u["ips"]), u["max_ips"]))
        if u.get("reserved"):
            lines.append("⏳ پلن رزرو: %s (بعد از تمام شدن پلن فعلی خودکار فعال می‌شود)"
                         % "، ".join(u["reserved"]))
        if u["receipt_waiting"]:
            lines.append("\n⏳ یک رسید در انتظار بررسی دارید.")
        offer = u.get("device_offer")
        if offer and offer.get("available"):
            return self.say(chat, "\n".join(lines), {"inline_keyboard": [[
                {"text": "📱 دستگاه اضافه — %s تومان" % money(offer["price"]),
                 "callback_data": "dev"}]]})
        self.say(chat, "\n".join(lines), MENU)

    def show_device(self, chat, sender):
        u = self.account(sender)
        offer = u.get("device_offer")
        if not offer:
            return self.say(chat, "دستگاه اضافه فروخته نمی‌شود.", MENU)
        if not offer.get("available"):
            return self.say(chat, "⚠️ %s" % offer.get("why"), MENU)
        rows = []
        if (u.get("wallet") or 0) >= offer["price"]:
            rows.append([{"text": "💰 پرداخت از کیف پول", "callback_data": "dwb"}])
        rows.append([{"text": "🧾 پرداخت با رسید", "callback_data": "drc"}])
        self.say(chat, "📱 دستگاه اضافه\nالان %d دستگاه دارید؛ هر دستگاه اضافه %s تومان، تا "
                 "وقتی همین پلن را تمدید کنید." % (offer["devices"], money(offer["price"])),
                 {"inline_keyboard": rows})

    def show_plans(self, chat, sender):
        u = self.account(sender)
        plans = self.panel.call("GET", "/plans")["plans"]
        trial = u.get("trial")
        rows = []
        if trial and trial.get("available"):
            p = trial["plan"]
            rows.append([{"text": "🎁 تست رایگان: %s، %d روز" % (
                size_fa(p["quota_bytes"]) if p["quota_bytes"] else "نامحدود", p["days"]),
                "callback_data": "trial"}])
        if not plans and not rows:
            return self.say(chat, "فعلاً پلنی برای فروش نیست. با پشتیبانی در تماس باشید.", MENU)
        for p in plans:
            size = size_fa(p["quota_bytes"]) if p["quota_bytes"] else "نامحدود"
            dev = " · %d دستگاه" % p["devices"] if (p.get("devices") or 1) > 1 else ""
            rows.append([{"text": "%s · %s · %d روز%s · %s تومان"
                          % (p["name"], size, p["days"], dev, format(p["price"], ",")),
                          "callback_data": "buy:%d" % p["id"]}])
        notes = "\n".join("• %s (%s): %s" % (p["name"], p["template"], p["note"])
                          for p in plans if p.get("note"))
        self.say(chat, "پلن را انتخاب کنید:" + ("\n\n" + notes if notes else ""),
                 {"inline_keyboard": rows})

    def plan_games(self, plan, most=12):
        """The games a plan covers, as the customer reads them.

        The panel sends the names; a long list is cut off here, because a
        message that scrolls past the price is not an answer to "what is in
        it". The count that follows says the rest is still there.
        """
        games = plan.get("games") or []
        if not games:
            return ""
        # One more name is shorter than saying there is one more.
        shown = games if len(games) <= most + 1 else games[:most]
        rest = len(games) - len(shown)
        line = "، ".join(shown)
        if rest > 0:
            line += " و %d بازی دیگر" % rest
        return "\n\n🎮 شامل: " + line

    def chose_plan(self, chat, sender, plan_id, code=""):
        sale = self.panel.call("GET", "/plans")
        plans = {p["id"]: p for p in sale["plans"]}
        if code:
            # The plans' prices after the code, as the panel reckons them.
            try:
                found = self.panel.call("POST", "/users/%d/discount" % sender["id"],
                                        {"code": code})
                plans = {p["id"]: p for p in found["plans"]}
            except ApiError as e:
                return self.say(chat, "⚠️ %s" % e, MENU)
        # What the admin panel says, first: changing the card there changes it
        # here at once. PAY_TEXT is only for a panel that has none set.
        pay = (sale.get("pay_text") or "").strip() or self.cfg["pay"]
        plan = plans.get(plan_id)
        if not plan:
            return self.say(chat, "این پلن دیگر فروخته نمی‌شود.", MENU)
        u = self.account(sender)
        warn = ""
        if u["status"] == "active" and u.get("expires_at"):
            warn = ("\n\n⏳ پلن فعلی شما%s هنوز تمام نشده؛ این پلن رزرو می‌شود و بعد از تمام "
                    "شدن آن خودکار فعال می‌شود." % (" «%s»" % u["plan"]["name"]
                                                    if u["plan"] else ""))
        self.state[chat] = ("receipt", (plan_id, code) if code else plan_id)
        price = ("%s تومان (به‌جای %s، با کد %s)" % (format(plan["price"], ","),
                                                  format(plan["list_price"], ","), code)
                 if code and plan.get("list_price") else "%s تومان" % format(plan["price"], ","))
        self.say(chat, "پلن «%s» — %s%s%s\n\n%s\n\nبعد از واریز، عکس رسید را همین‌جا "
                 "بفرستید." % (plan["name"], price,
                               self.plan_games(plan), warn,
                               pay or "برای روش پرداخت با پشتیبانی تماس بگیرید."),
                 CANCEL)
        wallet = u.get("wallet") or 0
        rows = []
        if wallet >= plan["price"] > 0:
            rows.append([{"text": "💰 پرداخت از کیف پول (موجودی %s تومان)" % money(wallet),
                          "callback_data": ("wbuy:%d:%s" % (plan_id, code))[:64]
                          if code else "wbuy:%d" % plan_id}])
        elif wallet > 0:
            self.say(chat, "💰 موجودی کیف پولتان %s تومان است؛ برای این پلن %s تومان کم "
                     "دارید." % (money(wallet), money(plan["price"] - wallet)))
        if not code:
            rows.append([{"text": "🏷 کد تخفیف دارم", "callback_data": "code:%d" % plan_id}])
        if rows:
            self.say(chat, "یا:", {"inline_keyboard": rows})

    def show_wallet(self, chat, sender):
        cal = self.account(sender).get("calendar")
        w = self.panel.call("GET", "/users/%d/wallet" % sender["id"])
        lines = ["💰 کیف پول", "موجودی: %s تومان" % money(w["balance"])]
        if w.get("moves"):
            lines.append("")
            for m in w["moves"][:8]:
                lines.append("%s %s%s تومان — %s%s" % (
                    show_date(m.get("at"), cal), "+" if m["amount"] > 0 else "−",
                    money(abs(m["amount"])), m.get("what") or "",
                    " (%s)" % m["note"] if m.get("note") else ""))
        rows = []
        if w.get("wallet_on"):
            rows.append([{"text": "➕ شارژ کیف پول", "callback_data": "topup"}])
        if w["balance"] > 0:
            rows.append([{"text": "🛒 خرید پلن", "callback_data": "plans"}])
        self.say(chat, "\n".join(lines), {"inline_keyboard": rows} if rows else MENU)

    def show_invite(self, chat, sender):
        """The customer's invitation links, while inviting pays - their own
        button, so the wallet is only about money."""
        self.account(sender)
        ref = self.panel.call("GET", "/users/%d/wallet" % sender["id"]).get("ref")
        if not ref or not (ref.get("bot_link") or ref.get("web_link")):
            return self.say(chat, "🎁 دعوت از دوستان فعلاً فعال نیست.", MENU)
        lines = ["🎁 دعوت از دوستان",
                 "هر کس با لینک شما حساب بسازد و پلن بخرد، %d٪ مبلغ %s به کیف پول شما "
                 "اضافه می‌شود." % (ref["percent"], "اولین خریدش"
                                    if ref.get("mode") == "first" else "هر خریدش")]
        if ref.get("bot_link"):
            lines += ["", "🤖 لینک ربات:", ref["bot_link"]]
        if ref.get("web_link"):
            lines += ["", "🌐 لینک ثبت‌نام در سایت:", ref["web_link"]]
        lines += ["", "تا حالا %d نفر با لینک شما آمده‌اند و %s تومان پورسانت گرفته‌اید."
                  % (ref.get("invited") or 0, money(ref.get("earned")))]
        self.say(chat, "\n".join(lines), MENU)

    def ask_topup(self, chat, sender):
        w = self.panel.call("GET", "/users/%d/wallet" % sender["id"])
        if not w.get("wallet_on"):
            return self.say(chat, "شارژ کیف پول فعلاً بسته است.", MENU)
        self.state[chat] = ("topup_amount", None)
        self.say(chat, "چقدر می‌خواهید شارژ کنید؟ مبلغ را به تومان بنویسید (حداقل %s):"
                 % money(w.get("topup_min")), CANCEL)

    def got_topup_amount(self, chat, sender, text):
        amount = typed_toman(text)
        sale = self.panel.call("GET", "/plans")
        least, most = sale.get("topup_min") or 0, sale.get("topup_max") or 10 ** 12
        if amount is None or not least <= amount <= most:
            return self.say(chat, "⚠️ مبلغ را به تومان و با عدد بنویسید، بین %s و %s."
                            % (money(least), money(most)), CANCEL)
        pay = (sale.get("pay_text") or "").strip() or self.cfg["pay"]
        self.state[chat] = ("topup_receipt", amount)
        self.say(chat, "شارژ کیف پول — %s تومان\n\n%s\n\nبعد از واریز، عکس رسید را همین‌جا "
                 "بفرستید." % (money(amount), pay or "برای روش پرداخت با پشتیبانی تماس "
                                                     "بگیرید."), CANCEL)

    def got_receipt(self, chat, sender, msg, plan_id, topup=None, device=False):
        file_id = None
        if msg.get("photo"):
            file_id = msg["photo"][-1]["file_id"]
        elif msg.get("document"):
            file_id = msg["document"]["file_id"]
        if not file_id:
            return self.say(chat, "عکس رسید را بفرستید (یا «انصراف»).", CANCEL)
        try:
            blob = self.tg.download(file_id)
        except ValueError as e:
            return self.say(chat, "⚠️ %s" % e, CANCEL)
        kind = image_type(blob)
        if not kind or len(blob) > MAX_FILE:
            return self.say(chat, "⚠️ فقط عکس (JPG، PNG، WEBP) یا PDF تا ۴ مگابایت.", CANCEL)
        code = ""
        if isinstance(plan_id, tuple):
            plan_id, code = plan_id
        # One key per Telegram message: a retry after a timeout is the same receipt.
        body = {"plan_id": plan_id, "code": code, "content_type": kind,
                "data": base64.b64encode(blob).decode()}
        if topup:
            body = dict(body, plan_id=None, kind="topup", amount=topup)
        if device:
            body = dict(body, plan_id=None, kind="device")
        res = self.panel.call("POST", "/users/%d/receipts" % sender["id"], body,
                              idem="receipt-%d-%d" % (sender["id"], msg["message_id"]))
        self.state.pop(chat, None)
        self.say(chat, "✅ " + res["message"], MENU)

    def got_ip(self, chat, sender, text):
        self.account(sender)
        ip = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")).strip()
        try:
            res = self.panel.call("POST", "/users/%d/ips" % sender["id"], {"ip": ip})
        except ApiError as e:
            return self.say(chat, "⚠️ %s\nدوباره بفرستید یا «انصراف»." % e, CANCEL)
        self.state.pop(chat, None)
        self.say(chat, "✅ %s" % res["message"], MENU)

    def show_tickets(self, chat, sender):
        self.account(sender)
        tickets = self.panel.call("GET", "/users/%d/tickets" % sender["id"])["tickets"]
        state = {"open": "⏳", "answered": "💬", "closed": "✔️"}
        rows = [[{"text": "%s %s" % (state.get(t["status"], ""), t["subject"]),
                  "callback_data": "tk:%d" % t["id"]}] for t in tickets[:8]]
        rows.append([{"text": "➕ تیکت تازه", "callback_data": "tknew"}])
        self.say(chat, "تیکت‌های شما:" if tickets else "هنوز تیکتی ندارید.",
                 {"inline_keyboard": rows})

    def show_ticket(self, chat, sender, tid):
        t = self.panel.call("GET", "/users/%d/tickets/%d" % (sender["id"], tid))["ticket"]
        lines = ["🎫 %s" % t["subject"]]
        for m in t["messages"][-10:]:
            lines.append("\n%s %s:\n%s%s" % ("👨‍💻" if m["from"] == "admin" else "👤",
                                              "پشتیبانی" if m["from"] == "admin" else "شما",
                                              m["body"], " 🖼" if m["has_image"] else ""))
        buttons = [{"text": "✍️ پیام تازه", "callback_data": "tkr:%d" % tid}]
        if t["status"] != "closed":
            buttons.append({"text": "✔️ بستن", "callback_data": "tkc:%d" % tid})
        self.say(chat, "\n".join(lines), {"inline_keyboard": [buttons]})

    def ticket_picture(self, msg):
        """A ticket message's picture: as a photo, or as a file when it is an
        image - a screenshot sent uncompressed. ({image_type, image_data} or
        {}, and why not, when there was one that cannot be taken)."""
        doc = msg.get("document") or {}
        if msg.get("photo"):
            file_id = msg["photo"][-1]["file_id"]
        elif (doc.get("mime_type") or "").startswith("image/"):
            file_id = doc["file_id"]
        else:
            return {}, ""
        try:
            blob = self.tg.download(file_id)
        except ValueError as e:
            return {}, str(e)
        kind = image_type(blob)
        if kind not in TICKET_IMAGES:
            return {}, "فقط عکس JPG، PNG یا WEBP"
        return {"image_type": kind, "image_data": base64.b64encode(blob).decode()}, ""

    def got_ticket(self, chat, sender, msg, subject=None, ticket=None):
        body = (msg.get("text") or msg.get("caption") or "").strip()
        picture, why = self.ticket_picture(msg)
        if why:
            return self.say(chat, "⚠️ %s — دوباره بفرستید (یا «انصراف»)." % why, CANCEL)
        # A screenshot of the error alone is a message too.
        if not body and not picture:
            return self.say(chat, "متن پیام را بنویسید یا عکس بفرستید (یا «انصراف»).", CANCEL)
        payload = dict(picture, body=body)
        idem = "ticket-%d-%d" % (sender["id"], msg["message_id"])
        if subject is not None:
            payload["subject"] = subject
            res = self.panel.call("POST", "/users/%d/tickets" % sender["id"], payload, idem)
        else:
            res = self.panel.call("POST", "/users/%d/tickets/%d/messages"
                                  % (sender["id"], ticket), payload, idem)
        self.state.pop(chat, None)
        self.say(chat, "✅ " + res["message"], MENU)

    # -- buttons ------------------------------------------------------------
    def on_button(self, q):
        chat, sender, data = q["message"]["chat"]["id"], q["from"], q.get("data") or ""
        try:
            self.tg.call("answerCallbackQuery", callback_query_id=q["id"])
        except Exception:
            pass
        kind, _, arg = data.partition(":")
        if kind in ("ok", "no", "areply", "aclose"):
            if not self.is_admin(sender["id"]):
                return
            return self.admin_button(chat, q, kind, int(arg))
        if kind == "joined":
            self.members.pop(sender["id"], None)
            if not self.in_channel(sender["id"]):
                return self.ask_to_join(chat, "هنوز عضو کانال نشده‌اید. ")
            return self.say(chat, "✅ ممنون! حالا از دکمه‌های پایین استفاده کنید.\n\n"
                            + self.help_text(), MENU)
        if not self.in_channel(sender["id"]):
            return self.ask_to_join(chat)
        if kind == "buy":
            return self.chose_plan(chat, sender, int(arg))
        if kind == "plans":
            return self.show_plans(chat, sender)
        if kind == "topup":
            return self.ask_topup(chat, sender)
        if kind == "dev":
            return self.show_device(chat, sender)
        if kind == "dwb":
            try:
                res = self.panel.call("POST", "/users/%d/devices/buy" % sender["id"])
            except ApiError as e:
                return self.say(chat, "⚠️ %s" % e, MENU)
            return self.say(chat, res["message"], MENU)
        if kind == "drc":
            sale = self.panel.call("GET", "/plans")
            pay = (sale.get("pay_text") or "").strip() or self.cfg["pay"]
            self.state[chat] = ("device_receipt", None)
            return self.say(chat, "📱 دستگاه اضافه — %s تومان\n\n%s\n\nبعد از واریز، عکس رسید "
                            "را همین‌جا بفرستید." % (money(sale.get("device_price")),
                                                   pay or "برای روش پرداخت با پشتیبانی تماس "
                                                          "بگیرید."), CANCEL)
        if kind == "code":
            self.state[chat] = ("code", int(arg))
            return self.say(chat, "کد تخفیف را بنویسید:", CANCEL)
        if kind == "wbuy":
            self.state.pop(chat, None)
            pid, _, code = arg.partition(":")
            try:
                res = self.panel.call("POST", "/users/%d/wallet/buy" % sender["id"],
                                      {"plan_id": int(pid), "code": code})
            except ApiError as e:
                return self.say(chat, "⚠️ %s" % e, MENU)
            self.say(chat, res["message"], MENU)
            if not res["user"]["ips"]:
                return self.ip_help(chat, sender)
            return None
        if kind == "login":
            link = self.login_button(sender)
            return self.say(chat, ("🔗 %s\n\n(%d دقیقه اعتبار دارد و یک بار کار می‌کند)"
                                   % (link["url"], link["minutes"])) if link
                            else "⚠️ آدرس پنل هنوز معلوم نیست؛ چند دقیقه دیگر امتحان کنید.")
        if kind == "iosprofile":
            return self.send_profiles(chat, self.profiles_of(self.account(sender)))
        if kind == "wg":
            return self.show_wg(chat, sender)
        if kind in ("wgget", "wgnew", "wgall"):
            try:
                res = (self.panel.call("POST", "/users/%d/wg" % sender["id"],
                                       {"relay": arg} if arg else None) if kind == "wgnew"
                       else self.panel.call("GET", "/users/%d/wg/all" % sender["id"])
                       if kind == "wgall"
                       else self.panel.call("GET", "/users/%d/wg/%d" % (sender["id"], int(arg))))
            except ApiError as e:
                return self.say(chat, "⚠️ " + str(e))
            if kind == "wgnew":
                self.say(chat, "✅ " + res.get("message", ""))
            if kind == "wgget":
                return self.send_wg(chat, res)
            return self.send_wg_all(chat, res.get("configs") or [res])
        if kind == "wgdel":
            try:
                res = self.panel.call("DELETE", "/users/%d/wg/%d" % (sender["id"], int(arg)))
            except ApiError as e:
                return self.say(chat, "⚠️ " + str(e))
            return self.show_wg(chat, sender, "🗑 " + res.get("message", ""))
        if kind == "dohnew":
            self.panel.call("POST", "/users/%d/doh-reset" % sender["id"])
            self.say(chat, "🔄 آدرس تازه ساخته شد. آدرس قبلی تا یک دقیقه دیگر کار نمی‌کند؛ "
                     "این را روی دستگاه‌هایتان بگذارید:")
            return self.show_dns(chat, sender)
        if kind == "newpw":
            res = self.panel.call("POST", "/users/%d/password" % sender["id"])
            return self.say(chat, "🔄 رمز تازهٔ پنل: %s\nنام کاربری: %s\n\nهر جا با رمز قبلی "
                            "وارد بودید، خارج شدید." % (res["password"], res["username"]))
        if kind == "trial":
            self.account(sender)
            res = self.panel.call("POST", "/users/%d/trial" % sender["id"])
            self.say(chat, res["message"], MENU)
            if not res["user"]["ips"]:
                return self.ip_help(chat, sender)
            return None
        if kind == "tk":
            return self.show_ticket(chat, sender, int(arg))
        if kind == "tknew":
            self.state[chat] = ("ticket_subject", None)
            return self.say(chat, "موضوع تیکت را در یک خط بنویسید:", CANCEL)
        if kind == "tkr":
            self.state[chat] = ("ticket_reply", int(arg))
            return self.say(chat, "پیامتان را بنویسید یا عکس بفرستید:", CANCEL)
        if kind == "tkc":
            res = self.panel.call("POST", "/users/%d/tickets/%d/close" % (sender["id"], int(arg)))
            return self.say(chat, "✔️ " + res["message"], MENU)

    # -- the operator -------------------------------------------------------
    def admin_command(self, chat, text):
        if text == "/stats":
            return self.say(chat, self.panel.call("GET", "/admin/stats")["text"])
        receipts = self.panel.call("GET", "/admin/receipts")["receipts"]
        if not receipts:
            return self.say(chat, "رسیدی در انتظار نیست.")
        for r in receipts[:10]:
            # The whole receipt from the panel; a panel too old to say it,
            # the short line it used to be.
            self.post_receipt(chat, r["id"], r.get("text") or "رسید از %s%s — %s تومان" % (
                r["user"]["label"], " برای شارژ کیف پول" if r.get("kind") == "topup"
                else " برای «%s»" % r["plan"]["name"] if r["plan"] else "",
                format(r["amount"], ",")))

    def post_receipt(self, chat, rid, caption):
        buttons = {"inline_keyboard": [[{"text": "✅ تأیید", "callback_data": "ok:%d" % rid},
                                        {"text": "❌ رد", "callback_data": "no:%d" % rid}]]}
        try:
            pic = self.panel.call("GET", "/admin/receipts/%d/image" % rid)
            self.tg.photo(chat, base64.b64decode(pic["data"]), self.t(caption),
                          self.t_markup(buttons))
        except ApiError:
            self.say(chat, caption, buttons)

    def ticket_notice(self, chat, text, markup, data, image_path):
        """A ticket's message to the operator or the customer, with its
        picture when it has one: the picture with the words under it, or
        after it when they are too long for a caption. Words alone if the
        picture cannot be had."""
        mid = data.get("message_id")
        if not data.get("has_image") or not mid:
            return self.say(chat, text, markup)
        try:
            pic = self.panel.call("GET", image_path % int(mid))
            blob = base64.b64decode(pic.get("data") or pic.get("image_data") or "")
        except Exception as e:
            log("ticket #%s: its picture could not be had (%s)" % (data.get("ticket_id"), e))
            return self.say(chat, text, markup)
        words = self.t(text)
        # photo() cuts a caption at 1000, a little under Telegram's own.
        if len(words) <= 1000:
            try:
                return self.tg.photo(chat, blob, words, self.t_markup(markup))
            except Exception as e:
                log("could not send to %s: %s" % (chat, e))
                return self.say(chat, text, markup)
        try:
            self.tg.photo(chat, blob, "")
        except Exception as e:
            log("could not send to %s: %s" % (chat, e))
        self.say(chat, text, markup)

    def admin_button(self, chat, q, kind, num):
        if kind in ("ok", "no"):
            try:
                res = self.panel.call("POST", "/admin/receipts/%d/%s"
                                      % (num, "approve" if kind == "ok" else "reject"))
                done = ("✅ " if kind == "ok" else "❌ ") + res["message"]
            except ApiError as e:
                done = "⚠️ " + str(e)
            # Take the buttons off, so nobody presses them again.
            try:
                self.tg.call("editMessageReplyMarkup", chat_id=chat,
                             message_id=q["message"]["message_id"],
                             reply_markup={"inline_keyboard": []})
            except Exception:
                pass
            return self.say(chat, "رسید #%d: %s" % (num, done))
        if kind == "areply":
            self.state[chat] = ("admin_reply", num)
            return self.say(chat, "جواب تیکت #%d را بنویسید یا عکس بفرستید:" % num, CANCEL)
        if kind == "aclose":
            res = self.panel.call("POST", "/admin/tickets/%d/close" % num)
            return self.say(chat, "تیکت #%d: %s" % (num, res["message"]))

    def admin_reply(self, chat, msg, tid):
        body = (msg.get("text") or msg.get("caption") or "").strip()
        picture, why = self.ticket_picture(msg)
        if why:
            return self.say(chat, "⚠️ %s — دوباره بفرستید (یا «انصراف»)." % why, CANCEL)
        if not body and not picture:
            return self.say(chat, "متن جواب را بنویسید یا عکس بفرستید (یا «انصراف»).", CANCEL)
        payload = dict(picture, body=body)
        self.panel.call("POST", "/admin/tickets/%d/reply" % tid, payload,
                        idem="areply-%d-%d" % (chat, msg["message_id"]))
        self.state.pop(chat, None)
        self.say(chat, "✅ جواب تیکت #%d فرستاده شد." % tid, MENU)

    # -- what the panel tells us -------------------------------------------
    def on_event(self, ev):
        with self.lock:
            if ev.get("id") in self.seen:
                return
            self.seen = (self.seen + [ev.get("id")])[-1000:]
        data, kind = ev.get("data") or {}, ev.get("event")
        text = data.get("text") or ""
        if ev.get("audience") == "admin" or kind == "ping":
            for admin in self.cfg["admins"]:
                if kind == "receipt.submitted":
                    self.post_receipt(admin, data["receipt_id"],
                                      text if text.startswith("🧾") else "🧾 " + text)
                elif kind in ("ticket.opened", "ticket.message"):
                    tid = data["ticket_id"]
                    buttons = {"inline_keyboard": [[
                        {"text": "✍️ جواب", "callback_data": "areply:%d" % tid},
                        {"text": "✔️ بستن", "callback_data": "aclose:%d" % tid}]]}
                    self.ticket_notice(admin, "🎫 #%d %s" % (tid, text), buttons, data,
                                       "/admin/tickets/%d/messages/%%d/image" % tid)
                elif text:
                    self.say(admin, text)
            return
        chat = ev.get("telegram_id")
        if kind == "broadcast" and chat and data.get("media"):
            time.sleep(0.05)
            return self.send_broadcast(chat, data)
        if not chat or not text:
            return
        if kind == "broadcast":
            # One of many: a pace Telegram accepts from one bot.
            time.sleep(0.05)
        markup = None
        if kind == "ticket.answered":
            markup = {"inline_keyboard": [[{"text": "✍️ جواب",
                                            "callback_data": "tkr:%d" % data["ticket_id"]}]]}
            return self.ticket_notice(chat, text, markup, data,
                                      "/users/%d/tickets/%d/messages/%%d/image"
                                      % (int(chat), data["ticket_id"]))
        elif kind in ("quota.warning", "quota.exhausted", "plan.expiring", "plan.expired"):
            markup = {"inline_keyboard": [[{"text": "🛒 تمدید", "callback_data": "plans"}]]}
        self.say(chat, text, markup)

    def send_broadcast(self, chat, data):
        """A broadcast with photos or videos - one on its own, several as an
        album - with the words under them, or after them when they are too
        long for a caption."""
        bid, kinds = data.get("broadcast_id"), data["media"]
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)[:10]
        words = data.get("text") or ""
        text = self.t(words)
        caption = text if len(text) <= CAPTION_MAX else None
        # The files' bytes, or once Telegram has them, Telegram's ids for them.
        have = self.media.get(bid)
        if have is None:
            try:
                have = [base64.b64decode(self.panel.call(
                    "GET", "/broadcasts/%d/media/%d" % (int(bid), n))["data"])
                    for n in range(len(kinds))]
            except Exception as e:
                log("broadcast #%s: its photos or videos could not be had (%s); the words "
                    "alone" % (bid, e))
                return self.say(chat, words) if words else None
            # Only the one going out now is kept.
            self.media = {bid: have}
        files = {"f%d" % n: blob for n, blob in enumerate(have) if isinstance(blob, bytes)}
        try:
            if len(kinds) == 1:
                kind = kinds[0]
                method = "sendPhoto" if kind == "photo" else "sendVideo"
                if files:
                    sent = [self.tg.upload(method, {kind: have[0]}, chat_id=chat,
                                           caption=caption)]
                else:
                    self.tg.call(method, chat_id=chat, caption=caption, **{kind: have[0]})
            else:
                album = [{"type": kind, "media": "attach://f%d" % n if "f%d" % n in files
                          else have[n]} for n, kind in enumerate(kinds)]
                if caption:
                    album[0]["caption"] = caption
                if files:
                    sent = self.tg.upload("sendMediaGroup", files, chat_id=chat, media=album)
                else:
                    self.tg.call("sendMediaGroup", chat_id=chat, media=album)
        except Exception as e:
            # Most often somebody who has blocked the bot.
            return log("broadcast #%s to %s: %s" % (bid, chat, e))
        if files:
            ids = [telegram_file_id(m, k) for m, k in zip(sent or [], kinds)]
            if len(ids) == len(kinds) and all(ids):
                self.media = {bid: ids}
        if caption is None and words:
            self.say(chat, words)


def telegram_file_id(message, kind):
    """The id Telegram keeps a sent photo or video under, to send it again
    without uploading it; a photo's is its largest size."""
    got = (message or {}).get(kind)
    got = got[-1] if isinstance(got, list) and got else got
    return got.get("file_id") if isinstance(got, dict) else None


# ---------------------------------------------------------- panel webhooks
def signed(secret, header, body, now=None):
    """The panel's signature: t=<time>,v1=HMAC-SHA256("<t>.<body>")."""
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        stamp = int(parts["t"])
    except (ValueError, KeyError):
        return False
    want = hmac.new(secret.encode(), ("%d." % stamp).encode() + body,
                    hashlib.sha256).hexdigest()
    return (hmac.compare_digest(want, parts.get("v1", ""))
            and abs((now or time.time()) - stamp) < 300)


def webhook_server(bot, work):
    host, _, port = bot.cfg["listen"].rpartition(":")

    class Hook(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, 1024 * 1024))
            if not signed(bot.cfg["secret"], self.headers.get("X-MasterDNS-Signature"), body):
                self.send_response(401)
                self.end_headers()
                return
            # Answer at once; Telegram calls happen after, on the worker.
            self.send_response(200)
            self.end_headers()
            try:
                work.put(json.loads(body))
            except ValueError:
                pass

        def log_message(self, *a):
            pass

    return http.server.ThreadingHTTPServer((host or "127.0.0.1", int(port)), Hook)


# How many customers are answered at once.
LANES = 8


def lane_of(update, lanes=LANES):
    """Which lane an update goes down: its chat's, always the same one."""
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat = (msg.get("chat") or {}).get("id") or \
        ((update.get("callback_query") or {}).get("from") or {}).get("id") or 0
    try:
        return abs(int(chat)) % lanes
    except (TypeError, ValueError):
        return 0


def start_lanes(handle, lanes=LANES):
    """Several customers at once, each in their own order: a chat's updates
    always go down the same lane, so a customer's photo and the text after it
    do not race, and one slow answer - a receipt being fetched - holds up
    nobody else's. Returns what an update is handed to."""
    queues = [queue.Queue() for _ in range(lanes)]

    def lane(q):
        while True:
            update = q.get()
            try:
                handle(update)
            except Exception:
                log("update failed:\n" + traceback.format_exc())

    for q in queues:
        threading.Thread(target=lane, args=(q,), daemon=True).start()
    return lambda update: queues[lane_of(update, lanes)].put(update)


def main():
    cfg = settings()
    if not cfg["secret"]:
        log("WEBHOOK_SECRET is empty: the panel's messages will be refused")
    bot = Bot(cfg)
    me = bot.tg.call("getMe")
    log("bot @%s up; panel messages on %s; %d admin(s)"
        % (me.get("username"), cfg["listen"], len(cfg["admins"])))
    work = queue.Queue()

    def events():
        while True:
            ev = work.get()
            try:
                bot.on_event(ev)
            except Exception:
                log("event failed:\n" + traceback.format_exc())

    threading.Thread(target=events, daemon=True).start()
    server = webhook_server(bot, work)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    hand = start_lanes(bot.handle)

    offset = None
    while True:
        try:
            # Long polling: Telegram holds the request up to 25 seconds. Short
            # enough that a connection that went dead on the way is noticed
            # and opened again in well under a minute.
            updates = bot.tg.call("getUpdates", http_timeout=40, timeout=25, offset=offset,
                                  allowed_updates=["message", "callback_query"])
        except Exception as e:
            log("getUpdates: %s" % e)
            # A dead connection is simply tried again; anything else - a
            # refusal, no network - is given a moment.
            time.sleep(1 if "timed out" in str(e) else 5)
            continue
        for u in updates or []:
            offset = u["update_id"] + 1
            hand(u)


if __name__ == "__main__":
    main()
