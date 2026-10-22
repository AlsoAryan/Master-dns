#!/usr/bin/env python3
"""--uninstall asks whether the database goes too, then whether the packages do.

The database is kept by default, so a reinstall on the same machine picks the
customers up again; deleted when the admin answers yes (or passes DELETE_DB=1),
after a last copy goes to the backups, where no install looks. Then the
packages, named in the question (or PURGE_PACKAGES=1): nginx, dnsmasq, coturn,
certbot - but never the machine's own python3, curl, openssl or nftables. The installer's own uninstall() is run, against stand-ins for
systemctl, apt and the rest, on a state directory in a temporary folder.
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIC = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
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

m = re.search(r"^uninstall\(\) \{\n.*?^\}\n", LOGIC, re.S | re.M)
check("the installer has an uninstall()", m is not None)
if m is None:
    sys.exit(1)
UNINSTALL = m.group(0)

HELP = LOGIC.split("# ---------------------------------------------------------------- the name")[0]
check("--help names DELETE_DB", "DELETE_DB=1" in HELP)
check("--help names PURGE_PACKAGES", "PURGE_PACKAGES=1" in HELP)

tmp = tempfile.mkdtemp()


def fwd(path):
    return path.replace("\\", "/")


def machine(with_db=True):
    root = tempfile.mkdtemp(dir=tmp)
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "broadcast"))
    open(os.path.join(state, "install-state"), "w").write("role exit\n")
    open(os.path.join(state, "version"), "w").write("0.9.8\n")
    open(os.path.join(state, "broadcast", "1-0"), "w").write("photo")
    if with_db:
        db = sqlite3.connect(os.path.join(state, "panel.db"))
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users (name) VALUES ('ali'), ('sara')")
        db.commit()
        db.close()
    return root, state, os.path.join(root, "backups")


PY = "python" if os.name == "nt" else "python3"
# Installed on the pretend machine; the install recorded python3 as its own too.
INSTALLED = "nginx libnginx-mod-stream dnsmasq coturn python3 curl openssl nftables"
RECORDED = "nginx libnginx-mod-stream dnsmasq coturn python3"


def run(answers, with_db=True, **env):
    root, state, backups = machine(with_db)
    calls = os.path.join(root, "calls")
    open(calls, "w").close()
    lib = os.path.join(root, "lib")
    os.makedirs(lib)
    script = "\n".join([
        "set -euo pipefail",
        'STAMP=test STATE_DIR="%s" BACKUP_DIR="%s"' % (fwd(state), fwd(backups)),
        'STATE="$STATE_DIR/install-state" CALLS="%s"' % fwd(calls),
        'B="" G="" Y="" RD="" N=""',
        'step() { echo "== $*"; }; info() { echo "   $*"; }; warn() { echo "WARN $*"; }',
        'die() { echo "DIE $*"; exit 1; }',
        'recall() { case "$1" in role) echo exit ;; installed-at) echo today ;;'
        ' packages-installed) echo "%s" ;; *) : ;; esac; }' % RECORDED,
        'recall_flat() { :; }; backup_file() { :; }',
        'systemctl() { [ "$1" = is-enabled ] && return 1; echo "systemctl $*" >> "$CALLS"; return 0; }',
        'nft() { return 1; }',
        'nginx() { echo "nginx $*" >> "$CALLS"; return 0; }',
        'dpkg-query() { case " %s " in *" $3 "*) echo "install ok installed" ;; esac; }' % INSTALLED,
        'apt-get() { echo "apt-get $*" >> "$CALLS"; }',
        'python3() { %s "$@"; }' % PY,
        UNINSTALL.replace("/usr/local/lib/smart-dns", fwd(lib)),
        "uninstall",
    ])
    e = dict(os.environ)
    for k in ("ASSUME_YES", "DELETE_DB", "PURGE_PACKAGES"):
        e.pop(k, None)
    e.update(env)
    path = os.path.join(root, "run.sh")
    with open(path, "w", newline="\n") as fh:
        fh.write(script + "\n")
    r = subprocess.run([BASH, fwd(path)], capture_output=True, timeout=60, env=e,
                       input=("\n".join(answers) + "\n").encode())
    out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    return r.returncode, out, state, backups, open(calls).read(), os.path.exists(lib)


def users(path):
    db = sqlite3.connect(path)
    try:
        return [n for (n,) in db.execute("SELECT name FROM users ORDER BY id")]
    finally:
        db.close()


def purged(calls):
    line = next((l for l in calls.splitlines() if l.startswith("apt-get purge")), "")
    return set(line.split()[2:]) - {"-y", "-qq"}


ASKED_DB = "Delete it too, to start from nothing?"
ASKED_PKG = "Remove these packages too, with their config?"


def db_kept(state):
    return os.path.exists(os.path.join(state, "panel.db"))


def db_copied(backups):
    return os.path.exists(os.path.join(backups, "panel.db.test"))


print("no, then no: all kept, as before")
rc, out, state, backups, calls, lib = run(["y", "n", "n"])
check("finishes", rc == 0, out)
check("asks about the database", ASKED_DB in out, out)
check("then about the packages", ASKED_PKG in out and out.index(ASKED_DB) < out.index(ASKED_PKG), out)
check("naming them", "nginx libnginx-mod-stream dnsmasq coturn" in out, out)
check("the database is still there", db_kept(state))
check("and says so", "database left where it is" in out, out)
check("the install record is gone all the same", not os.path.exists(os.path.join(state, "install-state")))
check("no copy made that nobody asked for", not db_copied(backups))
check("no package touched", "apt-get" not in calls, calls)
check("nginx put back into service", "systemctl restart nginx" in calls, calls)
check("and says how to remove the packages", "apt-get purge" in out, out)

print("just Enter to both: all kept")
rc, out, state, backups, calls, lib = run(["y", "", ""])
check("finishes", rc == 0, out)
check("the database is still there", db_kept(state))
check("no package touched", "apt-get" not in calls, calls)

print("the database yes, the packages no")
rc, out, state, backups, calls, lib = run(["y", "y", "n"])
check("finishes", rc == 0, out)
check("the state directory is gone", not os.path.exists(state), out)
copy = os.path.join(backups, "panel.db.test")
check("a last copy is in the backups", os.path.exists(copy), out)
check("and it holds the customers", os.path.exists(copy) and users(copy) == ["ali", "sara"])
check("says it is deleted", "database deleted" in out, out)
check("the packages stay", "apt-get" not in calls, calls)

print("the database no, the packages yes")
rc, out, state, backups, calls, lib = run(["y", "n", "y"])
check("finishes", rc == 0, out)
check("the database is still there", db_kept(state))
got = purged(calls)
check("purges nginx, its stream module, dnsmasq and coturn",
      {"nginx", "libnginx-mod-stream", "dnsmasq", "coturn"} <= got, calls)
check("never python3, curl, openssl or nftables",
      not got & {"python3", "curl", "openssl", "nftables"}, calls)
check("nothing that is not installed", not got & {"certbot", "nginx-common"}, calls)
check("and their leftovers", "apt-get autoremove --purge" in calls, calls)
check("BackPack's folder goes", not lib)
check("nginx not started again", "systemctl restart nginx" not in calls, calls)
check("no leftover advice to purge", "To also remove the packages" not in out, out)

print("both yes: nothing left")
rc, out, state, backups, calls, lib = run(["y", "y", "y"])
check("finishes", rc == 0, out)
check("the state directory is gone", not os.path.exists(state), out)
check("a last copy of the database is in the backups", db_copied(backups))
check("the packages purged", "nginx" in purged(calls), calls)

print("PURGE_PACKAGES=1 with ASSUME_YES: the packages, without asking")
rc, out, state, backups, calls, lib = run([], ASSUME_YES="1", PURGE_PACKAGES="1")
check("finishes", rc == 0, out)
check("does not ask", ASKED_DB not in out and ASKED_PKG not in out, out)
check("the database is still there", db_kept(state))
check("purges the packages", "nginx" in purged(calls), calls)

print("PURGE_PACKAGES=1 and DELETE_DB=1: both, without asking")
rc, out, state, backups, calls, lib = run([], ASSUME_YES="1", PURGE_PACKAGES="1", DELETE_DB="1")
check("finishes", rc == 0, out)
check("the state directory is gone", not os.path.exists(state), out)
check("purges the packages", "nginx" in purged(calls), calls)

print("ASSUME_YES alone: all kept, without asking")
rc, out, state, backups, calls, lib = run([], ASSUME_YES="1")
check("finishes", rc == 0, out)
check("does not ask", ASKED_DB not in out and ASKED_PKG not in out, out)
check("the database is still there", db_kept(state))
check("no package touched", "apt-get" not in calls, calls)

print("ASSUME_YES with DELETE_DB=1: the database deleted, the packages kept")
rc, out, state, backups, calls, lib = run([], ASSUME_YES="1", DELETE_DB="1")
check("finishes", rc == 0, out)
check("the state directory is gone", not os.path.exists(state), out)
check("a last copy is in the backups", db_copied(backups))
check("no package touched", "apt-get" not in calls, calls)

print("a relay, with no database: only the packages asked")
rc, out, state, backups, calls, lib = run(["y", "n"], with_db=False)
check("finishes", rc == 0, out)
check("does not ask about a database", ASKED_DB not in out, out)
check("asks about the packages", ASKED_PKG in out, out)
check("no package touched", "apt-get" not in calls, calls)
rc, out, state, backups, calls, lib = run(["y", "y"], with_db=False)
check("yes: finishes", rc == 0, out)
check("yes: purges the packages", "dnsmasq" in purged(calls), calls)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
