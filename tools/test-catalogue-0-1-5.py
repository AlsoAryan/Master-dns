#!/usr/bin/env python3
"""0.1.5: what the catalogue gained - Free Fire and Call of Duty: Mobile, Kimi, Qwen, Manus
and Kling, and Microsoft's Teams, OneDrive, Outlook and a fuller Xbox.

What has to hold: each name is in the group an operator would look for it in, once, and (unless
the group is opt-in) in the master list; Free Fire is off until chosen, like PUBG Mobile; the
new groups added to services that already existed reach the templates that route the rest of
them; Microsoft's mail protocols, which are not on 443, resolve directly; and every group a
game needs exists.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
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


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


spec = importlib.util.spec_from_loader("panel", importlib.machinery.SourceFileLoader(
    "panel", os.path.join(ROOT, "templates", "smartdns-panel")))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
panel.log = lambda *a, **k: None
cat = json.loads(read("domains/services.json"))["services"]
games = json.loads(read("domains/games.json"))["games"]
listed = [l.strip() for l in read("domains/domains.txt").splitlines() if l.strip()]
master = set(listed)
S = {s["key"]: s for s in cat}
G = {(s["key"], g["key"]): g for s in cat for g in s["groups"]}


def names(svc, grp):
    return set(G[(svc, grp)]["domains"])


print("the AI services")
check("Kimi has its sites and its platform API",
      {"kimi.com", "kimi.ai", "moonshot.cn"} <= names("kimi", "main") and "moonshot.ai" in names("kimi", "api"))
check("Qwen has its chat and its API and model hub",
      {"qwen.ai", "qwenlm.ai"} <= names("qwen", "main")
      and {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "modelscope.cn"} <= names("qwen", "api"))
check("Manus has its sites, its file CDN and the sites it publishes",
      {"manus.im", "manus.ai", "manuscdn.com", "manus.space"} <= names("manus", "main"))
check("Kling has both of its domains", {"klingai.com", "kling.ai"} <= names("kling", "main"))
check("all four are AI services, listed before the catch-all",
      all(S[k]["section"] == "ai" for k in ("kimi", "qwen", "manus", "kling"))
      and max(list(S).index(k) for k in ("kimi", "qwen", "manus", "kling")) < list(S).index("otherai"))
check("none is opt-in or locked: they are ordinary services",
      not any(g.get("opt_in") or g.get("locked") for k in ("kimi", "qwen", "manus", "kling") for g in S[k]["groups"]))
check("Alibaba's whole CDN is not routed for Qwen's sake",
      not any(d in master for d in ("alicdn.com", "aliyuncs.com", "alibabacloud.com")))

print("Microsoft")
m365 = S["microsoft365"]
check("Microsoft 365 is a service of its own, with a group each for the account, Teams, OneDrive and Outlook",
      [g["key"] for g in m365["groups"]] == ["account", "teams", "onedrive", "outlook"])
check("Teams", {"teams.microsoft.com", "teams.live.com", "teams.cloud.microsoft", "skype.com"} <= names("microsoft365", "teams"))
check("OneDrive", {"onedrive.com", "onedrive.live.com", "1drv.com", "1drv.ms", "sharepoint.com"} <= names("microsoft365", "onedrive"))
check("Outlook", {"outlook.com", "outlook.live.com", "outlook.office.com", "outlook.office365.com", "hotmail.com"}
      <= names("microsoft365", "outlook"))
check("the sign-in everything shares is its own group",
      {"live.com", "microsoftonline.com", "msauth.net", "office.com", "office365.com"} <= names("microsoft365", "account"))
check("the part that carries a customer's files says what it costs", bool(G[("microsoft365", "onedrive")].get("note")))
check("Windows Update and the Store's package hosts are not swept in",
      not any(d in master for d in ("windowsupdate.com", "microsoft.com", "azureedge.net", "windows.net")))
xbox_extra = G[("xbox", "extras")]
check("Xbox gains its sign-in and Microsoft's game back end, and the group joins the templates that route Xbox",
      {"login.live.com", "playfab.com", "forza.net"} <= set(xbox_extra["domains"]) and xbox_extra.get("join"))
check("the Xbox entry needs it, so a template without it is not told it has Xbox",
      "xbox/extras" in next(g for g in games if g["key"] == "xbox")["needs"])

print("Microsoft's mail stays direct")
mail = ["imap-mail.outlook.com", "pop-mail.outlook.com", "smtp-mail.outlook.com", "smtp.office365.com"]
msmail = G[("bypass", "msmail")]
conf = set(re.findall(r"^server=/([^/]+)/#$", read("common/bypass.conf"), re.M))
check("the catalogue and dnsmasq name the same four", sorted(msmail["domains"]) == mail and set(mail) <= conf)
check("the group is locked and opt-in, like EA's and Rockstar's, and says why",
      msmail.get("locked") and msmail.get("opt_in") and msmail.get("note"))
check("none of the four is routed by name", not (set(mail) & master))

print("the games")
by = {g["key"]: g for g in games}
check("Call of Duty: Mobile and Free Fire are games, in both languages, with a note",
      all(by[k]["name"] and by[k]["en"] and by[k].get("note") for k in ("codm", "freefire")))
check("Call of Duty: Mobile rests on Activision's own zone for it, which joins the templates that route Activision",
      by["codm"]["needs"] == ["blizzard/codm"] and G[("blizzard", "codm")].get("join")
      and "codm.activision.com" in names("blizzard", "codm") and "codm.activision.com" in master)
check("and the existing Call of Duty entries are unchanged",
      by["cod"]["needs"] == ["blizzard/main", "blizzard/cod-https", "blizzard/download"])
ff = S["freefire"]
check("Free Fire is a service of its own, off until chosen, saying how to tell it is needed",
      ff["section"] == "bypass" and all(g.get("opt_in") is True and g.get("note") for g in ff["groups"]))
check("with its CDN and login names", {"freefiremobile.com", "ggblueshark.com", "ggbluefox.com"} <= names("freefire", "main"))
check("none of them is in the shared list - the default plan would route them",
      not (names("freefire", "main") & master))
check("listed with the games, beside PUBG Mobile",
      list(S).index("freefire") == list(S).index("pubgmobile") + 1)
check("Garena's own site stays where it was", "garena.com" in names("othergames", "garena"))
check("PUBG Mobile is unchanged", names("pubgmobile", "main") == {"gcloudcs.com", "igamecj.com", "pubgmobile.com",
                                                                  "cloudpvp.com", "igamebuy.com", "intlgame.com"})

print("every name once, and in the master list")
seen, dupes = {}, []
for (s, g), grp in G.items():
    for d in grp["domains"]:
        if d in seen and seen[d] != (s, g):
            dupes.append((d, seen[d], (s, g)))
        seen[d] = (s, g)
check("no name is in two groups", not dupes, dupes[:3])
new_groups = [("kimi", "main"), ("kimi", "api"), ("qwen", "main"), ("qwen", "api"), ("manus", "main"),
              ("kling", "main"), ("microsoft365", "account"), ("microsoft365", "teams"),
              ("microsoft365", "onedrive"), ("microsoft365", "outlook"), ("xbox", "extras"), ("blizzard", "codm")]
absent = [d for k in new_groups for d in names(*k) if d not in master]
check("every name of a routed new group is in the master list", not absent, absent[:5])
check("the master list is still in order and without repeats", listed == sorted(set(listed)))
check("every game's groups exist", all(tuple(n.split("/")) in G for g in games for n in g["needs"]))
check("no game key is used twice", len({g["key"] for g in games}) == len(games))

print("what the templates do")
store = panel.Store(os.path.join(tempfile.mkdtemp(), "panel.db"))
default = store.ensure_default_template(cat)["id"]
routed = set(store.routed_for(default, cat))
check("the default template routes the AI services and Microsoft's, and not Free Fire",
      {"kimi.com", "qwen.ai", "manus.im", "klingai.com", "teams.microsoft.com", "onedrive.com",
       "outlook.office.com", "login.live.com"} <= routed
      and not (names("freefire", "main") & routed))
check("and resolves Microsoft's mail directly", set(mail) <= set(store.bypass_for(default, cat)))
tid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('xbox', 0, ?)", (panel.now(),)).lastrowid
store.run("INSERT INTO template_services (template_id, service_key, group_key) VALUES (?, 'xbox', 'online')", (tid,))
cid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('cod', 0, ?)", (panel.now(),)).lastrowid
store.run("INSERT INTO template_services (template_id, service_key, group_key) VALUES (?, 'blizzard', 'main')", (cid,))
panel.follow_catalogue_joins(store, cat)
check("a template that routed Xbox gets the new Xbox group, once",
      ("xbox", "extras") in store.template_groups(tid))
check("a template that routed Activision gets Call of Duty: Mobile's",
      ("blizzard", "codm") in store.template_groups(cid))
mid = store.run("INSERT INTO templates (name, is_default, created_at) VALUES ('teams only', 0, ?)", (panel.now(),)).lastrowid
store.run("INSERT INTO template_services (template_id, service_key, group_key) VALUES (?, 'microsoft365', 'teams')", (mid,))
r = set(store.routed_for(mid, cat))
check("a template that ticks only Teams routes Teams, and not OneDrive or Outlook",
      "teams.microsoft.com" in r and "onedrive.com" not in r and "outlook.office.com" not in r)

print("the version")
ver = re.search(r'^VERSION="(\d+)\.(\d+)\.(\d+)"', read("tools/installer-logic.sh"), re.M)
check("is 0.1.5 or later", ver is not None and tuple(map(int, ver.groups())) >= (0, 1, 5))
check("and the built installer carries the new catalogue",
      all(x in read("master-dns.sh") for x in ("klingai.com", "manuscdn.com", "freefiremobile.com", "msmail")))

print()
print("FAILED: %s" % ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
