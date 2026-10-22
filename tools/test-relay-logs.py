#!/usr/bin/env python3
"""The admin panel's logs page: this machine, the bot, and every relay.

A relay's journal is on the relay, so the relay sends its recent lines along
with a sync every few minutes, and the panel keeps the latest per relay. What
has to hold: not every sync (a log is for a person, and the link has better
things to carry); the sync secret never leaves the relay in it; one row per
relay, replaced; and the page shows each part, with the bot's token masked.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []
SECRET = "sync-secret-" + "q" * 20
TOKEN = "123456789:AAH" + "w" * 32


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
panel.log = lambda *a: None
panel.print = lambda *a, **k: None

print("the relay: when, and what")
t = [1000.0]
sync.LOG_SENT[0] = 0.0
check("the first sync carries its logs", sync.logs_due(lambda: t[0]))
t[0] += 30
check("the next one, thirty seconds on, does not", not sync.logs_due(lambda: t[0]))
t[0] += 300
check("five minutes on, it does again", sync.logs_due(lambda: t[0]))


class Ran:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


asked = []
sync.subprocess.run = lambda cmd, **kw: asked.append(cmd) or Ran(
    "2026-09-22 relay smartdns-sync: sync with %s\n"
    "2026-09-22 relay nginx: access forbidden by rule, client: 1.2.3.4\n"
    "2026-09-22 relay dnsmasq: started\n" % SECRET)
sync.CFG = {"SYNC_SECRET": SECRET, "SELF_IP": "203.0.113.4"}
text = sync.recent_logs()
check("the sync secret is masked", SECRET not in text and "<secret>" in text, text)
check("nginx turning strangers away is left out", "access forbidden" not in text)
check("the relay's parts are asked for, as smartdns-logs shows them",
      all(u in asked[-1] for u in ("smartdns-sync", "dnsmasq", "nginx", "coturn",
                                    "smartdns-dns@*")), str(asked[-1]))

sent = []
sync.post = lambda path, payload: sent.append(payload) or {
    "allowed": [], "profiles": {}, "extra_domains": [], "templates": {}}
for name in ("save_template_names", "save_user_names", "apply_custom_domains",
             "apply_speeds", "close_relay_when_ready", "dns_seen"):
    setattr(sync, name, lambda *a, **k: False)
sync.apply_profiles = lambda *a, **k: None
sync.apply_gate_dns = lambda: False
sync.current_state = lambda: []
sync.HEALTH = type("H", (), {"sample": staticmethod(lambda: {})})()
sync.LOG_SENT[0] = 0.0
sync.sync_once()
sync.sync_once()
check("a sync carries them when due, and the next does not",
      "logs" in sent[0] and "logs" not in sent[1], str([list(p) for p in sent]))
check("and nginx's error log, which is a file, not the journal", "nginx_logs" in sent[0])
errlog = os.path.join(tempfile.mkdtemp(), "error.log")
with open(errlog, "w") as fh:
    fh.write("2026/09/22 upstream timed out while connecting to upstream, client: 5.1.2.3\n"
             "2026/09/22 access forbidden by rule, client: 9.9.9.9\n")
got = sync.nginx_errors(path=errlog)
check("its lines are read, without nginx turning strangers away",
      "upstream timed out" in got and "access forbidden" not in got, got)
check("and no file is no lines, not a failure", sync.nginx_errors(path=errlog + ".x") == "")
check("the tunnel's lines go apart from the rest", "tunnel_logs" in sent[0]
      and "smartdns-tunnel" not in sync.LOG_UNITS and "smartdns-tunnel" in asked[-1],
      str(asked[-1]))

check("the DoH server and the certificate's renewal go with the rest",
      "smartdns-doh" in sync.LOG_UNITS and "smartdns-cert" in sync.LOG_UNITS)
lsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates",
                         "smartdns-logs"), encoding="utf-8").read()
check("and smartdns-logs on a relay shows the renewal too",
      'if [ "$role" = relay ] && [ -f /etc/systemd/system/smartdns-cert.timer ]; then' in lsrc)

print("the relay: smartdns-watch on the panel's request")
ran = []


class Thread:
    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


real_thread = sync.threading.Thread
sync.threading.Thread = Thread
sync.subprocess.run = lambda cmd, **kw: ran.append(cmd) or Ran(
    "watching ali - ctrl-c to stop\n08:01:02  www.ea.com   direct 1.2.3.4\n1 names: 1 direct")
sync.post = lambda path, payload: sent.append(payload) or {
    "allowed": [], "profiles": {}, "extra_domains": [], "templates": {},
    "watch": {"id": "job1", "target": "ali", "seconds": 120}}
sent.clear()
sync.sync_once()
check("a request starts it, for as long as asked, stopped by timeout",
      ran and ran[-1][:3] == ["timeout", "120", sync.WATCH] and ran[-1][3] == "ali", str(ran))
sync.sync_once()
check("what it saw goes back with the next sync", sent[1].get("watch_result", {}).get("id")
      == "job1" and "www.ea.com" in sent[1]["watch_result"]["text"])
check("once: the same request is not run again", len(ran) == 1)
sync.sync_once()
check("and its result is not sent twice", "watch_result" not in sent[2])
ran.clear()
sync.start_watch({"id": "job2", "target": "a; rm -rf /", "seconds": 60})
check("a target that is not a name or an address is not run",
      not ran and "not a customer" in sync.WATCH_DONE["result"]["text"])
sync.WATCH_DONE["result"] = None
sync.threading.Thread = real_thread

print("the panel keeps the latest, per relay")
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
panel.CATALOGUE = []
panel.DEFAULT_TEMPLATE[0] = store.ensure_default_template([])["id"]
panel.API.store = store
panel.API.secret = "s"
panel.API.relays = ("127.0.0.1",)
server = panel.make_api_server(None, 0)
threading.Thread(target=server.serve_forever, daemon=True).start()


def sync_call(body):
    req = urllib.request.Request("http://127.0.0.1:%d/sync" % server.server_address[1],
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer s")
    req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=10).status


check("a sync with logs is taken", sync_call({"counters": {}, "relay": "127.0.0.1",
                                               "logs": "first lines"}) == 200)
check("and kept", store.one("SELECT text FROM relay_logs")["text"] == "first lines")
sync_call({"counters": {}, "relay": "127.0.0.1"})
check("a sync without logs leaves them be",
      store.one("SELECT text FROM relay_logs")["text"] == "first lines")
sync_call({"counters": {}, "relay": "127.0.0.1", "logs": "newer lines"})
check("newer ones replace them - one row per relay",
      [dict(r) for r in store.q("SELECT relay, text FROM relay_logs")]
      == [{"relay": "127.0.0.1", "text": "newer lines"}])
sync_call({"counters": {}, "relay": "127.0.0.1", "logs": "newer lines",
           "tunnel_logs": "tunnel connected", "nginx_logs": "upstream timed out"})
check("with the tunnel's apart", store.one("SELECT tunnel FROM relay_logs")["tunnel"]
      == "tunnel connected")
check("and nginx's errors", store.one("SELECT nginx FROM relay_logs")["nginx"]
      == "upstream timed out")

print("the panel hands out a watch, and keeps what comes back")
store.set_setting("watch_job", json.dumps({"id": "abc", "target": "ali", "seconds": 60,
                                           "asked_at": panel.now()}))
check("a fresh request is handed to the relays",
      panel.watch_job(store) == {"id": "abc", "target": "ali", "seconds": 60})
sync_call({"counters": {}, "relay": "127.0.0.1",
           "watch_result": {"id": "abc", "text": "www.ea.com direct"}})
check("and what the relay saw is kept", store.one("SELECT job_id, text FROM watch_results")
      ["text"] == "www.ea.com direct")
store.set_setting("watch_job", json.dumps({"id": "old", "target": "", "seconds": 60,
                                           "asked_at": "2020-01-01T00:00:00+00:00"}))
check("a stale request is not", panel.watch_job(store) is None)
store.set_setting("watch_job", json.dumps({"id": "abc", "target": "ali", "seconds": 60,
                                           "asked_at": panel.now()}))
server.shutdown()

print("the logs page")
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.BOT_ENV = os.path.join(tmp, "bot.env")
with open(admin.BOT_ENV, "w") as fh:
    fh.write("BOT_TOKEN=%s\n" % TOKEN)
admin.subprocess.run = lambda cmd, **kw: Ran("line from %s with %s\n" % (cmd[2], TOKEN))
admin.NGINX_ERRORS = errlog
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
with open(admin.PANEL_ENV, "w") as fh:
    fh.write("RELAY_IP=127.0.0.1\n")
# A server taken off the panel before its log went with it.
admin.STORE.run("INSERT INTO relay_logs (relay, at, text) VALUES ('198.51.100.66', ?, ?)",
                (panel.now(), "lines of a server gone"))
page = admin.Admin.logs(None)
check("a server no longer on the panel: its log neither shown nor kept",
      "lines of a server gone" not in page and "198.51.100.66" not in page
      and not admin.STORE.q("SELECT 1 FROM relay_logs WHERE relay = '198.51.100.66'"))
check("the certificate renewals, and nginx, of this machine",
      "line from smartdns-cert" in page and "line from nginx" in page)
check("and this machine's nginx errors, without the gate's noise",
      "upstream timed out while connecting" in page and "access forbidden" not in page)
check("each relay's nginx errors", page.count("upstream timed out") >= 2
      and "خطاهای nginx — اگر مشتری" in page)
check("this machine's two services", "smartdns-panel" in page and "smartdns-admin" in page)
check("the bot's, with its token masked", "master-dns-bot" in page and TOKEN not in page)
check("and each relay's, with when it came", "newer lines" in page and "127.0.0.1" in page
      and "همین الان" in page, page[-600:])
check("and each relay's tunnel, apart", "tunnel connected" in page and "تونل" in page)
check("the watch: its form, and what came back", "watch-start" in page
      and "www.ea.com direct" in page)


class Rec:
    def __init__(self):
        self._headers_buffer = []
        self.sent = {}
        self.wfile = self
        self.headers = {}

    def write(self, b):
        pass

    def send_response(self, code):
        pass

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


for name in ("action", "redirect", "send"):
    setattr(Rec, name, getattr(admin.Admin, name))
Rec.one = staticmethod(admin.Admin.one)
admin.log = lambda *a: None
r = Rec()
r.action("watch-start", {"target": ["5.120.1.2"], "seconds": ["300"]})
job = json.loads(store.setting("watch_job"))
check("the operator's request is written for the next sync",
      job["target"] == "5.120.1.2" and job["seconds"] == 300 and job["id"] != "abc")
r = Rec()
r.action("watch-start", {"target": ["$(reboot)"], "seconds": ["60"]})
check("a target that is not a name or an address is refused",
      json.loads(store.setting("watch_job"))["target"] == "5.120.1.2")

shutil.rmtree(tmp, ignore_errors=True)
admin.STORE.run("INSERT OR REPLACE INTO relay_logs (relay, at, text) VALUES ('198.51.100.67', ?, 'x')",
                (panel.now(),))
admin.forget_server("198.51.100.67")
check("a server taken off the panel takes its log with it",
      not admin.STORE.q("SELECT 1 FROM relay_logs WHERE relay = '198.51.100.67'"))

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
