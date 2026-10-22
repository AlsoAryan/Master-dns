#!/usr/bin/env python3
"""BackPack's performance presets, picked by the admin for a tunnel.

What has to hold: balance, turbo, aggressive and throughput are BackPack's
own presets with its own numbers (v1.8.5's), and each kind of tunnel has
the ones BackPack gives it - throughput on kcp alone among the reverse
transports, no aggressive on the direct engine, no throughput on layer 3.
The installer, the admin panel and the relay's sync write the very same
lines for every end of every kind, so the two ends never disagree - error
correction above all, which BackPack does not negotiate. None picked is
the tunnel exactly as before. The pairing token carries a preset, the
panel's word on a tunnel keeps it, and a preset a tunnel does not have is
refused with the reason.
"""
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

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


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


sync = load("templates/smartdns-sync", "sync")
admin = load("templates/smartdns-admin", "admin")
sync.tunnel_cert = lambda: ("/tmp/c.pem", "/tmp/k.pem")
sync.CFG = {"SELF_IP": "198.51.100.7"}
LOGIC = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
PRESETS = ("",) + sync.TUNNEL_PRESETS

print("which kind has which - BackPack's own")
ok = {(d, t, p) for d in sync.TUNNEL_TRANSPORTS for t in sync.TUNNEL_TRANSPORTS[d]
      for p in PRESETS if sync.preset_ok(d, t, p)}
check("reverse: balance, turbo, aggressive - and throughput on kcp alone",
      all(("reverse", t, p) in ok for t in sync.TUNNEL_TRANSPORTS["reverse"]
          for p in ("", "balance", "turbo", "aggressive"))
      and {t for d, t, p in ok if d == "reverse" and p == "throughput"} == {"kcp"})
check("direct: balance, turbo, throughput - no aggressive",
      {p for d, t, p in ok if d == "direct"} == {"", "balance", "turbo", "throughput"})
check("l3: balance, turbo, aggressive - no throughput",
      {p for d, t, p in ok if d == "l3"} == {"", "balance", "turbo", "aggressive"})
check("the admin panel and the relay agree on it",
      all(admin.preset_ok(d, t, p) == sync.preset_ok(d, t, p)
          for d in sync.TUNNEL_TRANSPORTS for t in sync.TUNNEL_TRANSPORTS[d] for p in PRESETS))

print("the same lines everywhere")
sides = {"reverse": ("server", "client"), "direct": ("direct",), "l3": ("l3",)}
combos = [(d, t, p, side) for d, t, p in sorted(ok) for side in sides[d]]
py = {c: sync.tunnel_tuning({"direction": c[0], "transport": c[1], "preset": c[2]}, c[3])
      for c in combos}
check("the admin panel writes what the relay does, %d ends of tunnels" % len(combos),
      all(admin.tunnel_tuning({"direction": d, "transport": t, "preset": p}, s) == py[(d, t, p, s)]
          for d, t, p, s in combos))
BASH = shutil.which("bash")
if BASH:
    def function(name):
        m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), LOGIC, re.S | re.M)
        return m.group(0) if m else ""
    tmp = tempfile.mkdtemp()
    harness = os.path.join(tmp, "h.sh")
    with open(harness, "w", newline="\n", encoding="utf-8") as fh:
        fh.write("set -u\n" + function("tunnel_preset_ok") + function("tunnel_tuning"))
        for d, t, p, s in combos:
            fh.write('echo "@@ %s %s %s %s"; TUNNEL_PRESET=%s; TUNNEL_TRANSPORT=%s; tunnel_tuning %s\n'
                     % (d, t, p or "-", s, p, t, s))
    out = subprocess.run([BASH, posix(harness)], capture_output=True, timeout=120)
    text = out.stdout.decode().replace("\r\n", "\n")
    got = {}
    for part in text.split("@@ ")[1:]:
        head, _, body = part.partition("\n")
        d, t, p, s = head.split()
        got[(d, t, "" if p == "-" else p, s)] = body
    differ = [c for c in combos if got.get(c) != py[c]]
    check("and the installer, line for line", not differ and len(got) == len(combos),
          str(differ[:3]) + " " + (out.stderr.decode()[-300:]))
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print("  (bash not available - the installer's part skipped)")


def ends(direction, transport, preset):
    spec = {"direction": direction, "transport": transport, "port": 8477}
    if preset:
        spec["preset"] = preset
    relay = tomllib.loads(sync.tunnel_toml_text(spec, "203.0.113.9", "s3cret"))
    exit_ = tomllib.loads(admin.exit_tunnel_toml("198.51.100.7", spec, "s3cret", "/tmp/x"))
    return list(relay.values())[0], list(exit_.values())[0]


print("what a preset does")
r, e = ends("reverse", "xdi", "aggressive")
check("a KCP tunnel's error correction the same at both ends",
      (r["kcp_datashards"], r["kcp_parityshards"]) == (e["kcp_datashards"], e["kcp_parityshards"])
      == (10, 4) and r["kcp_sndwnd"] == 2048)
check("  the relay the server's knobs, the exit the client's",
      r["channel_size"] == 8192 and r["heartbeat"] == 25 and "connection_pool" not in r
      and e["connection_pool"] == 16 and e["aggressive_pool"] is True and "channel_size" not in e)
r, e = ends("reverse", "kcp", "throughput")
check("throughput: its own KCP - a slower tick, batched acknowledgements, a big window",
      r["kcp_interval"] == 20 and r["kcp_acknodelay"] is False and r["kcp_sndwnd"] == 4096
      and r["kcp_parityshards"] == e["kcp_parityshards"] == 1)
