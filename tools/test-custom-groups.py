#!/usr/bin/env python3
"""Logos beside the services, and the operator's own domains in groups.

What has to hold: each row of a template's page is drawn with its service's
logo from Simple Icons - the ones since taken out there too - and a row no
brand is with a plain icon for its kind or its section, so no row is left
without; once per page, as a sprite; a brand colour that would vanish into a
theme is left to the text's, and a plain icon that is more than shapes is
not drawn. The operator can make a group with a name and an icon (an SVG up
to 16 KB with nothing active in it, or a PNG up to 64 KB and 512x512), put
domains in it, move one to another group by adding it again, rename it and
change or drop its icon, and delete it with every domain in it. Each group is
a row of its own on a template's page, under "my groups", ticked and picked
domain by domain like any service; a relay is told a template's own domains
as its ungrouped ones while it routes them plus its ticked groups', less any
switched off, and the default template's as all of them. Saving the rows
keeps the foot's picks for the ungrouped ones, and the foot keeps the rows'.
A new template takes the groups too; a seller may not make them.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import urllib.parse

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


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
admin.log = panel.log = lambda *a: None

ICONS = json.load(open(os.path.join(ROOT, "domains", "icons.json"), encoding="utf-8"))
SERVICES = json.load(open(os.path.join(ROOT, "domains", "services.json"), encoding="utf-8"))

print("the logos file")
keys = {s["key"] for s in SERVICES["services"]}
refs = {"%s/%s" % (s["key"], g["key"]) for s in SERVICES["services"] for g in s["groups"]}
check("every row it names is in the catalogue",
      all(k in keys or k in refs for k in ICONS["rows"]),
      str([k for k in ICONS["rows"] if k not in keys and k not in refs]))
check("every icon it points at is in it",
      set(ICONS["rows"].values()) | set(ICONS["sections"].values()) <= set(ICONS["icons"]))
check("every section it names is one",
      set(ICONS["sections"]) <= {x["key"] for x in SERVICES["sections"]})
brands = {k: i for k, i in ICONS["icons"].items() if not i.get("line")}
check("each logo is one plain path and a colour",
      all(re.fullmatch(r"[0-9A-Za-z.,\- ]+", i["path"]) and re.fullmatch(r"[0-9A-F]{6}", i["hex"])
          for i in brands.values()))
check("each plain icon is shapes only",
      all(admin.LINE_ICON.fullmatch(i["line"]) for i in ICONS["icons"].values() if i.get("line")))
check("the ones taken out of Simple Icons are there too",
      {"xbox", "nintendoswitch", "openai", "microsoft", "microsoftazure", "adobe"} <= set(brands))
check("it says where the icons come from", "Simple Icons" in ICONS["source"]
      and "CC0" in ICONS["source"] and "Lucide" in ICONS["source"])
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
build = open(os.path.join(HERE, "build-installer.py"), encoding="utf-8").read()
check("the installer carries it beside the catalogue",
      '("ICONS", "domains/icons.json")' in build
      and "payload ICONS > /usr/local/share/smart-dns/icons.json" in logic
      and admin.ICONS_FILE == "/usr/local/share/smart-dns/icons.json")

print("a brand's colour")
check("black is left to the text's colour, on the dark theme", admin.logo_ink("000000") is None)
check("  and white, on the light one", admin.logo_ink("FFFFFF") is None)
check("PlayStation's blue is kept", admin.logo_ink("0070D1") == "#0070D1")

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.SERVICES_FILE = os.path.join(ROOT, "domains", "services.json")
admin.GAMES_FILE = os.path.join(ROOT, "domains", "games.json")
admin.ICONS_FILE = os.path.join(ROOT, "domains", "icons.json")
admin.CATALOGUE = admin.load_catalogue()
admin.ICONS.update(admin.load_icons())
admin.GAMES[:] = admin.load_games()
admin.SECTIONS[:] = admin.load_sections()
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('VIP', 0, ?)",
          (panel.now(),))
DEFAULT, VIP = 1, 2


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/domains"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "domains", "template_editor"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    admin.REQ.admin = None
    r = Rec()
    r.action(rest, {k: (v if isinstance(v, list) else [v]) for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def act_upload(rest, fields, icon=None):
    """A multipart form, as the browser sends the groups' forms."""
    admin.REQ.admin = None
    b = "----masterdns"
    body = b""
    for k, v in fields.items():
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (b, k, v)).encode("utf-8")
    body += ('--%s\r\nContent-Disposition: form-data; name="icon"; filename="i"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n' % b).encode()
    body += (icon or b"") + b"\r\n" + ("--%s--\r\n" % b).encode()
    r = Rec()
    r.raw_body = body
    r.headers = {"Content-Type": "multipart/form-data; boundary=%s" % b}
    r.action(rest, {})
    return urllib.parse.unquote(r.sent.get("Location", ""))


def png(w=16, h=16, pad=0):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h)
            + b"\x08\x06\x00\x00\x00" + b"\x00" * (4 + pad))


SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
       b'<circle cx="12" cy="12" r="10"/></svg>')


