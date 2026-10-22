#!/usr/bin/env python3
"""The admin panel's service check.

What has to hold: a host passes only when TLS completes with the service's
own certificate, and each way of failing is told apart; every routed group
of the catalogue is checked, a few domains each, with www. tried when the
bare domain has no address, and the groups that are never routed left out;
the exit checks straight out, each relay through its own 443, once per
request; the panel hands the hosts out and keeps what each relay found; the
page sets the exit beside each relay, the services in trouble first.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import ssl
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
          ((" - " + str(detail)[:500]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(ROOT, path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


sync = load("templates/smartdns-sync", "sync")
panel = load("templates/smartdns-panel", "panel")
admin = load("templates/smartdns-admin", "admin")
for m in (sync, panel, admin):
    m.log = lambda *a, **k: None
panel.print = lambda *a, **k: None

print("one host")
check("the relay's check and the exit's are the same code",
      sync.tls_probe.__code__.co_code == admin.tls_probe.__code__.co_code
      and read("templates/smartdns-admin").count("def tls_probe(") == 1)


def listener(handler):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def serve():
        try:
            conn, _ = srv.accept()
            handler(conn)
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()
    return srv


closed = socket.socket()
closed.bind(("127.0.0.1", 0))
port_closed = closed.getsockname()[1]
closed.close()


def at(port, host="x.example", timeout=2.0):
    real = socket.create_connection

    def to_port(addr, timeout=None):
        return real(("127.0.0.1", port), timeout=timeout)
    sync.socket.create_connection = to_port
    try:
        return sync.tls_probe(host, address="127.0.0.1", timeout=timeout)
    finally:
        sync.socket.create_connection = real


# Windows tries a refused connection again for two seconds, and it arrives as
# a timeout; Linux, where this runs for real, says refused at once.
check("nobody listening is told as refused", at(port_closed)[:2] == ["fail", "refused"]
      or (sys.platform == "win32" and at(port_closed)[:2] == ["fail", "timeout"]))
quiet = listener(lambda c: threading.Event().wait(5))
check("silence is told as a timeout", at(quiet.getsockname()[1], timeout=0.5)[:2] == ["fail", "timeout"])
plain = listener(lambda c: (c.recv(100), c.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n"), c.close()))
got = at(plain.getsockname()[1])
check("something that is not TLS is told as TLS failing", got[0] == "fail" and got[1] in ("tls", "reset"),
      got)
cut = listener(lambda c: c.close())
got = at(cut.getsockname()[1])
check("and a connection cut at once too", got[0] == "fail" and got[1] in ("tls", "reset"), got)
check("a name with no address says so", sync.tls_probe("nothing.invalid")[:2] == ["fail", "no address"])
real_gai = sync.socket.getaddrinfo
sync.socket.getaddrinfo = lambda h, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))]
check("one that resolves to this machine is not connected to, and says so - a name like that "
      "sent the exit's nginx round in a loop", sync.tls_probe("discordapp.io")[:2] == ["fail", "local address"]
      and sync.http_probe("discordapp.io") is None)
sync.socket.getaddrinfo = real_gai

tmp = tempfile.mkdtemp()
try:
    import subprocess
    key, crt = os.path.join(tmp, "k.pem"), os.path.join(tmp, "c.pem")
    made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                           "-subj", "/CN=x.example", "-keyout", key, "-out", crt],
                          capture_output=True).returncode == 0
except OSError:
    made = False
if made:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)

    def tls(c):
        try:
            with ctx.wrap_socket(c, server_side=True) as s:
                s.recv(200)
                s.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
        except (ssl.SSLError, OSError):
            pass
    fake = listener(tls)
    got = at(fake.getsockname()[1])
    check("a certificate that is not the service's is a failure, not a pass",
          got[:2] == ["fail", "certificate"], got)
else:
    print("  (no openssl here - the certificate case is left to the live test)")
check("the rest of the check is the same code on both too",
      all(getattr(sync, f).__code__.co_code == getattr(admin, f).__code__.co_code
          for f in ("check_host", "http_probe", "probe_all")))
real_tls, real_http = sync.tls_probe, sync.http_probe
answers = {}
sync.tls_probe = lambda h, a=None: answers.get(("tls", h), ["fail", "timeout", 6000])
sync.http_probe = lambda h, a=None: answers.get(("http", h))
answers[("tls", "adobelogin.com")] = ["fail", "name", 500]
check("a certificate for other names is the service, reached",
      sync.check_host("adobelogin.com")[0] == "ok")
answers[("tls", "www.battlenet.com")] = ["ok", "HTTP 301", 90]
check("a bare domain with nothing on 443 passes on its www.",
      sync.check_host("battlenet.com") == ["ok", "www.battlenet.com", 90])
answers[("http", "assets1.xboxlive.com")] = ["ok", "HTTP 200 on 80", 40]
check("a download host that only speaks HTTP passes on 80",
      sync.check_host("assets1.xboxlive.com")[1] == "HTTP 200 on 80")
answers[("http", "helldivers2.com")] = ["ok", "HTTP 301 on 80", 40]
check("but 80 is asked of the consoles' download hosts only - the only ones the exit carries there",
      sync.check_host("helldivers2.com")[0] == "fail")
exit_conf = read("templates/exit-nginx.conf")
check("  the same names as the exit's server on 80",
      "server_name ~^.*\\.(playstation\\.(net|com)|xboxlive\\.com|gamepass\\.com)$;" in exit_conf
      and sync.CONSOLE_HTTP.pattern == r"^.*\.(playstation\.(net|com)|xboxlive\.com|gamepass\.com)$")
answers[("tls", "filtered.example")] = ["fail", "certificate", 50]
sync.tls_probe, sync.http_probe = real_tls, real_http


def http_answer(head):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.recv(500)
        c.sendall(head)
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    real = socket.create_connection
    sync.socket.create_connection = lambda addr, timeout=None: real(("127.0.0.1", srv.getsockname()[1]),
                                                                     timeout=timeout)
    try:
        return sync.http_probe("x.example", address="127.0.0.1")
    finally:
        sync.socket.create_connection = real


check("on 80, being sent over to the same name's https is no proof - the exit says that for any name",
      http_answer(b"HTTP/1.1 301 Moved\r\nServer: nginx\r\nLocation: https://x.example/\r\n\r\n") is None)
check("  while a real answer is", http_answer(b"HTTP/1.1 404 Not Found\r\n\r\n")[:2]
      == ["ok", "HTTP 404 on 80"])
check("  and so is being sent somewhere else",
      http_answer(b"HTTP/1.1 302 Found\r\nLocation: http://cdn.example/a\r\n\r\n")[0] == "ok")
sync.tls_probe = lambda h, a=None: answers.get(("tls", h), ["fail", "timeout", 6000])
sync.http_probe = lambda h, a=None: answers.get(("http", h))
check("an untrusted certificate is still a failure, told as the first way it failed, www.'s beside it",
      sync.check_host("filtered.example") == ["fail", "certificate|www:timeout", 50]
      and sync.check_host("dead.example") == ["fail", "timeout|www:timeout", 6000])
answers[("tls", "gameranger.example")] = ["fail", "expired", 300]
check("a certificate the service let run out is the service, reached - its own fault, not ours",
      sync.check_host("gameranger.example") == ["ok", "expired certificate", 300])
answers[("tls", "loop.example")] = ["fail", "local address", 3]
check("a name pointing at this machine is not tried again with www.",
      sync.check_host("loop.example") == ["fail", "local address", 3])
tries = {}


def flaky(h, a=None):
    tries[h] = tries.get(h, 0) + 1
    return ["ok", "HTTP 200", 10] if h == "once.example" and tries[h] > 1 else ["fail", "timeout", 6000]


real_check = sync.check_host
sync.check_host = flaky
got = sync.probe_all(["once.example", "never.example"])
check("what fails is tried once more, and a name that answers then passes",
      got["once.example"][0] == "ok" and got["never.example"][0] == "fail"
      and tries == {"once.example": 2, "never.example": 2}, repr((got, tries)))
sync.check_host = real_check
sync.tls_probe, sync.http_probe = real_tls, real_http
src = read("templates/smartdns-sync")
check("a certificate for other names is told apart by its code - 62, the name not matching",
      '{62: "name", 10: "expired", 2: "expired", 20: "expired",' in src)
check("a pass is TLS completed with the service's own certificate, and its HTTP status",
      "ssl.create_default_context().wrap_socket(raw, server_hostname=host)" in src
      and 'done("ok", "HTTP " + m.group(1)' in src)

print("what is checked")
admin.CATALOGUE = [
    {"key": "spotify", "label": "Spotify", "groups": [
        {"key": "main", "domains": ["spotify.com", "scdn.co"] + ["d%d.example" % i for i in range(20)]}]},
    {"key": "ea", "label": "EA", "groups": [{"key": "main", "domains": ["ea.com"]},
                                            {"key": "pin", "locked": True, "domains": ["pinned.example"]}]},
    {"key": "riot", "label": "Riot chat", "groups": [
        {"key": "chat", "opt_in": True, "domains": ["wr.pvp.net"]}]},
    {"key": "bypass", "label": "Never routed", "groups": [
        {"key": "ea", "locked": True, "domains": ["gosredirector.ea.com"]}]},
    {"key": "custom", "label": "دامنه‌های شما", "groups": [{"key": "main", "domains": []}]}]
admin.catalogue_now = lambda: admin.CATALOGUE
items = admin.diag_items(set())
names = [d for _, _, d in items]
check("every routed group, a few domains each",
      "spotify.com" in names and "ea.com" in names and len([n for n in names if n.endswith(".example")]) ==
      admin.DIAG_PER_GROUP - 2, repr(names))
check("the groups never routed are left out", "gosredirector.ea.com" not in names and "pinned.example" not in names)
check("and those off by default that no template switched on - Riot chat is not even on 443",
      "wr.pvp.net" not in names)
check("  while one a template has switched on is checked",
      "wr.pvp.net" in [d for _, _, d in admin.diag_items({("riot", "chat")})])
check("a suffix has a real host under it to try, where one is always there, or none",
      admin.PROBE_HOSTS["jtvnw.net"] == "static-cdn.jtvnw.net" and admin.PROBE_HOSTS["nflxvideo.net"] is None
      and all(h is None or h.endswith("." + d) for d, h in admin.PROBE_HOSTS.items()))
known = {"spotify.com", "www.ea.com"}
admin.resolvable = lambda n: n in known
check("the bare domain when it has an address, www. when only that does, nothing when neither",
      admin.pick_host("spotify.com") == "spotify.com" and admin.pick_host("ea.com") == "www.ea.com"
      and admin.pick_host("scdn.co") is None)

print("the relay")
ran = []


class Now:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


sync.threading.Thread = Now
sync.probe_all = lambda hosts, address=None: ran.append((list(hosts), address)) or {
    h: ["ok", "HTTP 200", 40] for h in hosts}
check("a request is checked through the relay's own 443",
      sync.start_probe({"id": "j1", "hosts": ["spotify.com", "bad host!"]})
      and ran == [(["spotify.com"], "127.0.0.1")], repr(ran))
check("once", sync.start_probe({"id": "j1", "hosts": ["spotify.com"]}) is False and len(ran) == 1)
check("and what it found waits for the next sync",
      sync.PROBE_DONE["result"] == {"id": "j1", "results": {"spotify.com": ["ok", "HTTP 200", 40]}})
check("which carries it, and lets it go once the panel has it",
      'payload["probe_result"] = checked' in src and 'start_probe(answer.get("probe"))' in src
      and 'if checked and PROBE_DONE["result"] is checked:' in src)

print("the panel")
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
store.set_setting("probe_job", json.dumps({"id": "j1", "asked_at": panel.now(),
                                           "hosts": ["spotify.com"], "items": []}))
check("it hands the hosts to the relays while the request is fresh",
      panel.probe_job(store) == {"id": "j1", "hosts": ["spotify.com"]})
store.set_setting("probe_job", json.dumps({"id": "j0", "asked_at": "2020-01-01T00:00:00+00:00",
                                           "hosts": ["spotify.com"]}))
check("and not an old one", panel.probe_job(store) is None)
psrc = read("templates/smartdns-panel")
check("and keeps what each relay found",
      '"probe": probe_job(self.store),' in psrc
      and 'INSERT OR REPLACE INTO probe_results (source, job_id, at, data)' in psrc)

print("the page")
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.SYNC_ENV_HERE = os.path.join(tmp, "none")
check("before any check, a button and what it does", "action='/p/diagnose-start'" in admin.diagnose_page()
      and "از مسیر مشتری" in admin.diagnose_page())
job = {"id": "j2", "asked_at": admin.now(),
       "items": [["spotify", "Spotify", "spotify.com", "spotify.com"],
                 ["spotify", "Spotify", "scdn.co", None],
                 ["ea", "EA", "ea.com", "www.ea.com"],
                 ["ea", "EA", "origin.com", "www.origin.com"]],
       "hosts": ["spotify.com", "www.ea.com", "www.origin.com"]}
admin.STORE.run("INSERT OR REPLACE INTO settings (key, value) VALUES ('probe_job', ?)",
                (json.dumps(job),))
admin.STORE.run("INSERT INTO probe_results VALUES ('exit', 'j2', 'x', ?)", (json.dumps({
    "spotify.com": ["ok", "HTTP 200", 30], "www.ea.com": ["fail", "timeout", 6000],
    "www.origin.com": ["ok", "HTTP 301", 50]}),))
admin.STORE.run("INSERT INTO probe_results VALUES ('198.51.100.7', 'j2', 'x', ?)", (json.dumps({
    "spotify.com": ["fail", "certificate", 80], "www.ea.com": ["fail", "timeout", 6000],
    "www.origin.com": ["fail", "reset", 90]}),))
admin.STORE.run("UPDATE probe_results SET data = ? WHERE source = 'exit'", (json.dumps({
    "spotify.com": ["ok", "expired certificate", 30], "www.ea.com": ["fail", "local address", 3],
    "www.origin.com": ["ok", "HTTP 301", 50]}),))
page = admin.diagnose_page()
check("the exit beside each relay", page.index("از سرور خارج") < page.index("از مسیر مشتری — رله 198.51.100.7"))
check("each service summed up", "✓ همه (1)" in page and "1 از 2" in page and "✗ هیچ‌کدام (2)" in page)
check("the ones in trouble first", page.index("<b>EA</b>") < page.index("<b>Spotify</b>"))
check("an expired certificate is a warning, and said to be the service's own",
      "⚠ گواهی خود سایت منقضی یا ناقص است" in page)
check("a name the exit found pointing at itself is said so on the relay's side too",
      page.count("به آدرسی داخلی اشاره می‌کند") == 2, page.count("به آدرسی داخلی اشاره می‌کند"))
check("and what went wrong, in words", "گواهی نامعتبر" in page
      and "این اسم خودش سایتی ندارد" in page)
asrc = read("templates/smartdns-admin")
check("on its own page in the menu, run in a thread of its own",
      '("diagnose", "عیب‌یابی")' in asrc and "threading.Thread(target=run_diagnosis, daemon=True)" in asrc)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
