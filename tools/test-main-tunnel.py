#!/usr/bin/env python3
"""0.0.9: the installer's tunnel, changed from the panel's settings; servers with
names and a map; the three-dot menu's background.

What has to hold: the transport is changed for both ends from Settings (the exit's
own files and the relay's spec), the answer comes back to Settings, the installer's
tunnel is never turned off from the form, a spec the panel did not mark is still
the installer's, every server can be named and the name shows on the map and in the
tables, and an open menu is stacked above the rows after it.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
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
          ((" - " + str(detail)[:600]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, rel):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(
        name, os.path.join(ROOT, rel)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tmp = tempfile.mkdtemp()
RELAY, EXIT = "198.51.100.1", "203.0.113.9"

panel = load("panel", "templates/smartdns-panel")
admin = load("admin", "templates/smartdns-admin")
admin.log = lambda *a, **k: None
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
admin.INSTALL_STATE = os.path.join(tmp, "install-state")
admin.SYNC_CRT = os.path.join(tmp, "none.crt")
admin.MAIN_TUNNEL_DIR = os.path.join(tmp, "tunnel")
admin.MAIN_TUNNEL_NFT = os.path.join(tmp, "40-tunnel.conf")
admin.BACKPACK_BIN = "/bin/sh"                      # any executable will do
admin.CFG = {"ADMIN_PATH": "p", "ADMIN_PORT": "9443"}
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=%s\nTUNNEL=backpack\nTUNNEL_TRANSPORT=stealth\n"
             "TUNNEL_DIRECTION=reverse\nTUNNEL_PORT=8444\n" % RELAY)
with open(admin.INSTALL_STATE, "w") as fh:
    fh.write("exit-ip %s\n" % EXIT)
panel.print = lambda *a, **k: None
panel.log = panel.log_exception = lambda *a, **k: None
pstore = panel.Store(os.path.join(tmp, "panel.db"))   # makes the tables, and is the relays' view
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
admin.systemctl = lambda *a: None
ran = []


class Done:
    returncode = 0


admin.subprocess.run = lambda cmd, *a, **k: (ran.append(list(cmd)), Done())[1]


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, **form):
    r = Rec()
    admin.Admin.action(r, what, {k: [v] for k, v in form.items()})
    return r.to


print("the installer's tunnel, from Settings")
card = admin.tunnel_settings_card("p")
check("Settings has a card for it, with the transport list and a button for both servers",
      "id='tunnel'" in card and "name='transport'" in card and "اعمال روی هر دو سرور" in card
      and "name='back' value='settings'" in card, card[:300])
check("  in plain words, with the real name beside each",
      "مخفی و رمزشده" in card and "(wss)" in card)
check("  no off switch for the installer's own tunnel", "value='0'" not in card)
check("the panel still tells the first relay nothing while it is the installer's",
      panel.relay_tunnel(pstore, RELAY, (RELAY,), "backpack") is None)

to = act("relay-tunnel", ip=RELAY, exit=EXIT, on="1", direction="reverse", transport="wss",
         port="8444", preset="", back="settings")
check("changing it answers on Settings", to.startswith("settings?m="), to)
spec = json.loads(admin.setting("relay_tunnel:" + RELAY))
check("it is kept, marked as the panel's now", spec["transport"] == "wss" and spec.get("main"), spec)
told = panel.relay_tunnel(pstore, RELAY, (RELAY,), "backpack")
check("the relay is told, through its sync, the new transport",
      told and told["on"] and told["transport"] == "wss" and told["port"] == 8444, told)
toml = open(os.path.join(admin.MAIN_TUNNEL_DIR, "tunnel.toml")).read()
check("the exit's own end is rewritten for wss",
      'transport = "wss"' in toml and '[client]' in toml and RELAY + ":8444" in toml, toml)
env = open(admin.PANEL_ENV).read()
check("and panel.env keeps it, so the installer and upgrades do too",
      "TUNNEL_TRANSPORT=wss" in env and "TUNNEL=backpack" in env and "SYNC_SECRET=s3cret" in env, env)
check("the exit's service is restarted",
      ["systemctl", "restart", "smartdns-tunnel.service"] in ran, ran)

to = act("relay-tunnel", ip=RELAY, exit=EXIT, on="0", back="settings")
check("it cannot be turned off from the form", to.startswith("settings?m=!"), to)
to = act("relay-tunnel", ip=RELAY, exit=EXIT, on="1", direction="reverse", transport="udpx",
         port="8444", preset="", back="settings")
check("a transport it does not have is refused", to.startswith("settings?m=!"), to)
to = act("relay-tunnel", ip=RELAY, exit=EXIT, on="1", direction="reverse", transport="tcp",
         port="443", preset="", back="settings")
check("so is a port that is somebody else's", to.startswith("settings?m=!"), to)
check("a refused change leaves the last good one",
      json.loads(admin.setting("relay_tunnel:" + RELAY))["transport"] == "wss")

act("relay-tunnel", ip=RELAY, exit=EXIT, on="1", direction="l3", transport="pck",
    port="8460", preset="", back="nodes")
check("the direct kind writes the firewall rule on the exit, for the relay only",
      "tcp dport 8460 ip saddr != %s drop" % RELAY in open(admin.MAIN_TUNNEL_NFT).read())
check("a change from the nodes page answers on the nodes page",
      act("relay-tunnel", ip=RELAY, exit=EXIT, on="1", direction="reverse", transport="tcp",
          port="8444", preset="", back="nodes").startswith("nodes?m="))
check("and is back to reverse: the rule is gone", not os.path.exists(admin.MAIN_TUNNEL_NFT))

print("names and the map")
check("a server has no name until given one", admin.server_name(RELAY) == RELAY)
check("a name cannot be given to a stranger",
      act("server-name-save", ip="192.0.2.77", name="x").startswith("nodes?m=!"))
act("server-name-save", ip=RELAY, name="  تهران <b>۱</b>  ")
check("a name is kept, cleaned", "<" not in admin.server_name(RELAY) and admin.server_name(RELAY).startswith("تهران"), admin.server_name(RELAY))
act("server-name-save", ip=EXIT, name="آلمان ۱")
page = admin.nodes_page()
check("the map shows both, named, with the wire between them",
      "id='map'" in page and "آلمان ۱" in page and "تهران" in page and "class='wires'" in page)
check("  and the tables carry the name over the address",
      "<b>آلمان ۱</b><br><code dir='ltr'>%s</code>" % EXIT in page)
act("server-name-save", ip=RELAY, name="")
check("an empty name takes it off", admin.server_name(RELAY) == RELAY)
admin.forget_server(RELAY)
check("a server taken off the panel forgets its name",
      admin.setting("server_name:" + RELAY) == "")

print("Settings, in sections")
page = admin.Admin.settings(Rec())
check("quick links on top and four sections, the tunnel first",
      "class='card chips'" in page and page.index("id='conn'") < page.index("id='tunnel'")
      < page.index("id='cust'") < page.index("id='safe'") < page.index("id='keep'"))

print("the menu behind the three dots")
css = admin.CSS
check("an open menu is stacked above the rows and cards after it",
      "tbody tr:has(details[open]),.card:has(details[open]){position:relative;z-index:40}" in css)
check("rows and cards do not keep a stacking context of their own after they arrive",
      "tbody tr:nth-child(-n+10){animation:md-row .45s cubic-bezier(.2,.8,.2,1) backwards}" in css
      and "animation:md-rise .55s cubic-bezier(.2,.8,.2,1) backwards" in css)

print("the panel's own defences")
check("a POST from another site is refused, from its own pages and from none at all is not",
      admin.cross_site("https://evil.example", "panel.example:9443")
      and admin.cross_site("null", "panel.example:9443")
      and not admin.cross_site("https://panel.example:9443", "panel.example:9443")
      and not admin.cross_site("", "panel.example:9443"))
src = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("every page carries a content policy that allows no other site and no framing",
      "frame-ancestors 'none'" in admin.ADMIN_CSP and "default-src 'none'" in admin.ADMIN_CSP
      and 'send_header("Content-Security-Policy", ADMIN_CSP)' in src
      and "Strict-Transport-Security" in src)

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
