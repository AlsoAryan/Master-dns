#!/usr/bin/env python3
"""0.1.3: what the catalogue gained - Claude's own domain, more AI, two Steam games.

What has to hold: the names are in the groups an operator would look for them in, once each and
in the master list; the two new AI groups reach the templates that already route the service
and the default template; and the games' groups all exist.
"""
import importlib.machinery
import importlib.util
import json
import os
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
    print(("  ok   " if cond else "  FAIL ") + label + ((" - " + str(detail)[:300]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


spec = importlib.util.spec_from_loader("panel", importlib.machinery.SourceFileLoader(
    "panel", os.path.join(ROOT, "templates", "smartdns-panel")))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
panel.log = lambda *a, **k: None
cat = json.load(open(os.path.join(ROOT, "domains", "services.json"), encoding="utf-8"))["services"]
games = json.load(open(os.path.join(ROOT, "domains", "games.json"), encoding="utf-8"))["games"]
master = {l.strip() for l in open(os.path.join(ROOT, "domains", "domains.txt"), encoding="utf-8") if l.strip()}
S = {s["key"]: s for s in cat}
G = {(s["key"], g["key"]): g for s in cat for g in s["groups"]}

print("the AI assistants")
check("Claude is on its own domain as well as claude.ai, and so are its artifacts",
      {"claude.ai", "claude.com", "claudeusercontent.com", "anthropic.com"} <= set(G[("anthropic", "main")]["domains"]))
check("OpenAI's video and short chat addresses", {"sora.com", "chat.com"} <= set(G[("openai", "main")]["domains"]))
check("Google's AI sites are with Google's", {"ai.google.dev", "notebooklm.google", "aistudio.google.com"}
      <= set(G[("google", "main")]["domains"]) | master and "notebooklm.google" in G[("google", "main")]["domains"])
check("and Meta's and the arena's with the other assistants",
      {"meta.ai", "llama.com", "lmarena.ai"} <= set(G[("otherai", "main")]["domains"]))

print("what is made with AI")
creative, builders = G[("otherai", "creative")], G[("otherai", "builders")]
check("image, video, voice and music have a group of their own",
      {"suno.com", "elevenlabs.io", "runwayml.com", "leonardo.ai", "civitai.com"} <= set(creative["domains"]))
check("so have the tools that write an app", {"lovable.dev", "bolt.new", "v0.dev", "githubcopilot.com"}
      <= set(builders["domains"]))
check("each reaches the templates that already route the service (join)", creative.get("join") and builders.get("join"))
check("and neither is opt-in or locked: they are ordinary services", not any(
    g.get("opt_in") or g.get("locked") for g in (creative, builders)))

print("every name once, and in the master list")
seen, dupes = {}, []
for (s, g), grp in G.items():
    for d in grp["domains"]:
        if d in seen and seen[d] != (s, g):
            dupes.append((d, seen[d], (s, g)))
        seen[d] = (s, g)
check("no name is in two groups", not dupes, dupes[:3])
new = ["claude.com", "claudeusercontent.com", "sora.com", "chat.com", "ai.google.dev", "notebooklm.google",
       "meta.ai", "llama.com", "lmarena.ai", "vultr.com", "namecheap.com", "godaddy.com"] + creative["domains"] + builders["domains"]
check("every new name is in the master list", all(d in master for d in new), [d for d in new if d not in master])
check("the master list is still in order", sorted(master) == [l.strip() for l in open(
    os.path.join(ROOT, "domains", "domains.txt"), encoding="utf-8") if l.strip()])
check("hosting and domain registrars have their own names in Cloud & hosting",
      {"vultr.com", "namecheap.com", "godaddy.com"} <= set(G[("cloud", "main")]["domains"]))

print("what the templates do with them")
store = panel.Store(os.path.join(tempfile.mkdtemp(), "panel.db"))
default = store.ensure_default_template(cat)["id"]
check("the default template routes both new groups", {("otherai", "creative"), ("otherai", "builders")}
      <= store.template_groups(default))
tid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('ai', 0, ?)", (panel.now(),)).lastrowid
store.run("INSERT INTO template_services (template_id, service_key, group_key) VALUES (?, 'otherai', 'main')", (tid,))
panel.follow_catalogue_joins(store, cat)
check("a template that routes the other AI gets them once",
      {("otherai", "creative"), ("otherai", "builders")} <= store.template_groups(tid))
check("so a customer on it can reach Claude on its new domain",
      "claude.com" in store.routed_for(default, cat))

print("the games")
by = {g["key"]: g for g in games}
for key, name in (("cs2", "Counter-Strike 2"), ("dota2", "Dota 2")):
    g = by[key]
    check("%s is a game with a Persian name and its note" % name, g["en"] == name and g["name"] and g["note"])
    check("  and needs groups that exist", all(tuple(n.split("/")) in G for n in g["needs"]), g["needs"])
check("both rest on Steam's groups, which already hold Valve's names",
      {"dota2.com", "counter-strike.net"} <= set(G[("steam", "main")]["domains"]))
check("no game key is used twice", len({g["key"] for g in games}) == len(games))
check("every game's groups exist", all(tuple(n.split("/")) in G for g in games for n in g["needs"]),
      [(g["key"], n) for g in games for n in g["needs"] if tuple(n.split("/")) not in G][:3])

print("the Steam row keeps its name")
sys.path.insert(0, HERE)
admin_spec = importlib.util.spec_from_loader("admin", importlib.machinery.SourceFileLoader(
    "admin", os.path.join(ROOT, "templates", "smartdns-admin")))
admin = importlib.util.module_from_spec(admin_spec)
admin_spec.loader.exec_module(admin)
admin.GAMES[:] = games
carried = admin.games_by_group()
steam = next(x for x in cat if x["key"] == "steam")
head, _ = admin.group_title(steam, steam["groups"][0], carried.get("steam/main", []))
check("two games on Valve's names do not rename the store's row after themselves", head == "Steam", head)
check("and are still in the index, by name", {"cs2", "dota2"} <= {g["key"] for g in admin.GAMES})
check("a game with a zone of its own still names its own row",
      any(g["key"] == "helldivers" for g in carried.get("shooters/helldivers", [])))

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
