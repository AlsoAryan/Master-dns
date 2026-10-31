#!/usr/bin/env python3
"""0.1.0: the names a relay carries itself, from Iran, instead of through the exit.

What has to hold: the panel keeps a list and sends it to the relays; a relay
cleans it, answers the names with itself in dnsmasq - for the main resolver and
for every profile - tries the CDN's addresses with a real TLS handshake, and puts
only the ones that answered in nginx; a name with no good address is left out of
nginx (so it goes through the exit as before); an nginx that refuses the files gets
the old ones back; the DNS answer is read correctly, compression pointers included;
and the 443 server consults the new map in both of its forms.
"""
import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading

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


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


tmp = tempfile.mkdtemp()
sync = load("sync", "templates/smartdns-sync")
panel = load("panel", "templates/smartdns-panel")
admin = load("admin", "templates/smartdns-admin")
for m in (sync, panel, admin):
    m.log = lambda *a, **k: None

print("what the relay accepts")
got = sync.clean_egress([
    {"name": "Auth.Embark.NET.", "ips": ["8.8.8.8", "10.0.0.1", "nonsense", "8.8.8.8"]},
    {"name": "auth.embark.net"}, {"name": "bad name"}, {"name": "x"}, "junk", None,
    {"name": "a.example.com", "ips": "1.1.1.1"}])
check("a name is lower-cased and kept once; strangers are dropped",
      [e["name"] for e in got] == ["auth.embark.net", "a.example.com"], got)
check("only public IPv4 addresses are kept, once",
      got[0]["ips"] == ["8.8.8.8"] and got[1]["ips"] == [], got)
check("not a list is no list", sync.clean_egress({"name": "a.example.com"}) == [])
check("at most forty names",
      len(sync.clean_egress([{"name": "h%d.example.com" % i} for i in range(80)])) == 40)

print("reading a DNS answer")
IP1, IP2 = "23.45.67.89", "104.18.1.2"


def answer(query, with_cname=True):
    """What a resolver says: the question, a CNAME (a pointer to the name) and two A records."""
    qid = query[:2]
    end = query.index(b"\x00", 12) + 5
    question = query[12:end]
    out = qid + bytes([0x81, 0x80, 0, 1, 0, 3 if with_cname else 2, 0, 0, 0, 0]) + question
    if with_cname:
        out += bytes([0xC0, 12, 0, 5, 0, 1, 0, 0, 0, 60, 0, 2, 0xC0, 12])
    for ip in (IP1, IP2):
        out += bytes([0xC0, 12, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4]) + socket.inet_aton(ip)
    return out


srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
srv.bind(("127.0.0.1", 0))
port = srv.getsockname()[1]
asked = []


def serve():
    while True:
        try:
            q, who = srv.recvfrom(512)
        except OSError:
            return
        asked.append(q)
        srv.sendto(answer(q), who)


threading.Thread(target=serve, daemon=True).start()
found = sync.dns_lookup("127.0.0.1#%d" % port, "ps5.np.playstation.net")
check("the A records come out, past the CNAME and its pointers", found == [IP1, IP2], found)
check("a resolver that is not there gives nothing, quickly",
      sync.dns_lookup("127.0.0.1#9", "a.example.com", timeout=0.3) == [])

print("which addresses to try")
sync.current_upstream = lambda: ["127.0.0.1#%d" % port]
cands = sync.egress_candidates({"name": "ps5.np.playstation.net", "ips": []})
check("with none given the relay asks, and sees both of the CDN's", cands == [IP1, IP2], cands)
asked_before = len(asked)
check("with some given, only those, and nobody is asked",
      sync.egress_candidates({"name": "a.example.com", "ips": ["8.8.4.4"]}) == ["8.8.4.4"]
      and len(asked) == asked_before)
check("with none given it asks a few times, not once",
      asked_before >= sync.LOCAL_EGRESS_LOOKS)

print("which of them answer")
sync.egress_ip_answers = lambda ip, name, timeout=4.0: ip == IP2
state = sync.look_at_egress([{"name": "ps5.np.playstation.net", "ips": []},
                             {"name": "dead.example.com", "ips": ["8.8.4.4"]}])
