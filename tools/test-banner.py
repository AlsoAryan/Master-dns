#!/usr/bin/env python3
"""The installer opens with its name, large.

What has to hold: before anything else - root or not - an install, an
upgrade or an uninstall shows MASTER DNS in block letters and the version,
on one line in a wide terminal and on two in a narrow one, never wider than
it says; --version and --help print only what a script asking wants.
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
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:400]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


with open(os.path.join(ROOT, "tools", "installer-logic.sh"), encoding="utf-8") as fh:
    logic = fh.read()
body = logic[logic.index("banner() {"):logic.index("\n}\n", logic.index("banner() {"))]
rows = re.findall(r"^\s+'(  [^']*)'", body, re.M)
wide, narrow = rows[:6], rows[6:]
limit = int(re.search(r'\[ "\$cols" -ge (\d+) \]', body).group(1))
check("one line of six rows where the terminal is wide, never wider than it asks",
      len(wide) == 6 and max(len(r) for r in wide) <= limit, (len(wide), max(map(len, wide)), limit))
check("two words of six rows each where it is narrow, inside 80 columns",
      len(narrow) == 12 and max(len(r) for r in narrow) <= 80, max(map(len, narrow)))
check("in block letters", all("█" in r or "╚" in r for r in wide) and sum("█" in r for r in wide) == 5)
check("the version beneath", '"$VERSION"' in body and "smart DNS for Iran" in body)
check("before the preflight, so even a run without root shows it",
      logic.index("    *) banner; syscheck || true ;;")
      < logic.index("# ---------------------------------------------------------------- preflight")
      < logic.index('[ "$(id -u)" = 0 ] || die "run as root'))
check("  and after --version and --help are answered, which print only themselves",
      logic.index("--version|-V) printf '%s\\n' \"$VERSION\"; exit 0 ;;")
      < logic.index("banner() {"))

bash = shutil.which("bash")
if bash:
    script = "VERSION=9.9.9\nB=\n" + body + "\n}\nbanner\n"
    for cols, lines in (("120", 6), ("80", 12)):
        r = subprocess.run([bash, "-c", script], capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, COLUMNS=cols))
        drawn = [l for l in r.stdout.splitlines() if "█" in l or "╚" in l]
        check("at %s columns it draws %d rows" % (cols, lines), len(drawn) == lines
              and "version 9.9.9" in r.stdout, r.stdout[-300:] + r.stderr)

print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
