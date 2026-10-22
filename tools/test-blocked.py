#!/usr/bin/env python3
"""The operator's blocks and forwards, per template.

What has to hold: a blocked domain and everything under it is answered "no
such name", and a forwarded one asked of the operator's resolver, by the
resolver of each template it is for and no other; every other rule of that
resolver for those names is left out, because in dnsmasq a rule for the same
name beats a block or a forward whatever it says; a forward under a block of
the same template is dropped - the block wins; the default template, whose
customers are otherwise answered from the installer's own files, gets a
stand-in resolver while it has any, DNS and DoH both, and goes back to :53
when it has none; the panel works out each template's list, a rule "for
every template" including those made later; the admin panel picks templates
when adding, shows them, and changes them from a template's own page.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:500]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (sync, panel, admin):
    m.log = lambda *a, **k: None
panel.print = lambda *a, **k: None

ME = "198.51.100.1"
tmp = tempfile.mkdtemp()
d_main, d_base, d_prof = (os.path.join(tmp, n) for n in ("dnsmasq.d", "base", "profiles"))
for d in (d_main, d_base, d_prof):
    os.makedirs(d)


def put(path, lines):
    with open(path, "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


put(os.path.join(d_main, "smart-dns.conf"),
    ["no-resolv", "server=1.1.1.1", "cache-size=10000",
     "address=/google.com/%s" % ME, "address=/spotify.com/%s" % ME,
     "address=/tiktok.com/tiktokv.com/%s" % ME])
put(os.path.join(d_main, "bypass.conf"), ["server=/gosredirector.ea.com/#"])
pins = os.path.join(d_main, "epic-pins.conf")
put(pins, ["address=/pin.epicgames.com/203.0.113.9"])
sync.DNSMASQ_D, sync.BASE_DIR, sync.PROFILE_DIR = d_main, d_base, d_prof
sync.HIJACK_CONF = os.path.join(d_main, "smart-dns.conf")
sync.BYPASS_CONF = os.path.join(d_main, "bypass.conf")
sync.CUSTOM_CONF = os.path.join(d_main, "50-smartdns-custom.conf")
sync.EPIC_PINS = pins
sync.CFG = {"SELF_IP": ME}
sync.sync_base_dir = lambda: False


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


calls, running = [], set()


def fake_sh(*args):
    calls.append(args)
    if args[:2] == ("systemctl", "is-active"):
        return R("active\n" if args[2] in running else "inactive\n")
    if args[:2] == ("systemctl", "restart"):
        running.add(args[2])
    return R()


sync.sh = fake_sh
quiet = contextlib.redirect_stdout(io.StringIO())


def conf(key):
    with open(os.path.join(d_prof, "%s.conf" % key)) as fh:
        return fh.read()


print("which names a rule covers")
B = {"tiktok.com", "ads.example"}
check("the domain itself and everything under it",
      sync.is_overridden("tiktok.com", B) and sync.is_overridden("v16.tiktok.com", B)
      and sync.is_overridden("WWW.Ads.Example.", B))
check("and nothing that only looks like it",
      not sync.is_overridden("nottiktok.com", B) and not sync.is_overridden("tiktok.co", B))
check("a rule naming several domains keeps the ones not covered",
      sync.drop_overridden(["address=/tiktok.com/tiktokv.com/%s" % ME, "no-resolv"], B)
      == ["address=/tiktokv.com/%s" % ME, "no-resolv"])
blocked, forwards, taken = sync.template_rules(
    {"blocked": ["tiktok.com", "bad name"],
     "forwards": {"x.tiktok.com": ["9.9.9.9"], "example.com": ["1.1.1.1", "nope"]}})
check("a template's rules, checked, with a forward under its block dropped",
      blocked == ["tiktok.com"] and forwards == {"example.com": ["1.1.1.1"]}
      and taken == {"tiktok.com", "example.com"}, repr((blocked, forwards)))

print("each template's resolver")
with quiet:
    sync.apply_profiles({
        "2": {"routed": ["spotify.com", "tiktok.com", "x.tiktok.com", "example.com"],
              "custom": ["m.ads.example", "kmplayer.com"],
              "bypass": ["gosredirector.ea.com", "cdn.tiktok.com"], "pins": True,
              "blocked": ["tiktok.com", "ads.example", "epicgames.com"],
              "forwards": {"example.com": ["1.1.1.1", "10.0.0.2#5353"]}},
        "3": {"routed": ["spotify.com", "tiktok.com"], "pins": True}}, {})
two, three = conf(2), conf(3)
check("its blocks answered \"no such name\", its forwards asked of the operator's resolvers",
      "address=/tiktok.com/\n" in two and "address=/ads.example/\n" in two
      and "server=/example.com/1.1.1.1\n" in two and "server=/example.com/10.0.0.2#5353\n" in two)
check("and no other rule for those names: routes, custom domains, bypasses, pins",
      "address=/spotify.com/%s" % ME in two and "tiktok.com/%s" % ME not in two
      and "example.com/%s" % ME not in two and "m.ads.example" not in two
      and "kmplayer.com" in two and "cdn.tiktok.com" not in two
      and "gosredirector.ea.com" in two and "pin.epicgames.com" not in two, two)
check("a template they are not for is left as it was",
      "address=/tiktok.com/%s" % ME in three and "address=/tiktok.com/\n" not in three
      and "example.com" not in three and "pin.epicgames.com" in three)

print("the default template")
check("with no rules of its own it stays on the main resolver",
      sync.default_profile({"blocked": [], "forwards": {}}, []) is None
      and sync.default_profile(None, []) is None)
stand = sync.default_profile({"blocked": ["tiktok.com"], "forwards": {"spotify.com": ["9.9.9.9"]}},
                             ["kmplayer.com"])
with quiet:
    sync.apply_profiles({sync.DEFAULT_PROFILE: stand}, {})
body = conf("default")
check("with some, a stand-in with the main resolver's settings and rules",
      "cache-size=10000" in body and "address=/google.com/%s" % ME in body
      and "address=/tiktokv.com/%s" % ME in body and "server=/gosredirector.ea.com/#" in body
      and "kmplayer.com" in body and "pin.epicgames.com" in body, body)
check("less the names its rules cover, which it answers its own way",
      "/tiktok.com/%s" % ME not in body and "spotify.com/%s" % ME not in body
      and "address=/tiktok.com/\n" in body and "server=/spotify.com/9.9.9.9\n" in body)

print("a sync")
got = {}
sync.post = lambda path, payload: {
    "allowed": [{"ip": "203.0.113.5", "name": "u1", "profile": ""},
                {"ip": "203.0.113.6", "name": "u2", "profile": "2"}],
    "profiles": {"2": {"routed": ["spotify.com"], "forwards": {"a.example": ["8.8.8.8"]}}},
    "extra_domains": ["kmplayer.com"], "templates": {},
    "default_rules": {"blocked": ["tiktok.com"], "forwards": {"a.example": ["9.9.9.9"]}},
    "doh": {"h1": {"uid": 1, "profile": ""}, "h2": {"uid": 2, "profile": "2"}}}
for name in ("save_template_names", "save_user_names", "apply_speeds", "close_relay_when_ready",
             "dns_seen", "apply_upstream"):
    setattr(sync, name, lambda *a, **k: False)
sync.apply_custom_domains = lambda d: got.setdefault("custom", list(d)) and False
sync.maybe_check_forwards = lambda f: got.setdefault("checked", f)


def fake_profiles(profiles, assignment, restart=False, names=None):
    got.update(profiles=profiles, assignment=dict(assignment))
    return {k: 5300 + i for i, k in enumerate(sorted(profiles))}


sync.apply_profiles = fake_profiles
sync.write_doh_state = lambda tokens, ports, assignment, allowed: got.update(doh=(tokens, ports))
sync.current_state = lambda: []
sync.acl = lambda *a: R()
sync.HEALTH = type("H", (), {"sample": staticmethod(lambda: {})})()
with quiet:
    sync.sync_once()
check("the default template's customers go to its stand-in, the others stay",
      got.get("assignment") == {"203.0.113.5": "default", "203.0.113.6": "2"}
      and got["profiles"]["default"]["blocked"] == ["tiktok.com"], repr(got.get("assignment")))
check("every template's forwards are checked, once per domain",
      got.get("checked") == {"a.example": ["8.8.8.8", "9.9.9.9"]}, repr(got.get("checked")))
src = open(os.path.join(ROOT, "templates", "smartdns-sync"), encoding="utf-8").read()
check("and the default template's DoH goes with its DNS",
      'ports.get(DEFAULT_PROFILE, MAIN_DNS_PORT)' in src)

print("the panel")
store = panel.Store(os.path.join(tmp, "panel.db"))
default = store.one("SELECT id FROM templates WHERE is_default = 1")
d_id = default["id"] if default else store.run(
    "INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, 'x')").lastrowid
t_game = store.run("INSERT INTO templates (name, created_at) VALUES ('بازی', 'x')").lastrowid
t_work = store.run("INSERT INTO templates (name, created_at) VALUES ('کار', 'x')").lastrowid
store.run("INSERT INTO blocked_domains (domain, added_at) VALUES ('ads.example', 'x')")
store.run("INSERT INTO blocked_domains (domain, added_at, all_templates)"
          " VALUES ('tiktok.com', 'x', 0)")
store.run("INSERT INTO blocked_templates (domain, template_id) VALUES ('tiktok.com', ?)", (t_game,))
store.run("INSERT INTO dns_forwards (domain, servers, added_at, all_templates)"
          " VALUES ('v.tiktok.com', '9.9.9.9', 'x', 0)")
store.run("INSERT INTO forward_templates (domain, template_id) VALUES ('v.tiktok.com', ?)",
          (t_game,))
store.run("INSERT INTO forward_templates (domain, template_id) VALUES ('v.tiktok.com', ?)",
          (t_work,))
check("a rule for every template reaches each, one for some only those",
      store.template_rules(t_work) == {"blocked": ["ads.example"],
                                       "forwards": {"v.tiktok.com": ["9.9.9.9"]}, "lists": {}}
      and store.template_rules(d_id) == {"blocked": ["ads.example"], "forwards": {},
                                         "lists": {}},
      repr(store.template_rules(t_work)))
check("a forward under a block of the same template is left out",
      store.template_rules(t_game) == {"blocked": ["ads.example", "tiktok.com"], "forwards": {},
                                       "lists": {}})
psrc = open(os.path.join(ROOT, "templates", "smartdns-panel"), encoding="utf-8").read()
check("each profile carries its template's, and the default's are sent apart",
      "profiles[str(tid)].update(self.template_rules(tid))" in psrc
      and '"default_rules": self.store.template_rules(' in psrc)
user = store.create_user(111, "ali", "علی")
store.run("UPDATE users SET template_id = ? WHERE id = ?", (t_game, user["id"]))
reason = panel.qlog_reasoner(store, store.one("SELECT * FROM users WHERE id = ?", (user["id"],)),
                             panel.CATALOGUE)
check("the DNS report goes by the customer's own template",
      reason("v16.tiktok.com") == "blocked:" and reason("x.ads.example") == "blocked:"
      and reason("example.org") == "outside:")
store.run("UPDATE users SET template_id = ? WHERE id = ?", (t_work, user["id"]))
reason = panel.qlog_reasoner(store, store.one("SELECT * FROM users WHERE id = ?", (user["id"],)),
                             panel.CATALOGUE)
check("where another template has a forward instead, it says so",
      reason("a.v.tiktok.com") == "forward:9.9.9.9"
      and "blocked" in admin.QLOG_REASON and "forward" in sync.QLOG_REASON)

print("the admin panel")
admin.DB = os.path.join(tmp, "panel.db")
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(admin.DB)
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('doh_host', 'dns.myservice.example')")
admin.CATALOGUE = [{"key": "spotify", "label": "Spotify",
                    "groups": [{"key": "main", "domains": ["spotify.com"]}]},
                   {"key": "custom", "label": "x", "groups": [{"key": "main", "domains": []}]}]


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, **form):
    r = Rec()
    admin.Admin.action(r, what, {k: v if isinstance(v, list) else [v] for k, v in form.items()})
    return r.to


check("a domain is blocked for every template",
      "مسدود شد" in act("blocked-add", domain="https://www.Ads2.example/x")
      and admin.rule_templates("blocked_domains", "www.ads2.example") == (True, set()))
act("blocked-add", domain="games.example")
admin.set_scope("blocked_domains", "games.example", False, [t_game, t_work])
check("not twice", "از قبل" in act("blocked-add", domain="www.ads2.example"))
check("not the name customers reach the service by",
      act("blocked-add", domain="myservice.example").startswith("domains?m=!"))
said = act("blocked-add", domain="spotify.com")
check("one the service routes is blocked, and it says which service",
      "Spotify" in said and "دیگر از رله نمی‌رود" in said, said)
page = admin.blocked_card("p")
check("the domains page says which templates each is for, and adds for every one",
      "همهٔ قالب‌ها" in page and "بازی، کار" in page and "name='tpl'" not in page
      and "action='/p/blocked-del'" in page)

editor = admin.template_rules_card({"id": t_work, "name": "کار", "is_default": 0}, "p")
check("a template's page ticks what it has",
      "value='games.example' checked" in editor and "value='www.ads2.example' checked" in editor
      and "value='tiktok.com'>" in editor and "action='/p/template-rules'" in editor, editor[:400])
all_ids = [r["id"] for r in admin.STORE.q("SELECT id FROM templates ORDER BY id")]
act("template-rules", id=str(t_work), b=["games.example", "tiktok.com"], f=["v.tiktok.com"])
check("un-ticking one for every template keeps it for all the others",
      admin.rule_templates("blocked_domains", "www.ads2.example")
      == (False, set(all_ids) - {t_work}))
check("ticking one adds this template, un-ticking removes only this one",
      admin.rule_templates("blocked_domains", "tiktok.com") == (False, {t_game, t_work})
      and admin.rule_templates("blocked_domains", "games.example") == (False, {t_game, t_work}))
act("template-rules", id=str(t_work), b=[], f=[])
check("and the forwards the same way",
      admin.rule_templates("dns_forwards", "v.tiktok.com") == (False, {t_game})
      and admin.rule_templates("blocked_domains", "games.example") == (False, {t_game}))
default_page = admin.Admin.template_editor(
    Rec(), admin.STORE.one("SELECT * FROM templates WHERE id = ?", (d_id,)))
check("the default template's page has it too", "action='/p/template-rules'" in default_page)
act("blocked-del", domain="games.example")
check("freeing a domain takes its templates with it",
      not admin.STORE.one("SELECT 1 FROM blocked_templates WHERE domain = 'games.example'"))
admin.STORE.run("DELETE FROM users")
admin.STORE.run("DELETE FROM templates WHERE id = ?", (t_game,))
check("and a deleted template is dropped from every rule",
      not admin.STORE.one("SELECT 1 FROM blocked_templates WHERE template_id = ?", (t_game,)))

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
