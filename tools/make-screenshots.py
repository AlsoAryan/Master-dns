#!/usr/bin/env python3
"""Screenshots of the admin panel, for the README: docs/screenshots/*.png.

Runs the real panel - the same code, the same stylesheet, the same fonts - against
a throw-away database filled with invented customers, plans and traffic, then
photographs each page with a headless browser. Nothing here touches a real panel,
and every address is one reserved for documentation (203.0.113.0/24,
198.51.100.0/24).

    python3 tools/make-screenshots.py            # all pages
    python3 tools/make-screenshots.py home api   # only these

Needs Playwright with Chromium (pip install playwright && playwright install chromium)
and, to keep the files small, Pillow. Run it again after the interface changes.
"""
import http.client
import importlib.machinery
import importlib.util
import json
import os
import random
import re
import sys
import tempfile
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "docs", "screenshots")
rng = random.Random(20261101)


def load(name, rel):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(
        name, os.path.join(ROOT, rel)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


panel = load("panel", "templates/smartdns-panel")
admin = load("admin", "templates/smartdns-admin")
admin.log = panel.log = lambda *a, **k: None
panel.print = lambda *a, **k: None

tmp = tempfile.mkdtemp()
DB = os.path.join(tmp, "panel.db")
EXIT, RELAYS, NODE = "203.0.113.9", ("198.51.100.2", "198.51.100.3"), "203.0.113.50"
SECRET, SALT, PASSWORD = "demo", "d1" * 16, "demo-pass-1"

# ------------------------------------------------------------------ the panel
with open(os.path.join(tmp, "panel.env"), "w") as fh:
    fh.write("SYNC_SECRET=demo\nRELAY_IP=%s\nNODE_IP=%s\nTUNNEL=backpack\nTUNNEL_TRANSPORT=stealth\n"
             "TUNNEL_DIRECTION=reverse\nTUNNEL_PORT=8444\n" % (",".join(RELAYS), NODE))
with open(os.path.join(tmp, "install-state"), "w") as fh:
    fh.write("exit-ip %s\n" % EXIT)
admin.PANEL_ENV = os.path.join(tmp, "panel.env")
admin.INSTALL_STATE = os.path.join(tmp, "install-state")
admin.SERVICES_FILE = os.path.join(ROOT, "domains", "services.json")
admin.CATALOGUE[:] = admin.load_catalogue()
panel.CATALOGUE[:] = admin.CATALOGUE
admin.bot_api_url = lambda: "https://panel.example.com:8445/api/v1/"


class Fine:
    """What a service manager says when everything is running."""
    returncode, stdout, stderr = 0, "", ""


# This machine has no systemd; the demo servers' services are all running.
_run = admin.subprocess.run
admin.subprocess.run = lambda cmd, *a, **k: Fine() if cmd and cmd[0] == "systemctl" else _run(cmd, *a, **k)

store = panel.Store(DB)
store.ensure_default_template(admin.CATALOGUE)
store.set_setting("owner_username", "admin")
store.set_setting("customer_panel_url", "https://my.example.com/")
admin.CFG = {"ADMIN_PATH": SECRET, "ADMIN_SALT": SALT, "ADMIN_PORT": "0",
             "ADMIN_HASH": admin.hash_password(PASSWORD, SALT)}
admin.STORE = admin.Store(DB)
NOW = datetime.now(timezone.utc)
iso = lambda t: t.isoformat(timespec="seconds")
GB = 1024 ** 3

# --------------------------------------------------------------- the business
default_t = store.one("SELECT id FROM templates WHERE is_default = 1")["id"]


def template(name, services):
    tid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES (?, 0, ?)",
                    (name, panel.now())).lastrowid
    for svc in admin.CATALOGUE:
        if svc["key"] in services:
            for g in svc["groups"]:
                store.run("INSERT INTO template_services (template_id, service_key, group_key)"
                          " VALUES (?, ?, ?)", (tid, svc["key"], g["key"]))
    return tid


