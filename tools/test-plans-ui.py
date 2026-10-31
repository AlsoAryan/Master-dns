#!/usr/bin/env python3
"""
0.1.0: the plans and templates pages, as cards - what they show, the dialogs that edit,
and copying a template.
"""
import base64
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []
GB = 1024 ** 3


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(HERE, "..", "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("smartdns-panel", "panel")
admin = load("smartdns-admin", "admin")
sync = load("smartdns-sync", "sync")

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
admin.log = lambda *a: None

store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('بازی', 0, ?)",
          (panel.now(),))
FULL, GAME = 1, 2
store.run("INSERT INTO users (username, first_name, created_at, status)"
          " VALUES ('ali', 'علی', ?, 'pending')", (panel.now(),))
session = store.open_session(1)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "receipts", "plans",
             "users"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


class Api:
    relays = ("198.51.100.1",)

    def __init__(self, store):
        self.store = store


for name in ("do_user_receipt", "do_user_info", "_session_user", "_must_choose"):
    setattr(Api, name, getattr(panel.API, name))
api = Api(store)


def pay(plan_id, **extra):
    body = {"session": session, "content_type": "image/png",
            "data": base64.b64encode(PNG).decode(), "plan_id": plan_id}
    body.update(extra)
    return api.do_user_receipt(body)


def user():
    return store.one("SELECT * FROM users WHERE id = 1")


print("before there are plans, a receipt is what it always was")
check("accepted with no plan", pay(0).get("ok"))
check("the admin page still says to set it by hand",
      "حجم و زمان" in Rec().receipts())
store.run("DELETE FROM transactions")

print("making a plan")
where = act("plan-save", name="گیمینگ ماهانه", template_id=str(GAME), days="30",
            quota_gb="", price="200000")
check("an empty allowance is refused, not taken as unlimited", "m=!" in where, where)
check("and nothing was made", not store.one("SELECT 1 FROM plans"))
where = act("plan-save", name="گیمینگ ماهانه", template_id=str(GAME), days="30",
            quota_gb="0", price="200000")
check("so is zero", "m=!" in where, where)
where = act("plan-save", name="x", template_id="99", days="30", quota_gb="5",
            price="1")
check("a template that is not there is refused", "m=!" in where, where)
where = act("plan-save", name="x", template_id=str(GAME), days="2.5",
            quota_gb="5", price="1")
check("half a day is refused", "m=!" in where, where)
where = act("plan-save", name="گیمینگ ماهانه", template_id=str(GAME), days="۳۰",
            quota_gb="۱۰۰", price="۲۰۰,۰۰۰", speed_mb="20", note="فقط بازی‌ها")
check("Persian digits and a thousands comma are understood", "m=!" not in where, where)
game = store.one("SELECT * FROM plans WHERE name = 'گیمینگ ماهانه'")
check("the plan holds what was typed",
      game and (game["template_id"], game["days"], game["quota_bytes"],
                game["price"], game["speed_kbps"]) == (GAME, 30, 100 * GB, 200000,
                                                       20000),
      str(dict(game)) if game else "none")
act("plan-save", name="کامل ماهانه", template_id=str(FULL), days="30",
    quota_gb="", unlimited="1", price="500000")
full = store.one("SELECT * FROM plans WHERE name = 'کامل ماهانه'")
check("unlimited only with its own tick", full and full["quota_bytes"] == 0)

page = None

print("the plans page, as cards")
page = Rec().plans()
check("each plan is a card with what it gives and what it costs",
      page.count("<article class='plan") == 2 and "200,000" in page and "500,000" in page
      and "100 گیگ" in page and "نامحدود" in page and "20 Mb/s" in page, page[:200])
check("with its own editor, a dialog, and a way to open it",
      page.count("<dialog class='pd'") == 3 and "data-dlg='pd%d'" % game["id"] in page
      and "id='pdnew'" in page)
check("the editor's fields are the ones the form has always posted",
      all("name='%s'" % n in page for n in ("name", "template_id", "days", "quota_gb", "unlimited",
                                            "price", "trial", "speed_mb", "devices", "note")))
check("an editor carries the plan's own values",
      "value='گیمینگ ماهانه'" in page and "name='quota_gb' value='100'" in page
      and "name='price' value='200000'" in page)
check("a card for a plan switched off says so, and offers to switch it on",
      (act("plan-active", id=str(full["id"]), to="0"), "خاموش</span>" in Rec().plans()
       and "روشن کردن" in Rec().plans())[1])
act("plan-active", id=str(full["id"]), to="1")
check("money has its commas", admin.money(1200000) == "1,200,000" and admin.money(None) == "0")
check("the page opens and closes dialogs itself, and without its script still reads",
      "showModal()" in page and "data-close" in page)

print("templates, as cards")
act("template-new", name="کپی", **{"from": ""})
page = admin.Admin.template_list(Rec())
check("a template is a card with how much of the catalogue it routes",
      "<article class='tpl'>" in page and "class='meter'" in page and "templates?t=" in page)
check("the default one says what it is, with nothing to edit",
      "پیش‌فرض" in page and page.count("class='btn'") == page.count("<article class='tpl'>") - 1)
check("the new-template form can copy another", "name='from'" in page and "کپی از" in page)

print("copying a template")
src_id = GAME
store.run("INSERT OR IGNORE INTO template_domains_off (template_id, domain) VALUES (?, 'example.org')",
          (src_id,))
for row in (("steam", "store"), ("riot", "valorant")):
    store.run("INSERT OR IGNORE INTO template_services (template_id, service_key, group_key)"
              " VALUES (?, ?, ?)", (src_id,) + row)
before = {r["service_key"] + "/" + r["group_key"] for r in
          store.q("SELECT * FROM template_services WHERE template_id = ?", (src_id,))}
where = act("template-new", name="نسخهٔ بازی", **{"from": str(src_id)})
new = store.one("SELECT id FROM templates WHERE name = 'نسخهٔ بازی'")
check("the copy is made, and says from what", new and "m=" in where and "m=!" not in where, where)
after = {r["service_key"] + "/" + r["group_key"] for r in
         store.q("SELECT * FROM template_services WHERE template_id = ?", (new["id"],))}
check("it routes what the other does", len(before) == 2 and after == before, str((before, after)))
check("and leaves out what the other leaves out",
      store.one("SELECT 1 FROM template_domains_off WHERE template_id = ? AND domain = 'example.org'",
                (new["id"],)) is not None)
check("the other is as it was", {r["service_key"] + "/" + r["group_key"] for r in store.q(
    "SELECT * FROM template_services WHERE template_id = ?", (src_id,))} == before)
check("a copy of one that is not there is a fresh template",
      "m=" in act("template-new", name="نو", **{"from": "9999"}))
check("a name taken is refused", "m=!" in act("template-new", name="نسخهٔ بازی", **{"from": ""}))

editor = admin.Admin.template_editor(Rec(), store.one("SELECT * FROM templates WHERE id = ?", (new["id"],)))
check("the editor filters rows on and off, and keeps its save bar in view",
      "data-show='on'" in editor and "data-show='off'" in editor and "class='savebar'" in editor
      and "id='tcount'" in editor)
check("and the buttons that tick everything are not the ones that filter",
      "mass.dataset.allRows === undefined" in editor)

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