r, e = ends("reverse", "wssmux", "turbo")
check("a mux transport: its mux at both ends, under each end's own name",
      r["mux_con"] == 8 and e["mux_session"] == 8
      and r["mux_streambuffer"] == e["mux_streambuffer"] == 2 << 20)
r, e = ends("direct", "stealth", "throughput")
check("the direct engine: its sessions and mux", r["sessions"] == e["sessions"] == 4
      and r["mux_recievebuffer"] == 32 << 20)
r, e = ends("l3", "xdi", "aggressive")
check("layer 3: the interface's queue and the carrier's buffers, at both ends",
      r["txqueuelen"] == e["txqueuelen"] == 16384 and r["sockbuf"] == 32 << 20
      and r["qdisc"] == "fq_codel" and r["preset"] == "aggressive")
r, e = ends("reverse", "xdi", "")
check("none: as before - the KCP drive, no error correction, nothing else",
      "preset" not in r and "kcp_datashards" not in r and r["kcp_sndwnd"] == 1024
      and "channel_size" not in r and "connection_pool" not in e)
r, e = ends("reverse", "stealth", "")
check("  and nothing at all on a plain transport", not any(k.startswith(("kcp_", "mux_", "so_"))
                                                           for k in list(r) + list(e)))

print("carried and kept")
if BASH:
    def sh(code):
        tmp = tempfile.mkdtemp()
        h = os.path.join(tmp, "h.sh")
        names = ("tunnel_transport_ok", "tunnel_preset_ok", "tunnel_port_problem",
                 "parse_tunnel_spec")
        variables = "\n".join(re.findall(r"^TUNNEL_(?:REVERSE|DIRECT|L3)_TRANSPORTS=.*$|"
                                         r"^TUNNEL_LOCAL_\w+=.*$", LOGIC, re.M))
        with open(h, "w", newline="\n", encoding="utf-8") as fh:
            fh.write("set -u\n" + variables + "\n")
            for n in names:
                m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(n), LOGIC, re.S | re.M)
                fh.write(m.group(0) if m else "")
            fh.write(code + "\n")
        r = subprocess.run([BASH, posix(h)], capture_output=True, timeout=60)
        shutil.rmtree(tmp, ignore_errors=True)
        return r.stdout.decode().strip()
    check("the pairing token carries it",
          sh('parse_tunnel_spec bp-xdi-8477-l-aggressive && echo "$TUNNEL_DIRECTION $TUNNEL_TRANSPORT $TUNNEL_PRESET"')
          == "l3 xdi aggressive"
          and sh('parse_tunnel_spec bp-stealth-8444-r && echo "[$TUNNEL_PRESET]"') == "[]")
    check("  and one this kind does not have is not taken",
          sh('parse_tunnel_spec bp-xdi-8477-l-throughput || echo refused') == "refused"
          and sh('parse_tunnel_spec bp-stealth-8444-d-aggressive || echo refused') == "refused")
check("the installer makes the token with it, and keeps it across runs",
      '${TUNNEL_PRESET:+-$TUNNEL_PRESET}"' in LOGIC
      and 'set_env_key /etc/smart-dns/panel.env TUNNEL_PRESET' in LOGIC
      and 'set_env_key /etc/smart-dns/sync.env TUNNEL_PRESET' in LOGIC)
check("the relay takes the panel's, and none it does not have",
      sync.clean_tunnel({"on": True, "direction": "l3", "transport": "xdi", "port": 8477,
                         "preset": "turbo"})["preset"] == "turbo"
      and sync.clean_tunnel({"on": True, "direction": "l3", "transport": "xdi", "port": 8477,
                             "preset": "throughput"}) is None
      and "preset" not in sync.clean_tunnel({"on": True, "direction": "reverse",
                                             "transport": "tcp", "port": 8444}))

print("the admin panel")
check("refused with the reason",
      "throughput" in admin.preset_problem("reverse", "xdi", "throughput")
      and "aggressive" in admin.preset_problem("direct", "tcp", "aggressive")
      and admin.preset_problem("l3", "xdi", "turbo") == ""
      and admin.preset_problem("reverse", "tcp", "fast") != "")
asrc = open(os.path.join(ROOT, "templates", "smartdns-admin"), encoding="utf-8").read()
check("a choice on the tunnel's form, kept with the tunnel, shown on its card",
      "<select name='preset'>" in asrc and asrc.count('spec["preset"] = one("preset")') == 2
      and '"، " + spec["preset"] if spec.get("preset") else ""' in asrc)


class NoRows:
    def one(self, *a):
        return None


admin.STORE = NoRows()
admin.exit_address = lambda: "198.51.100.1"
admin.installer_tunnel = lambda: None
admin.node_list = lambda: []
admin.relay_tunnel = lambda ip, to: {}
card = admin.tunnel_block("p", "203.0.113.5", "198.51.100.1")
said = dict(re.findall(r"<option value='([a-z]+)' data-d='([a-z0-9 ]*)'", card))
check("the form shows only what the direction picked has: direct, not reverse's transports",
      said.get("xdi") == "reverse l3" and said.get("sni") == "l3"
      and said.get("kcp") == "reverse" and said.get("stealth") == "reverse direct"
      and said.get("aggressive") == "reverse l3" and said.get("throughput") == "reverse direct"
      and "onchange='tunnelPick(this)'" in card
      and card.index("</form><script>") < card.index("tunnelPick(document.currentScript"))

print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
