#!/usr/bin/env python3
"""The users table, more of it as the admin scrolls down.

What has to hold: however many customers there are, the page opens with a
hundred rows - a few thousand at once froze the browser - and the count of
all of them in the heading. Scrolling to its end fetches the next hundred,
in the same order and search, until every customer has been shown exactly
once; a page's worth or fewer has nothing more to fetch. An action on a row
- saving it, blocking it - comes back to the same search with as many rows
as there were, and to that row. A seller scrolls through their own customers
only, and fetching rows is the users section's, as the page is.
"""
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import urllib.parse

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
admin = load("templates/smartdns-admin", "admin")
admin.log = panel.log = lambda *a: None

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
admin.DB = db
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db)
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
open(admin.PANEL_ENV, "w").write("RELAY_IP=198.51.100.1\n")
store.run("INSERT INTO admins (username, password_hash, password_salt, own_only, created_at)"
          " VALUES ('shop', 'x', 'x', 1, ?)", (panel.now(),))
shop = store.one("SELECT * FROM admins WHERE username = 'shop'")

# 250 of the owner's customers, user000 the oldest, and 5 of the seller's.
for i in range(250):
    store.run("INSERT INTO users (username, created_at, status) VALUES (?, ?, 'active')",
              ("user%03d" % i, "2026-01-01T00:%02d:%02d+00:00" % (i // 60, i % 60)))
for i in range(5):
    store.run("INSERT INTO users (username, created_at, status, owner_admin)"
              " VALUES (?, ?, 'active', ?)", ("shop%d" % i, panel.now(), shop["id"]))
store.run("UPDATE users SET username = 'gamer' || substr(username, 5)"
          " WHERE username LIKE 'user1%'")      # user100..user199 -> gamer100..gamer199
EVERYBODY = sorted(r["username"] for r in store.q("SELECT username FROM users"))


class Rec:
    def __init__(self, path, cookie=""):
        self.path = path
        self.headers = {"Cookie": cookie}


def page(path, cookie=""):
    admin.REQ.admin = None
    return admin.Admin.users(Rec(path, cookie))


def more(path):
    admin.REQ.admin = None
    return admin.Admin.users(Rec(path), rows_only=True)


def names(body):
    return re.findall(r"<td class='who' title='([^']*)'", body)


def scroll(body):
    """What the page's script does: fetch from the end of the table until
    there is nothing more."""
    m = re.search(r"<div id='umore' class='umore' data-total='(\d+)' data-next='([^']*)'", body)
    shown = names(body)
    if not m:
        return shown, 0
    total, base = int(m.group(1)), m.group(2).replace("&amp;", "&")
    fetches = 0
    while len(shown) < total:
        part = more(base + str(len(shown)))
        fetches += 1
        if not names(part) or fetches > 50:
            break
        shown += names(part)
    return shown, fetches


print("more as the admin scrolls")
body = page("/p/users")
check("it opens with a hundred rows, not all of them", len(names(body)) == 100,
      str(len(names(body))))
check("  the heading counts everybody", "<h2>کاربران (255)</h2>" in body)
check("  the newest first, as before", names(body)[0] == "shop4")
check("  each row is one to come back to", "<tr id='r255'><td class='who' title='shop4'" in body)
check("  the end of the table says how many of how many, and where the rest is",
      "data-total='255' data-next='/p/users-rows?q=&amp;sort=new&amp;from='>100 از 255</div>"
      in body)
check("  the help is above the table, not after rows still to come",
      body.index("ثبت‌نام تازه با وضعیت") < body.index("<table class='users'>"))
shown, fetches = scroll(body)
check("scrolling fetches the rest, a hundred at a time", fetches == 2 and len(shown) == 255)
check("  every customer exactly once, in order", sorted(shown) == EVERYBODY
      and shown[-1] == "user000")
part = more("/p/users-rows?q=&sort=new&from=100")
check("  what is fetched is rows only", part.startswith("<tr id='r") and "<table" not in part
      and "<h2>" not in part and "<script" not in part)
check("  past the end, nothing", more("/p/users-rows?from=900") == "")
check("  a start that is no number is the start",
      names(more("/p/users-rows?from=x';--")) == names(body))

print("with a search and an order")
found = page("/p/users?q=user&sort=user")
check("the search is counted, and the first hundred of it shown",
      "<h2>کاربران (150 از 255)</h2>" in found and len(names(found)) == 100
      and names(found)[0] == "user000")
check("  the rest keeps the search and the order",
      "data-next='/p/users-rows?q=user&amp;sort=user&amp;from='" in found)
shown, _ = scroll(found)
check("  and brings the rest of what was found, nothing else",
      len(shown) == 150 and shown[-1] == "user249"
      and all(n.startswith("user") for n in shown))
few = page("/p/users?q=shop")
check("a page's worth or fewer: nothing to fetch",
      "id='umore'" not in few and len(names(few)) == 5)

print("after an action on a row")
check("the page notes where it was when a form is sent",
      "addEventListener('submit'" in body and "'&upto='+rows()" in body
      and "sessionStorage.setItem('urow'" in body)
check("  and goes back to that row", "scrollIntoView" in body)
check("as many rows as there were", len(names(page("/p/users?upto=250"))) == 255)
check("  rounded up to a fetch's worth", len(names(page("/p/users?upto=130"))) == 200)
check("  never fewer than a hundred", len(names(page("/p/users?upto=0"))) == 100)


class Sent(Exception):
    pass


class Act:
    def __init__(self, cookie):
        self.headers = {"Cookie": cookie}

    def send(self, body, code, headers):
        raise Sent(headers["Location"])


Act.redirect = admin.Admin.redirect


def back(cookie, to="users?m=ذخیره شد"):
    try:
        Act(cookie).redirect(to)
    except Sent as e:
        return urllib.parse.unquote(str(e))


check("back with the search and the rows it had",
      back("uview=" + urllib.parse.quote("q=user&upto=250"))
      == "/p/users?m=ذخیره شد&q=user&upto=250")
check("  the first hundred need no number", back("uview=" + urllib.parse.quote("q=&upto=100"))
      == "/p/users?m=ذخیره شد")
check("  and nothing noted, the plain page", back("") == "/p/users?m=ذخیره شد")
check("  a search cannot add to the query",
      back("uview=" + urllib.parse.quote("q=a%26m%3Dx&upto=3")) == "/p/users?m=ذخیره شد&q=a m x")
check("  other pages go where they were sent",
      back("uview=" + urllib.parse.quote("upto=300"), "receipts?m=ok") == "/p/receipts?m=ok")

print("a seller")
admin.REQ.admin = shop
mine = admin.Admin.users(Rec("/p/users"))
theirs = admin.Admin.users(Rec("/p/users-rows?from=0"), rows_only=True)
admin.REQ.admin = None
check("scrolls through their own only",
      names(mine) == ["shop4", "shop3", "shop2", "shop1", "shop0"]
      and names(theirs) == names(mine) and "<h2>کاربران (5)</h2>" in mine)
check("fetching rows is the users section's, as the page is",
      admin.PAGE_SECTION["users-rows"] == admin.PAGE_SECTION["users"] == "users")

store.db.close()
admin.STORE.db.close()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d failed" % len(fails))
    sys.exit(1)
print("all passed")
