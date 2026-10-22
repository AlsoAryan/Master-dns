#!/usr/bin/env python3
"""The Telegram bot's log: smartdns-bot-logs on the server, and the full log
on the admin panel's bot page.

Both show the bot's journal, and in both the bot's token is masked wherever
it turns up: this is exactly what gets pasted into a chat asking for help.
"""
import importlib.machinery
import importlib.util
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
TOOL = os.path.join(HERE, "..", "templates", "smartdns-bot-logs")
fails = []
TOKEN = "123456789:AAH" + "z" * 32


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def posix(path):
    path = os.path.abspath(path)
    if len(path) > 1 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path.replace("\\", "/")


tmp = tempfile.mkdtemp()
env_file = os.path.join(tmp, "master-dns-bot.env")
with open(env_file, "w", newline="\n") as fh:
    fh.write("BOT_TOKEN=%s\nAPI_KEY=dd_x\n" % TOKEN)
JOURNAL = ["2026-09-22T07:42:52 bot @shop_bot up; panel messages on 127.0.0.1:18990",
           "2026-09-22T07:43:10 could not send to 42: telegram sendMessage: chat not found",
           "2026-09-22T07:44:00 getUpdates: <urlopen https://api.telegram.org/bot%s/getUpdates>"
           % TOKEN]

print("the command")
BASH = shutil.which("bash")
if BASH is None:
    print("  (bash not available - skipping the command)")
else:
    fake = os.path.join(tmp, "bin")
    os.makedirs(fake)

    def tool(name, body):
        p = os.path.join(fake, name)
        with open(p, "w", newline="\n") as fh:
            fh.write("#!/bin/bash\n" + body + "\n")
        os.chmod(p, 0o755)

    tool("id", "echo 0")
    tool("systemctl", 'case "$1" in is-active) echo active ;; esac')
    tool("journalctl", "\n".join("echo '%s'" % l for l in JOURNAL))
    env = dict(os.environ, PATH=posix(fake) + ":" + os.environ.get("PATH", ""),
               SMARTDNS_BOT_ENV=posix(env_file))

    def run(*args):
        return subprocess.run([BASH, posix(TOOL)] + list(args), capture_output=True,
                              text=True, timeout=60, env=env)

    check("it parses", subprocess.run([BASH, "-n", posix(TOOL)]).returncode == 0)
    r = run("-h")
    check("-h explains it", r.returncode == 0 and "smartdns-bot-logs -f" in r.stdout)
    check("a wrong option is refused", run("--bogus").returncode != 0)
    check("so is a line count that is not a number", run("-n", "lots").returncode != 0)
    r = run()
    check("it says whether the bot runs, then its lines",
          "Telegram bot: active" in r.stdout and "bot @shop_bot up" in r.stdout, r.stdout)
    check("the token is masked", TOKEN not in r.stdout and "<token>" in r.stdout, r.stdout)
    r = run("-e")
    check("-e keeps only what went wrong", "could not send" in r.stdout
          and "getUpdates" in r.stdout and "bot @shop_bot up" not in r.stdout, r.stdout)
    os.unlink(env_file)
    r = run()
    check("with no bot set up, it says where to set one up",
          "not set up" in r.stdout and "Bot page" in r.stdout, r.stdout)
    with open(env_file, "w", newline="\n") as fh:
        fh.write("BOT_TOKEN=%s\n" % TOKEN)

print("the admin panel")


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(HERE, "..", path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


admin = load("templates/smartdns-admin", "admin")
admin.BOT_ENV = env_file
admin.BOT_BIN = os.path.join(tmp, "master-dns-bot")
open(admin.BOT_BIN, "w").close()
admin.CFG = {"ADMIN_PATH": "p"}


class FakeRun:
    def __init__(self, text):
        self.stdout, self.returncode = text, 0


asked = []


def fake_run(cmd, **kw):
    asked.append(cmd)
    return FakeRun("\n".join(JOURNAL) + "\n")


admin.subprocess.run = fake_run
log = admin.bot_journal(200)
check("the full log is asked for 200 lines", ["-n", "200"] == asked[-1][3:5], str(asked[-1]))
check("and comes back with the token masked", TOKEN not in log and "<token>" in log)


class Page:
    path = "/p/bot?log=1"


page = admin.Admin.bot_page(Page())
check("the page shows it, masked and escaped", "chat not found" in page and TOKEN not in page
      and "&lt;token&gt;" in page, page[:300])
check("with a way back", "href='/p/bot'" in page)


class Front:
    path = "/p/bot"


admin.STORE = type("S", (), {"one": staticmethod(lambda *a: None),
                              "q": staticmethod(lambda *a: [])})()
front = admin.Admin.bot_page(Front())
check("the bot page has the button", "bot?log=1" in front and "نمایش لاگ کامل" in front)
check("and its three lines are masked too", TOKEN not in front)

print("the installer and the menu")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
build = open(os.path.join(HERE, "build-installer.py"), encoding="utf-8").read()
menu = open(os.path.join(HERE, "..", "templates", "smartdns-menu"), encoding="utf-8").read()
check("the installer puts the command on the exit",
      '("SMARTDNS_BOT_LOGS", "templates/smartdns-bot-logs")' in build
      and "payload SMARTDNS_BOT_LOGS > /usr/local/bin/smartdns-bot-logs" in logic)
check("and the menu offers it", "run smartdns-bot-logs" in menu)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
