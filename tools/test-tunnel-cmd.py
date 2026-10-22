#!/usr/bin/env python3
"""smartdns-tunnel: see the tunnel, stop it, start it - one command each.

The real script, against a machine laid out in a temporary directory, with the
system commands it calls answered by stand-ins that write down what they were
asked to do.
"""
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "templates", "smartdns-tunnel")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


BASH = shutil.which("bash")
if BASH is None:
    print("bash not available - skipping")
    sys.exit(0)


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


tmp = tempfile.mkdtemp()
fake = os.path.join(tmp, "bin")
os.makedirs(fake)
CALLS = os.path.join(tmp, "calls")


def tool(name, body):
    p = os.path.join(fake, name)
    with open(p, "w", newline="\n") as fh:
        fh.write("#!/bin/bash\n" + body + "\n")
    os.chmod(p, 0o755)


tool("id", 'echo "${FAKE_UID:-0}"')
tool("sleep", "exit 0")
tool("systemctl", 'echo "systemctl $*" >> "$CALLS"; [ "$1" = is-active ] && echo "${FAKE_STATE:-active}"; exit 0')
tool("curl", 'echo "curl $*" >> "$CALLS"; printf "%s" "${FAKE_CODE:-200}"')
tool("ss", 'for i in $(seq 1 ${FAKE_CONNS:-0}); do echo conn; done')


def machine(role, configured=True, tunnel="backpack"):
    etc = tempfile.mkdtemp(dir=tmp)
    env_file = os.path.join(etc, "sync.env" if role == "relay" else "panel.env")
    with open(env_file, "w", newline="\n") as fh:
        fh.write("X=1\n")
        if configured:
            fh.write("TUNNEL=%s\nTUNNEL_TRANSPORT=stealth\nTUNNEL_DIRECTION=reverse\nTUNNEL_PORT=8444\n" % tunnel)
    if configured:
        os.makedirs(os.path.join(etc, "tunnel"))
        open(os.path.join(etc, "tunnel", "tunnel.toml"), "w").close()
    return etc, env_file


def run(etc, *args, **env):
    if os.path.exists(CALLS):
        os.remove(CALLS)
    e = dict(os.environ, PATH=posix(fake) + ":" + os.environ.get("PATH", ""),
             SMARTDNS_ETC=posix(etc), CALLS=posix(CALLS))
    e.update(env)
    r = subprocess.run([BASH, posix(TOOL)] + list(args), capture_output=True, text=True,
                       timeout=60, env=e)
    calls = open(CALLS).read() if os.path.exists(CALLS) else ""
    return r, calls


def env_value(path, key):
    for line in open(path):
        if line.startswith(key + "="):
            return line.strip().split("=", 1)[1]
    return None


print("the command itself")
check("it parses", subprocess.run([BASH, "-n", posix(TOOL)], capture_output=True).returncode == 0)
r = subprocess.run([BASH, posix(TOOL), "-h"], capture_output=True, text=True)
check("-h explains it, without needing root", r.returncode == 0 and "smartdns-tunnel off" in r.stdout, r.stdout)
etc, _ = machine("relay")
check("a command it does not know is refused", run(etc, "sideways")[0].returncode != 0)
r, _ = run(etc, "off", FAKE_UID="1000")
check("it wants root", r.returncode != 0 and "sudo" in r.stderr, r.stderr)

print("status")
r, calls = run(etc)
check("on a relay: what it is", "BackPack, stealth, reverse, port 8444" in r.stdout, r.stdout)
check("  whether it is on and running", "setting    on" in r.stdout and "service    active" in r.stdout, r.stdout)
check("  and whether it carries traffic, asked of the tunnel's own end, not of nginx",
      "through the tunnel" in r.stdout and "127.0.0.1:18443" in calls, r.stdout + calls)
r, _ = run(etc, FAKE_CODE="000")
check("a tunnel that carries nothing says so", "straight to the exit" in r.stdout, r.stdout)
xetc, _ = machine("exit")
r, _ = run(xetc, FAKE_CONNS="3")
check("on an exit: how many tunnel connections it has with the relay",
      "3 tunnel connection(s)" in r.stdout, r.stdout)
netc, _ = machine("relay", configured=False)
r, _ = run(netc)
check("no tunnel set up: it says so, and how to set one up",
      r.returncode == 0 and "no tunnel is set up" in r.stdout and "--tunnel" in r.stdout, r.stdout)

print("off - back to plain TCP in one command")
etc, env_file = machine("relay")
r, calls = run(etc, "off")
check("it stops the tunnel and keeps it from starting at boot",
      r.returncode == 0 and "systemctl disable --now smartdns-tunnel.service" in calls, calls)
check("and says traffic goes straight to the exit", "straight to the exit now" in r.stdout, r.stdout)
check("the choice is kept, so an upgrade does not bring it back", env_value(env_file, "TUNNEL") == "off")
check("the rest of the settings stay, for on", env_value(env_file, "TUNNEL_TRANSPORT") == "stealth")
check("the other lines of the file are left alone", env_value(env_file, "X") == "1")
r, calls = run(netc, "off")
check("off with no tunnel does nothing and says there is none",
      r.returncode == 0 and "systemctl" not in calls and "no tunnel" in r.stdout, calls + r.stdout)

print("on - the way back")
r, calls = run(etc, "on")
check("it starts the tunnel and keeps it on at boot",
      r.returncode == 0 and "systemctl enable --now smartdns-tunnel.service" in calls, calls)
check("and the choice is kept", env_value(env_file, "TUNNEL") == "backpack")
check("then shows where things stand", "BackPack, stealth" in r.stdout, r.stdout)
r, calls = run(netc, "on")
check("on with nothing set up refuses, and says how to set one up",
      r.returncode != 0 and "systemctl" not in calls and "--tunnel" in r.stdout, calls + r.stdout)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
