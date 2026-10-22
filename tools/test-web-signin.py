#!/usr/bin/env python3
"""A web sign-in for customers who come through the bot.

The bot asks for a name and a username and the panel makes the password, so
every customer can use the web page too; and a one-time link signs them
straight in to register the address of the connection they open it on.

What has to hold: usernames follow the web signup's rules and stay unique; a
link works once, for ten minutes, and is used up only by the browser posting
it back - not by whatever fetches the address first, such as a messenger
drawing a preview; and the relay tells the panel where its page is, so
nobody types that twice.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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


def load(path, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, os.path.join(HERE, "..", path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


panel = load("templates/smartdns-panel", "panel")
sync = load("templates/smartdns-sync", "sync")
panel.log = lambda *a: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
store.run("INSERT INTO users (telegram_id, first_name, created_at, status)"
          " VALUES (111, 'از تلگرام', ?, 'pending')", (panel.now(),))
store.create_web_user("taken", "x", "some-password")


def bot_user():
    return store.user_by_telegram(111)


print("a username and a password")
check("a bad username is refused",
      panel.give_credentials(store, bot_user(), "a b", "علی")["error"] == "bad_username")
check("one somebody has is refused",
      panel.give_credentials(store, bot_user(), "TAKEN", "علی")["error"] == "username_taken")
res = panel.give_credentials(store, bot_user(), "Ali.G", "علی رضایی")
u = bot_user()
check("a good one is taken, as the web signup would write it",
      res["ok"] and u["username"] == "ali.g" and res["username"] == "ali.g")
check("with the name chosen", u["first_name"] == "علی رضایی")
check("and a password that works", panel.check_password(u, res["password"]))
check("the account can sign in on the web now", store.user_by_username("ali.g")["id"] == u["id"])
check("a second time is refused, not overwritten",
      panel.give_credentials(store, bot_user(), "other", "x")["error"] == "has_username"
      and bot_user()["username"] == "ali.g")
store.open_session(u["id"])
fresh = panel.fresh_password(store, bot_user())
check("a new password replaces it", panel.check_password(bot_user(), fresh["password"])
      and not panel.check_password(bot_user(), res["password"]))
check("and signs everybody out", not store.one(
    "SELECT 1 FROM panel_sessions WHERE user_id = ?", (u["id"],)))

print("the one-time link")
check("with no relay heard from yet, there is no address to send",
      panel.login_link(store, bot_user())["error"] == "no_panel")
store.set_setting("customer_panel_url", "https://user.example.com:8443/")
link = panel.login_link(store, bot_user())
token = link["url"].rsplit("/", 1)[1]
check("an address on the customer panel", link["url"].startswith(
    "https://user.example.com:8443/go/") and link["minutes"] == 10)
page = sync.go_page(token)
check("opening it only shows a page that posts it back",
      "method='post' action='/go'" in page and token in page
      and store.one("SELECT 1 FROM user_codes WHERE kind = 'login'"))
res = panel.use_login_link(store, token)
check("posting it signs this customer in", res["ok"] and store.one(
    "SELECT user_id FROM panel_sessions WHERE token = ?", (res["session"],))["user_id"] == u["id"])
check("once", panel.use_login_link(store, token)["error"] == "bad_link")
token = panel.login_link(store, bot_user())["url"].rsplit("/", 1)[1]
store.run("UPDATE user_codes SET expires_at = ? WHERE kind = 'login'",
          ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
check("not after its ten minutes", panel.use_login_link(store, token)["error"] == "link_expired")
check("rubbish is refused", panel.use_login_link(store, "<script>")["error"] == "bad_link")

print("where the customer panel is")
src_sync = open(os.path.join(HERE, "..", "templates", "smartdns-sync"), encoding="utf-8").read()
check("the relay sends its own address with every sync",
      '"panel_url": url' in src_sync and "CFG[\"PANEL_DOMAIN\"], PANEL_TLS_PORT" in src_sync)
src_panel = open(os.path.join(HERE, "..", "templates", "smartdns-panel"),
                 encoding="utf-8").read()
check("and the panel keeps only an https address",
      'set_setting("customer_panel_url", url)' in src_panel
      and 're.fullmatch(r"https://' in src_panel)

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
