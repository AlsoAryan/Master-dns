#!/usr/bin/env python3
"""The ready block lists: ads and porn, closed per template.

What has to hold: the panel fetches a list only while it is on somewhere,
cleans it - the names under another name in it, and anything the service
itself needs or routes, taken out - and rebuilds it when those change; a
failed fetch is tried again later, not every minute; a template gets a list's
hash only for the templates the admin picked, and only once the list is
built; a relay fetches a list once, by that hash, never takes one that does
not match it, and reads it into that template's resolver alone.
"""
import contextlib
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse

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
sync = load("templates/smartdns-sync", "sync")
for m in (panel, admin, sync):
    m.log = lambda *a: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
panel.BLOCKLIST_DIR = os.path.join(tmp, "lists")
with open(os.path.join(HERE, "..", "domains", "blocks.json"), encoding="utf-8") as fh:
    _cat = json.load(fh)
admin.BLOCK_SECTIONS[:], admin.BLOCKS[:] = _cat["sections"], _cat["blocks"]
panel.BLOCKS[:] = _cat["blocks"]
panel.CATALOGUE = [{"key": "psn", "groups": [{"key": "main",
                                              "domains": ["playstation.net", "ads.game.com"]}]}]
for name in ("کامل", "بازی"):
    store.run("INSERT INTO templates (name, is_default, created_at) VALUES (?, ?, ?)",
              (name, 1 if name == "کامل" else 0, panel.now()))
FULL, GAME = 1, 2
store.set_setting("customer_panel_url", "https://users.example.net:8443/")


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.written = b""
        self.wfile = self
        self.path = "/p/templates"
        self.headers = {}

    def write(self, b):
        self.written += b

    def send_response(self, code):
        self.sent["code"] = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send", "domains", "templates", "template_list",
             "template_editor"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)


def act(rest, **form):
    r = Rec()
    r.action(rest, {k: v if isinstance(v, list) else [v] for k, v in form.items()})
    return urllib.parse.unquote(r.sent.get("Location", ""))


HOSTS = """# StevenBlack-style hosts
127.0.0.1 localhost
0.0.0.0 0.0.0.0
0.0.0.0 doubleclick.net
0.0.0.0 ad.doubleclick.net   # under the one above
0.0.0.0 tracker.example.org
0.0.0.0 ads.game.com
0.0.0.0 game.com
0.0.0.0 example.net
0.0.0.0 1.2.3.4
plainlist.example
0.0.0.0 bad_name!.com
""" + "".join("0.0.0.0 ad%d.adnet.example\n" % i for i in range(120))

print("reading a list")
names = panel.parse_hosts(HOSTS)
check("the names, without localhost, addresses or what is not a name",
      "doubleclick.net" in names and "plainlist.example" in names
      and "localhost" not in names and "1.2.3.4" not in names and "0.0.0.0" not in names
      and not any("!" in n for n in names))
clean = panel.clean_blocklist(names, {"ads.game.com", "users.example.net", "playstation.net"})
check("cleaned: a name under another one in the list goes, the parent covers it",
      "doubleclick.net" in clean and "ad.doubleclick.net" not in clean)
check("  and what the service needs stays open - the name itself and its parents",
      "ads.game.com" not in clean and "game.com" not in clean and "example.net" not in clean
      and "tracker.example.org" in clean)

print("the admin panel")
tpl = lambda i: admin.STORE.one("SELECT * FROM templates WHERE id = ?", (i,))
check("not on the templates list, nor on the domains page",
      "template-blocklists" not in Rec().template_list()
      and "template-blocklists" not in Rec().domains()
      and "مسدودی‌های دستی" in Rec().domains())
page = Rec().template_editor(tpl(GAME))
check("at the foot of a template's own page, both lists, off",
      "مسدودی‌ها" in page and "name='l' value='ads'>" in page
      and "name='l' value='porn'>" in page
      and page.index("template-blocklists") > page.index("DNS جداگانهٔ این قالب"))
check("  the default template's page too",
      "template-blocklists" in Rec().template_editor(tpl(FULL)))
act("template-blocklists", id=str(FULL), l=["ads"])
act("template-blocklists", id=str(GAME), l=["ads", "porn"])
check("ticked there, on for that template",
      panel.blocklist_scope(store, "ads") == ("some", [FULL, GAME])
      and panel.blocklist_scope(store, "porn") == ("some", [GAME]))
check("a template that is not one is refused", "!" in act("template-blocklists", id="99",
                                                          l=["ads"]))
check("  while it is being fetched, the page says so",
      "در حال دریافت" in Rec().template_editor(tpl(GAME)))
check("nothing is sent to the relays before a list is built",
      store.template_rules(FULL)["lists"] == {})

print("the panel fetches and builds")
fetched = []


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout=0):
    fetched.append(req.full_url)
    return Resp((HOSTS if "porn" not in req.full_url
                 else HOSTS.replace("adnet", "pornnet")).encode())


