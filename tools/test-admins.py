#!/usr/bin/env python3
"""Admins: the owner's username, and the admins they make.

What has to hold: after the upgrade the owner signs in with the password
they had and is made to choose a username, and from then on needs both; an
admin signs in with their own and sees only the parts ticked for them - in
the menu, the pages and the actions alike; what is the owner's alone stays
so; a change to an admin signs them out everywhere, and a disabled one
cannot sign in; the owner can see the panel as an admin sees it and come
back; an admin changes their own password; and deleting one gives their
customers to the owner.
"""
import http.client
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import threading
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
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
store.ensure_default_template([])
SECRET = "adminpath"
SALT = "b2" * 16
admin.CFG = {"ADMIN_PATH": SECRET, "ADMIN_SALT": SALT, "ADMIN_PORT": "0",
             "ADMIN_HASH": admin.hash_password("owner-pw-1", SALT)}
admin.CFG["ADMIN_USER"] = "mehdi"
admin.CONFIG = os.path.join(tmp, "admin.env")
admin.STORE = admin.Store(db)
admin.CATALOGUE = []
srv = admin.make_admin_server(None, 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


class Browser:
    def __init__(self):
        self.cookie = ""

    def go(self, method, path, form=None):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        headers = dict(FORM) if form is not None else {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        c.request(method, "/%s/%s" % (SECRET, path),
                  body=urllib.parse.urlencode(form, doseq=True) if form is not None else None,
                  headers=headers)
        r = c.getresponse()
        body = r.read().decode("utf-8", "replace")
        m = re.search(r"sdns=([^;]*)", r.getheader("Set-Cookie") or "")
        if m:
            self.cookie = "sdns=" + m.group(1)
        loc = urllib.parse.unquote(r.getheader("Location") or "")
        c.close()
        return r.status, loc, body

    def login(self, username, password):
        self.cookie = ""
        return self.go("POST", "", {"username": username, "password": password})

    def post(self, path, **form):
        return self.go("POST", path, form)

    def get(self, path=""):
        return self.go("GET", path)


print("the owner")
check("signs in with the username chosen at install and the password",
      Browser().login("mehdi", "owner-pw-1")[0] == 303)
owner = Browser()
owner.login("Mehdi", "owner-pw-1")
check("  lower-cased", owner.get("")[0] == 200)
check("  not with the password alone", Browser().login("", "owner-pw-1")[0] == 200)
check("  nor another name", Browser().login("someone", "owner-pw-1")[0] == 200)
check("  nor a wrong password", Browser().login("mehdi", "nope")[0] == 200)
check("the settings page can change it", "owner-name-save" in owner.get("settings")[2])
owner.post("owner-name-save", username="Boss")
check("  changed, in admin.env", admin.CFG["ADMIN_USER"] == "boss"
      and "ADMIN_USER=boss" in open(admin.CONFIG).read())
check("  the new one signs in, the old one not", Browser().login("boss", "owner-pw-1")[0] == 303
      and Browser().login("mehdi", "owner-pw-1")[0] == 200)
owner.post("owner-name-save", username="mehdi")
store.set_setting("owner_username", "fromdb")
admin.move_owner_name()
check("a name kept in the database by an earlier build moves to admin.env",
      admin.owner_name() == "fromdb" and not store.setting("owner_username")
      and "ADMIN_USER=fromdb" in open(admin.CONFIG).read())
owner.post("owner-name-save", username="mehdi")

print("making an admin")
status, loc, _ = owner.post("admin-save", id="0", username="Ali", password="ali-pw-123",
                            perm=["users", "tickets"], own_only="1", max_users="50",
                            cap_gb="100", days="30", templates_mode="pick")
row = admin.STORE.one("SELECT * FROM admins WHERE username = 'ali'")
check("made, with what they may see", row is not None
      and admin.admin_perms(row) == {"users", "tickets"} and row["own_only"] == 1
      and row["cap_bytes"] == 100 * admin.GB and row["expires_at"], loc)
check("a short password is refused", "!" in owner.post("admin-save", id="0", username="reza",
                                                       password="short")[1])
check("the owner's own username is refused", "!" in owner.post(
    "admin-save", id="0", username="mehdi", password="long-enough-1")[1])
check("the admins page lists them", "ali" in owner.get("admins")[2])

print("the admin")
ali = Browser()
status, _, _ = ali.login("ali", "ali-pw-123")
check("signs in with their own", status == 303)
status, _, body = ali.get("")
check("  the menu has only what they may see",
      "/users'" in body and "/tickets'" in body and "/plans'" not in body
      and "/settings'" not in body and "/admins'" not in body and "/me'" in body)
check("  the pages they may", ali.get("users")[0] == 200 and ali.get("tickets")[0] == 200)
check("  not the others", ali.get("plans")[0] == 403 and ali.get("settings")[0] == 403
      and ali.get("admins")[0] == 403 and ali.get("backup.db")[0] == 403)
check("  nor their actions", ali.post("plan-save", name="x")[0] == 403
      and ali.post("password", password="x", password_again="x")[0] == 403
      and ali.post("admin-save", id="0", username="evil", password="evil-pw-123")[0] == 403)
check("  their own password is theirs", "رمز عوض شد" in ali.post(
    "me-password", current="ali-pw-123", new="ali-pw-456")[1])
check("  with the old one they are out", Browser().login("ali", "ali-pw-123")[0] == 200)

print("the owner sees as the admin")
owner.post("admin-view-as", id=str(row["id"]))
status, _, body = owner.get("")
check("the panel as the admin sees it, with the way back",
      "view-as-stop" in body and "/plans'" not in body)
check("  their limits hold", owner.get("plans")[0] == 403)
owner.post("view-as-stop")
check("  and back", owner.get("plans")[0] == 200)

print("changes")
ali.login("ali", "ali-pw-456")
check("signed in", ali.get("users")[0] == 200)
owner.post("admin-save", id=str(row["id"]), username="ali", password="", perm=["users"],
           templates_mode="pick", days="30")
check("a change signs them out", ali.get("users")[0] == 200 and "password" in ali.get("users")[2]
      and "name=\"username\"" in ali.get("users")[2])
check("  and the password is kept when left empty", ali.login("ali", "ali-pw-456")[0] == 303)
print("disabling")
check("  signed in before", ali.login("ali", "ali-pw-456")[0] == 303 and ali.get("users")[0] == 200)
check("the form has buttons, not a tick", "admin-disable" in owner.get("admins?id=%d" % row["id"])[2]
      and "name='disabled'" not in owner.get("admins?id=%d" % row["id"])[2])
owner.post("admin-save", id=str(row["id"]), username="ali", password="", perm=["users"],
           own_only="1", templates_mode="pick")
ali.login("ali", "ali-pw-456")
store.run("INSERT INTO users (username, created_at, status, owner_admin) VALUES"
          " ('cust', ?, 'active', ?)", (panel.now(), row["id"]))
cust = store.one("SELECT id FROM users WHERE username = 'cust'")[0]
store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, '10.9.9.9', ?)", (cust, panel.now()))
check("  their customer is served", cust in {r["uid"] for r in store.allowed()})
owner.post("admin-disable", id=str(row["id"]), to="1")
check("disabled: signed out, and cannot sign in", "name=\"username\"" in ali.get("users")[2]
      and Browser().login("ali", "ali-pw-456")[0] == 200)
check("  their customers stop", cust not in {r["uid"] for r in store.allowed()})
c = store.one("SELECT * FROM users WHERE id = ?", (cust,))
check("  nothing of theirs is sold",
      panel.create_receipt(store, c, {"plan_id": 1})["error"] == "seller_disabled"
      and panel.seller_stopped(store, row["id"]) == "disabled")
owner.post("admin-disable", id=str(row["id"]), to="0")
check("enabled: all back", Browser().login("ali", "ali-pw-456")[0] == 303
      and cust in {r["uid"] for r in store.allowed()})

print("deleting")
tid = store.run("INSERT INTO templates (name, is_default, created_at, owner_admin) VALUES"
                " ('ali-t', 0, ?, ?)", (panel.now(), row["id"])).lastrowid
store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, active, created_at,"
          " owner_admin) VALUES ('ali-p', ?, 30, 0, 1, 1, ?, ?)", (tid, panel.now(), row["id"]))