def template(tid):
    return store.one("SELECT * FROM templates WHERE id = ?", (tid,))


def page(tid):
    return Rec().template_editor(template(tid))


print("the logos on a template's page")
html = page(VIP)
check("Steam's row carries Steam's logo", "<use href='#si-steam'/>" in html)
check("  PlayStation's in its own blue", "style='color:#0070D1'><use href='#si-playstation'/>" in html)
check("each logo drawn once, as a sprite",
      html.count("<symbol id='si-steam'") == 1 and html.count("<use href='#si-steam'/>") >= 2)
check("  only the ones the page uses",
      set(re.findall(r"<symbol id='si-([a-z0-9-]+)'", html))
      == set(re.findall(r"<use href='#si-([a-z0-9-]+)'/>", html)))
check("Xbox's, taken out of Simple Icons, is there all the same",
      re.search(r"<use href='#si-xbox'/></svg>Xbox", html) is not None)
check("a game no brand logo has gets the games' plain icon",
      re.search(r"<svg class='logo plain'[^>]*><use href='#si-gamepad-2'/></svg>ARC Raiders",
                html) is not None)
check("  drawn in lines", "<symbol id='si-gamepad-2' viewBox='0 0 24 24' fill='none' "
      "stroke='currentColor'" in html)
check("every row has an icon, none a letter", "logo letter" not in html.split("گروه‌های من")[-1]
      if "گروه‌های من" in html else "logo letter" not in html)
saved = dict(admin.ICONS["icons"]["gamepad-2"])
admin.ICONS["icons"]["gamepad-2"]["line"] = '<path d="M0 0"/><script>x()</script>'
check("a plain icon that is more than shapes is not drawn",
      "<script>" not in admin.logo_sprite({"gamepad-2"})
      and "si-gamepad-2" not in admin.logo_sprite({"gamepad-2"}))
admin.ICONS["icons"]["gamepad-2"] = saved

print("groups")
check("a group is made with a name and a PNG icon",
      "ساخته شد" in act_upload("group-add", {"name": "سایت‌های دانشگاه"}, png()))
g1 = store.one("SELECT * FROM custom_groups WHERE name = 'سایت‌های دانشگاه'")
check("  kept in the database, icon and all",
      g1 is not None and g1["icon_type"] == "image/png" and bytes(g1["icon"]) == png())
check("  or with an SVG", "ساخته شد" in act_upload("group-add", {"name": "بانک‌ها"}, SVG))
g2 = store.one("SELECT * FROM custom_groups WHERE name = 'بانک‌ها'")
check("  or with none", "ساخته شد" in act_upload("group-add", {"name": "بدون عکس"}))
g3 = store.one("SELECT * FROM custom_groups WHERE name = 'بدون عکس'")
check("  and an icon is optional", g3 is not None and g3["icon"] is None)
check("the same name twice is refused",
      "m=!" in act_upload("group-add", {"name": "بانک‌ها"}))
