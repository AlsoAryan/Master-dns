#!/usr/bin/env python3
"""The game index: the list an operator thinks in, over the groups that route.

domains/games.json holds no domains. Each entry names the groups its game
needs, so ticking "Valorant" ticks Riot's groups - and the two files can
disagree in ways nobody would notice until a customer bought a plan that
routes half a game. So: every reference resolves, every group in the games
section is reachable from some entry, and a game is only reported as covered
when all of it is.

The other half is what happens when a group is split. A template names groups
by key; breaking "every shooter" into a group per game makes the old key
vanish, and a template that named it would route none of them. The panel
follows the split instead, which is what follow_catalogue_split is for.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def load(name, mod):
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(
            mod, os.path.join(ROOT, "templates", name)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


with open(os.path.join(ROOT, "domains", "services.json"), encoding="utf-8") as fh:
    catalogue = json.load(fh)
with open(os.path.join(ROOT, "domains", "games.json"), encoding="utf-8") as fh:
    index = json.load(fh)

services = catalogue["services"]
games = index["games"]
groups = {"%s/%s" % (s["key"], g["key"]) for s in services for g in s["groups"]}
game_section = {s["key"] for s in services if s.get("section") == "games"}

print("the file itself")
check("every service is in a section",
      all(s.get("section") for s in services),
      str([s["key"] for s in services if not s.get("section")]))
known = {sec["key"] for sec in catalogue.get("sections", [])}
sections = [sec["key"] for sec in catalogue["sections"]]
group_sections = {g.get("section") for s_ in services for g in s_["groups"]} - {None}
check("a group that names its own section names a real one",
      group_sections <= set(sections), str(group_sections))
check("every download group is under the downloads heading",
      all(g.get("section") == "downloads"
          for s_ in services for g in s_["groups"] if g["key"] == "download"),
      str([s_["key"] for s_ in services for g in s_["groups"]
           if g["key"] == "download" and g.get("section") != "downloads"]))
check("what is off until asked for sits at the bottom, out of the games",
      all(next(x for x in services if x["key"] == k)["section"] == "bypass"
          for k in ("pubgmobile", "gamebackend", "bypass")))
check("every section a service names is one of the headings",
      {s.get("section") for s in services} <= known,
      str({s.get("section") for s in services} - known))
check("the games have unique keys",
      len({g["key"] for g in games}) == len(games))
check("every game says who it is, in both languages",
      all(g.get("name") and g.get("en") for g in games))
missing = sorted({ref for g in games for ref in g["needs"] if ref not in groups})
check("every group a game needs exists in the catalogue", not missing, str(missing))
reachable = {ref for g in games for ref in g["needs"]}
orphan = sorted(k for k in groups if k.split("/")[0] in game_section
                and k not in reachable)
check("no group of a game service is unreachable from the list", not orphan,
      str(orphan))
check("a game needs at least one group",
      all(g["needs"] for g in games))

# A domain in two groups would make the "switched off" list ambiguous: it is
# keyed by the domain alone, which the schema comment relies on.
seen = {}
twice = []
for s in services:
    for g in s["groups"]:
        for d in g["domains"]:
            if d in seen:
                twice.append("%s (%s, %s/%s)" % (d, seen[d], s["key"], g["key"]))
            seen[d] = "%s/%s" % (s["key"], g["key"])
check("no domain is in two groups", not twice, str(twice[:5]))

print("\nwhat a template covers")
panel = load("smartdns-panel", "panel")
tmp = tempfile.mkdtemp()
store = panel.Store(os.path.join(tmp, "panel.db"))
panel.CATALOGUE = services
panel.GAMES = games

store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('کامل', 1, ?)",
          (panel.now(),))
store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('بازی', 0, ?)",
          (panel.now(),))
full = store.one("SELECT id FROM templates WHERE is_default = 1")["id"]
part = store.one("SELECT id FROM templates WHERE is_default = 0")["id"]

covered = panel.games_in_template(store, full)
check("the default template covers the games", len(covered) > 50, str(len(covered)))
optin = [g["name"] for g in games
         if any(ref in {"pubgmobile/main", "gamebackend/main"} for ref in g["needs"])]
check("except the ones whose groups are off until asked for",
      optin and not (set(optin) & set(covered)), str(optin))

# Valorant needs two of Riot's groups. One of them is not Valorant.
riot = [g for g in games if g["key"] == "valorant"][0]
store.run("INSERT INTO template_services (template_id, service_key, group_key)"
          " VALUES (?, 'riot', 'main')", (part,))
check("a game with only half its groups is not covered",
      "والورانت" not in panel.games_in_template(store, part))
store.run("INSERT INTO template_services (template_id, service_key, group_key)"
          " VALUES (?, 'riot', 'download')", (part,))
check("and is covered once all of them are routed",
      "والورانت" in panel.games_in_template(store, part), str(riot["needs"]))

print("\nfollowing a group that was split")
# A template from before the split names shooters/main, which is gone.
store.run("DELETE FROM template_services WHERE template_id = ?", (part,))
store.run("INSERT INTO template_services (template_id, service_key, group_key)"
          " VALUES (?, 'shooters', 'main')", (part,))
moved = panel.follow_catalogue_split(store, services)
now = store.template_groups(part)
shooters = {g["key"] for g in next(s for s in services if s["key"] == "shooters")["groups"]
            if not g.get("opt_in")}
check("the old key is gone", ("shooters", "main") not in now)
check("every group the service has now is routed instead",
      {k for s, k in now if s == "shooters"} == shooters,
      str(sorted({k for s, k in now if s == "shooters"} ^ shooters)))
check("it says how many rows it moved", moved == 1, str(moved))
check("running it again changes nothing",
      panel.follow_catalogue_split(store, services) == 0)

# A service that vanished entirely is not something this can guess at.
store.run("INSERT INTO template_services (template_id, service_key, group_key)"
          " VALUES (?, 'gone', 'main')", (part,))
panel.follow_catalogue_split(store, services)
check("a row for a service that no longer exists is left alone",
      ("gone", "main") in store.template_groups(part))

# An opt-in group is never handed out by the migration: the tick that would
# have turned it on was never given.
store.run("DELETE FROM template_services WHERE template_id = ?", (part,))
store.run("INSERT INTO template_services (template_id, service_key, group_key)"
          " VALUES (?, 'pubgmobile', 'everything')", (part,))
panel.follow_catalogue_split(store, services)
check("a split never turns on a group that is off by default",
      ("pubgmobile", "main") not in store.template_groups(part))

print("\nthe editor")
admin = load("smartdns-admin", "admin")
admin.GAMES[:] = games
admin.SECTIONS[:] = catalogue.get("sections", [])

carried = admin.games_by_group()
check("a group knows which games ride on it",
      {g["key"] for g in carried.get("riot/main", [])} >=
      {"valorant", "lol", "tft"},
      str([g["key"] for g in carried.get("riot/main", [])]))
check("a group nothing names carries nothing",
      "webdev/main" not in carried)

riot = next(s for s in services if s["key"] == "riot")
head, under = admin.group_title(riot, riot["groups"][0], carried["riot/main"])
check("the row is named after its games", "Valorant" in head, head)
check("and still says which group it is", under == riot["groups"][0]["label"], under)
long_head, _ = admin.group_title(riot, riot["groups"][0], carried["riot/main"], most=2)
check("a row with many games says how many more",
      "بازی دیگر" in long_head, long_head)
one_more, _ = admin.group_title(riot, riot["groups"][0], carried["riot/main"],
                                most=len(carried["riot/main"]) - 1)
check("but one more name is written, not counted",
      "بازی دیگر" not in one_more
      and all(g["en"] in one_more for g in carried["riot/main"]), one_more)
backend = next(s for s in services if s["key"] == "gamebackend")
head, _ = admin.group_title(backend, backend["groups"][0],
                            carried.get("gamebackend/main", []))
check("a row that is off by default is named for everything in it",
      head == backend["label"], head)
byp = next(s for s in services if s["key"] == "bypass")
head, _ = admin.group_title(byp, byp["groups"][0], [])
check("and the never-routed rows do not repeat their own heading",
      head == byp["groups"][0]["label"], head)

web = next(s for s in services if s["key"] == "webdev")
head, under = admin.group_title(web, web["groups"][0], [])
check("a service with no games keeps its own name", head == web["label"], head)

deps = admin.also_needed(carried["shooters/helldivers"], "shooters/helldivers", services)
check("a row says what else its game needs",
      any("Steam" in d for d in deps) and any("PlayStation" in d for d in deps),
      str(deps))
check("a store is named after itself, not after a sample of its games",
      "Steam" in deps, str(deps))
check("and never names the row itself",
      not any(d.startswith("شوترها") for d in deps), str(deps))
# The store's row is its own domains - the shop, the community, the login -
# so it is named after the store. The fifty-nine games that pass through it
# say so on their own rows.
steam = next(x for x in services if x["key"] == "steam")
head, _ = admin.group_title(steam, steam["groups"][0], carried["steam/main"])
check("a store's row is named after the store", head == "Steam", head)
check("and not after the games that merely need it",
      "Apex" not in head and "بازی دیگر" not in head, head)
check("a game that rides a store still says so on its own row",
      any("Steam" in d for d in
          admin.also_needed(carried["coop/terraria"], "coop/terraria", services)),
      str(admin.also_needed(carried["coop/terraria"], "coop/terraria", services)))

print("\nwhat the customer is told")
sync = load("smartdns-sync", "sync")
line = sync.plan_games({"games": ["والورانت", "فورتنایت"]})
check("the plan card names the games", "والورانت" in line and "فورتنایت" in line)
check("a plan with none says nothing", sync.plan_games({"games": []}) == "")
check("an older panel that does not send them is fine", sync.plan_games({}) == "")
many = sync.plan_games({"games": ["بازی %d" % i for i in range(40)]}, most=5)
check("a long list is cut off and counted", "و 35 بازی دیگر" in many, many)
six = sync.plan_games({"games": ["بازی %d" % i for i in range(6)]}, most=5)
check("one over the line is named rather than counted",
      "دیگر" not in six and "بازی 5" in six, six)

spec = importlib.util.spec_from_loader(
    "bot", importlib.machinery.SourceFileLoader(
        "bot", os.path.join(ROOT, "examples", "telegram-bot", "bot.py")))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
say = bot.Bot.plan_games(None, {"games": ["والورانت", "فورتنایت"]})
check("the bot says it too", "والورانت" in say and say.startswith("\n\n"))
check("and says nothing when there is nothing", bot.Bot.plan_games(None, {}) == "")
check("and it too names the thirteenth rather than counting it",
      "دیگر" not in bot.Bot.plan_games(
          None, {"games": ["بازی %d" % i for i in range(13)]}))

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d checks, %d failed" % (len(fails) + 0, len(fails)) if fails
      else "\nall good")
sys.exit(1 if fails else 0)
