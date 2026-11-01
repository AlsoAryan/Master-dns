#!/usr/bin/env python3
"""0.1.1: two reports for the operator's bot - traffic, and is-it-up.

What has to hold: the traffic report adds up the same tables the charts are drawn
from, sets the window against the one before it, finds the busiest hour in Tehran's
time, names the heaviest customers and services, keeps a reseller to their own
customers, and refuses a window it cannot give; the status report says which
servers are online, that one silent for two minutes is not, keeps what is wrong, and
is the owner's alone.
"""
import importlib.machinery
import importlib.util
import json
import os
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
          ((" - " + str(detail)[:500]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


spec = importlib.util.spec_from_loader("panel", importlib.machinery.SourceFileLoader(
    "panel", os.path.join(HERE, "..", "templates", "smartdns-panel")))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
panel.log = lambda *a, **k: None
panel.print = lambda *a, **k: None
DBPATH = os.path.join(tempfile.mkdtemp(), "panel.db")
store = panel.Store(DBPATH)
admin_src = open(os.path.join(HERE, "..", "templates", "smartdns-admin"), encoding="utf-8").read()
GB = 10 ** 9
NOW = datetime.now(timezone.utc)
panel.CATALOGUE[:] = [{"key": "psn", "label": "PlayStation", "groups": []},
                      {"key": "steam", "label": "Steam", "groups": []}]


def user(name, seller=None, quota=0, used=0, tg=None):
    store.run("INSERT INTO users (username, created_at, status, owner_admin, quota_bytes,"
              " used_bytes, telegram_id) VALUES (?, ?, 'active', ?, ?, ?, ?)",
              (name, panel.now(), seller, quota, used, tg))
    return store.one("SELECT id FROM users WHERE username = ?", (name,))["id"]


def traffic(uid, ago_days, up, down, hour=None):
    """A day's traffic, and - when `hour` is given - that hour of it, in Tehran's time."""
    when = (NOW - timedelta(days=ago_days)).astimezone(panel.TEHRAN)
    if hour is not None:
        when = when.replace(hour=hour, minute=10)
    store.run("INSERT OR REPLACE INTO usage (user_id, grain, bucket, up, down) VALUES (?, '1d', ?, ?, ?)",
              (uid, panel.usage_buckets(when)["1d"], up, down))
    if hour is not None:
        store.run("INSERT OR REPLACE INTO usage (user_id, grain, bucket, up, down)"
                  " VALUES (?, '1h', ?, ?, ?)", (uid, panel.usage_buckets(when)["1h"], up, down))


def service(uid, ago_days, key, n):
    day = panel.usage_buckets(NOW - timedelta(days=ago_days))["1d"]
    store.run("INSERT OR REPLACE INTO usage_service (user_id, day, service, bytes) VALUES (?, ?, ?, ?)",
              (uid, day, key, n))


big = user("big", quota=100 * GB, used=40 * GB)
small = user("small")
resold = user("resold", seller=7)
traffic(big, 0, 1 * GB, 9 * GB, hour=21)            # today, 21:xx
traffic(big, 2, 1 * GB, 4 * GB, hour=21)
traffic(small, 1, 0, 2 * GB, hour=14)
traffic(resold, 1, 1 * GB, 1 * GB, hour=14)
traffic(big, 9, 2 * GB, 8 * GB)                     # before a 7-day window: the one it is set against
service(big, 0, "psn", 8 * GB)
service(big, 1, "steam", 1 * GB)
service(small, 1, "_other", 1 * GB)
service(resold, 1, "psn", 2 * GB)
for extra in range(2):                      # five customers of the owner's in all
    service(user("extra%d" % extra), 1, "steam", 1 * GB)

print("the traffic report")
r = panel.traffic_report(store, None, 7)
check("the window's total is every customer's, up and down",
      r["total"] == {"up": 3 * GB, "down": 16 * GB}, r["total"])
check("today is on its own", r["today"] == {"up": 1 * GB, "down": 9 * GB}, r["today"])
check("set against the seven days before it: 19 GB now, 10 GB before", r["previous"] == {"up": 2 * GB, "down": 8 * GB}
      and r["change_percent"] == 90.0, (r["previous"], r["change_percent"]))
check("one line for each day, oldest first, the quiet ones as zero",
      len(r["daily"]) == 7 and r["daily"][0]["day"] < r["daily"][-1]["day"]
      and sum(d["up"] + d["down"] for d in r["daily"]) == 19 * GB
      and any(d["up"] == d["down"] == 0 for d in r["daily"]), r["daily"])
check("the busiest hour is 21, and every hour of the day is there",
      len(r["by_hour"]) == 24 and r["peak"] == {"hour": 21, "bytes": 15 * GB}
      and r["by_hour"][14] == 4 * GB, (r["peak"], r["by_hour"]))
check("the heaviest customer first, with their share and how much of their quota is gone",
      r["top_users"][0]["label"] == "big" and r["top_users"][0]["bytes"] == 15 * GB
      and r["top_users"][0]["share_percent"] == 78.9 and r["top_users"][0]["quota_percent"] == 40.0
      and r["top_users"][1]["quota_percent"] is None, r["top_users"])
check("and the services, by the names the customer knows them by",
      [s["label"] for s in r["services"]] == ["PlayStation", "Steam", "سایر"]
      and r["services"][0]["bytes"] == 10 * GB and not r["services_hidden"], r["services"])
check("with something to send as it is", "ترافیک 7 روز اخیر" in r["text"] and "شلوغ‌ترین ساعت: 21 تا 22" in r["text"]
      and "big" in r["text"] and "PlayStation" in r["text"], r["text"])

print("a reseller's own")
mine = panel.traffic_report(store, 7, 7)
check("only their customers are counted", mine["total"] == {"up": 1 * GB, "down": 1 * GB}, mine["total"])
check("  and only theirs are named",
      [u["label"] for u in mine["top_users"]] == ["resold"])
check("  the services of one customer are not given, however they are asked for: they are theirs",
      mine["services"] == [] and mine["services_hidden"] is True)
check("  and the same for the owner's, when only a few customers are in it",
      panel.traffic_report(store, 8, 7)["services"] == [])
check("  with no peak hour borrowed from anybody else", mine["peak"] == {"hour": 14, "bytes": 2 * GB})
empty = panel.traffic_report(store, 99, 7)
check("a seller with nobody has an empty report, not a failure",
      empty["total"] == {"up": 0, "down": 0} and empty["peak"] is None and empty["top_users"] == []
      and empty["services"] == [] and empty["change_percent"] is None)

print("the window")
check("the longest it can be asked for is sixty days, the shortest one",
      panel.traffic_report(store, None, 60)["days"] == 60 and panel.traffic_report(store, None, 1)["days"] == 1
      and len(panel.traffic_report(store, None, 1)["daily"]) == 1)
check("beyond that it is cut to them", panel.traffic_report(store, None, 500)["days"] == 60
      and panel.traffic_report(store, None, 0)["days"] == 1)
check("a longer window than the hours are kept says this week, and the service days are thirty",
      panel.traffic_report(store, None, 60)["services_days"] == 30)


class Fake:
    """The one thing the handlers need of the server: what was asked, and the store."""
    def __init__(self, query, seller=None):
        self.query, self._seller, self.store = query, seller, store
        self.relays, self.nodes = ["198.51.100.2"], ["203.0.113.50"]


def call(name, query=None, seller=None):
    return getattr(panel.AdminAPI, "api_" + name)(Fake(query or {}, seller), {})


Api = panel.BotAPI
code, body = call("admin_traffic", {"days": ["3"]})
check("the API answers with it, ok", code == 200 and body["ok"] and body["days"] == 3)
check("a window that is not a number, or not in range, is refused",
      call("admin_traffic", {"days": ["x"]})[0] == 400 and call("admin_traffic", {"days": ["61"]})[0] == 400
      and call("admin_traffic", {"days": ["0"]})[1]["error"] == "bad_days")
check("the default is a week", call("admin_traffic")[1]["days"] == 7)
check("a reseller's key gets its own", call("admin_traffic", seller=7)[1]["total"]["down"] == 1 * GB)
check("the routes are there, and for a key with admin rights only",
      any(n == "admin_traffic" for _, _, n in Api.ROUTES) and any(n == "admin_status_report" for _, _, n in Api.ROUTES)
      and all(p.pattern.startswith("/api/v1/admin/") for _, p, n in Api.ROUTES
              if n in ("admin_traffic", "admin_status_report")))

print("the status report")
EXIT, RELAY, NODE = "203.0.113.9", "198.51.100.2", "203.0.113.50"


def sample(host, age, cpu=12.0):
    at = (NOW - timedelta(seconds=age)).isoformat(timespec="seconds")
    store.run("INSERT INTO metrics (host, at, cpu, mem_used, mem_total, disk_used, disk_total, rx_bps, tx_bps,"
              " uptime) VALUES (?, ?, ?, 500, 1000, 250, 1000, 100, 200, 172800)", (host, at, cpu))


sample("exit", 20)
sample(RELAY, 30, cpu=55.5)
sample(NODE, 400)                                   # silent for more than two minutes
store.set_setting("server_name:" + RELAY, "تهران ۱")
store.set_setting("relay_tunnel_state:" + RELAY, json.dumps({"on": True, "running": True}))
s = panel.status_report(store, [RELAY], [NODE], EXIT)
by = {x["ip"]: x for x in s["servers"]}
check("every server is listed, the exit first, with its kind",
      [x["kind"] for x in s["servers"]] == ["exit", "node", "relay"], [x["kind"] for x in s["servers"]])
check("one heard from lately is online, with how full it is",
      by[RELAY]["online"] and by[RELAY]["cpu_percent"] == 55.5 and by[RELAY]["memory_percent"] == 50.0
      and by[RELAY]["disk_percent"] == 25.0 and by[RELAY]["uptime_days"] == 2, by[RELAY])
check("one silent for 400 seconds is not, whatever it last said",
      by[NODE]["online"] is False and by[NODE]["seen_seconds_ago"] >= 400)
check("a relay carries its name and its tunnel",
      by[RELAY]["name"] == "تهران ۱" and by[RELAY]["tunnel"] == {"on": True, "running": True, "error": ""})
check("so the service is not healthy", s["healthy"] is False and "قطع" in s["text"] and "مشکل هست" in s["text"])
sample(NODE, 10)
check("with the node back, and nothing open, it is", panel.status_report(store, [RELAY], [NODE], EXIT)["healthy"])
store.set_setting("alert_state:relay-down:" + RELAY, "down")
store.run("INSERT INTO alerts (at, key, ok, text) VALUES (?, ?, 0, ?)",
          (panel.now(), "relay-down:" + RELAY, "رله " + RELAY + " جواب نمی‌دهد"))
s = panel.status_report(store, [RELAY], [NODE], EXIT)
check("an alert still open keeps it unhealthy, with its words",
      not s["healthy"] and s["problems"][0]["text"].startswith("رله") and "⚠️" in s["text"], s["problems"])
store.set_setting("alert_state:relay-down:192.0.2.77", "down")
check("an alert about a server no longer on the panel is not shown",
      len(panel.status_report(store, [RELAY], [NODE], EXIT)["problems"]) == 1)
check("a server that never reported is offline, not missing",
      panel.status_report(store, [RELAY, "198.51.100.77"], [NODE], EXIT)["servers"][-1]["online"] is False)
check("the API gives it to the owner's key, ok",
      call("admin_status_report")[0] == 200 and call("admin_status_report")[1]["ok"])
check("and refuses a reseller's: the servers are not theirs",
      call("admin_status_report", seller=7)[0] == 403
      and call("admin_status_report", seller=7)[1]["error"] == "owner_only")

print("the index")
check("a report across customers reads the buckets by their index",
      any("usage_by_bucket" in (r["name"] or "") for r in store.q("PRAGMA index_list(usage)")))
plan = " ".join(r["detail"] for r in store.q(
    "EXPLAIN QUERY PLAN SELECT SUM(up) FROM usage WHERE grain = '1d' AND bucket >= '2026-01-01'"))
check("  and the query uses it", "usage_by_bucket" in plan, plan)

print("the same on the home page")
spec = importlib.util.spec_from_loader("admin", importlib.machinery.SourceFileLoader(
    "admin", os.path.join(HERE, "..", "templates", "smartdns-admin")))
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)
admin.log = lambda *a, **k: None
admin.STORE = admin.Store(DBPATH)
admin.catalogue_now = lambda: panel.CATALOGUE
hours = admin.busy_hours()
check("the busy hours are the same 24 sums, in Tehran's time", len(hours) == 24 and hours[21] == 15 * GB
      and hours[14] == 4 * GB and sum(hours) == 19 * GB, hours)
check("and a seller's are their customers' alone", admin.busy_hours(7)[14] == 2 * GB and admin.busy_hours(7)[21] == 0)
names, whole = admin.top_services()
check("services are named as the customer knows them, most first",
      [n for n, _ in names][:2] == ["PlayStation", "Steam"], names)
check("not given for a few customers, whoever asks", admin.top_services(7)[0] == [] and admin.top_services(8)[0] == [])
card = admin.traffic_insight_card()
check("the card draws the day by the hour, marks the peak, and lists the services",
      "class='hours'" in card and card.count("class='hr") == 24 and "class='hr peak'" in card
      and "شلوغ‌ترین ساعت: <b>21 تا 22</b>" in card and "PlayStation" in card, card[:300])
check("and a seller's card has no services to list", "PlayStation" not in admin.traffic_insight_card(7))
check("an empty panel draws nothing rather than an empty box",
      admin.traffic_insight_card(8) == "")
check("it is on the home page for the owner and for a seller",
      admin_src.count("traffic_insight_card(") >= 3)

print("the API page")
admin.bot_api_url = lambda: "https://panel.example.com:8445/api/v1/"
admin.CFG = {"ADMIN_PATH": "p"}
S = admin.STORE
S.run("INSERT INTO api_tokens (name, token_hash, scope, allow_ips, created_at, webhook_url) VALUES (?, 'h1', 'admin',"
      " '203.0.113.7', ?, 'https://bot.example.com/hook')", ("ربات مدیر", panel.now()))
S.run("INSERT INTO api_tokens (name, token_hash, scope, created_at, revoked_at) VALUES (?, 'h2', 'customer', ?, ?)",
      ("ربات قدیمی", panel.now(), panel.now()))
page = admin.Admin.api_keys(type("R", (), {})())
check("a key is a card with whose it is, where it works from and its webhook",
      "<article class='plan'>" in page and "ربات مدیر" in page and "ادمین" in page and "203.0.113.7" in page
      and "https://bot.example.com/hook" in page)
check("a revoked one is kept apart, and marked", "کلیدهای باطل‌شده (1)" in page and "باطل شده</span>" in page)
check("the address and a command to try it are there to copy",
      "data-copy='https://panel.example.com:8445/api/v1/'" in page and "Authorization: Bearer dd_..." in page)
check("a new key is made in a dialog, with the old form's fields",
      "id='keynew'" in page and "name='allow_ips'" in page and "name='admin'" in page
      and "/p/api-key-new" in page)
check("both new reports are listed, as new, with what they are for",
      page.count("class='pill ok'>تازه") == 2 and "admin/traffic?days=7" in page and "admin/status" in page)
check("and every recipe's address is one the API has",
      all(any(rx.pattern.replace("$", "").rstrip("/?") .startswith("/api/v1/" + path.split("?")[0].split("/")[0])
              for _, rx, _ in panel.BotAPI.ROUTES) for _, _, _, path, _, _ in admin.API_RECIPES if path))
check("a page with no keys says so and still shows how to try the API",
      "هنوز کلیدی نساخته‌اید" in (S.run("DELETE FROM api_tokens"), admin.Admin.api_keys(type("R", (), {})()))[1])

print("the forecast on a customer's page")
now = datetime(2026, 10, 1, tzinfo=timezone.utc)
u = {"quota_bytes": 100 * GB, "used_bytes": 40 * GB, "expires_at": (now + timedelta(days=30)).isoformat()}
f = admin.quota_forecast(u, 14 * GB, now)
check("40 of 100 GB at 2 GB a day: 30 days left, the period has 30: not before its end",
      f["percent"] == 40 and f["days_left"] == 30 and f["days_to_end"] == 30 and not f["runs_out_first"], f)
f = admin.quota_forecast(u, 35 * GB, now)
check("at 5 GB a day it is gone in 12 days, well before the 30 - and it says so",
      f["days_left"] == 12 and f["runs_out_first"], f)
check("unlimited has no forecast, only a pace",
      admin.quota_forecast(dict(u, quota_bytes=0), 7 * GB, now)["days_left"] is None)
check("a week of nothing cannot be forecast, not 'never'",
      admin.quota_forecast(u, 0, now)["days_left"] is None and not admin.quota_forecast(u, 0, now)["runs_out_first"])
check("used up is shown as used up, whatever the pace",
      "حجم تمام شده است" in admin.forecast_card(dict(u, used_bytes=100 * GB), GB))
check("a period already over is not 'before its end'",
      not admin.quota_forecast(dict(u, expires_at=(now - timedelta(days=2)).isoformat()), 35 * GB, now)["runs_out_first"])
page_ok = admin.forecast_card(dict(u, expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat()), 35 * GB)
check("the card shows the bar, the verdict and the pace",
      "class='bar'" in page_ok and "تا <b>12 روز</b> دیگر" in page_ok and "در روز" in page_ok, page_ok[:200])
check("it is on the page", "forecast_card(user, week[0] + week[1])" in admin_src)

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