check("no name is refused", "m=!" in act_upload("group-add", {"name": "  "}))
check("a name over 40 is refused", "m=!" in act_upload("group-add", {"name": "x" * 41}))
check("an SVG with a script in it is refused",
      "m=!" in act_upload("group-add", {"name": "بد"},
                          b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'))
check("  and one with an onload",
      "m=!" in act_upload("group-add", {"name": "بد"},
                          b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>'))
check("an SVG over 16 KB is refused",
      "m=!" in act_upload("group-add", {"name": "بد"}, SVG[:-6] + b" " * 17000 + b"</svg>"))
check("a PNG over 64 KB is refused",
      "m=!" in act_upload("group-add", {"name": "بد"}, png(pad=70000)))
check("a PNG over 512x512 is refused",
      "m=!" in act_upload("group-add", {"name": "بد"}, png(600, 20)))
check("anything else is refused",
      "m=!" in act_upload("group-add", {"name": "بد"}, b"GIF89a....."))
check("  and none of those were made",
      store.one("SELECT COUNT(*) n FROM custom_groups")["n"] == 3)

print("domains in groups")
act("domain-add", domain="uni-a.example", group=str(g1["id"]))
act("domain-add", domain="uni-b.example", group=str(g1["id"]))
act("domain-add", domain="bank.example", group=str(g2["id"]))
act("domain-add", domain="loose.example")
gid = lambda d: store.one("SELECT group_id FROM custom_domains WHERE domain = ?", (d,))["group_id"]
check("a domain goes into the group picked", gid("uni-a.example") == g1["id"])
check("  or into none", gid("loose.example") is None)
check("added again with another group, it moves there",
      "رفت" in act("domain-add", domain="bank.example", group=str(g3["id"]))
      and gid("bank.example") == g3["id"])
act("domain-add", domain="bank.example", group=str(g2["id"]))
check("added again with the same, it is already there",
      "m=!" in act("domain-add", domain="bank.example", group=str(g2["id"])))
check("a group that is not there is refused",
      "m=!" in act("domain-add", domain="x.example", group="999"))
dom = Rec().domains()
check("the domains page has the groups, with their icons",
      "گروه‌های من" in dom and "data:image/png;base64," in dom
      and "data:image/svg+xml;base64," in dom and "action='/p/group-add'" in dom)
check("  a group to pick when adding", "<select name='group'>" in dom
      and "<option value='%d'>بانک‌ها</option>" % g2["id"] in dom)
check("  and each domain's group", re.search(r"uni-a\.example</code></td><td><img class='logo'",
                                            dom) is not None)

print("each group, a row of a template")
html = page(VIP)
check("under «my groups», first",
      "<h3 class='sec'>گروه‌های من</h3>" in html
      and html.index("گروه‌های من") < html.index("#si-playstation"))
check("with its own icon", "<img class='logo' src='data:image/png;base64," in html)
check("  and its letter where it has none",
      "<span class='logo letter' aria-hidden='true'>ب</span>بدون عکس" in html)
check("a tick for the group, and its domains in the drawer",
      "value='cg%d.main'" % g1["id"] in html and "uni-a.example" in html)
check("the domains in no group stay at the foot",
      "name='c' value='loose.example'" in html and "name='c' value='uni-a.example'" not in html)

act("template-save", id=str(VIP), g=["cg%d.main" % g1["id"], "steam.main"], d=["uni-a.example"])
check("ticked and saved like a service",
      ("cg%d" % g1["id"], "main") in store.template_groups(VIP))
check("  a domain un-ticked inside it is off",
      "uni-b.example" in store.template_domains_off(VIP))
act("template-rules", id=str(VIP), c=["loose.example"])
check("a relay is told the ticked group's picks and the ungrouped ones",
      store.custom_for(VIP) == ["loose.example", "uni-a.example"])
act("template-save", id=str(VIP), g=["cg%d.main" % g1["id"]], d=["uni-a.example"])
check("  saving the rows keeps the foot's", "loose.example" in store.custom_for(VIP))
act("template-rules", id=str(VIP), c=[])
check("  and the foot keeps the rows'",
      store.custom_for(VIP) == ["uni-a.example"]
      and "uni-b.example" in store.template_domains_off(VIP))
check("a group not ticked is not told",
      "bank.example" not in store.custom_for(VIP))
check("the default template's are all of them",
      store.custom_for(DEFAULT) == ["bank.example", "loose.example", "uni-a.example",
                                    "uni-b.example"])
store.run("INSERT INTO users (username, created_at, status, template_id)"
          " VALUES ('alice', ?, 'active', ?)", (panel.now(), VIP))
uid = store.one("SELECT id FROM users WHERE username = 'alice'")["id"]
store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, '192.0.2.10', ?)",
          (uid, panel.now()))
_, profiles = store.profiles(panel.load_catalogue() if hasattr(panel, "load_catalogue")
                             else [], DEFAULT)
check("the relay's profile for the template says so",
      profiles.get(str(VIP), {}).get("custom") == ["uni-a.example"])

act("template-new", name="تازه")
new = store.one("SELECT id FROM templates WHERE name = 'تازه'")["id"]
check("a new template takes the groups too",
      ("cg%d" % g1["id"], "main") in store.template_groups(new))

print("changing and deleting a group")
act_upload("group-save", {"id": str(g1["id"]), "name": "دانشگاه"})
row = store.one("SELECT * FROM custom_groups WHERE id = ?", (g1["id"],))
check("renamed, its icon kept", row["name"] == "دانشگاه" and row["icon"] is not None)
act_upload("group-save", {"id": str(g1["id"]), "name": "دانشگاه"}, SVG)
check("  its icon changed", store.one("SELECT icon_type FROM custom_groups WHERE id = ?",
                                      (g1["id"],))["icon_type"] == "image/svg+xml")
act_upload("group-save", {"id": str(g1["id"]), "name": "دانشگاه", "drop": "1"})
check("  or dropped", store.one("SELECT icon FROM custom_groups WHERE id = ?",
                                (g1["id"],))["icon"] is None)
check("  a name another has is refused",
      "m=!" in act_upload("group-save", {"id": str(g1["id"]), "name": "بانک‌ها"}))
msg = act("group-del", id=str(g1["id"]))
check("deleted, with every domain in it", "2" in msg and not store.q(
    "SELECT 1 FROM custom_domains WHERE domain LIKE 'uni-%'"))
check("  and what the templates kept of either",
      not store.q("SELECT 1 FROM template_services WHERE service_key = ?", ("cg%d" % g1["id"],))
      and "uni-b.example" not in store.template_domains_off(VIP))
check("  so no relay is told them", store.custom_for(VIP) == []
      and "uni-a.example" not in store.custom_for(DEFAULT))
check("  and the other groups' are still there", gid("bank.example") == g2["id"])

print("sellers")
check("a seller may not make, change or delete groups",
      {"group-add", "group-save", "group-del"} <= admin.RESELLER_NEVER
      and all(admin.ACTION_SECTION.get(a) == "templates"
              for a in ("group-add", "group-save", "group-del"))
      if hasattr(admin, "ACTION_SECTION") else
      {"group-add", "group-save", "group-del"} <= admin.RESELLER_NEVER)

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