check("only the ones a handshake went through are kept",
      state["ps5.np.playstation.net"] == {"ips": [IP2], "tried": 2}, state)
check("a name nothing answered for says so", state["dead.example.com"] == {"ips": [], "tried": 1})

print("what nginx is given")
spec = sync.clean_egress([{"name": "ps5.np.playstation.net"}, {"name": "dead.example.com"},
                          {"name": "auth.embark.net"}])
state["auth.embark.net"] = {"ips": ["8.8.8.8", "8.8.4.4"], "tried": 2}
map_text, ups_text = sync.egress_nginx_texts(spec, state)
check("a name with a good address gets a line for itself and its subdomains",
      "~*(^|\\.)ps5\\.np\\.playstation\\.net$ local_ps5_np_playstation_net;" in map_text
      and "~*(^|\\.)auth\\.embark\\.net$ local_auth_embark_net;" in map_text, map_text)
check("a name with none is left out, so it goes through the exit",
      "dead" not in map_text and "dead" not in ups_text)
check("the upstream is the good addresses on 443, and no others",
      "server %s:443" % IP2 in ups_text and IP1 not in ups_text
      and ups_text.count("server 8.8.8.8:443") == 1 and ups_text.count("upstream ") == 2, ups_text)
check("nothing in either file can end a line early or open a block",
      all(c not in map_text.replace("{", "", 0) for c in "{}") and map_text.count(";") == 2)

print("putting it in nginx")
sync.LOCAL_EGRESS_MAP = os.path.join(tmp, "egress.map")
sync.LOCAL_EGRESS_UPS = os.path.join(tmp, "egress.conf")
ran = []


class R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


verdict = {"rc": 0}


def fake_sh(*a):
    ran.append(a)
    return R(verdict["rc"] if a[:2] == ("nginx", "-t") else 0)


sync.sh = fake_sh
check("written and reloaded", sync.apply_egress_nginx(spec, state)
      and ["systemctl", "reload", "nginx"] in [list(a) for a in ran]
      and open(sync.LOCAL_EGRESS_MAP).read() == map_text)
n = len(ran)
check("the same again touches nothing", sync.apply_egress_nginx(spec, state) and len(ran) == n)
before = open(sync.LOCAL_EGRESS_MAP).read()
verdict["rc"] = 1
state2 = {"ps5.np.playstation.net": {"ips": [IP1], "tried": 1}}
check("an nginx that refuses is refused back: the old files return",
      not sync.apply_egress_nginx(spec, state2) and open(sync.LOCAL_EGRESS_MAP).read() == before)
verdict["rc"] = 0

print("the names, in dnsmasq")
me = "198.51.100.2"
text = sync.egress_dns_text(["b.example.com", "a.example.com"], me)
check("each answers with this relay, in order",
      text.splitlines()[-2:] == ["address=/a.example.com/%s" % me, "address=/b.example.com/%s" % me])
sync.LOCAL_EGRESS_DNS = os.path.join(tmp, "55-egress.conf")
sync.CFG = {"SELF_IP": me}
sync.is_single = lambda: False
sync.refresh_local_egress = lambda force=False: None
check("a list changes the main resolver, and it is restarted",
      sync.apply_local_egress([{"name": "a.example.com"}]) is True
      and ("systemctl", "restart", "dnsmasq") in ran
      and "address=/a.example.com/%s" % me in open(sync.LOCAL_EGRESS_DNS).read())
n = len(ran)
check("the same list changes nothing", sync.apply_local_egress([{"name": "a.example.com"}]) is False
      and len(ran) == n)
check("an empty list takes the rules away",
      sync.apply_local_egress([]) is True and "address=" not in open(sync.LOCAL_EGRESS_DNS).read())
sync.is_single = lambda: True
check("one machine that is relay and exit has nothing to carry itself",
      sync.apply_local_egress([{"name": "z.example.com"}]) is False)
