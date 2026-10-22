#!/usr/bin/env python3
"""The way back: the customer panel, for an address not let in.

What has to hold: while the relay's door is closed, port 53 from an address
that is not let in - never registered, or out of volume or days - goes to a
resolver of its own, which answers the customer panel's names truly and every
other name with the relay itself - no service, no hijack, no upstream for the
rest - at a limited rate per address; port 80 from such an address goes to a
page that sends it to the customer panel, the relay's own or, with none, the
one the panel names; the redirects and the resolver are set once, not rebuilt
every sync, and put right on a relay upgraded from before the page; and when
the door opens, or there is no panel to send anybody to, all of it goes.
"""
import http.client
import threading
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

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


spec = importlib.util.spec_from_loader("sync", importlib.machinery.SourceFileLoader(
    "sync", os.path.join(HERE, "..", "templates", "smartdns-sync")))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
sync.log = lambda *a: None

tmp = tempfile.mkdtemp()
main = os.path.join(tmp, "smart-dns.conf")
with open(main, "w") as fh:
    fh.write("server=1.1.1.1\ncache-size=10000\ndomain-needed\nbogus-priv\nno-hosts\n"
             "bind-interfaces\nlisten-address=127.0.0.1,198.51.100.7\n"
             "address=/tiktok.com/198.51.100.7\n")
sync.HIJACK_CONF = main
sync.PROFILE_DIR = os.path.join(tmp, "prof")
sync.GATE_CONF = os.path.join(sync.PROFILE_DIR, "gate.conf")
sync.ENFORCE_FILE = os.path.join(tmp, "enforce.conf")
sync.GATE_UNIT_FILE = os.path.join(tmp, "gate.service")
open(sync.GATE_UNIT_FILE, "w").close()
sync.current_upstream = lambda: ["1.1.1.1", "9.9.9.9"]
sync.CFG = {"PANEL_DOMAIN": "users.example.com", "SELF_IP": "198.51.100.7"}


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


calls, chain, active = [], {"exists": False, "rules": []}, set()


def fake_nft(*args):
    calls.append(("nft",) + args)
    if args[:2] == ("list", "chain"):
        if not chain["exists"]:
            return R("", 1)
        return R("\n".join(chain["rules"]))
    if args[:2] == ("add", "chain"):
        chain["exists"] = True
    if args[:2] == ("flush", "chain"):
        chain["rules"] = []
    if args[:2] == ("add", "rule"):
        chain["rules"].append(" ".join(args[5:]))
    return R()


def fake_sh(*args):
    calls.append(args)
    if args[:2] == ("systemctl", "is-active"):
        return R("active\n" if args[2] in active else "inactive\n")
    if args[:2] == ("systemctl", "restart"):
        active.add(args[2])
    if args[:2] == ("systemctl", "stop"):
        active.discard(args[2])
    return R()


sync.nft, sync.sh = fake_nft, fake_sh

print("with the door open")
check("nothing is set up", sync.apply_gate_dns() is False
      and not os.path.exists(sync.GATE_CONF) and not chain["exists"])

print("with the door closed")
open(sync.ENFORCE_FILE, "w").close()
check("the way back is set up", sync.apply_gate_dns() is True)
conf = open(sync.GATE_CONF).read()
check("its resolver knows the customer panel's name, asked of the relay's upstream",
      "server=/users.example.com/1.1.1.1" in conf and "server=/users.example.com/9.9.9.9" in conf)
check("  every other name answered with the relay itself, never kept",
      "address=/#/198.51.100.7" in conf and "local-ttl=0" in conf)
check("  and nothing else: no upstream for the rest, no service rule",
      "\nserver=1.1.1.1" not in conf and "tiktok" not in conf and "no-resolv" in conf)
check("  on its own port, listening where the main resolver does",
      "port=5299" in conf and "listen-address=127.0.0.1,198.51.100.7" in conf
      and "cache-size=10000" not in conf)
check("  and it runs", "smartdns-dns-gate" in active)
check("unregistered port 53 goes to it, both kinds",
      any("udp dport 53 redirect to :5299" in r for r in chain["rules"])
      and any("tcp dport 53 redirect to :5299" in r for r in chain["rules"])
      and all("saddr != @allowed" in r for r in chain["rules"]))
check("  at a limited rate per address, before it is answered",
      "gateflood" in chain["rules"][0] and "drop" in chain["rules"][0])
check("unregistered port 80 goes to the page that sends them to the panel",
      any("tcp dport 80 redirect to :5298" in r for r in chain["rules"]))
check("and DoT and DoH to nginx's doors for the gate",
      any("tcp dport 853 redirect to :8853" in r for r in chain["rules"])
      and any("tcp dport 443 redirect to :5297" in r for r in chain["rules"]))
calls.clear()
sync.apply_gate_dns()
check("the next sync changes nothing", not any(c[:2] in (("nft", "flush"), ("nft", "add"))
                                                or c[:2] == ("systemctl", "restart")
                                                for c in calls), str(calls))

chain["rules"] = [r for r in chain["rules"] if ":5298" not in r and "gateflood" not in r]
sync.apply_gate_dns()
check("a relay upgraded from before the page is given it",
      any(":5298" in r for r in chain["rules"]) and len(chain["rules"]) == 6)

print("the admin turns the page off")
sync.PANEL_TO["url"] = ""
answer = {"portal": "", "portal_open": False}
sync.PORTAL["open"] = answer.get("portal_open") is not False
active.discard("smartdns-dns-gate")
sync.apply_gate_dns()
conf = open(sync.GATE_CONF).read()
check("the panel's own name still answers, so a customer who goes to it gets in",
      "server=/users.example.com/1.1.1.1" in conf)
check("  but no other name does: nothing sends them there by itself",
      "address=/#/" not in conf and "smartdns-dns-gate" in active)