store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at) VALUES"
          " (?, 5, 'card', 'pending', ?)", (cust, panel.now()))
store.run("INSERT INTO api_tokens (name, token_hash, created_at, scope, admin_id) VALUES"
          " ('bot', 'h', ?, 'admin', ?)", (panel.now(), row["id"]))
store.set_setting("pay_text:%d" % row["id"], "card")
owner.post("admin-delete", id=str(row["id"]))
check("gone, and everything of theirs with them",
      not store.one("SELECT 1 FROM admins WHERE id = ?", (row["id"],))
      and not store.one("SELECT 1 FROM users WHERE id = ?", (cust,))
      and not store.one("SELECT 1 FROM ips WHERE user_id = ?", (cust,))
      and not store.one("SELECT 1 FROM transactions WHERE user_id = ?", (cust,))
      and not store.one("SELECT 1 FROM plans WHERE name = 'ali-p'")
      and not store.one("SELECT 1 FROM templates WHERE id = ?", (tid,))
      and not store.one("SELECT 1 FROM api_tokens WHERE admin_id = ?", (row["id"],))
      and not store.setting("pay_text:%d" % row["id"]))

print("the installer")
logic = open(os.path.join(HERE, "installer-logic.sh"), encoding="utf-8").read()
access = open(os.path.join(HERE, "..", "templates", "smartdns-access"), encoding="utf-8").read()
check("asks for the username on a new install and keeps it in admin.env",
      'read -r -p "  username [admin]: " ADMIN_USER' in logic and "ADMIN_USER=$ADMIN_USER" in logic)
check("  gives one to a panel from before, and shows it",
      "ADMIN_USER=%s" in logic and "username: %s" in logic)
check("smartdns-access changes it from the server", "set_key ADMIN_USER" in access)

srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("all checks passed")
