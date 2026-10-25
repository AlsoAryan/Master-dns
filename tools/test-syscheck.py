#!/usr/bin/env python3
"""The installer's system check and its timeline, run for real in bash.

The check says what this machine is before anything is changed - one line each,
a mark, a name, the finding - and `--check` answers with its exit status. The
timeline closes an install by saying where the time went.
"""
import os
import re
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
fails = []


def check(name, ok, detail=""):
    print("  %s %s" % ("ok  " if ok else "FAIL", name))
    if not ok:
        fails.append(name + (" - " + str(detail)[:300] if detail else ""))


def function(name):
    start = logic.index("\n%s() {" % name) + 1
    end = logic.index("\n}\n", start) + 3
    return logic[start:end]


bash = shutil.which("bash")
if not bash:
    print("no bash here; nothing to run")
    sys.exit(0)

print("the system check")
out = subprocess.run([bash, "-c", "set -euo pipefail\nB=; G=; Y=; RD=; N=; D=\n" + function("syscheck") + "\nif syscheck; then echo rc=0; else echo rc=1; fi"],
                     capture_output=True, text=True, encoding="utf-8")
text = out.stdout
for label in ("system", "systemd", "root", "memory", "disk", "address", "port 53", "port 80", "port 443"):
    check("a line for %s" % label, re.search(r"^\s+\S\s+%s\s+\S" % re.escape(label), text, re.M) is not None, text)
check("it ends by counting what to look at and what blocks", re.search(r"\d+ to look at, \d+ blocking", text) is not None)
check("every line starts with a mark: +, ! or x", all(re.match(r"\s+[+!x] ", l) for l in text.splitlines() if re.match(r"\s+\S\s+\S+\s{2,}", l) and "to look at" not in l))
check("it does not stop at a tool the machine lacks", "rc=" in text and out.returncode == 0, out.stderr + text[-120:])
blocking = int(re.search(r"(\d+) blocking", text).group(1))
check("its status says whether anything blocks", ("rc=0" in text) == (blocking == 0), text[-200:])
check("it changes nothing: no writes, no installs", not re.search(r"\b(apt-get|systemctl (start|stop|enable)|rm |mv |cp |tee )", function("syscheck")))

print("the flag")
check("--check is answered after the banner and before anything is asked",
      logic.index("--check) banner; syscheck; exit $? ;;") < logic.index("# ---------------------------------------------------------------- preflight"))
check("--help lists it", "--check        look at this machine" in logic)

print("the timeline")
script = ("set -euo pipefail\nB=; G=; Y=; RD=; N=; D=; C=\n" + function("timeline") +
          "\nSTEP_N=3; STEP_NAME=(x 'one' 'two' 'three'); STEP_AT=(0 100 101 105)\n"
          "date() { echo 106; }\ntimeline\n")
t = subprocess.run([bash, "-c", script], capture_output=True, text=True, encoding="utf-8").stdout
rows = [l for l in t.splitlines() if re.match(r"\s+0\d\s", l)]
check("a row per step", len(rows) == 3, t)
bars = [l.count("#") for l in rows]
check("the longest step has the full bar, the others in proportion", bars == [5, 20, 5] or bars == [5, 20, 5], bars)
check("each row says its seconds", [r.split()[-1] for r in rows] == ["1s", "4s", "1s"] or [r.split()[-1] for r in rows] == ["1s", "4s", "1s"], rows)
check("it is printed just before the ready box", logic.index("    timeline\n    printf '  %s+--") > 0)

print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
