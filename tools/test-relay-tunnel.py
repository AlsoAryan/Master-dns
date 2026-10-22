#!/usr/bin/env python3
"""A tunnel to every relay, set from the admin panel.

What has to hold: the relay's nginx reaches the exit through upstreams kept in
a file of their own, so the tunnel can be turned on and off without the
installer; the installer and smartdns-sync write that file, the tunnel's
config and its firewall rule the same to the byte, so neither undoes the
other; the panel tells each relay what the admin set - and nothing for the
first relay while the installer tunnels to it; the relay's sync sets up or
takes down its end and keeps sync.env in step for the installer's next run;
the exit runs one BackPack per relay from its own directory; and the admin
panel refuses a port that is taken or not a port at all.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
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


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def load(name, rel):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(
        name, os.path.join(ROOT, rel)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def func(text, name):
    start = text.index(name + "() {")
    return text[start:text.index("\n}\n", start) + 3]


tmp = tempfile.mkdtemp()
logic = read("tools/installer-logic.sh")
sync = load("sync", "templates/smartdns-sync")
sync.log = sync.log_exception = lambda *a, **k: None

print("the relay's nginx")
conf = read("templates/relay-nginx.conf")
check("reaches the exit through the upstreams in their own file",
      conf.count("include /etc/nginx/smartdns-exit.conf;") == 1 and "__EXIT_" not in conf
      and not re.search(r"^\s*upstream ", conf, re.M))
check("  on every port it passes on",
      all(("proxy_pass ${to_exit}_%s;" % n) in conf for n in ("https", "http", "spotify", "blizzard"))
      and "default       ${to_exit}_https;" in conf)
check("the installer writes that file on a relay, before its nginx",
      logic.index('if [ "$ROLE" = relay ]; then exit_upstreams; fi')
      < logic.index("install_payload RELAY_NGINX /etc/nginx/nginx.conf && NGINX_CHANGED=1"))

bash = shutil.which("bash")
consts = "\n".join(l for l in logic.splitlines() if re.match(r"TUNNEL_LOCAL_\w+=\d+$", l))


def installer(script):
    # From a file: Windows passes a long `bash -c` argument on mangled.
    path = os.path.join(tmp, "installer-part.sh")
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(script)
    unix = path.replace("\\", "/")
    if len(unix) > 1 and unix[1] == ":":
        unix = "/" + unix[0].lower() + unix[2:]
    r = subprocess.run([bash, unix], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else "rc=%d %s" % (r.returncode, r.stderr)


if bash:
    body = func(logic, "exit_upstreams")
    for tunnel in (True, False):
        out = os.path.join(tmp, "exit.conf").replace("\\", "/")
        if len(out) > 1 and out[1] == ":":
            out = "/" + out[0].lower() + out[2:]
        got = installer(consts + "\nnote_file() { :; }\ninfo() { :; }\n"
                        + body.replace('EXIT_CONF"', 'OUT"').replace("$EXIT_CONF", out)
                        + "\nOUT=%s\nEXIT_CONF=%s\nEXIT_IP=203.0.113.9\nTUNNEL=%s\nexit_upstreams\ncat %s\n"
                        % (out, out, "backpack" if tunnel else "off", out))
        check("the installer's and the sync's way to the exit agree, %s" %
              ("through the tunnel" if tunnel else "straight"),
              got == sync.exit_conf_text("203.0.113.9", tunnel), got)
    toml = func(logic, "tunnel_toml") + func(logic, "tunnel_tuning")
    tok = func(logic, "tunnel_token") + func(logic, "tunnel_pair_token")
    for direction, transport in (("reverse", "stealth"), ("direct", "tcp"), ("reverse", "kcp")):
        got = installer(consts + "\n" + tok + toml + "\nROLE=relay\nEXIT_IP=203.0.113.9\n"
                        "TUNNEL_DIRECTION=%s\nTUNNEL_TRANSPORT=%s\nTUNNEL_PORT=8444\n"
                        "tunnel_toml s3cret\n" % (direction, transport))
        want = sync.tunnel_toml_text({"direction": direction, "transport": transport,
                                      "port": 8444}, "203.0.113.9", "s3cret")
        check("  and the tunnel's config, %s %s" % (direction, transport), got == want,
              "\n" + got + "\n---\n" + want)
nft = sync.tunnel_nft_text("8444", "203.0.113.9")
block = logic[logic.index('cat > "$TUNNEL_NFT" <<EOF\n') + 26:]
block = block[:block.index("\nEOF\n") + 1]
check("  and the firewall rule on the listening end",
      block.replace("$TUNNEL_PORT", "8444").replace("$peer", "203.0.113.9") == nft,
      block + "---\n" + nft)

print("the installer")
check("BackPack on every relay and exit, quietly when nobody asked for a tunnel yet",
      'BACKPACK_QUIET=1 install_backpack || true' in logic
      and '[ "$TUNNEL" != backpack ] && [ "$ROLE" != single ]' in logic)
check("the relay's unit always, and the exit's one-per-relay unit",
      "install_payload TUNNEL_SERVICE /etc/systemd/system/smartdns-tunnel.service && changed=1 || true\n"
      in logic and "        install_payload TUNNEL_INSTANCE /etc/systemd/system/smartdns-tunnel@.service"
      " || true\n        systemctl daemon-reload\n    elif" in logic
      and "install_payload TUNNEL_INSTANCE /etc/systemd/system/smartdns-tunnel@.service" in logic
      and '("TUNNEL_INSTANCE", "templates/smartdns-tunnel@.service")' in read("tools/build-installer.py"))
unit = read("templates/smartdns-tunnel@.service")
check("  each relay's from a directory of its own, with its own firewall file",
      "/etc/smart-dns/relay-tunnels/%i/tunnel.toml" in unit
      and "/etc/nftables.d/41-smartdns-tunnel-%i.conf" in unit)
check("uninstall stops them too", "smartdns-tunnel@*" in logic
      and "41-smartdns-tunnel-*.conf" in logic)

print("the panel's word to each relay")
panel = load("panel", "templates/smartdns-panel")
panel.print = lambda *a, **k: None
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
relays = ("198.51.100.1", "198.51.100.2")
check("nothing for the first relay while the installer tunnels to it",
      panel.relay_tunnel(store, "198.51.100.1", relays, "backpack") is None)
check("  nor for a machine that is not a relay",
      panel.relay_tunnel(store, "192.0.2.7", relays, "off") is None)
check("off for one the admin set nothing for - the first too, with no installer tunnel",
      panel.relay_tunnel(store, "198.51.100.2", relays, "backpack") == {"on": False}
      and panel.relay_tunnel(store, "198.51.100.1", relays, "off") == {"on": False})
store.set_setting("relay_tunnel:198.51.100.2", json.dumps(
    {"transport": "stealth", "direction": "reverse", "port": 9000, "exit": "203.0.113.9"}))
check("and what the admin set", panel.relay_tunnel(store, "198.51.100.2", relays, "off")
      == {"on": True, "transport": "stealth", "direction": "reverse", "port": 9000,
          "exit": "203.0.113.9"})
src = read("templates/smartdns-panel")
check("  sent with every sync, and the relay's report kept",
      '"tunnel": relay_tunnel(self.store, who, self.relays,' in src
      and '"relay_tunnel_state:" + who' in src)

print("the relay's sync")
check("None leaves it alone; off; a good one; nonsense refused",
      sync.clean_tunnel(None) is None and sync.clean_tunnel({"on": False}) == {}
      and sync.clean_tunnel({"on": True, "direction": "reverse", "transport": "kcp", "port": "9000"})
      == {"direction": "reverse", "transport": "kcp", "port": 9000}
      and sync.clean_tunnel({"on": True, "direction": "direct", "transport": "kcp", "port": 9000}) is None
      and sync.clean_tunnel({"on": True, "direction": "reverse", "transport": "tcp", "port": 70000}) is None)

etc = os.path.join(tmp, "relay")
os.makedirs(etc)
sync.CONFIG = os.path.join(etc, "sync.env")
sync.TUNNEL_DIR = os.path.join(etc, "tunnel")
sync.TUNNEL_TOML = os.path.join(sync.TUNNEL_DIR, "tunnel.toml")
sync.TUNNEL_NFT = os.path.join(etc, "nft", "40-smartdns-tunnel.conf")
sync.TUNNEL_UNIT_FILE = os.path.join(etc, "smartdns-tunnel.service")
sync.BACKPACK_BIN = os.path.join(etc, "backpack")
sync.EXIT_CONF = os.path.join(etc, "smartdns-exit.conf")
sync.PANEL_ENV_HERE = os.path.join(etc, "panel.env")
with open(sync.CONFIG, "w") as fh:
    fh.write("PANEL_HOST=203.0.113.9\nSYNC_SECRET=s3cret\nSYNC_FINGERPRINT=ab\n"
             "SELF_IP=198.51.100.2\nTUNNEL=off\n")
with open(sync.EXIT_CONF, "w") as fh:
    fh.write(sync.exit_conf_text("203.0.113.9", False))
sync.CFG = sync.load_config()
ran = []
active = {"smartdns-tunnel": False}


class R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def fake_sh(*args):
    ran.append(args)
    if args[:2] == ("systemctl", "is-active"):
        return R(0 if active["smartdns-tunnel"] else 3)
    if args[:2] == ("systemctl", "restart"):
        active["smartdns-tunnel"] = True
    if args[:2] == ("systemctl", "disable"):
        active["smartdns-tunnel"] = False
    return R(0)


sync.sh = fake_sh
sync.nft = lambda *a: fake_sh("nft", *a)
spec = {"on": True, "direction": "reverse", "transport": "stealth", "port": 9000,
        "exit": "203.0.113.9"}
sync.apply_tunnel(spec)
check("with no BackPack here it stays off, and says why",
      sync.TUNNEL_REPORT["state"]["on"] is False and "BackPack" in sync.TUNNEL_REPORT["state"]["error"]
      and not os.path.exists(sync.TUNNEL_TOML))
for path in (sync.BACKPACK_BIN, sync.TUNNEL_UNIT_FILE):
    open(path, "w").close()
    os.chmod(path, 0o755)
ran.clear()
sync.apply_tunnel(spec)
env = open(sync.CONFIG).read()
check("on: its config, its firewall rule, the service started, nginx through the tunnel",
      open(sync.TUNNEL_TOML).read() == sync.tunnel_toml_text(spec, "203.0.113.9", "s3cret")
      and open(sync.TUNNEL_NFT).read() == sync.tunnel_nft_text(9000, "203.0.113.9")
      and ("systemctl", "restart", "smartdns-tunnel") in ran
      and open(sync.EXIT_CONF).read() == sync.exit_conf_text("203.0.113.9", True)
      and ("nginx", "-t") in ran and ("systemctl", "reload", "nginx") in ran, repr(ran))
check("  and sync.env says so, for the API's way in and the installer's next run",
      "TUNNEL=backpack" in env and "TUNNEL_PORT=9000" in env and "TUNNEL_DIRECTION=reverse" in env
      and "SYNC_SECRET=s3cret" in env and sync.CFG["TUNNEL"] == "backpack"
      and sync.api_endpoints()[0] == ("127.0.0.1", sync.API_TUNNEL_PORT))
check("  reported running", sync.TUNNEL_REPORT["state"] == {"on": True, "error": "", "running": True})
ran.clear()
sync.apply_tunnel(spec)
check("the same again changes nothing and restarts nothing",
      ("systemctl", "restart", "smartdns-tunnel") not in ran and ("nginx", "-t") not in ran, repr(ran))
spec2 = dict(spec, direction="direct", transport="tcp", port=9100)
sync.apply_tunnel(spec2)
check("direct: the relay dials the exit, and nothing listens here to guard",
      'addr = "203.0.113.9:9100"' in open(sync.TUNNEL_TOML).read()
      and not os.path.exists(sync.TUNNEL_NFT))
ran.clear()
sync.apply_tunnel({"on": False})
check("off: the service stopped, its files gone, nginx straight to the exit",
      ("systemctl", "disable", "--now", "smartdns-tunnel") in ran
      and not os.path.exists(sync.TUNNEL_TOML)
      and open(sync.EXIT_CONF).read() == sync.exit_conf_text("203.0.113.9", False)
      and "TUNNEL=off" in open(sync.CONFIG).read())
ran.clear()
sync.apply_tunnel({"on": False})
check("  and off again touches nothing", not any(a[:2] == ("systemctl", "disable") for a in ran)
      and ("nginx", "-t") not in ran)
sync.apply_tunnel(None)
open(sync.PANEL_ENV_HERE, "w").close()
ran.clear()
sync.apply_tunnel(spec)
check("a single machine, and a relay the installer tunnels, are left alone",
      not ran and not os.path.exists(sync.TUNNEL_TOML))

print("the exit's admin panel")
admin = load("admin", "templates/smartdns-admin")
admin.log = lambda *a, **k: None
admin.PANEL_ENV = os.path.join(tmp, "admin-panel.env")
admin.INSTALL_STATE = os.path.join(tmp, "install-state")
admin.RELAY_TUNNELS = os.path.join(tmp, "relay-tunnels")
admin.BACKPACK_BIN = sync.BACKPACK_BIN
admin.TUNNEL_INSTANCE_UNIT = sync.TUNNEL_UNIT_FILE
admin.tunnel_nft_path = lambda ip: os.path.join(tmp, "41-%s.conf" % ip)
admin.CFG = {"ADMIN_PORT": "2053"}
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=198.51.100.1,198.51.100.2,198.51.100.3\n"
             "TUNNEL=backpack\nTUNNEL_TRANSPORT=stealth\nTUNNEL_DIRECTION=direct\nTUNNEL_PORT=8444\n")
with open(admin.INSTALL_STATE, "w") as fh:
    fh.write("exit-ip 203.0.113.9\n")
admin.STORE = admin.Store(db)
exits = []


def fake_run(cmd, **kw):
    exits.append(tuple(cmd))
    return R(0)


admin.subprocess.run = fake_run
check("ports: not a port, one of ours, this panel's, the installer's own direct one",
      admin.tunnel_port_problem(0, "198.51.100.2", "reverse")
      and admin.tunnel_port_problem(8443, "198.51.100.2", "reverse")
      and admin.tunnel_port_problem(2053, "198.51.100.2", "reverse")
      and admin.tunnel_port_problem(5301, "198.51.100.2", "reverse")
      and admin.tunnel_port_problem(8444, "198.51.100.2", "direct")
      and not admin.tunnel_port_problem(8444, "198.51.100.2", "reverse"))


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(**form):
    r = Rec()
    admin.Admin.action(r, "relay-tunnel", {k: [v] for k, v in form.items()})
    return r.to


check("the first relay's tunnel is the installer's, not set here",
      act(ip="198.51.100.1", on="1", direction="reverse", transport="stealth",
          port="9000").startswith("nodes?m=!"))
check("a direct transport there is none of is refused",
      act(ip="198.51.100.2", on="1", direction="direct", transport="kcp",
          port="9000").startswith("nodes?m=!"))
check("the old direct engine is not taken for a new tunnel",
      "مستقیم قدیمی دیگر انتخاب نمی‌شود" in act(ip="198.51.100.2", on="1", direction="direct",
                                                transport="stealth", port="9000"))
got = act(ip="198.51.100.2", on="1", direction="l3", transport="xdi", port="9000")
saved = admin.relay_tunnel("198.51.100.2")
where = os.path.join(admin.RELAY_TUNNELS, "198.51.100.2")
check("a good one is saved, with this exit's address for the relay",
      not got.startswith("nodes?m=!") and saved == {"transport": "xdi", "direction": "l3",
                                                       "port": 9000, "exit": "203.0.113.9"}, got)
toml = open(os.path.join(where, "tunnel.toml")).read()
check("  this end: listening for that relay, on the token of this relay and this exit",
      'mode = "listen"' in toml and 'addr = "0.0.0.0:9000"' in toml
      and 'token = "%s"' % sync.tunnel_token("s3cret", {"transport": "xdi"}, "198.51.100.2",
                                             "203.0.113.9") in toml)
check("  its port answering that relay only",
      "ip saddr != 198.51.100.2 drop" in open(admin.tunnel_nft_path("198.51.100.2")).read())
check("  and its own BackPack started",
      ("systemctl", "restart", "smartdns-tunnel@198.51.100.2.service") in exits)
check("another relay may not take the same port on this exit",
      "9000" in act(ip="198.51.100.3", on="1", direction="l3", transport="pck", port="9000"))
act(ip="198.51.100.3", on="1", direction="reverse", transport="kcp", port="9000")
check("  but a reverse one may - that port is on the relay",
      '[client]\nremote_addr = "198.51.100.3:9000"' in
      open(os.path.join(admin.RELAY_TUNNELS, "198.51.100.3", "tunnel.toml")).read())
cell = admin.tunnel_cell("p", "198.51.100.2")
check("the card shows it, with the relay's word on it",
      "xdi، مستقیم، درگاه 9000" in cell and "رله هنوز نگرفته است" in cell
      and "action='/p/relay-tunnel'" in cell and "value='direct'" not in cell)
admin.STORE.run("UPDATE settings SET value = ? WHERE key = 'relay_tunnel:198.51.100.2'",
                (json.dumps({"transport": "stealth", "direction": "direct", "port": 9000,
                             "exit": "203.0.113.9"}),))
check("a tunnel that has the old direct keeps it, shown as such, and may be saved as it is",
      "<option value='direct' selected>مستقیم قدیمی" in admin.tunnel_cell("p", "198.51.100.2")
      and not act(ip="198.51.100.2", on="1", direction="direct", transport="stealth",
                  port="9000").startswith("nodes?m=!"))
act(ip="198.51.100.2", on="1", direction="l3", transport="xdi", port="9000")
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('relay_tunnel_state:198.51.100.2', ?)",
                (json.dumps({"on": True, "running": True, "error": ""}),))
check("  and both ends up once they are",
      "روشن در هر دو سر" in admin.tunnel_cell("p", "198.51.100.2"))
check("  the installer's own shown as the installer's",
      "--tunnel" in admin.tunnel_cell("p", "198.51.100.1"))
exits.clear()
act(ip="198.51.100.2", on="0")
check("off: its BackPack stopped and its files gone",
      admin.relay_tunnel("198.51.100.2") == {} and not os.path.exists(where)
      and ("systemctl", "disable", "--now", "smartdns-tunnel@198.51.100.2.service") in exits)
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=198.51.100.1,198.51.100.2\n")
admin.apply_exit_tunnels()
check("a relay no longer served loses its tunnel when the panel starts",
      not os.path.exists(os.path.join(admin.RELAY_TUNNELS, "198.51.100.3"))
      and admin.relay_tunnel("198.51.100.3") == {})

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
