#!/usr/bin/env python3
"""English: the admin panel, the customer's page and the bot.

What has to hold: every Persian phrase the code can show has its English in
domains/i18n-en.json, so nothing new slips through untranslated; a phrase is
only swapped whole, never the front of a longer Persian word; a page turns
English, left to right, only for a browser that pressed EN, and Persian for
everyone else; the bot speaks English only when the admin set it so, and
still understands its own buttons.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DICT = os.path.join(ROOT, "domains", "i18n-en.json")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


extract = load("tools/i18n-extract.py", "extract")
admin = load("templates/smartdns-admin", "admin")
sync = load("templates/smartdns-sync", "sync")
botmod = load("examples/telegram-bot/bot.py", "bot")
for m in (admin, sync, botmod):
    m.I18N_FILE = DICT
    m.I18N.clear()

print("the translation file")
found = extract.all_phrases()
en = json.load(open(DICT, encoding="utf-8"))
missing = [p for p in found if p not in en]
check("every Persian phrase the code can show has its English", not missing,
      "%d without: %s" % (len(missing), missing[:5]))
check("  and every phrase in it is still used", not [k for k in en if k not in found],
      str([k for k in en if k not in found][:5]))
check("no English has markup in it", not [v for v in en.values() if re.search(r"[<>&]", v)])
check("the installer carries it to every server",
      '("I18N_EN", "domains/i18n-en.json")' in open(os.path.join(HERE, "build-installer.py"),
                                                   encoding="utf-8").read()
      and "payload I18N_EN" in open(os.path.join(HERE, "installer-logic.sh"),
                                    encoding="utf-8").read())

print("swapping phrases")
t = admin.to_english
check("a phrase becomes its English", t("ذخیره شد") == "Saved")
check("  whole, next to Persian punctuation", t("از این سرور جواب نداد؛ چیزی عوض نشد")
      == "did not answer from this server; nothing changed")
check("  never the front of a longer Persian word",
      "Full" not in t("کاملاً") and t("کاملاً") == "کاملاً")
check("  and what nobody translated - a name - left as it was, its quotes English",
      t("«مهدی»") == "“مهدی”")
check("an apostrophe cannot break the page: the English is typographic",
      "'" not in "".join(v for bucket in admin.english_index().values() for _, v in bucket))
check("with no file, nothing changes", (lambda m: (m.I18N.clear(), setattr(m, "I18N_FILE",
      "/nonexistent"), m.to_english("ذخیره شد"))[2])(sync) == "ذخیره شد")
sync.I18N_FILE = DICT
sync.I18N.clear()

print("the pages")
page = admin.login_page({"ADMIN_PATH": "p"})
check("Persian for everybody by default", 'dir="rtl"' in page and "ورود" in page)
check("  with a button to English", "class='theme lang'" in page and "lang='+" in page)
english = admin.english_page(page)
check("English, left to right, for a browser that pressed it",
      'lang="en" dir="ltr"' in english and "Sign in" in english and "ورود" not in english)
check("the choice is the browser's, by a cookie", admin.wants_english({"Cookie": "lang=en"})
      and not admin.wants_english({"Cookie": "lang=fa"}) and not admin.wants_english({}))


class Rec:
    def __init__(self, cookie=""):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.headers = {"Cookie": cookie} if cookie else {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


Rec.send = admin.Admin.send
r = Rec("lang=en")
r.send(admin.login_page({"ADMIN_PATH": "p"}))
check("the admin panel sends it in English to that browser",
      b'dir="ltr"' in r.written and "Sign in".encode() in r.written)
r = Rec()
r.send(admin.login_page({"ADMIN_PATH": "p"}))
check("  and in Persian to the rest", b'dir="rtl"' in r.written)
Rec.send_html = sync.UserPanel.send_html
r = Rec("lang=en")
r.send_html(sync.landing())
check("the customer's page too", b'dir="ltr"' in r.written and b"Sign up" in r.written)
check("both carry the same buttons", admin.THEME_BUTTON == sync.THEME_BUTTON)

print("the bot")


class FakeTelegram:
    def __init__(self):
        self.out = []

    def send(self, chat, text, markup=None):
        self.out.append((text, markup))

    def call(self, *a, **k):
        return True


cfg = botmod.settings({"BOT_TOKEN": "t", "API_URL": "http://x", "API_KEY": "k",
                       "BOT_LANG": "en"})
check("the admin's choice reaches the bot", cfg["lang"] == "en"
      and botmod.settings({"BOT_TOKEN": "t", "API_URL": "x", "API_KEY": "k"})["lang"] == "fa")
tg = FakeTelegram()
bot = botmod.Bot(cfg, telegram=tg)
bot.say(1, "لغو شد.", botmod.MENU)
text, markup = tg.out[-1]
check("it speaks English, its keyboard too", text == "Cancelled."
      and "📊 My account" in sum(markup["keyboard"], []))
seen = []
bot.show_wallet = lambda chat, sender: seen.append("wallet")
bot.ready = lambda chat, sender: True
bot.on_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 1},
                "text": botmod.to_english(botmod.B_WALLET)})
check("  and knows its English buttons for what they are", seen == ["wallet"])
fa = botmod.Bot(dict(cfg, lang="fa"), telegram=tg)
fa.say(1, "لغو شد.")
check("in Persian it is left alone", tg.out[-1][0] == "لغو شد.")
check("the admin panel's bot page has the choice",
      "name='lang'" in open(os.path.join(ROOT, "templates", "smartdns-admin"),
                            encoding="utf-8").read()
      and '"BOT_LANG": "en" if one("lang") == "en" else "fa"' in open(
          os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read())

print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