panel.refresh_blocklists(store, opener)
meta = panel.blocklist_meta(store, "ads")
check("each list on somewhere is fetched from its authors",
      any(u.endswith("/hosts") and "porn" not in u for u in fetched)
      and any("sinfonietta" in u for u in fetched) and any("clefspeare13" in u for u in fetched))
with open(os.path.join(panel.BLOCKLIST_DIR, "ads.conf.gz"), "rb") as fh:
    text = gzip.decompress(fh.read())
check("built as dnsmasq rules, its hash and size noted",
      meta["sha"] == hashlib.sha256(text).hexdigest() and b"address=/doubleclick.net/\n" in text
      and meta["count"] == text.count(b"\n") and b"ads.game.com" not in text)
check("a template gets the lists on for it, by hash",
      store.template_rules(FULL)["lists"] == {"ads": meta["sha"]}
      and set(store.template_rules(GAME)["lists"]) == {"ads", "porn"})
check("  and the page says how many and when",
      format(meta["count"], ",") in Rec().template_editor(tpl(GAME)))
act("template-blocklists", id=str(FULL), l=["ads", "porn"])
check("ticking porn on another template adds it",
      panel.blocklist_scope(store, "porn") == ("some", [FULL, GAME]))
store.set_setting("blocklist:ads", json.dumps({"mode": "all", "templates": []}))
act("template-blocklists", id=str(FULL), l=["porn"])
check("un-ticking a list on for every template keeps it for the others",
      panel.blocklist_scope(store, "ads") == ("some", [GAME]))
act("template-blocklists", id=str(GAME), l=["porn"])
check("  and the last one un-ticked turns it off", panel.blocklist_scope(store, "ads") is None)
act("template-blocklists", id=str(FULL), l=["ads"])
act("template-blocklists", id=str(GAME), l=["ads", "porn"])
fetched.clear()
panel.refresh_blocklists(store, opener)
check("not fetched again within the week", not fetched)
panel.refresh_blocklists(store, opener, stamp=time.time() + 8 * 86400)
check("  and fetched again after it", fetched)
store.run("INSERT INTO custom_domains (domain, added_at) VALUES ('tracker.example.org', ?)",
          (panel.now(),))
panel.refresh_blocklists(store, opener)
new = panel.blocklist_meta(store, "ads")
check("a domain the admin routes since is taken out: the list is rebuilt, a new hash",
      new["sha"] != meta["sha"] and "tracker.example.org" not in panel.blocklist_names("ads"))
check("the DNS report says why such a name did not answer",
      panel.blocklist_names("ads") and "doubleclick.net" in panel.blocklist_names("ads"))

print("a failed fetch")
act("template-blocklists", id=str(GAME), l=["ads"])
check("off, a template no longer gets it", "porn" not in store.template_rules(GAME)["lists"])
os.remove(os.path.join(panel.BLOCKLIST_DIR, "ads.raw.gz"))
fetched.clear()


def broken(req, timeout=0):
    fetched.append(req.full_url)
    raise OSError("unreachable")


panel.refresh_blocklists(store, broken)
check("is noted, and the list already built stays in use",
      "unreachable" in panel.blocklist_meta(store, "ads")["error"]
      and store.template_rules(FULL)["lists"] == {"ads": new["sha"]})
fetched.clear()
panel.refresh_blocklists(store, broken)
check("  and not tried again for an hour", not fetched)

print("the relay")
d_main, d_base, d_prof = (os.path.join(tmp, x) for x in ("main", "base", "prof"))
for d in (d_main, d_base, d_prof):
    os.makedirs(d)
open(os.path.join(d_main, "smart-dns.conf"), "w").write("no-resolv\n")
open(os.path.join(d_main, "bypass.conf"), "w").write("")
sync.DNSMASQ_D, sync.BASE_DIR, sync.PROFILE_DIR = d_main, d_base, d_prof
sync.HIJACK_CONF = os.path.join(d_main, "smart-dns.conf")
sync.BYPASS_CONF = os.path.join(d_main, "bypass.conf")
sync.EPIC_PINS = os.path.join(d_main, "none.conf")
sync.LIST_DIR = os.path.join(tmp, "relay-lists")
sync.CFG = {"SELF_IP": "198.51.100.1"}
sync.sync_base_dir = lambda: False


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


sync.sh = lambda *a: R()
sync.nft = lambda *a: R()
served = []
with open(os.path.join(panel.BLOCKLIST_DIR, "ads.conf.gz"), "rb") as fh:
    blob = fh.read()
sync.fetch = lambda path: (served.append(path), (blob, {}))[1]
sha = panel.blocklist_meta(store, "ads")["sha"]
quiet = contextlib.redirect_stdout(io.StringIO())
with quiet:
    sync.apply_profiles({"2": {"routed": [], "lists": {"ads": sha}}, "3": {"routed": []}}, {})