check("  and port 80 no longer goes to the page",
      not any(":5298" in r for r in chain["rules"]) and len(chain["rules"]) == 5)
check("  DoT and DoH still reach the gate - its answers, the panel's name only",
      any(":8853" in r for r in chain["rules"]) and any(":5297" in r for r in chain["rules"]))
calls.clear()
sync.apply_gate_dns()
check("  and the next sync leaves it so", not any(c[:2] in (("nft", "flush"), ("nft", "add"))
                                                  or c[:2] == ("systemctl", "restart")
                                                  for c in calls), str(calls))
sync.PORTAL["open"] = True
sync.apply_gate_dns()
check("turned on again, it all comes back",
      "address=/#/198.51.100.7" in open(sync.GATE_CONF).read()
      and any(":5298" in r for r in chain["rules"]) and len(chain["rules"]) == 6)
src = open(os.path.join(HERE, "..", "templates", "smartdns-sync"), encoding="utf-8").read()
check("the relay takes it from the panel's answer, a panel from before meaning open",
      'opened = answer.get("portal_open") is not False' in src
      and 'PORTAL["open"] = opened' in src)
psrc = open(os.path.join(HERE, "..", "templates", "smartdns-panel"), encoding="utf-8").read()
check("the panel says it to every relay, open unless the admin said no",
      '"portal_open": self.store.setting("portal_open")' in psrc and '!= "0"' in psrc)
asrc = open(os.path.join(HERE, "..", "templates", "smartdns-admin"), encoding="utf-8").read()
check("the admin's settings have the tick, on until turned off",
      "action='/%s/portal-open'" in asrc and "باز کردن خودکار پنل مشتری برای کسی که سرویس ندارد"
      in asrc and 'if rest == "portal-open":' in asrc
      and '"portal-open": "settings"' in asrc)

print("a relay with no panel of its own")
sync.CFG = {"PANEL_DOMAIN": "", "SELF_IP": "198.51.100.8"}
sync.PANEL_TO["url"] = ""
check("its page sends people to the panel the panel names",
      sync.portal_target("https://users.example.com:8443/") == "https://users.example.com:8443/")
check("  never to anything that is not an https address",
      sync.portal_target("http://evil.example/") == "" and sync.portal_target(None) == "")
sync.PORTAL["url"] = sync.portal_target("https://users.example.com:8443/")
check("and its resolver answers that panel's name truly",
      sync.apply_gate_dns() is True and "server=/users.example.com/1.1.1.1" in
      open(sync.GATE_CONF).read() and "address=/#/198.51.100.8" in open(sync.GATE_CONF).read())
sync.CFG = {"PANEL_DOMAIN": "r2.example.com", "SELF_IP": "198.51.100.8"}
check("with a panel of its own, its own",
      sync.portal_target("https://users.example.com:8443/") == "https://r2.example.com:8443/")
sync.PANEL_TO["url"] = "https://users.example.com:8443/"
check("  unless the admin took that panel off it",
      sync.portal_target("") == "https://users.example.com:8443/")
sync.PANEL_TO["url"] = ""

print("the page on port 80")
srv = sync.http.server.HTTPServer(("127.0.0.1", 0), sync.PortalPage)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def ask(method, path, host="connectivitycheck.gstatic.com"):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request(method, path, headers={"Host": host})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Location"), body


sync.PORTAL["url"] = "https://users.example.com:8443/"
st, loc, body = ask("GET", "/generate_204")
check("Android's check is sent to the panel, which is what makes it offer to open it",
      st == 302 and loc == "https://users.example.com:8443/", repr((st, loc)))
check("  with a link on the page for whatever does not follow",
      b"users.example.com:8443" in body and b"dir='rtl'" in body)
check("so is the iPhone's, and any http site",
      ask("GET", "/hotspot-detect.html", "captive.apple.com")[:2] == (302, loc)
      and ask("GET", "/", "example.org")[:2] == (302, loc))
check("  a HEAD too, with no page", ask("HEAD", "/")[:2] == (302, loc))
sync.PORTAL["url"] = ""
check("with nowhere to send them, nothing", ask("GET", "/")[0] == 503)
srv.shutdown()
src = open(os.path.join(HERE, "..", "templates", "smartdns-sync"), encoding="utf-8").read()
check("it runs beside the sync, and never stops the relay if the port is taken",
      "threading.Thread(target=serve_portal, daemon=True).start()" in src
      and "could not start on %d" in src)
sync.CFG = {"PANEL_DOMAIN": "users.example.com", "SELF_IP": "198.51.100.7"}

print("undone")
sync.CFG = {"PANEL_DOMAIN": ""}
sync.PORTAL["url"] = ""
sync.apply_gate_dns()
check("with no panel to send anybody to, the redirects go and the resolver stops",
      not chain["rules"] and "smartdns-dns-gate" not in active)
sync.CFG = {"PANEL_DOMAIN": "users.example.com"}
sync.apply_gate_dns()
os.remove(sync.ENFORCE_FILE)
sync.apply_gate_dns()
check("with the door open again, the same", not chain["rules"]
      and "smartdns-dns-gate" not in active)

print("the installer")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
check("carries the unit, and takes it away on uninstall",
      "install_payload DNS_GATE_UNIT /etc/systemd/system/smartdns-dns-gate.service" in logic
      and "systemctl disable --now smartdns-dns-gate.service" in logic
      and "nft delete chain inet smartdns gatedns" in logic)
unit = open(os.path.join(HERE, "..", "templates", "smartdns-dns-gate.service"),
            encoding="utf-8").read()
check("  a unit that reads its own file and nothing else",
      "--conf-file=/etc/smartdns-profiles/gate.conf" in unit and "conf-dir" not in unit)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
