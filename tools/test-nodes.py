#!/usr/bin/env python3
"""Other exits joined to one panel - nodes.

What has to hold: the installer makes a node an exit without a panel, with
its own sync to the panel and the rule that keeps nginx off itself; the panel
lets only its relays and nodes into the API, answers a node with the relays
to let in and the resolvers to ask, and keeps its health and logs; each relay
is told its exits in order - the admin's pick first, the rest as nginx's
fallbacks, the tunnel just before the exit it goes to; the node's sync lets
in exactly the panel's relays and never none; and the admin panel adds and
takes off nodes and picks each relay's exit.
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


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


tmp = tempfile.mkdtemp()
logic = read("tools/installer-logic.sh")
bash = shutil.which("bash")

print("the installer")
check("node is a fourth answer, and an exit", "4|node)   ROLE=node;   break ;;" in logic
      and 'is_exit()  { [ "$ROLE" = exit ] || [ "$ROLE" = single ] || [ "$ROLE" = node ]; }' in logic)
check("it gets no panel, no tunnel of its own, no HTTPS question",
      'if is_exit && [ "$ROLE" != node ] && [ -z "$JOINED" ]; then\n    step "Panel: database and sync API"' in logic
      and '{ [ "$ROLE" = single ] || [ "$ROLE" = node ]; } && TUNNEL=off' in logic
      and '[ -z "$UPGRADE" ] && [ "$ROLE" != node ]; then\n    printf \'\\n%sHTTPS' in logic)
check("it needs the pairing token, and keeps its pairing on an upgrade",
      "a node needs the pairing token" in logic
      and "elif [ -f /etc/smart-dns/node.env ]; then ROLE=node" in logic)
check("its sync runs from node.env with --node, and is checked",
      "cat > /etc/smart-dns/node.env <<EOF" in logic
      and "install_payload NODE_SERVICE /etc/systemd/system/smartdns-node.service" in logic
      and '"the panel\'s API answers this node"' in logic
      and '("NODE_SERVICE", "templates/smartdns-node.service")' in read("tools/build-installer.py"))
unit = read("templates/smartdns-node.service")
check("  as root, with the rule that keeps nginx off this machine first",
      "ExecStart=/usr/local/bin/smartdns-sync --node" in unit
      and "ExecStartPre=-+/usr/local/bin/smartdns-api-guard" in unit)
if bash:
    body = logic[logic.index("relay_allows() {"):logic.index("\n}\n", logic.index("relay_allows() {")) + 3]
    valid = logic[logic.index("valid_ip() {"):logic.index("\n}\n", logic.index("valid_ip() {")) + 3]
    conf = os.path.join(tmp, "relays.conf")
    with open(conf, "w", newline="\n") as fh:
        fh.write("# header\nallow 198.51.100.1;\nallow 198.51.100.2;\n")
    script = (valid + body.replace("/etc/smart-dns/panel.env", "/nonexistent/panel.env")
              + "note_file() { :; }\nRELAYS_CONF=%s\nROLE=node\nRELAY_IP=\nrelay_allows\n" % posix(conf))
    r = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    check("an upgrade keeps the relays the panel last gave the node",
          re.findall(r"allow (\S+);", open(conf).read()) == ["198.51.100.1", "198.51.100.2"],
          r.stderr + open(conf).read())

print("the API guard")
guard = os.path.join(ROOT, "templates", "smartdns-api-guard")
if bash:
    etc = os.path.join(tmp, "etc")
    os.makedirs(etc)
    with open(os.path.join(etc, "panel.env"), "w", newline="\n") as fh:
        fh.write("RELAY_IP=198.51.100.1\nNODE_IP=93.184.216.20\n")
    out = subprocess.run([bash, posix(guard), "--print"], capture_output=True, text=True,
                         env=dict(os.environ, SMARTDNS_ETC=posix(etc))).stdout
    check("the panel lets its nodes in beside its relays",
          "{ 127.0.0.1, 198.51.100.1, 10.10.184.29, 93.184.216.20 } accept" in out, out)
    out = subprocess.run([bash, posix(guard), "--print"], capture_output=True, text=True,
                         env=dict(os.environ, SMARTDNS_ETC=posix(os.path.join(tmp, "none")))).stdout
    check("a node, with no panel, closes no API port - and keeps nginx off itself",
          "dport 8443" not in out and "fib daddr type local reject" in out, out)

print("the panel")
panel = load("panel", "templates/smartdns-panel")
panel.print = lambda *a, **k: None
panel.log = panel.log_exception = lambda *a, **k: None
store = panel.Store(os.path.join(tmp, "panel.db"))
relays, nodes, main = ("198.51.100.1", "198.51.100.2"), ("93.184.216.20", "203.0.113.30"), "203.0.113.9"
check("a relay goes through this exit first, the nodes after it",
      panel.relay_exits(store, "198.51.100.1", relays, nodes, main)
      == {"main": main, "order": [main, "93.184.216.20", "203.0.113.30"], "capped": []})
store.set_setting("relay_exit:198.51.100.2", "203.0.113.30")
check("  or through the one the admin picked, this exit next",
      panel.relay_exits(store, "198.51.100.2", relays, nodes, main)
      == {"main": main, "order": ["203.0.113.30", main, "93.184.216.20"], "capped": []})
store.set_setting("relay_exit:198.51.100.2", "192.0.2.99")
check("  a pick since taken off is this exit again",
      panel.relay_exits(store, "198.51.100.2", relays, nodes, main)["order"][0] == main)
check("  nothing for a stranger, or without this exit's own address",
      panel.relay_exits(store, "192.0.2.7", relays, nodes, main) is None
      and panel.relay_exits(store, "198.51.100.1", relays, nodes, "") is None)


class Api:
    relays = relays
    nodes = nodes

    def __init__(self):
        self.store = store
        self.client_address = ("93.184.216.20", 5555)


Api.do_node = panel.API.do_node
api = Api()
store.set_setting("dns_upstream", "8.8.8.8 1.1.1.1")
code, reply = api.do_node({"node": "93.184.216.20", "host": {"cpu": 12.5},
                           "logs": "node log", "nginx_logs": "err",
                           "resolvers": {"using": "8.8.8.8 1.1.1.1", "error": ""}})
check("a node is told the relays to let in, the resolvers to ask and to time",
      code == 200 and reply == {"relays": list(relays), "upstream": ["8.8.8.8", "1.1.1.1"],
                                "bench": panel.bench_wanted(store),
                                "tunnels": {}, "upgrade": None, "upgrade_ack": False,
                                "panel": {"host": panel.exit_self(), "standby": ""},
                                "standby": None}, reply)
check("  and its health, logs and resolvers are kept",
      store.one("SELECT cpu FROM metrics WHERE host = '93.184.216.20'")["cpu"] == 12.5
      and store.one("SELECT text FROM relay_logs WHERE relay = '93.184.216.20'")["text"] == "node log"
      and json.loads(store.setting("node_resolvers:93.184.216.20"))["using"] == "8.8.8.8 1.1.1.1")
api.client_address = ("198.51.100.1", 5555)
check("a relay, or anyone else, is not a node", api.do_node({"node": "198.51.100.1"})[0] == 403)
src = read("templates/smartdns-panel")
check("only relays, nodes and loopback reach the API at all",
      "and self.client_address[0] not in self.nodes \\" in src
      and 'API.nodes = tuple(x.strip() for x in (cfg.get("NODE_IP") or "").split(",")' in src)
check("every relay is told its exits with every sync", '"exits": relay_exits(' in src)

print("the relay's way to the exits")
sync = load("sync", "templates/smartdns-sync")
sync.log = sync.log_exception = lambda *a, **k: None
text = sync.exit_conf_text(main, True, [main, "93.184.216.20"])
check("this exit first: the tunnel, this exit, then the node",
      "server 127.0.0.1:18443;\n    server 203.0.113.9:443 backup;\n"
      "    server 93.184.216.20:443 backup;" in text, text)
text = sync.exit_conf_text(main, True, ["93.184.216.20", main])
check("  a node first: the node, then the tunnel and this exit",
      "server 93.184.216.20:443;\n    server 127.0.0.1:18443 backup;\n"
      "    server 203.0.113.9:443 backup;" in text, text)
check("  and with one exit and no order, exactly as before",
      sync.exit_conf_text(main, False) == sync.exit_conf_text(main, False, [main]))
ran = []


class R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


nginx_ok = [True]


def fake_sh(*args):
    ran.append(args)
    if args[:2] == ("nginx", "-t"):
        return R(0 if nginx_ok[0] else 1)
    return R(0)


sync.sh = fake_sh
sync.CFG = {"TUNNEL": "off", "PANEL_HOST": main}
sync.EXIT_CONF = os.path.join(tmp, "smartdns-exit.conf")
sync.PANEL_ENV_HERE = os.path.join(tmp, "no-panel.env")
sync.apply_exits({"main": main, "order": ["93.184.216.20", main]})
check("the relay writes the order the panel sent",
      open(sync.EXIT_CONF).read() == sync.exit_conf_text(main, False, ["93.184.216.20", main])
      and ("systemctl", "reload", "nginx") in ran)
ran.clear()
sync.apply_exits({"main": main, "order": ["93.184.216.20", main]})
check("  the same again touches nothing", not ran)
sync.apply_exits({"main": "bad", "order": ["x"]})
sync.apply_exits(None)
check("  and nonsense, or an older panel's silence, changes nothing",
      open(sync.EXIT_CONF).read() == sync.exit_conf_text(main, False, ["93.184.216.20", main]))

print("each customer's exit")
store.run("INSERT INTO users (telegram_id, username, status, exit, created_at) VALUES"
          " (1, 'ali', 'active', '93.184.216.20', '2026-01-01')")
store.run("INSERT INTO users (telegram_id, username, status, exit, created_at) VALUES"
          " (2, 'reza', 'active', '192.0.2.99', '2026-01-01')")
store.run("INSERT INTO users (telegram_id, username, status, created_at) VALUES"
          " (3, 'sara', 'active', '2026-01-01')")
for uid, ip in ((1, "198.18.0.1"), (1, "198.18.0.2"), (2, "198.18.0.3"), (3, "198.18.0.4")):
    store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, '2026-01-01')", (uid, ip))
check("the panel sends the addresses of customers sent through an exit it still has",
      panel.customer_exits(store, [main, "93.184.216.20"])
      == {"198.18.0.1": "93.184.216.20", "198.18.0.2": "93.184.216.20"})
check("  with every sync, for the relay asking", '"customer_exits": customer_exits(' in src
      and "list(self.nodes), who)," in src)
store.run("UPDATE users SET relay_exits = ? WHERE username = 'sara'",
          (json.dumps({"198.51.100.2": "93.184.216.20"}),))
store.run("UPDATE users SET relay_exits = ? WHERE username = 'ali'",
          (json.dumps({"198.51.100.2": main}),))
check("a customer's exit on one relay, and the one for all on the others",
      panel.customer_exits(store, [main, "93.184.216.20"], "198.51.100.2")
      == {"198.18.0.1": main, "198.18.0.2": main, "198.18.0.4": "93.184.216.20"}
      and panel.customer_exits(store, [main, "93.184.216.20"], "198.51.100.1")
      == {"198.18.0.1": "93.184.216.20", "198.18.0.2": "93.184.216.20"})
order = [main, "93.184.216.20"]
text = sync.exit_conf_text(main, True, order)
check("the relay has upstreams for each exit that put it first, the rest after",
      "upstream to_ip_93_184_216_20_https {\n    server 93.184.216.20:443;\n"
      "    server 127.0.0.1:18443 backup;\n    server 203.0.113.9:443 backup;\n}" in text
      and "upstream to_ip_203_0_113_9_https {\n    server 127.0.0.1:18443;\n" in text, text)
check("  and with one exit, only its own, as before",
      "to_ip_" not in sync.exit_conf_text(main, True, [main]))
sync.CUSTOMER_EXITS = os.path.join(tmp, "customer-exits.map")
sync.EXIT_PLAN.update(main=main, order=order)
sync.apply_customer_exits({"198.18.0.1": "93.184.216.20", "198.18.0.9": main,
                           "bad": "93.184.216.20", "198.18.0.5": "192.0.2.1"})
check("  the relay sends those addresses through it, and no others",
      re.findall(r"^(\S+) (\S+);$", open(sync.CUSTOMER_EXITS).read(), re.M)
      == [("198.18.0.1", "to_ip_93_184_216_20")])
conf = read("templates/relay-nginx.conf")
check("  by the customer's address, this relay's own exit for everybody else",
      "map $remote_addr $to_exit {\n        include /etc/nginx/smartdns-customer-exits.map;\n"
      "        default to_exit;" in conf and "proxy_pass ${to_exit}_https;" in conf
      and "default       ${to_exit}_https;" in conf)
check("  the installer leaves the list empty, never naming upstreams it did not write",
      'CUSTOMER_EXITS=/etc/nginx/smartdns-customer-exits.map' in logic
      and '> "$CUSTOMER_EXITS"' in logic)

print("the node's sync")
sync.RELAYS_CONF = os.path.join(tmp, "node-relays.conf")
ran.clear()
sync.apply_node_relays(["198.51.100.1", "bad", "198.51.100.2", "198.51.100.1"])
check("it lets in exactly the panel's relays, checked by nginx",
      re.findall(r"allow (\S+);", open(sync.RELAYS_CONF).read()) == ["198.51.100.1", "198.51.100.2"]
      and ("nginx", "-t") in ran)
sync.apply_node_relays([])
sync.apply_node_relays(None)
check("  and never none - an empty answer would shut every relay out",
      open(sync.RELAYS_CONF).read().count("allow ") == 2)
nginx_ok[0] = False
sync.apply_node_relays(["198.51.100.3"])
check("  nginx refusing puts the old list back",
      "198.51.100.3" not in open(sync.RELAYS_CONF).read())
nginx_ok[0] = True
check("  in the installer's own words", sync.relays_conf_text(["1.2.3.4"]).splitlines()[0]
      == "# The relays this exit lets in: written by the installer and the admin panel.")
sync.NGINX_CONF = os.path.join(tmp, "nginx.conf")
sync.UPSTREAM_FILE = os.path.join(tmp, "upstream")
with open(sync.NGINX_CONF, "w") as fh:
    fh.write("stream {\n    resolver 1.1.1.1 9.9.9.9 ipv6=off;\n}\n")
sync.dns_probe = lambda ip, **k: None if ip == "9.9.9.9" else 5
sync.apply_node_resolvers(["8.8.8.8", "9.9.9.9"])
check("nginx asks the panel's resolvers - those that answer from here",
      "resolver 8.8.8.8 ipv6=off;" in open(sync.NGINX_CONF).read()
      and open(sync.UPSTREAM_FILE).read() == "8.8.8.8\n"
      and sync.NODE_STATE["resolvers"] == {"using": "8.8.8.8", "error": ""})
sync.dns_probe = lambda ip, **k: None
sync.apply_node_resolvers(["4.4.4.4"])
check("  none answering changes nothing, and says so",
      "resolver 8.8.8.8 ipv6=off;" in open(sync.NGINX_CONF).read()
      and sync.NODE_STATE["resolvers"]["error"])
check("--node runs from node.env", 'if "--node" in sys.argv[1:]:\n        CONFIG = NODE_CONFIG'
      in read("templates/smartdns-sync"))

print("the admin panel")
admin = load("admin", "templates/smartdns-admin")
admin.log = lambda *a, **k: None
admin.PANEL_ENV = os.path.join(tmp, "admin-panel.env")
admin.INSTALL_STATE = os.path.join(tmp, "install-state")
admin.SYNC_CRT = os.path.join(tmp, "none.crt")
admin.CFG = {"ADMIN_PATH": "p"}
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=198.51.100.1,198.51.100.2\n")
with open(admin.INSTALL_STATE, "w") as fh:
    fh.write("exit-ip 203.0.113.9\n")
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
restarts = []
admin.systemctl = lambda *a: restarts.append(a)


class Rec:
    def redirect(self, where, headers=None):
        self.to = where


Rec.one = admin.Admin.__dict__["one"]


def act(what, **form):
    r = Rec()
    admin.Admin.action(r, what, {k: [v] for k, v in form.items()})
    return r.to


for bad in ("", "10.0.0.5", "203.0.113.9", "198.51.100.1"):
    if not act("node-add", ip=bad).startswith("nodes?m=!"):
        check("refuses %r" % bad, False)
check("not an address, a private one, this exit, or a relay", admin.node_list() == [])
act("node-add", ip="93.184.216.20")
check("a node is added: panel.env, and the panel restarted to let it in",
      admin.node_list() == ["93.184.216.20"] and ("restart", "smartdns-panel") in restarts
      and "RELAY_IP=198.51.100.1,198.51.100.2" in open(admin.PANEL_ENV).read())
check("a relay's exit can be picked only among the exits",
      act("relay-exit", ip="198.51.100.2", exit="192.0.2.1").startswith("nodes?m=!"))
act("relay-exit", ip="198.51.100.2", exit="93.184.216.20")
check("  and is kept", admin.relay_exit("198.51.100.2") == "93.184.216.20"
      and admin.relay_exit("198.51.100.1") == "203.0.113.9")
card = admin.nodes_card("p")
check("the Node page lists this exit and the node, and which relays go where",
      "این سرور — پنل" in card and "93.184.216.20" in card and "198.51.100.2" in card
      and "action='/p/node-add'" in card)
check("  and the relays card a column to pick with", "action='/p/relay-exit'" in admin.relays_card("p"))
admin.STORE.run("INSERT INTO users (telegram_id, username, status, created_at) VALUES"
                " (9, 'nima', 'active', '2026-01-01')")
nima = admin.STORE.one("SELECT id FROM users WHERE username = 'nima'")["id"]
urow = lambda: admin.STORE.one("SELECT * FROM users WHERE id = ?", (nima,))
check("a customer's exit can be picked only among the exits",
      act("user-exit", id=str(nima), **{"r_198.51.100.1": "192.0.2.1"}).startswith("users?m=!"))
act("user-exit", id=str(nima), **{"r_198.51.100.1": "93.184.216.20",
                                  "r_198.51.100.2": "93.184.216.20"})
check("  the same on every relay is kept as the customer's one exit",
      urow()["exit"] == "93.184.216.20" and urow()["relay_exits"] is None)
act("user-exit", id=str(nima), **{"r_198.51.100.1": "", "r_198.51.100.2": "93.184.216.20"})
check("  and different ones, relay by relay",
      urow()["exit"] is None and json.loads(urow()["relay_exits"]) == {"198.51.100.2": "93.184.216.20"})
cell = admin.user_exit_cell("p", urow(), ["198.51.100.1", "198.51.100.2"])
check("  shown in the users table: each relay's own, and a box for all of them",
      "name='r_198.51.100.2'" in cell and "93.184.216.20' selected" in cell
      and "همهٔ رله‌ها" in cell and "93.184.216.20 ← 198.51.100.2" in cell, cell)
act("user-exit", id=str(nima), **{"r_198.51.100.1": "", "r_198.51.100.2": ""})
check("  and back to each relay's own", urow()["exit"] is None and urow()["relay_exits"] is None)
admin.STORE.run("INSERT OR IGNORE INTO templates (name, is_default, created_at)"
                " VALUES ('default', 1, '2026-01-01')")
tid = admin.STORE.one("SELECT id FROM templates ORDER BY id LIMIT 1")["id"]
check("a plan's server abroad can be only one of them",
      act("plan-save", name="x", template_id=str(tid), days="30", quota_gb="10", price="1",
          **{"re_198.51.100.1": "off", "re_198.51.100.2": "192.0.2.1"}).startswith("plans?m=!"))
act("plan-save", name="France", template_id=str(tid), days="30", quota_gb="10", price="1",
    **{"re_198.51.100.1": "93.184.216.20", "re_198.51.100.2": "93.184.216.20"})
act("plan-save", name="All", template_id=str(tid), days="30", quota_gb="10", price="1",
    **{"re_198.51.100.1": "auto", "re_198.51.100.2": "auto"})
fr = admin.STORE.one("SELECT * FROM plans WHERE name = 'France'")
every = admin.STORE.one("SELECT * FROM plans WHERE name = 'All'")
check("  one on an exit from every relay is put on it; one for all, none",
      fr["exit"] == "93.184.216.20" and fr["relays"] is None
      and every["exit"] is None and every["relays"] is None)
admin.STORE.run("UPDATE users SET exit = NULL, relay_exits = ? WHERE id = ?",
                (json.dumps({"198.51.100.1": main}), nima))
admin.STORE.apply_plan(nima, every["id"])
check("a plan sold on all of them leaves the customer's exit as it was",
      urow()["exit"] is None and urow()["relay_exits"] is not None)
admin.STORE.run("UPDATE users SET expires_at = NULL WHERE id = ?", (nima,))
admin.STORE.apply_plan(nima, fr["id"])
check("  one sold on an exit puts them on it, on every relay",
      urow()["exit"] == "93.184.216.20" and urow()["relay_exits"] is None)
admin.STORE.run("UPDATE users SET exit = NULL, expires_at = NULL WHERE id = ?", (nima,))
panel.apply_plan(store, nima, fr["id"])
check("  the same when the panel gives it - a receipt, a trial, the bot",
      store.one("SELECT exit FROM users WHERE id = ?", (nima,))["exit"] == "93.184.216.20")
page = admin.Admin.plans(Rec())
check("the plans page has a line per server, with the exits",
      "<th>سرورها</th>" in page and "name='re_198.51.100.2'" in page
      and "<option value='93.184.216.20' selected>" in page)
act("user-exit", id=str(nima), **{"r_198.51.100.1": "93.184.216.20", "r_198.51.100.2": ""})
# What a node leaves on the home page once taken off: its samples, and an
# alert about it that nothing would close again.
for host in ("exit", "198.51.100.1", "93.184.216.20", "192.0.2.77"):
    admin.STORE.run("INSERT INTO metrics (host, at, cpu) VALUES (?, ?, 1)", (host, panel.now()))
for key in ("silent:93.184.216.20", "disk:93.184.216.20", "cap80:93.184.216.20",
            "exit:198.51.100.1:93.184.216.20", "silent:198.51.100.1"):
    admin.STORE.run("INSERT OR REPLACE INTO settings (key, value) VALUES (?, 'down')",
                    ("alert_state:" + key,))
act("node-del", ip="93.184.216.20")
check("  a node taken off leaves no samples behind",
      not admin.STORE.q("SELECT 1 FROM metrics WHERE host = '93.184.216.20'")
      and admin.STORE.q("SELECT 1 FROM metrics WHERE host = '198.51.100.1'"))
open_alerts = {r["key"] for r in admin.STORE.q(
    "SELECT key FROM settings WHERE key LIKE 'alert_state:%' AND value = 'down'")}
check("  nor an open alert about it, and the others' stay",
      not any("93.184.216.20" in k for k in open_alerts)
      and "alert_state:silent:198.51.100.1" in open_alerts)
check("  the home page's servers are the ones the panel has now, not a stranger's samples",
      admin.current_hosts() == {"exit", "198.51.100.1", "198.51.100.2"}
      and "if r[\"host\"] in hosts]" in read("templates/smartdns-admin"))
admin.STORE.run("INSERT OR REPLACE INTO settings (key, value) VALUES"
                " ('alert_state:silent:192.0.2.77', 'down')")
admin.STORE.run("INSERT INTO alerts (key, ok, text, at) VALUES ('silent:192.0.2.77', 0,"
                " 'gone-one', ?)", (panel.now(),))
admin.STORE.run("INSERT INTO alerts (key, ok, text, at) VALUES ('silent:198.51.100.1', 0,"
                " 'still-here', ?)", (panel.now(),))
card = admin.alerts_card()
check("  an alert left open about a server taken off before is not shown",
      "gone-one" not in card.split("<details>")[0] and "still-here" in card)
admin.STORE.run("DELETE FROM metrics")
admin.STORE.run("DELETE FROM settings WHERE key LIKE 'alert_state:%'")
admin.STORE.run("DELETE FROM alerts")
check("  a node taken off sends its customers back to their relays' own",
      urow()["exit"] is None and urow()["relay_exits"] is None)
check("  and its plans are sold on all of them",
      admin.STORE.one("SELECT exit FROM plans WHERE name = 'France'")["exit"] is None
      and "<th>سرور خارج</th>" not in admin.Admin.plans(Rec()))
check("a node taken off: its relays back to this exit",
      admin.node_list() == [] and admin.relay_exit("198.51.100.2") == "203.0.113.9"
      and "relay-exit" not in admin.relays_card("p"))

print("usage by server")
sync.EXIT_PLAN.update(main=main, order=[main, "93.184.216.20"])
check("the exit a connection left by: an exit's address, the tunnel's end for the main one,"
      " nothing for what stayed here",
      sync.exit_of("93.184.216.20:443") == "93.184.216.20"
      and sync.exit_of("127.0.0.1:18443") == main
      and sync.exit_of("127.0.0.1:8453") is None and sync.exit_of("-") is None)
sync.USAGE_LOG = os.path.join(tmp, "usage")
sync.USAGE_TAKEN = sync.USAGE_LOG + ".taken"
with open(sync.USAGE_TAKEN, "w") as fh:
    fh.write("198.18.0.1 443 a.example 100 900 127.0.0.1:18443\n"
             "198.18.0.1 443 b.example 50 50 93.184.216.20:443\n"
             "198.18.0.1 443 c.example 10 10 93.184.216.20:443, 203.0.113.9:443\n"
             "198.18.0.2 853 - 5 5 127.0.0.1:8054\n"
             "198.18.0.3 443 old.example 1 1\n")
sync.USAGE_PENDING.clear()
sync.EXIT_PENDING.clear()
services = sync.take_usage()
check("the relay adds up each customer's bytes by the exit that carried them, up and down",
      sync.EXIT_PENDING == {"198.18.0.1": {main: [910, 110], "93.184.216.20": [50, 50]}},
      sync.EXIT_PENDING)
check("  and still by name, old lines too", services["198.18.0.1"]["a.example"] == 1000
      and services["198.18.0.3"]["old.example"] == 2)
check("  and sends them with its sync", 'payload["exit_usage"]' in read("templates/smartdns-sync")
      and "$bytes_received $upstream_addr';" in read("templates/relay-nginx.conf"))
panel.record_exit_usage(store, {"198.18.0.1": {main: [910, 110], "93.184.216.20": [50, 50]},
                                "10.9.9.9": {main: [5, 5]}, "198.18.0.2": {"nonsense": [7, 7]}})
uid = store.one("SELECT user_id FROM ips WHERE ip = '198.18.0.1'")["user_id"]
check("the panel keeps it per customer, per exit, per day",
      {r["server"]: (r["up"], r["down"]) for r in store.q(
          "SELECT server, up, down FROM user_server_usage WHERE kind = 'exit'")}
      == {main: (910, 110), "93.184.216.20": (50, 50)})
check("  and per exit in every grain, for its charts",
      {r["grain"] for r in store.q("SELECT grain FROM server_usage WHERE kind = 'exit'"
                                   " AND server = ?", (main,))} == {"5m", "1h", "1d"})
store.fold_counters("198.51.100.1", {"198.18.0.1": 1000})
store.fold_counters("198.51.100.1", {"198.18.0.1": 1500})
store.fold_counters("198.51.100.2", {"198.18.0.1": 300})
check("  and what each relay counted of it, as the bill is",
      {r["server"]: r["up"] + r["down"] for r in store.q(
          "SELECT server, up, down FROM user_server_usage WHERE kind = 'relay' AND user_id = ?",
          (uid,))} == {"198.51.100.1": 1500, "198.51.100.2": 300})
check("  kept as long as the customers' own figures",
      "DELETE FROM server_usage WHERE grain = ? AND bucket < ?" in src
      and "DELETE FROM user_server_usage WHERE day < ?" in src)
admin.STORE = admin.Store(os.path.join(tmp, "panel.db"))
check("the admin panel adds it up by server, today and thirty days",
      admin.server_totals("exit") == {main: (1020, 1020), "93.184.216.20": (100, 100)}
      and admin.server_totals("relay") == {"198.51.100.1": (1500, 1500),
                                           "198.51.100.2": (300, 300)})
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1,198.51.100.2\nNODE_IP=93.184.216.20\n")
with open(admin.INSTALL_STATE, "w") as fh:
    fh.write("exit-ip %s\n" % main)
card = admin.servers_card()
check("  the Node page: every server, what went through it, how it is, and a link to it",
      "ترافیک و آمار سرورها" in card and "server?k=exit&amp;h=93.184.216.20" in card
      and "server?k=relay&amp;h=198.51.100.2" in card and "هنوز آماری دریافت نشده" in card)
card = admin.user_servers_card(uid)
check("  and the customer's page: theirs, by relay and by exit, each a link",
      "ترافیک به تفکیک سرور" in card and "server?k=relay&amp;h=198.51.100.1" in card
      and "server?k=exit&amp;h=%s" % main in card and "۷ روز" in card)


def page_of(query):
    r = Rec()
    r.path = "/p/server?" + query
    return admin.Admin.server_page(r)


page = page_of("k=exit&h=93.184.216.20")
check("a server's own page: its charts, like the home page's, and its customers",
      "مصرف — 93.184.216.20" in page and "روزانه" in page and "پرمصرف‌ترین‌ها" in page
      and "usage?u=%d" % uid in page, page[:300])
check("  a relay's too", "مصرف — 198.51.100.1" in page_of("k=relay&h=198.51.100.1"))
check("  and nothing for a server that is not ours",
      "پیدا نشد" in page_of("k=exit&h=192.0.2.1") and "پیدا نشد" in page_of("k=relay&h=" + main))
check("the home page still draws everybody's the same way",
      "مصرف کل — همهٔ مشتری‌ها" in admin.total_usage_card("p"))

print("alerts")
text = sync.exit_conf_text(main, False, [main, "93.184.216.20"], {main})
check("an exit that stopped answering goes last, everywhere, still a fallback",
      "upstream to_exit_https {\n    server 93.184.216.20:443;\n    server 203.0.113.9:443 backup;"
      in text and "upstream to_ip_203_0_113_9_https {\n    server 93.184.216.20:443;\n" in text,
      text)
sync.EXIT_PLAN.update(main=main, order=[main, "93.184.216.20"])
sync.CFG = {"TUNNEL": "off", "PANEL_HOST": main}
answers = {"203.0.113.9": None, "93.184.216.20": 12}
sync.exit_answers = lambda addr, port, timeout=5.0: answers.get(addr)
sync.EXIT_HEALTH.clear()
sync.probe_exits()
check("  one miss moves nothing", not sync.EXIT_HEALTH[main]["down"])
sync.probe_exits()
check("  two in a row, and it is down", sync.EXIT_HEALTH[main]["down"]
      and not sync.EXIT_HEALTH["93.184.216.20"]["down"])
answers[main] = 30
sync.probe_exits()
check("  one answer is not yet back", sync.EXIT_HEALTH[main]["down"])
sync.probe_exits()
check("  two, and it is", not sync.EXIT_HEALTH[main]["down"] and sync.EXIT_HEALTH[main]["ms"] == 30)
sync.CFG = {"TUNNEL": "backpack", "PANEL_HOST": main}
tried = []
sync.exit_answers = lambda addr, port, timeout=5.0: (tried.append((addr, port)), None)[1]
sync.probe_exits()
check("  the main exit is tried through the tunnel first, then straight",
      ("127.0.0.1", 18443) in tried and (main, 443) in tried
      and ("127.0.0.1", 18443) not in [t for t in tried if t[0] == "93.184.216.20"])
check("  and the relay tells the panel", 'payload["exit_health"]' in read("templates/smartdns-sync"))
store.run("DELETE FROM alerts")
events = []
panel.emit_admin = lambda st, event, data: events.append((event, data["text"]))
panel.record_exit_health(store, "198.51.100.1", {main: {"ok": True, "ms": 10}})
check("the panel says nothing of an exit first seen well", not events)
panel.record_exit_health(store, "198.51.100.1", {main: {"ok": False, "ms": None}})
panel.record_exit_health(store, "198.51.100.1", {main: {"ok": False, "ms": None}})
check("  one alert when it goes down, to the bot as well",
      len(events) == 1 and events[0][0] == "server.down" and main in events[0][1]
      and "198.51.100.1" in events[0][1]
      and store.one("SELECT count(*) c FROM alerts")["c"] == 1)
panel.record_exit_health(store, "198.51.100.1", {main: {"ok": True, "ms": 10}})
check("  and one when it is back", len(events) == 2 and events[1][0] == "server.up")
store.run("INSERT INTO metrics (host, at) VALUES ('198.51.100.2', ?)",
          ((panel.datetime.now(panel.timezone.utc) - panel.timedelta(minutes=10))
           .isoformat(timespec="seconds"),))
panel.check_servers(store, ("198.51.100.2", "198.51.100.77"), ())
check("a relay quiet for minutes is an alert; one never installed is not",
      any("198.51.100.2" in t and "گزارش نداده" in t for _, t in events)
      and not any("198.51.100.77" in t for _, t in events))
card = admin.alerts_card()
check("the admin panel shows what is wrong now, and what changed",
      "اعلان‌های سیستم" in card and "198.51.100.2" in card and "رویدادهای اخیر" in card)
store.run("DELETE FROM settings WHERE key LIKE 'alert_state:%'")
check("  on the home page only when something is wrong",
      admin.alerts_card(always=False) == "" and "همهٔ سرورها سالم‌اند" in admin.alerts_card())

print("tunnels to the nodes")
store.set_setting("relay_tunnel:198.51.100.1:93.184.216.20", json.dumps(
    {"transport": "stealth", "direction": "reverse", "port": 9555, "exit": "93.184.216.20",
     "slot": 1}))
store.set_setting("relay_tunnel:198.51.100.2:93.184.216.30", json.dumps(
    {"transport": "tcp", "direction": "direct", "port": 9666, "exit": "93.184.216.30", "slot": 2}))
check("the panel knows each relay's tunnels to the nodes, and each node's",
      set(panel.node_tunnel_specs(store)) == {("198.51.100.1", "93.184.216.20"),
                                              ("198.51.100.2", "93.184.216.30")}
      and list(panel.node_tunnel_specs(store, relay="198.51.100.1")) == [("198.51.100.1",
                                                                        "93.184.216.20")]
      and list(panel.node_tunnel_specs(store, node="93.184.216.30")) == [("198.51.100.2",
                                                                        "93.184.216.30")])
Api.relays, Api.nodes = relays, ("93.184.216.20", "93.184.216.30")
api.client_address = ("93.184.216.20", 5555)
code, reply = api.do_node({"node": "93.184.216.20", "tunnel_state": {"198.51.100.1": True}})
check("a node is told its ends of the relays' tunnels, and says how they run",
      reply["tunnels"] == {"198.51.100.1": {"transport": "stealth", "direction": "reverse",
                                           "port": 9555, "exit": "93.184.216.20", "slot": 1}}
      and store.setting("node_tunnel_state:93.184.216.20:198.51.100.1") == "1", reply)
check("  and a relay its own, by node, with every sync",
      '"node_tunnels": {' in src and "node_tunnel_specs(\n" in src)

text = sync.exit_conf_text(main, True, [main, "93.184.216.20"], (), {"93.184.216.20": 20010})
check("the relay goes to a node through the tunnel to it first, the node itself next",
      "upstream to_ip_93_184_216_20_https {\n    server 127.0.0.1:20010;\n"
      "    server 93.184.216.20:443 backup;\n    server 127.0.0.1:18443 backup;" in text
      and "upstream to_ip_93_184_216_20_blizzard {\n    server 127.0.0.1:20013;" in text, text)
sync.RELAY_TUNNEL_DIR = os.path.join(tmp, "relay-tunnels")
sync.BACKPACK_BIN = sys.executable
sync.TUNNEL_INSTANCE_FILE = os.path.join(tmp, "unit")
open(sync.TUNNEL_INSTANCE_FILE, "w").close()
sync.TUNNEL_DIR = os.path.join(tmp, "tunnel")
sync.instance_nft = lambda ip: (os.path.join(tmp, "41-%s.conf" % ip), "t_" + ip.replace(".", "_"))
sync.PANEL_ENV_HERE = os.path.join(tmp, "no-panel.env")
sync.CFG = {"SYNC_SECRET": "s3cret", "TUNNEL": "off", "PANEL_HOST": main}
ran.clear()
sync.apply_node_tunnels({"93.184.216.20": {"on": True, "transport": "stealth",
                                           "direction": "reverse", "port": 9555, "slot": 1},
                         "93.184.216.30": {"on": True, "transport": "kcp",
                                           "direction": "direct", "port": 9666, "slot": 2}})
toml = open(os.path.join(sync.RELAY_TUNNEL_DIR, "93.184.216.20", "tunnel.toml")).read()
check("the relay runs one BackPack per node, on that node's own ports",
      '"127.0.0.1:20010=443", "127.0.0.1:20011=80", "127.0.0.1:20012=4070", "127.0.0.1:20013=1119"'
      in toml and "8443" not in toml and 'bind_addr = "0.0.0.0:9555"' in toml
      and ("systemctl", "restart", "smartdns-tunnel@93.184.216.20") in ran, toml)
check("  its port answering that node only, where the relay listens",
      "ip saddr != 93.184.216.20 drop" in open(sync.instance_nft("93.184.216.20")[0]).read())
check("  a direct transport the relay has not got is refused",
      not os.path.exists(os.path.join(sync.RELAY_TUNNEL_DIR, "93.184.216.30")))
check("  and it reports them, and knows its ports", sync.NODE_TUNNELS == {"93.184.216.20": 20010}
      and set(sync.NODE_TUNNEL_REPORT["state"]) == {"93.184.216.20"})
check("  its usage through them is the node's", sync.exit_of("127.0.0.1:20011") == "93.184.216.20"
      and sync.exit_of("127.0.0.1:20099") is None)
sync.apply_node_tunnels({})
check("  and a tunnel taken off here goes", not os.listdir(sync.RELAY_TUNNEL_DIR)
      and ("systemctl", "disable", "--now", "smartdns-tunnel@93.184.216.20") in ran
      and sync.NODE_TUNNELS == {})
sync.apply_relay_tunnels({"198.51.100.1": {"transport": "stealth", "direction": "reverse",
                                           "port": 9555},
                          "198.51.100.2": {"transport": "tcp", "direction": "direct", "port": 9666}})
node_toml = open(os.path.join(sync.RELAY_TUNNEL_DIR, "198.51.100.1", "tunnel.toml")).read()
check("a node dials a reverse relay, on the shared token",
      '[client]\nremote_addr = "198.51.100.1:9555"' in node_toml
      and 'token = "%s"' % sync.tunnel_token("s3cret") in node_toml)
check("  and listens for a direct one, that relay only",
      'addr = "0.0.0.0:9666"' in open(os.path.join(sync.RELAY_TUNNEL_DIR, "198.51.100.2",
                                                   "tunnel.toml")).read()
      and "ip saddr != 198.51.100.2 drop" in open(sync.instance_nft("198.51.100.2")[0]).read()
      and set(sync.NODE_STATE["tunnels"]) == {"198.51.100.1", "198.51.100.2"})

admin.CFG = {"ADMIN_PATH": "p", "ADMIN_PORT": "2053"}
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("SYNC_SECRET=s3cret\nRELAY_IP=198.51.100.1,198.51.100.2\nNODE_IP=93.184.216.20\n")
admin.STORE.run("DELETE FROM settings WHERE key LIKE 'relay_tunnel%' OR key LIKE 'node_slot:%'")
got = act("relay-tunnel", ip="198.51.100.1", exit="93.184.216.20", on="1", direction="reverse",
          transport="stealth", port="9555")
spec = admin.relay_tunnel("198.51.100.1", "93.184.216.20")
check("the admin panel keeps a relay's tunnel to a node, with the node's slot",
      not got.startswith("nodes?m=!") and spec.get("slot") == 1 and spec.get("port") == 9555, got)
check("  a second reverse tunnel may not listen on the same relay port",
      act("relay-tunnel", ip="198.51.100.1", exit=main, on="1", direction="reverse",
          transport="stealth", port="9555").startswith("nodes?m=!"))
check("  nor on the relay's ports for the nodes' tunnels",
      act("relay-tunnel", ip="198.51.100.2", exit="93.184.216.20", on="1", direction="reverse",
          transport="stealth", port="20010").startswith("nodes?m=!"))
cell = admin.tunnel_cell("p", "198.51.100.1")
check("  the relays card shows one line per exit",
      cell.count("action='/p/relay-tunnel'") == 2 and "name='exit' value='93.184.216.20'" in cell)
act("relay-tunnel", ip="198.51.100.1", exit="93.184.216.20", on="0")
check("  and turns one off", admin.relay_tunnel("198.51.100.1", "93.184.216.20") == {})

print("a single server joined to this panel")
check("the installer: ROLE=single with another machine's PANEL_IP is joined to it",
      'if [ -n "${PANEL_IP:-}" ] && [ "$PANEL_IP" != 127.0.0.1 ] && [ "$PANEL_IP" != "$SELF_IP" ]; then\n'
      "        JOINED=1" in logic)
check("  no panel of its own, its sync to the other one's 8443, and it says it is single",
      'if [ -n "$JOINED" ]; then set_env_key /etc/smart-dns/sync.env PANEL_PORT ""' in logic
      and "set_env_key /etc/smart-dns/sync.env SINGLE 1" in logic
      and 'if [ -n "$JOINED" ]; then api_at="$PANEL_IP"; api_port=8443' in logic)
check("  an upgrade finds it again, without its state file too",
      "elif grep -qx 'SINGLE=1' /etc/smart-dns/sync.env 2>/dev/null; then ROLE=single" in logic)
sync.PANEL_ENV_HERE = os.path.join(tmp, "no-panel.env")
sync.CFG = {"SINGLE": "1", "PANEL_HOST": main, "SYNC_SECRET": "s3cret"}
check("its sync knows it is single, with no panel here", sync.is_single())
ran.clear()
sync.apply_exits({"main": main, "order": [main, "93.184.216.20"]})
sync.apply_node_tunnels({"93.184.216.20": {"on": True, "transport": "stealth",
                                           "direction": "reverse", "port": 9555, "slot": 1}})
check("  and takes no exits and no tunnels - it is its own exit", not ran)
sync.CFG = {"PANEL_HOST": main}
check("  a relay is not single", not sync.is_single())
store.set_setting("single:198.51.100.2", "1")
check("the panel offers a single server no exits and no tunnel",
      panel.relay_exits(store, "198.51.100.2", relays, nodes, main) is None
      and panel.relay_tunnel(store, "198.51.100.2", relays, "off") is None
      and panel.relay_exits(store, "198.51.100.1", relays, nodes, main) is not None)
check("  from what its sync says of itself", '"single": is_single(),' in read("templates/smartdns-sync")
      and 'self.store.set_setting("single:" + who, single)' in src)
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('single:198.51.100.2', '1')"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value")
card, relays_only = admin.singles_card("p"), admin.relays_card("p")
check("the Node page lists single servers apart, with how to join one",
      "198.51.100.2" in card and "action='/p/single-add'" in card
      and "198.51.100.2" not in relays_only and "198.51.100.1" in relays_only
      and "sudo env ROLE=single " in read("templates/smartdns-admin"))
admin.set_relays = lambda ips: (open(admin.PANEL_ENV, "w").write(
    "SYNC_SECRET=s3cret\nRELAY_IP=%s\nNODE_IP=93.184.216.20\n" % ",".join(ips)), "")[1]
check("  one is added from its own form, marked single before it has said so",
      act("single-add", ip="93.184.216.40").startswith("nodes?m=")
      and "93.184.216.40" in admin.relay_list() and admin.is_single_server("93.184.216.40"))
check("  and not over a node", act("single-add", ip="93.184.216.20").startswith("nodes?m=!"))
admin.STORE.run("INSERT INTO metrics (host, at, cpu) VALUES ('93.184.216.40', ?, 1)",
                (panel.now(),))
act("relay-del", ip="93.184.216.40")
check("  and a relay or single server taken off leaves no samples either",
      not admin.STORE.q("SELECT 1 FROM metrics WHERE host = '93.184.216.40'"))
check("  taken off, it is no longer marked", not admin.is_single_server("93.184.216.40")
      and "93.184.216.40" not in admin.relay_list())

print("the words for each kind of server")
sync.CFG = {"SINGLE": "1"}
page = sync.user_page("<p>آدرس سرور ایران را در کنسول بگذارید؛ رله جواب می‌دهد.</p>")
check("a joined single server's own customer page speaks of one server, not Iran's",
      "سرور ایران" not in page and "رله" not in page and "آدرس سرور را" in page)
sync.CFG = {}
check("  a relay's still speaks of the relay",
      "رله" in sync.user_page("<p>رله جواب می‌دهد.</p>"))
events.clear()
store.set_setting("single:198.51.100.2", "1")
store.set_setting("alert_state:silent:198.51.100.2", "ok")
panel.check_servers(store, ("198.51.100.2",), ())
check("an alert names a single server as one", events and "تک‌سرور 198.51.100.2" in events[-1][1],
      events)
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('single:198.51.100.2', '1')"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value")
check("the admin panel names each server by what it is",
      admin.server_word("exit") == "این سرور" and admin.server_word("198.51.100.2") == "تک‌سرور"
      and admin.server_word("198.51.100.1") == "رله"
      and admin.server_word("93.184.216.20") == "نود")
asrc = read("templates/smartdns-admin")
check("  in the logs, the diagnosis and smartdns-watch's results",
      '{"رله": "سرور ایران", "نود": "سرور خارج (نود)"}.get(word, word)' in asrc
      and '"از مسیر مشتری — %s %s" % (server_word(s), s)' in asrc
      and '"تک‌سرور" if is_single_server(r["relay"]) else "سرور ایران"' in asrc)

print("every server's logs, a part at a time")
sync.recent_logs = lambda units=None, lines=None: "%s: %d lines\n" % (",".join(units), lines)
sync.kernel_log = lambda lines: "kernel: Out of memory: Killed process 123 (nginx)\n"
parts = sync.log_parts(sync.RELAY_LOG_PARTS)
keys = [k for k, _, _ in parts]
check("a relay sends each part of itself apart, the kernel's warnings among them",
      keys == ["sync", "dnsmasq", "templates", "doh", "nginx", "coturn", "epic", "acl", "cert",
               "kernel"] and "smartdns-sync: 80 lines" in parts[0][2]
      and "Out of memory" in parts[-1][2])
check("  a node its own parts", [k for k, _, _ in sync.log_parts(sync.NODE_LOG_PARTS)]
      == ["sync", "nginx", "cert", "kernel"])
ssrc = read("templates/smartdns-sync")
check("  both with their tunnels and nginx's errors",
      ssrc.count('payload["log_parts"] = log_parts(') == 2
      and 'payload["tunnel_logs"] = recent_logs(("smartdns-tunnel@*",), 80)' in ssrc)
stored = json.loads(panel.clean_log_parts([["sync", "همگام‌سازی", "x" * 20000], ["bad"], 5]))
check("the panel keeps them, each within bounds, and nothing that is not one",
      len(stored) == 1 and len(stored[0][2]) == 10000
      and panel.clean_log_parts("nonsense") is None)
store.run("INSERT OR REPLACE INTO relay_logs (relay, at, text, tunnel, nginx, parts)"
          " VALUES ('198.51.100.1', ?, '', 'tunnel up', '', ?)",
          (panel.now(), json.dumps([["sync", "همگام‌سازی", "ok\nsync failed (1): timed out\n"],
                                    ["kernel", "هسته", ""]])))
row = admin.STORE.one("SELECT * FROM relay_logs WHERE relay = '198.51.100.1'")
card = admin.server_logs_card(row, lambda t: "<pre>%s</pre>" % t)
check("the logs page: a card per server, a folded section per part, its trouble counted",
      "سرور ایران <code>198.51.100.1</code>" in card and card.count("<details>") == 3
      and "1 خط خطا یا هشدار" in card and "چیزی نیست" in card and "تونل‌ها" in card, card)
store.run("INSERT OR REPLACE INTO relay_logs (relay, at, text) VALUES ('198.51.100.9', ?,"
          " 'old relay text')", (panel.now(),))
row = admin.STORE.one("SELECT * FROM relay_logs WHERE relay = '198.51.100.9'")
check("  and an older relay's one block still shown",
      "old relay text" in admin.server_logs_card(row, lambda t: t))
noisy = ("x resolvconf[12]: Failed to set DNS configuration: Link lo is loopback device.\n"
         "x resolvconf[13]: Failed to revert interface configuration: Link lo is loopback.\n"
         "dnsmasq[3]: LOUD WARNING: use --bind-dynamic rather than --bind-interfaces\n"
         "dnsmasq[3]: LOUD WARNING: listening on 1.2.3.4 may accept requests via interfaces\n"
         "dnsmasq[4]: warning: ignoring resolv-file flag because no-resolv is set\n"
         "nginx: upstream timed out (110: Connection timed out)\n")
check("  what every machine prints is not counted as trouble, a real timeout is",
      "1 خط خطا یا هشدار" in admin.log_section("x", noisy, lambda t: t))
kept = sync.KERNEL_TROUBLE
check("  and of the kernel only what matters: memory, the connection table, crashes",
      kept.search("Out of memory: Killed process 1234 (nginx)")
      and kept.search("nf_conntrack: table full, dropping packet")
      and not kept.search("faux_driver regulatory: Direct firmware load for regulatory.db failed")
      and not kept.search("acpi PNP0A03:00: fail to add MMCONFIG information")
      and not kept.search("kernel:  oom_kill_process+0xd7/0x250"))
check("this machine's kernel warnings and its relays' tunnels too",
      '"-k", "-p", "warning"' in asrc and '"smartdns-tunnel@*", "تونل رله‌ها — سرور خارج"' in asrc)

print("alerts on each server's own figures")
events.clear()
store.run("DELETE FROM metrics")
store.run("DELETE FROM settings WHERE key LIKE 'alert_state:%'")


def sample(host, minutes_ago=0, **kw):
    at = (panel.datetime.now(panel.timezone.utc)
          - panel.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    row = dict(cpu=10.0, mem_used=100, mem_total=1000, disk_used=100, disk_total=1000,
               conntrack=10, conntrack_max=1000, cert_days=80)
    row.update(kw)
    cols = ", ".join(row)
    store.run("INSERT INTO metrics (host, at, %s) VALUES (?, ?, %s)"
              % (cols, ", ".join("?" * len(row))), [host, at] + list(row.values()))


for i in range(9):
    sample("198.51.100.1", minutes_ago=9 - i)
panel.check_health(store, ("198.51.100.1",), ())
check("a server whose figures are well says nothing", not events)
store.run("DELETE FROM metrics")
for i in range(9):
    sample("198.51.100.1", minutes_ago=0, cpu=95.0, mem_used=960, disk_used=950,
           conntrack=900, cert_days=5)
panel.check_health(store, ("198.51.100.1",), ())
said = " ".join(t for _, t in events)
check("a full disk, a full connection table, a certificate not renewed, and memory and "
      "processor high for ten minutes are each an alert",
      "دیسک رله 198.51.100.1 پر شده" in said and "جدول اتصال‌های رله 198.51.100.1" in said
      and "تا 5 روز دیگر منقضی" in said and "رم رله 198.51.100.1" in said
      and "پردازندهٔ رله 198.51.100.1" in said and len(events) == 5, said)
count = len(events)
store.run("DELETE FROM metrics")
sample("198.51.100.1", disk_used=880, conntrack=750, cert_days=16)
panel.check_health(store, ("198.51.100.1",), ())
check("  a figure back below the line but not well below it clears nothing",
      len(events) == count)
sample("198.51.100.1", disk_used=500, conntrack=100, cert_days=89)
panel.check_health(store, ("198.51.100.1",), ())
check("  well below, and each is cleared once",
      len(events) == count + 3 and "✅ گواهی HTTPS رله 198.51.100.1 تمدید شد." in events[-1][1]
      or "✅ گواهی HTTPS رله 198.51.100.1 تمدید شد." in " ".join(t for _, t in events[-3:]))
check("  run every half minute beside the check for silent servers",
      "check_health(store, API.relays, API.nodes)" in src)
check("every machine reports its connection table, certificate and traffic since boot",
      all(k in ssrc and k in src for k in ('"conntrack": track[0]', '"cert_days": cert_days(',
                                           '"rx_total": self.net[0]')))
crt = os.path.join(tmp, "c.pem")
made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
                       "-subj", "/CN=t", "-keyout", os.path.join(tmp, "k.pem"), "-out", crt],
                      capture_output=True).returncode == 0 if shutil.which("openssl") else False
if made:
    check("  the days a certificate has left, read from it", sync.cert_days(crt) in (29, 30)
          and panel.cert_days(crt) in (29, 30) and sync.cert_days(None) is None)
else:
    print("  --   no openssl here - the certificate's days are left to the live test")

print("a monthly traffic cap per server")
from datetime import date
check("the provider's month starts on its day, this month or the last",
      panel.period_start(5, date(2026, 9, 26)) == date(2026, 9, 5)
      and panel.period_start(28, date(2026, 9, 26)) == date(2026, 8, 28)
      and panel.period_start(1, date(2026, 3, 1)) == date(2026, 3, 1))
store.run("DELETE FROM settings WHERE key LIKE 'net_month:%' OR key LIKE 'cap:%'")
store.record_metrics("93.184.216.20", {"cpu": 1.0, "rx_total": 1000, "tx_total": 5000})
store.record_metrics("93.184.216.20", {"cpu": 1.0, "rx_total": 4000, "tx_total": 9000})
store.record_metrics("93.184.216.20", {"cpu": 1.0, "rx_total": 500, "tx_total": 1500})
check("the month is counted from the network card's own counters, through a reboot",
      panel.month_used(store, "93.184.216.20") == (3000 + 4000 + 500 + 1500, 0))
store.set_setting("cap:93.184.216.20", json.dumps({"gb": "0.00001", "day": 1, "count": "out",
                                                  "action": "move"}))
check("  only what leaves, when the provider counts only that",
      panel.month_used(store, "93.184.216.20") == (4000 + 1500, 10000))
events.clear()
store.record_metrics("93.184.216.20", {"cpu": 1.0, "rx_total": 500, "tx_total": 6000})
panel.check_caps(store, (), ("93.184.216.20",))
said = " ".join(t for _, t in events)
check("reaching the cap is one alert, the highest, saying what happens",
      len(events) == 1 and "به سقف ماهانه‌اش رسید" in said
      and "رله‌ها از سرورهای خارج دیگر می‌روند" in said, said)
check("  the lower levels counted as crossed without a word of their own",
      store.setting("alert_state:cap80:93.184.216.20") == "down"
      and store.setting("alert_state:cap95:93.184.216.20") == "down")
before = len(events)
panel.check_caps(store, (), ("93.184.216.20",))
check("  and nothing again the next half minute", len(events) == before)
check("  and an exit past it, when the admin chose so, goes last on every relay",
      panel.capped_exits(store, ("93.184.216.20",)) == ["93.184.216.20"])
store.set_setting("cap:93.184.216.20", json.dumps({"gb": "0.00001", "day": 1, "count": "out",
                                                  "action": "alert"}))
check("  while one set to alert only stays where it is",
      panel.capped_exits(store, ("93.184.216.20",)) == [])
store.set_setting("cap:93.184.216.20", json.dumps({"gb": "1000", "day": 1, "count": "out",
                                                  "action": "alert"}))
events.clear()
panel.check_caps(store, (), ("93.184.216.20",))
check("a month back under the cap - or a bigger cap - is one message",
      len(events) == 1 and "دوباره زیر 80٪ سقف" in events[0][1], events)
store.set_setting("alert_state:cap80:93.184.216.20", "down")
store.run("DELETE FROM settings WHERE key = 'cap:93.184.216.20'")
panel.check_caps(store, (), ("93.184.216.20",))
check("  and a cap taken away leaves nothing of it open",
      store.one("SELECT 1 FROM settings WHERE key LIKE 'alert_state:cap%'") is None)
check("  small amounts in MB", panel.human_gb(5_400_000) == "5 MB"
      and panel.human_gb(2.5e9) == "2.5 GB")
sync.EXIT_PLAN.update(main=main, order=[main, "93.184.216.20"], capped=set())
sync.EXIT_CONF = os.path.join(tmp, "exit-cap.conf")
sync.CFG = {"TUNNEL": "off", "PANEL_HOST": main}
sync.PANEL_ENV_HERE = os.path.join(tmp, "no-panel.env")
sync.EXIT_HEALTH.clear()
sync.NODE_TUNNELS.clear()
sync.sh = fake_sh
sync.apply_exits({"main": main, "order": ["93.184.216.20", main], "capped": ["93.184.216.20"]})
check("  the relay puts the capped exit last, though it is the one picked",
      "upstream to_exit_https {\n    server 203.0.113.9:443;\n    server 93.184.216.20:443 backup;"
      in open(sync.EXIT_CONF).read())
admin.STORE.run("DELETE FROM settings WHERE key LIKE 'cap:%'")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1\nNODE_IP=93.184.216.20\n")
got = act("cap-save", host="93.184.216.20", gb="1000", day="5", count="both", action="move")
cap = json.loads(admin.STORE.one("SELECT value FROM settings WHERE key = 'cap:93.184.216.20'")["value"])
check("the admin sets a server's cap, the day its month starts and what happens at it",
      cap == {"gb": "1000", "day": 5, "count": "both", "action": "move"}, got)
check("  nonsense refused", act("cap-save", host="93.184.216.20", gb="10", day="31")
      .startswith("nodes?m=!") and act("cap-save", host="192.0.2.1", gb="10").startswith("nodes?m=!"))
cell = admin.cap_cell("p", "93.184.216.20", True)
check("  and sees the month against it, on the Node page",
      "از 1000 GB" in cell and "رله‌ها از سرورهای خارج دیگر بروند" in cell
      and "رله‌ها از سرورهای خارج دیگر بروند" not in admin.cap_cell("p", "198.51.100.1", False))
act("cap-save", host="93.184.216.20", gb="")
check("  or takes it away", admin.STORE.one(
    "SELECT value FROM settings WHERE key = 'cap:93.184.216.20'") is None)

print("upgrading every server from the panel")
check("the exit keeps the installer it ran, for the others",
      'cp -f "$0" "$STATE_DIR/installer/master-dns.sh.tmp"' in logic
      and 'if [ "$ROLE" = exit ] && [ -f "$0" ] && grep -qx "VERSION=' in logic)
events.clear()
for h, v in (("198.51.100.1", "0.8.6"), ("198.51.100.2", "0.9.0"), ("93.184.216.20", "0.8.6")):
    store.set_setting("version:" + h, v)
job = {"version": "0.9.0", "sha": "abc", "queue": ["198.51.100.2", "93.184.216.20"],
       "current": "198.51.100.1", "asked_at": panel.now(), "done": [], "logs": {}, "stopped": ""}
panel.save_upgrade_job(store, job)
check("only the server whose turn it is is told",
      panel.upgrade_order(store, "198.51.100.1") == {"version": "0.9.0", "sha": "abc"}
      and panel.upgrade_order(store, "93.184.216.20") is None)
panel.upgrade_report(store, "198.51.100.1", {"version": "0.9.0", "ok": True, "log": "fine"})
job = panel.upgrade_job(store)
check("a success moves on - past a server already there - to the next",
      job["current"] == "93.184.216.20" and job["done"] == ["198.51.100.1", "198.51.100.2"])
panel.upgrade_report(store, "93.184.216.20", {"version": "0.8.6", "ok": False, "log": "broke"})
job = panel.upgrade_job(store)
check("a failure stops the job, keeps its log, and is an alert",
      job["stopped"] and job["current"] is None and job["logs"]["93.184.216.20"] == "broke"
      and any("نشد" in t for _, t in events))
job.update(stopped="", current="198.51.100.1", asked_at="2026-01-01T00:00:00+00:00")
panel.save_upgrade_job(store, job)
panel.upgrade_timeout(store)
check("  so does silence for twenty minutes", "جوابی نداد" in panel.upgrade_job(store)["stopped"])
store.set_setting("version:93.184.216.20", "0.9.0")
job = {"version": "0.9.0", "sha": "abc", "queue": [], "current": "198.51.100.1",
       "asked_at": panel.now(), "done": [], "logs": {}, "stopped": ""}
panel.save_upgrade_job(store, job)
events.clear()
panel.upgrade_report(store, "198.51.100.1", {"version": "0.9.0", "ok": True, "log": "fine"})
check("  and the last success says all are done",
      panel.upgrade_job(store)["current"] is None and "همهٔ سرورها" in events[-1][1])

up = os.path.join(tmp, "upgrade")
sync.UPGRADE_DIR = up
sync.VERSION_FILE = os.path.join(tmp, "version")
with open(sync.VERSION_FILE, "w") as fh:
    fh.write("0.8.6\n")
blob = b"#!/bin/bash\nVERSION=\"0.9.0\"\n"
sha = __import__("hashlib").sha256(blob).hexdigest()
sync.fetch = lambda path: (blob, {})


def upgrade_sh(*args):
    ran.append(args)
    # Nothing upgrading here yet.
    return R(3) if args[:2] == ("systemctl", "is-active") else R(0)


sync.sh = upgrade_sh
ran.clear()
try:
    sync.start_upgrade({"version": "0.9.0", "sha": "0" * 64})
    refused = False
except RuntimeError:
    refused = True
check("a relay refuses an installer that is not the one the panel named", refused
      and not any(a[:1] == ("systemd-run",) for a in ran))
sync.start_upgrade({"version": "0.9.0", "sha": sha})
check("  and runs the right one as a unit of its own, which outlives its restart",
      open(os.path.join(up, "master-dns.sh"), "rb").read() == blob
      and any(a[:2] == ("systemd-run", "--unit=smartdns-upgrade") for a in ran))
ran.clear()
sync.start_upgrade({"version": "0.9.0", "sha": sha})
check("  once", not any(a[:1] == ("systemd-run",) for a in ran))
check("  nothing to say before it has finished", sync.upgrade_result() is None)
with open(os.path.join(up, "run.log"), "w") as fh:
    fh.write("...\nrelay is installed and working, version 0.9.0.\n")
with open(os.path.join(up, "rc"), "w") as fh:
    fh.write("0\n")
with open(sync.VERSION_FILE, "w") as fh:
    fh.write("0.9.0\n")
result = sync.upgrade_result()
check("  then the version it is on, how it went and the log's end",
      result["ok"] and result["version"] == "0.9.0" and "installed and working" in result["log"])
sync.upgrade_heard()
check("  sent until the panel has it", sync.upgrade_result() is None)
with open(os.path.join(admin.PANEL_ENV), "w") as fh:
    fh.write("RELAY_IP=198.51.100.1\nNODE_IP=93.184.216.20\n")
admin.INSTALLER_COPY = os.path.join(tmp, "installer.sh")
with open(admin.INSTALLER_COPY, "wb") as fh:
    fh.write(blob)
admin.STORE.run("INSERT INTO settings (key, value) VALUES ('version:198.51.100.1', '0.8.6')"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value")
admin.STORE.run("DELETE FROM settings WHERE key = 'upgrade_job'")
card, behind, running = admin.servers_versions("p")
check("under the GitHub card: every other server's version, and which are behind",
      "0.8.6" in card and behind == ["198.51.100.1"] and not running)
check("  and no button of its own: the GitHub one upgrades them",
      "upgrade-start" not in card and "servers_versions" in admin.github_card.__code__.co_names
      and "upgrade_card" not in admin.nodes_page.__code__.co_names)
got = act("upgrade-start")
job = json.loads(admin.STORE.one("SELECT value FROM settings WHERE key = 'upgrade_job'")["value"])
check("  which starts with the first behind, the installer's hash in the job",
      job["current"] == "198.51.100.1" and job["sha"] == sha and job["version"] == "0.9.0", got)
act("upgrade-stop")
check("  and can be stopped", "متوقف" in json.loads(admin.STORE.one(
    "SELECT value FROM settings WHERE key = 'upgrade_job'")["value"])["stopped"])

print("moving the panel to a standby")
panel.STANDBY_BUNDLE = os.path.join(tmp, "standby-bundle.enc")
with open(panel.STANDBY_BUNDLE, "wb") as fh:
    fh.write(b"Salted__bundle")
store.set_setting("standby", "93.184.216.20")
info = panel.standby_info(store, "93.184.216.20")
check("the standby, and only it, is told the bundle and installer to keep",
      info and info["bundle"] == __import__("hashlib").sha256(b"Salted__bundle").hexdigest()
      and panel.standby_info(store, "93.184.216.30") is None)
check("every server is told where the panel is, and its standby",
      panel.panel_where(store) == {"host": panel.exit_self(), "standby": "93.184.216.20"}
      and '"panel": panel_where(self.store),' in src
      and 'if self.store.setting("standby") != self.client_address[0]:' in src)
env = os.path.join(tmp, "relay-sync.env")
with open(env, "w") as fh:
    fh.write("PANEL_HOST=203.0.113.9\nSYNC_SECRET=x\nSYNC_FINGERPRINT=ab\nSELF_IP=198.51.100.1\n")
sync.CONFIG = env
sync.CFG = sync.load_config()
sync.PANEL_ENV_HERE = os.path.join(tmp, "no-panel.env")
sync.follow_panel({"host": "203.0.113.9", "standby": "93.184.216.20"})
check("a relay learns the standby, and tries it last",
      "PANEL_STANDBY=93.184.216.20" in open(env).read()
      and sync.api_endpoints()[-1] == ("93.184.216.20", 8443)
      and sync.api_endpoints()[0] == ("203.0.113.9", 8443))
sync.follow_panel({"host": "93.184.216.20", "standby": ""})
check("  and once the standby is the panel, it syncs there for good",
      "PANEL_HOST=93.184.216.20" in open(env).read() and sync.CFG["PANEL_HOST"] == "93.184.216.20"
      and sync.api_endpoints() == [("93.184.216.20", 8443)])
sync.STANDBY_DIR = os.path.join(tmp, "standby")
served = {"/standby-bundle": b"Salted__bundle", "/installer": b"#!/bin/bash\nVERSION=\"0.9.0\"\n"}
sync.fetch = lambda path: (served[path], {})
h = lambda b: __import__("hashlib").sha256(b).hexdigest()
kept = sync.keep_standby({"bundle": h(served["/standby-bundle"]), "installer": h(served["/installer"])})
check("the standby keeps the latest bundle and installer, by their hashes",
      kept == {"bundle": h(served["/standby-bundle"]), "installer": h(served["/installer"])}
      and open(os.path.join(sync.STANDBY_DIR, "backup.enc"), "rb").read() == b"Salted__bundle")
sync.fetch = lambda path: (b"not it", {})
sync.keep_standby({"bundle": h(b"newer"), "installer": h(served["/installer"])})
check("  and not something else in their place",
      open(os.path.join(sync.STANDBY_DIR, "backup.enc"), "rb").read() == b"Salted__bundle")
sync.keep_standby(None)
check("  a node no longer the standby keeps nothing", not os.path.exists(sync.STANDBY_DIR))

with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=198.51.100.1\nNODE_IP=93.184.216.20\n")
admin.BACKUP_PASS_FILE = os.path.join(tmp, "backup.pass")
admin.STANDBY_BUNDLE = os.path.join(tmp, "admin-standby.enc")
made_bundles = []
admin.make_bundle = lambda pw: (made_bundles.append(pw), b"Salted__x")[1]
check("the standby is one of the nodes", act("standby-save", ip="198.51.100.1")
      .startswith("nodes?m=!"))
act("standby-save", ip="93.184.216.20")
card = admin.standby_card("p")
check("  without a backup password, the card says to set one first",
      "رمز بکاپ" in card and not os.path.exists(admin.STANDBY_BUNDLE))
with open(admin.BACKUP_PASS_FILE, "w") as fh:
    fh.write("correct horse\n")
act("standby-save", ip="93.184.216.20")
check("  with one, its bundle is made at once, then every six hours",
      open(admin.STANDBY_BUNDLE, "rb").read() == b"Salted__x" and made_bundles == ["correct horse"])
admin.refresh_standby()
check("  not again before then", made_bundles == ["correct horse"])
card = admin.standby_card("p")
check("  and the card says what to do on the day",
      "--take-over" in card and "93.184.216.20" in card and "هنوز آماده نیست" in card)
check("the installer takes over only from a backup the password opens, and only with the "
      "admin panel's name already pointing here",
      "--take-over|take-over) TAKE_OVER=1 ;;" in logic
      and 'die "that password does not open the backup"' in logic
      and 'die "point $dom at this server ($me) first' in logic
      and 'grep -vx "$me"' in logic and "systemctl disable --now smartdns-node.service" in logic)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