src = read("templates/smartdns-sync")
check("the profiles route them too, for every template",
      'for e in sorted(LOCAL_EGRESS["spec"], key=lambda e: e["name"])' in src)
check("they are applied before the profiles, and reported back",
      src.find("apply_profiles(", src.index("apply_local_egress(answer.get")) > 0
      and 'payload["local_egress_state"]' in src)

print("the 443 server")
conf = read("templates/relay-nginx.conf")
check("it reads the map, and the upstreams, in both forms",
      conf.count("smartdns-local-egress.map") == 1 and conf.count("smartdns-local-egress.conf") == 1
      and "default       $egress_https;" in conf and "proxy_pass $egress_https;" in conf)
check("and falls back to the exit for every name not in the map",
      "default ${to_exit}_https;" in conf)
logic = read("tools/installer-logic.sh")
check("the installer seeds both files, empty, and leaves working ones alone",
      "LOCAL_EGRESS_MAP=/etc/nginx/smartdns-local-egress.map" in logic
      and 'if [ ! -f "$f" ]; then' in logic)

print("the panel's side")
pst = panel.Store(os.path.join(tmp, "panel.db"))
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
admin.CFG = {"ADMIN_PATH": "p", "ADMIN_PORT": "9443"}
check("no list is an empty one", panel.local_egress(pst) == [])
admin.egress_save([{"name": "a.example.com", "ips": []}])
check("what the admin saved is what the relay is sent",
      panel.local_egress(pst) == [{"name": "a.example.com", "ips": []}])
pst.set_setting("local_egress", "not json")
check("an unreadable list is no list", panel.local_egress(pst) == [])


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, **form):
    r = Rec()
    admin.Admin.action(r, what, {k: [v] for k, v in form.items()})
    return r.to


admin.egress_save([])
to = act("egress-add", name="Auth.Embark.net  es-dis.net", ips="")
check("names are added, several at once, cleaned",
      to.startswith("settings?m=2") and [e["name"] for e in admin.egress_list()] ==
      ["auth.embark.net", "es-dis.net"], to)
to = act("egress-add", name="auth.embark.net", ips="8.8.8.8, 8.8.4.4")
check("one again replaces it, with addresses",
      [e for e in admin.egress_list() if e["name"] == "auth.embark.net"][0]["ips"]
      == ["8.8.8.8", "8.8.4.4"] and len(admin.egress_list()) == 2, to)
check("an address that is not public is refused",
      act("egress-add", name="x.example.com", ips="192.168.0.1").startswith("settings?m=!"))
check("so is one that is not an address",
      act("egress-add", name="x.example.com", ips="hello").startswith("settings?m=!"))
check("so is a name that is not a name", act("egress-add", name="not a/ name?").startswith("settings?m=")
      and "x.example.com" not in [e["name"] for e in admin.egress_list()])
act("egress-preset", i="0")
names = [e["name"] for e in admin.egress_list()]
check("the PlayStation list is one tap, once",
      all(n in names for n in admin.EGRESS_PRESETS[0][1]) and len(names) == 6, names)
act("egress-preset", i="0")
check("and again adds nothing", len(admin.egress_list()) == 6)
act("egress-del", name="es-dis.net")
check("a name is taken off", "es-dis.net" not in [e["name"] for e in admin.egress_list()])
check("a preset that is not there is refused", act("egress-preset", i="9").startswith("settings?m=!"))
RELAY = "198.51.100.2"
admin.relay_list = lambda: [RELAY]
admin.is_single_server = lambda ip: False
admin.one_server = lambda: False
admin.STORE.run("INSERT INTO settings (key, value) VALUES (?, ?)",
                ("local_egress_state:" + RELAY, json.dumps({"ps5.np.playstation.net": [3, 8]})))
card = admin.egress_card("p")
check("the card shows each name with how many addresses answered",
      "3 آدرس سالم از 8" in card and "auth.embark.net" in card and "egress-add" in card, card[:200])
check("and is on the settings page", "egress_card(p)" in read("templates/smartdns-admin"))

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
