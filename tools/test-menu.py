#!/usr/bin/env python3
"""smartdns-menu: every command in one place, and each one it runs is real.

Driven the way a person drives it - numbers and answers on stdin - against
stand-ins for every tool, which write down what they were asked to do. Then
held to the installer, so it never offers a command that is not installed, and
to the README, so a command documented there is never missing from it.
"""
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
TOOL = os.path.join(ROOT, "templates", "smartdns-menu")
SRC = open(TOOL, encoding="utf-8").read()
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
TOOLS = ["smartdns", "smartdns-acl", "smartdns-logs", "smartdns-rules", "smartdns-watch",
         "smartdns-shape", "smartdns-tunnel", "smartdns-restart", "smartdns-cert", "smartdns-access"]
for t in TOOLS:
    tool(t, 'echo "%s $*" >> "$CALLS"' % t)
vfile = os.path.join(tmp, "version")
open(vfile, "w").write("0.9.9\n")


def machine(role):
    etc = tempfile.mkdtemp(dir=tmp)
    open(os.path.join(etc, "sync.env" if role == "relay" else "panel.env"), "w").write("X=1\n")
    return etc


RELAY, EXIT = machine("relay"), machine("exit")


def drive(etc, answers, **env):
    if os.path.exists(CALLS):
        os.remove(CALLS)
    e = dict(os.environ, PATH=posix(fake) + ":" + os.environ.get("PATH", ""),
             SMARTDNS_ETC=posix(etc), SMARTDNS_VERSION_FILE=posix(vfile), CALLS=posix(CALLS))
    e.update(env)
    r = subprocess.run([BASH, posix(TOOL)], capture_output=True, timeout=60, env=e,
                       input=("\n".join(answers) + "\n").encode())
    out = r.stdout.decode("utf-8", "replace")
    calls = open(CALLS).read() if os.path.exists(CALLS) else ""
    return r.returncode, out, calls


print("the command itself")
check("it parses", subprocess.run([BASH, "-n", posix(TOOL)], capture_output=True).returncode == 0)
r = subprocess.run([BASH, posix(TOOL), "-h"], capture_output=True, text=True)
check("-h explains it, without needing root", r.returncode == 0 and "smartdns-menu" in r.stdout, r.stdout)
rc, out, _ = drive(RELAY, ["0"], FAKE_UID="1000")
check("it wants root", rc != 0)
rc, out, _ = drive(tempfile.mkdtemp(dir=tmp), ["0"])
check("a machine without master dns is told so", rc != 0)

print("on a relay")
rc, out, calls = drive(RELAY, ["0"])
check("it opens on the relay's menu, with its version", rc == 0 and "master dns 0.9.9 - relay" in out, out[:300])
check("the top menu quits, and says so", "0) quit" in out, out)
for part in ("status and logs", "domains", "customers and access", "tunnel to the exit",
             "restart everything", "installation"):
    check("it offers %s" % part, part in out)
rc, out, calls = drive(RELAY, ["2", "0", "0"])
check("a menu under it goes back rather than quitting", "0) back" in out, out[-500:])
rc, out, calls = drive(RELAY, ["2", "3", "example.org", "", "0", "0"])
check("routing a domain runs smartdns add with what was typed",
      calls.strip() == "smartdns add example.org", calls)
check("  and shows the command before it runs", "$ smartdns add example.org" in out, out[-400:])
rc, out, calls = drive(RELAY, ["2", "3", "", "0", "0"])
check("an empty answer goes back without running anything", calls == "", calls)
rc, out, calls = drive(RELAY, ["2", "3", "a.com; touch /tmp/x", "", "0", "0"])
check("what is typed reaches the command as one argument, never as shell",
      calls.strip() == "smartdns add a.com; touch /tmp/x", calls)