t_play = template("گیمینگ کنسول", {"playstation", "xbox", "steam", "epic", "ea", "blizzard", "ubisoft",
                                   "riot", "nintendo", "anticheat"})
t_ps = template("فقط PlayStation", {"playstation", "anticheat", "blizzard"})
store.run("UPDATE templates SET name = 'همهٔ سرویس‌ها' WHERE id = ?", (default_t,))


def plan(name, tpl, days, gb, price, mbps=0, devices=1, trial=0, active=1):
    return store.run("INSERT INTO plans (name, template_id, days, quota_bytes, price, speed_kbps, devices,"
                     " is_trial, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (name, tpl, days, gb * GB, price, mbps * 1000, devices, trial, active,
                      panel.now())).lastrowid


p_trial = plan("تست رایگان", t_play, 1, 2, 0, 5, trial=1)
p_gold = plan("گیمینگ ماهانه", t_play, 30, 200, 250000, 50, 2)
p_ps = plan("PlayStation ماهانه", t_ps, 30, 100, 180000, 30)
p_max = plan("نامحدود سه‌ماهه", default_t, 90, 0, 1200000, 0, 3)
p_old = plan("پلن قدیمی", t_play, 30, 50, 100000, active=0)

NAMES = ["علی", "مریم", "رضا", "سارا", "حسین", "نگار", "امیر", "مهسا", "پویا", "الهه", "کیان", "نازنین",
         "آرش", "ریحانه", "سینا", "الناز", "مهدی", "پریسا", "بهنام", "ملیکا", "یاسر", "هدیه", "فرزاد",
         "نیلوفر", "داریوش", "شیدا", "کامران", "آیدا", "بابک", "سحر"]
SERVICES = ["playstation", "playstation", "playstation", "steam", "xbox", "blizzard", "epic", "ea", "riot"]
users = []
for n, name in enumerate(NAMES, 1):
    pl = rng.choice([p_gold, p_gold, p_ps, p_ps, p_max, p_trial])
    row = store.one("SELECT * FROM plans WHERE id = ?", (pl,))
    used = int(row["quota_bytes"] * rng.uniform(0.05, 0.95)) if row["quota_bytes"] else rng.randint(20, 400) * GB
    seen = NOW - timedelta(minutes=rng.choice([1, 2, 40, 300, 2000, 6000, 6000, 40000]))
    uid = store.run(
        "INSERT INTO users (username, first_name, telegram_id, created_at, status, plan_id, template_id,"
        " quota_bytes, used_bytes, expires_at, last_seen, max_ips, speed_kbps) VALUES"
        " (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)",
        ("user%d" % n, name, 700000000 + n, iso(NOW - timedelta(days=rng.randint(3, 120))), pl,
         row["template_id"], row["quota_bytes"], used,
         iso(NOW + timedelta(days=rng.randint(2, 28))), iso(seen), row["devices"] or 1,
         row["speed_kbps"])).lastrowid
    store.run("INSERT INTO ips (user_id, ip, added_at) VALUES (?, ?, ?)",
              (uid, "192.0.2.%d" % (10 + n), panel.now()))
    users.append((uid, rng.uniform(0.3, 2.2), rng.choice(SERVICES)))
store.run("UPDATE users SET status = 'suspended' WHERE id = ?", (users[-1][0],))