two = open(os.path.join(d_prof, "2.conf")).read()
check("a template with the list reads it from its own file",
      "conf-file=%s" % os.path.join(sync.LIST_DIR, "ads.conf") in two
      and "conf-file" not in open(os.path.join(d_prof, "3.conf")).read())
check("  fetched once, by its hash", served == ["/blocklist/ads"]
      and hashlib.sha256(open(os.path.join(sync.LIST_DIR, "ads.conf"), "rb").read())
      .hexdigest() == sha)
with quiet:
    sync.apply_profiles({"2": {"routed": [], "lists": {"ads": sha}}}, {})
check("  and not again", served == ["/blocklist/ads"])
served.clear()
sync.LIST_TRIED.clear()
with quiet:
    sync.apply_profiles({"2": {"routed": [], "lists": {"ads": "0" * 64}}}, {})
check("a list that is not the one named is not taken",
      served == ["/blocklist/ads"] and "conf-file" not in open(os.path.join(d_prof, "2.conf")).read())
served.clear()
with quiet:
    sync.apply_profiles({"2": {"routed": [], "lists": {"ads": "0" * 64}}}, {})
check("  nor asked for again straight away", not served)
check("the default template gets a resolver of its own for a list",
      sync.default_profile({"blocked": [], "forwards": {}, "lists": {"ads": sha}}, [])
      is not None)
check("the change is logged by name",
      "block list ads on" in sync.describe_change("x\n", "# block list ads %s\n" % sha[:16], "")
      and "block list ads off" in sync.describe_change("# block list ads %s\n" % sha[:16], "",
                                                        ""))

print("the rows, like the services'")
cat = json.load(open(os.path.join(HERE, "..", "domains", "blocks.json"), encoding="utf-8"))
keys = [b["key"] for b in cat["blocks"]]
secs = {x["key"] for x in cat["sections"]}
alld = [d for b in cat["blocks"] for d in b["domains"]]
check("the catalogue: ad networks and adult sites, each with its domains",
      {"ads", "porn"} <= secs and len(keys) == len(set(keys))
      and all(b["section"] in secs and b["domains"] for b in cat["blocks"])
      and len(alld) == len(set(alld)) and all(sync.is_domain(d) for d in alld))
check("  and among the ads, the Iranian networks",
      any("tapsell.ir" in b["domains"] for b in cat["blocks"] if b["section"] == "ads"))
admin.BLOCK_SECTIONS[:], admin.BLOCKS[:] = cat["sections"], cat["blocks"]
panel.BLOCKS[:] = cat["blocks"]
page = Rec().template_editor(tpl(GAME))
check("a template's page draws them like its services: a row, a drawer of domains",
      "name='bg' value='pornhub'>" in page and "name='bd' value='phncdn.com'>" in page
      and "<details class='svc blk'" in page and "📢 تبلیغات" in page and "🔞 پورن" in page)
check("  each kind with its full list under it", page.index("value='porn'") >
      page.index("value='hentai'") and "فهرست کامل" in page)
ph = next(b for b in cat["blocks"] if b["key"] == "pornhub")["domains"]
act("template-blocklists", id=str(GAME), bg=["pornhub", "tapsell"],
    bd=[d for d in ph if d != "phncdn.com"], l=["ads"])
check("a row ticked with a domain taken out of it is kept as such, unknown rows dropped",
      json.loads(store.setting("blocks:%d" % GAME)) == {"on": ["pornhub"], "off": ["phncdn.com"]})
rules = store.template_rules(GAME)["blocked"]
check("  and those domains are the template's blocks, the one taken out not",
      "pornhub.com" in rules and "pornhub.org" in rules and "phncdn.com" not in rules
      and "pornhub.com" not in store.template_rules(FULL)["blocked"])
page = Rec().template_editor(tpl(GAME))
check("  the drawer opens on it, with its count",
      "<details class='svc blk' open><summary><label><input type='checkbox' name='bg' "
      "value='pornhub' checked>" in page and "%d از %d دامنه" % (len(ph) - 1, len(ph)) in page)
check("  the full list left as it was", "ads" in store.template_rules(GAME)["lists"])
act("template-blocklists", id=str(GAME), l=["ads"])
check("nothing ticked, nothing kept", not store.setting("blocks:%d" % GAME)
      and "pornhub.com" not in store.template_rules(GAME)["blocked"])
check("the services' script leaves the block rows alone",
      "details.svc:not(.blk)" in open(os.path.join(HERE, "..", "templates", "smartdns-admin"),
                                      encoding="utf-8").read())
check("the installer carries the catalogue",
      '("BLOCKS", "domains/blocks.json")' in open(os.path.join(HERE, "build-installer.py"),
                                                  encoding="utf-8").read()
      and "payload BLOCKS > /usr/local/share/smart-dns/blocks.json" in open(
          os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read())

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
