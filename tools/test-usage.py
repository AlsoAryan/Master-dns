#!/usr/bin/env python3
"""The usage dashboard: what a customer's traffic turns into, and who sees it.

What has to hold: the bill is exactly what it was - the charts add to it,
never change it; upload and download are counted apart and a reset counter is
growth, not a negative; the names a relay reports become catalogue services
and nothing else is stored; the relay reads and deletes its raw lines and
keeps what it could not deliver; the customer's page draws with no data and
with a lot; and the per-service part is the customer's alone - neither the
admin page nor a bot's key gets it.
"""
import importlib.machinery
import importlib.util
import json
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
GB = 1024 ** 3


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
sync = load("templates/smartdns-sync", "sync")
panel.log = lambda *a: None
panel.print = lambda *a, **k: None
admin.log = lambda *a: None
sync.log = lambda *a: None

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "panel.db")
store = panel.Store(db_path)
admin.DB = db_path
admin.CFG = {"ADMIN_PATH": "p"}
admin.STORE = admin.Store(db_path)
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
u = store.create_user(111, "ali", "علی")
store.run("UPDATE users SET status = 'active', quota_bytes = ?, used_bytes = 0,"
          " expires_at = ? WHERE id = ?",
          (100 * GB, (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(), u["id"]))
panel.register_ip(store, u["id"], "203.0.113.5")
user = lambda: store.one("SELECT * FROM users WHERE id = ?", (u["id"],))

# ---------------------------------------------------------------- counting
print("the bill and the charts")
store.fold_counters("r1", {"203.0.113.5": 1000}, {"203.0.113.5": [100, 900]})
store.fold_counters("r1", {"203.0.113.5": 5000}, {"203.0.113.5": [600, 4400]})
check("the bill is the growth of the total, as before", user()["used_bytes"] == 5000,
      str(user()["used_bytes"]))
rows = store.q("SELECT grain, up, down FROM usage WHERE user_id = ?", (u["id"],))
by = {r["grain"]: (r["up"], r["down"]) for r in rows}
check("each grain has upload and download apart",
      by.get("5m") == (600, 4400) and by.get("1h") == (600, 4400) and by.get("1d") == (600, 4400),
      repr(by))
store.fold_counters("r1", {"203.0.113.5": 300}, {"203.0.113.5": [50, 250]})
day = store.one("SELECT up, down FROM usage WHERE user_id = ? AND grain = '1d'", (u["id"],))
check("a counter that went back is growth from zero, not a negative",
      (day["up"], day["down"]) == (650, 4650) and user()["used_bytes"] == 5300,
      "%r %r" % (tuple(day), user()["used_bytes"]))
store.fold_counters("r1", {"203.0.113.5": 800})
day = store.one("SELECT up, down FROM usage WHERE user_id = ? AND grain = '1d'", (u["id"],))
check("an older relay without the split still shows up, as download",
      (day["up"], day["down"]) == (650, 5150), repr(tuple(day)))
store.fold_counters("r1", {"198.51.100.1": 10 ** 9}, {"198.51.100.1": [1, 10 ** 9 - 1]})
check("an address nobody has is not charted for anybody",
      store.one("SELECT count(*) c FROM usage")["c"] == 3)

# ---------------------------------------------------------------- services
print("services")
catalogue = [{"key": "psn", "label": "پلی‌استیشن",
              "groups": [{"domains": ["playstation.net", "playstation.com"]}]},
             {"key": "steam", "label": "استیم",
              "groups": [{"domains": ["steampowered.com"]}]}]
idx = panel.service_index(catalogue)
check("a host is its catalogue service by its longest name of ours",
      panel.service_of("gs2.ww.prod.dl.playstation.net", idx) == "psn"
      and panel.service_of("STORE.steampowered.com.", idx) == "steam")
check("a name not ours is 'other', and a bare port is what the port is",
      panel.service_of("example.org", idx) == "_other"
      and panel.service_of(":80", idx) == "_http"
      and panel.service_of(":4070", idx) == "spotify")
panel.record_services(store, {"203.0.113.5": {"gs2.ww.prod.dl.playstation.net": 700,
                                              "ps4.playstation.com": 300,
                                              "example.org": 50, ":80": 20, "x": "bad"},
                              "198.51.100.1": {"playstation.net": 99}}, catalogue)
svc = {r["service"]: r["bytes"] for r in store.q("SELECT * FROM usage_service")}
check("names become services, added up per customer per day",
      svc == {"psn": 1000, "_other": 50, "_http": 20}, repr(svc))
kept = json.dumps([dict(r) for r in store.q("SELECT * FROM usage_service")])
check("no host name is kept anywhere", "playstation.net" not in kept
      and "example.org" not in kept)

view = panel.usage_view(store, user(), catalogue)
check("the customer's view has the services, biggest first, with labels",
      [s["key"] for s in view["services"]] == ["psn", "_other", "_http"]
      and view["services"][0]["label"] == "پلی‌استیشن")
check("and the week, the month, and when the allowance runs out",
      sum(view["week"]) == 650 + 5150 and view["runs_out_days"] is not None
      and view["runs_out_days"] > 1000)
check("without services when asked", "services" not in
      panel.usage_view(store, user(), catalogue, with_services=False))

old = (datetime.now(panel.TEHRAN) - timedelta(days=40)).strftime("%Y-%m-%d")
store.run("INSERT INTO usage_service (user_id, day, service, bytes) VALUES (?, ?, 'psn', 5)",
          (u["id"], old))
store.run("INSERT INTO usage (user_id, grain, bucket, up, down) VALUES (?, '5m', ?, 1, 1)",
          (u["id"], old + "T10:00"))
panel.prune_usage(store)
check("pruning drops services after 30 days and five-minute rows after two",
      not store.one("SELECT 1 FROM usage_service WHERE day = ?", (old,))
      and not store.one("SELECT 1 FROM usage WHERE bucket = ?", (old + "T10:00",))
      and store.one("SELECT count(*) c FROM usage")["c"] == 3)

# ---------------------------------------------------------------- the relay
print("the relay's side")
sync.USAGE_LOG = os.path.join(tmp, "usage.log")
sync.USAGE_TAKEN = sync.USAGE_LOG + ".taken"
reopened = []
sync.sh = lambda *a: reopened.append(a)
with open(sync.USAGE_LOG, "w") as fh:
    fh.write("203.0.113.5 443 gs2.playstation.net 900 100\n"
             "203.0.113.5 443 gs2.playstation.net 50 50\n"
             "203.0.113.5 80 - 10 10\n"
             "garbage\n")
first = sync.take_usage()
check("the first pass only moves the file aside and has nginx reopen it",
      first == {} and os.path.exists(sync.USAGE_TAKEN)
      and not os.path.exists(sync.USAGE_LOG) and reopened == [("nginx", "-s", "reopen")])
second = sync.take_usage()
check("the next reads it, adds it up by name, and deletes it",
      second == {"203.0.113.5": {"gs2.playstation.net": 1100, ":80": 20}}
      and not os.path.exists(sync.USAGE_TAKEN), repr(second))
check("what was not delivered is still there next time",
      sync.take_usage() == second)
sync.USAGE_PENDING.clear()
check("and gone once delivered", sync.take_usage() == {})

# ---------------------------------------------------------------- pages
print("pages")
sync.CFG = {"PANEL_DOMAIN": "users.example.com"}
empty = sync.usage_page({"ok": True})
check("the page draws with nothing to show",
      "<svg" in empty and "هنوز چیزی نیست" in empty)
full = panel.usage_view(store, user(), catalogue)
full = json.loads(json.dumps(full))       # as the relay receives it
page = sync.usage_page(full)
check("and with data: charts, legend, table, services, the connection check",
      page.count("<svg") == 3 and "دانلود" in page and "جدول روزانه" in page
      and "پلی‌استیشن" in page and "/ping" in page)
check("every bar says what it is on hover", "<title>" in page)

admin_page = admin.Admin.user_usage_page(type("R", (), {"path": "/p/usage?u=%d" % u["id"]})())
check("the admin sees the charts", admin_page.count("<svg") == 3)
check("but not the services", "پلی‌استیشن" not in admin_page and "سرویس" in admin_page)
missing = admin.Admin.user_usage_page(type("R", (), {"path": "/p/usage?u=99999"})())
check("an account that is not there is said so", "پیدا نشد" in missing)

api = panel.BotAPI.__new__(panel.BotAPI)
api.store = store
api.relays = ()
code, res = api.api_usage({}, "111")
check("a bot's key gets the numbers, not the services",
      code == 200 and res["ok"] and "services" not in res and res["days"]
      and set(res["days"][0]) == {"day", "up", "down"}, repr(res)[:200])
code, res = api.api_usage({}, "999")
check("and a stranger's Telegram id is refused", code == 404)

shutil.rmtree(tmp, ignore_errors=True)
print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