# ---------------------------------------------------------------- the traffic
# A day of an evening-heavy audience: busy after dinner, quiet before dawn.
SHAPE = [2, 1, 1, 1, 1, 2, 3, 4, 5, 5, 6, 6, 7, 7, 8, 9, 10, 12, 14, 17, 19, 20, 14, 7]
today_tehran = NOW.astimezone(panel.TEHRAN)
with store.lock:
    for uid, weight, favourite in users:
        for back in range(7, -1, -1):
            for hour in range(24):
                when = (today_tehran - timedelta(days=back)).replace(hour=hour, minute=rng.randint(0, 55))
                if when > NOW.astimezone(panel.TEHRAN):
                    break
                down = int(SHAPE[hour] * weight * rng.uniform(0.4, 1.6) * 40 * 1024 ** 2)
                panel.record_usage(store.db, uid, down // 12, down, when)
        for back in range(60, 7, -1):
            when = today_tehran - timedelta(days=back)
            down = int(sum(SHAPE) * weight * rng.uniform(0.3, 1.5) * 40 * 1024 ** 2)
            store.db.execute("INSERT OR REPLACE INTO usage (user_id, grain, bucket, up, down)"
                             " VALUES (?, '1d', ?, ?, ?)",
                             (uid, panel.usage_buckets(when)["1d"], down // 12, down))
        for back in range(30):
            day = panel.usage_buckets(today_tehran - timedelta(days=back))["1d"]
            for svc, share in ((favourite, 0.7), ("steam", 0.15), ("_other", 0.1), ("_http", 0.05)):
                store.db.execute("INSERT OR REPLACE INTO usage_service (user_id, day, service, bytes)"
                                 " VALUES (?, ?, ?, ?)",
                                 (uid, day, svc, int(weight * share * rng.uniform(0.5, 1.5) * 3 * GB)))
    store.db.commit()

# --------------------------------------------------------- servers and health
def sample(host, cpu, mem, disk, rx, tx, ago=15):
    store.run("INSERT INTO metrics (host, at, cpu, load, mem_used, mem_total, swap_used, swap_total,"
              " disk_used, disk_total, rx_bps, tx_bps, uptime) VALUES (?, ?, ?, 1.0, ?, ?, 0, 0, ?, ?, ?, ?, ?)",
              (host, iso(NOW - timedelta(seconds=ago)), cpu, int(mem * 8 * GB), 8 * GB,
               int(disk * 80 * GB), 80 * GB, rx, tx, 86400 * rng.randint(3, 40)))


sample("exit", 23, 0.46, 0.31, 8_400_000, 31_000_000)
sample(RELAYS[0], 41, 0.62, 0.22, 36_000_000, 9_200_000)
sample(RELAYS[1], 12, 0.35, 0.18, 4_100_000, 1_000_000)
sample(NODE, 8, 0.28, 0.2, 2_000_000, 7_000_000)
for ip, name in ((EXIT, "آلمان ۱"), (NODE, "هلند ۱"), (RELAYS[0], "تهران ۱"), (RELAYS[1], "اصفهان ۱")):
    store.set_setting("server_name:" + ip, name)
store.set_setting("relay_tunnel_state:" + RELAYS[0], json.dumps({"on": True, "running": True}))
store.set_setting("relay_tunnel_state:" + RELAYS[1], json.dumps({"on": False}))
store.set_setting("relay_tunnel:" + RELAYS[1], json.dumps(
    {"transport": "wss", "direction": "reverse", "port": 8447, "exit": EXIT}))
store.set_setting("local_egress", json.dumps([
    {"name": n, "ips": []} for n in ("rex-ps5-loginservice.prod.demonware.net", "ps5.np.playstation.net",
                                     "gs-sec.ww.np.dl.playstation.net", "auth.embark.net")]))
store.set_setting("local_egress_state:" + RELAYS[0], json.dumps(
    {"rex-ps5-loginservice.prod.demonware.net": [4, 6], "ps5.np.playstation.net": [3, 8],
     "gs-sec.ww.np.dl.playstation.net": [5, 7], "auth.embark.net": [0, 3]}))

# ------------------------------------------------------ receipts, tickets, keys
for uid, amount, plan_id in ((users[3][0], 250000, p_gold), (users[7][0], 180000, p_ps),
                             (users[11][0], 1200000, p_max)):
    store.run("INSERT INTO transactions (user_id, amount, kind, status, created_at, plan_id, list_price)"
              " VALUES (?, ?, 'card', 'pending', ?, ?, ?)",
              (uid, amount, iso(NOW - timedelta(minutes=rng.randint(5, 400))), plan_id, amount))
for uid, subject in ((users[5][0], "اتصال PlayStation وصل نمی‌شود"), (users[9][0], "تمدید پلن")):
    tid = store.run("INSERT INTO tickets (user_id, subject, status, created_at, updated_at)"
                    " VALUES (?, ?, 'open', ?, ?)", (uid, subject, panel.now(), panel.now())).lastrowid
    store.run("INSERT INTO ticket_messages (ticket_id, from_admin, body, created_at) VALUES (?, 0, ?, ?)",
              (tid, "سلام، وقت بخیر. کمک می‌خواستم.", panel.now()))
store.run("INSERT INTO api_tokens (name, token_hash, scope, allow_ips, created_at, last_used_at, webhook_url)"
          " VALUES ('ربات مدیریت', 'h1', 'admin', '203.0.113.77', ?, ?, 'https://bot.example.com/hook')",
          (iso(NOW - timedelta(days=12)), iso(NOW - timedelta(minutes=2))))
store.run("INSERT INTO api_tokens (name, token_hash, scope, created_at, last_used_at)"
          " VALUES ('ربات فروش', 'h2', 'customer', ?, ?)",
          (iso(NOW - timedelta(days=40)), iso(NOW - timedelta(minutes=9))))

# ---------------------------------------------------------------- the server
srv = admin.make_admin_server(None, 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


def sign_in():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("POST", "/%s/" % SECRET, urllib.parse.urlencode({"username": "admin", "password": PASSWORD}),
              {"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    m = re.search(r"sdns=([^;]*)", r.getheader("Set-Cookie") or "")
    if not m:
        sys.exit("could not sign in to the demo panel")
    return m.group(1)


# ----------------------------------------------------------------- the photos
# name: (path, what to wait for, height in px, dark theme). The users table is the widest
# thing in the panel, and is photographed in a wider window.
WIDE = {"users": 1760}
user_id = users[2][0]
PAGES = {
    "home": ("", None, 1500, False),
    "home-dark": ("", None, 900, True),
    "users": ("users", None, 900, False),
    "user-usage": ("user-usage?u=%d" % user_id, None, 1500, False),
    "plans": ("plans", None, 1100, False),
    "templates": ("templates", None, 760, False),
    "template-editor": ("templates?t=%d" % t_play, None, 900, False),
    "nodes": ("nodes", None, 1250, False),
    "settings": ("settings", None, 1350, False),
    "api": ("api", None, 1500, False),
    "receipts": ("receipts", None, 800, False),
}


def shoot(wanted):
    from playwright.sync_api import sync_playwright
    try:
        from PIL import Image
    except ImportError:
        Image = None
    os.makedirs(OUT, exist_ok=True)
    cookie = sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (path, _, height, dark) in PAGES.items():
            if wanted and name not in wanted:
                continue
            width = WIDE.get(name, 1360)
            ctx = browser.new_context(viewport={"width": width, "height": height}, locale="fa-IR",
                                      color_scheme="dark" if dark else "light")
            ctx.add_cookies([{"name": "sdns", "value": cookie, "url": "http://127.0.0.1:%d" % PORT}])
            page = ctx.new_page()
            if dark:
                page.add_init_script("try{localStorage.setItem('theme','dark')}catch(e){}")
            page.goto("http://127.0.0.1:%d/%s/%s" % (PORT, SECRET, path))
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1300)             # the cards' arrival animations
            wide = page.evaluate("document.documentElement.scrollWidth")
            if wide > width + 2:
                # A page wider than the window is a layout bug, not a photo to take.
                sys.exit("%s is %d px wide in a %d px window" % (name, wide, width))
            target = os.path.join(OUT, name + ".png")
            page.screenshot(path=target)
            ctx.close()
            if Image:
                Image.open(target).convert("RGB").quantize(colors=160, method=2).save(target, optimize=True)
            print("%-16s %6.0f KB" % (name, os.path.getsize(target) / 1024))
        browser.close()


if __name__ == "__main__":
    shoot(set(sys.argv[1:]))
    srv.shutdown()