rc, out, calls = drive(RELAY, ["3", "7", "y", "", "0", "0"])
check("closing the relay asks first, then runs enforce on", calls.strip() == "smartdns-acl enforce on", calls)
rc, out, calls = drive(RELAY, ["3", "7", "n", "0", "0"])
check("and answering no runs nothing", calls == "", calls)
rc, out, calls = drive(RELAY, ["3", "3", "5.1.2.3", "ali", "", "0", "0"])
check("registering an address passes the address and the name", calls.strip() == "smartdns-acl add 5.1.2.3 ali", calls)
rc, out, calls = drive(RELAY, ["4", "2", "y", "", "0", "0"])
check("the tunnel goes back to plain TCP from here", calls.strip() == "smartdns-tunnel off", calls)
rc, out, calls = drive(RELAY, ["1", "7", "", "", "0", "0"])
check("watching everybody is an empty answer", calls.strip() == "smartdns-watch", calls)
rc, out, calls = drive(RELAY, ["5", "y", "", "0"])
check("restarting asks first on a relay, where customers notice", calls.strip() == "smartdns-restart", calls)
rc, out, calls = drive(RELAY, ["x", "99", "0"])
check("an answer that is not on the menu is ignored", rc == 0 and calls == "", calls)
rc, out, calls = drive(RELAY, ["2"])
check("running out of input ends it cleanly", rc == 0)

print("on an exit")
rc, out, calls = drive(EXIT, ["0"])
check("it opens on the exit's menu", "master dns 0.9.9 - exit" in out and "admin panel" in out, out[:400])
check("with nothing that belongs on a relay", "domains" not in out and "customers" not in out, out)
rc, out, calls = drive(EXIT, ["2", "1", "", "0", "0"])
check("the admin panel's address is one choice away", calls.strip() == "smartdns-access", calls)
rc, out, calls = drive(EXIT, ["1", "0", "0"])
check("its logs menu has no relay-only tools", "smartdns-watch" not in out and "smartdns status" not in out, out)

print("every command it runs is installed")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
ran = set(re.findall(r"\brun (smartdns[\w-]*)", SRC))
check("it runs the tools it should", {"smartdns", "smartdns-acl", "smartdns-logs", "smartdns-tunnel"} <= ran, str(ran))
for cmd in sorted(ran):
    check("%s is installed by the installer" % cmd,
          re.search(r"(payload \w+|\| sed [^\n]*) > /usr/local/bin/%s\b" % re.escape(cmd), logic) is not None
          or re.search(r"payload \w+ > /usr/local/bin/%s$" % re.escape(cmd), logic, re.M) is not None)
check("the installer installs the menu itself", "payload SMARTDNS_MENU > /usr/local/bin/smartdns-menu" in logic)

print("every command in the README is in the menu")
EXAMPLES = {"example.com", "api.example.com", "spotify", "5.188.44.19", "ali", "<ip>|--all", "test",
            "gemini.google.com", "500", "9443", "[new]", "panel.example.com", "sudo", "bash"}
# The published README, and only that: the pair is how the published tree is
# told from a working copy, whose README is something else. README_DIR points
# at another tree's pair, to check one before it is published.
docs = os.environ.get("README_DIR") or ROOT
readme = os.path.join(docs, "README.md")
if not (os.path.exists(readme) and os.path.exists(os.path.join(docs, "README.fa.md"))):
    print("  --   not the published tree - skipped")
else:
    text = open(readme, encoding="utf-8").read()
    blocks = re.findall(r"```sh\n(.*?)```", text, re.S)
    wanted = set()
    for block in blocks:
        for line in block.splitlines():
            line = line.split("#")[0].strip()
            words = [w for w in line.split() if w not in EXAMPLES]
            if not words or not re.match(r"(smartdns[\w-]*|master-dns\.sh)$", words[0]):
                continue
            if words[0] in ("smartdns-menu",):
                continue
            wanted.add(" ".join(words))
    check("the README's commands were found", len(wanted) > 20, str(sorted(wanted)))
    for cmd in sorted(wanted):
        check("the menu offers `%s`" % cmd, cmd in SRC)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
