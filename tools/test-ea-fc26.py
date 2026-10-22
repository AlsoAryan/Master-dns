#!/usr/bin/env python3
"""EA FC 26 through the service.

What has to hold: FC 26's Blaze redirector (spring25.client.blazeredirector
.ea.com) resolves directly, like FC 25's gosredirector, on the main resolver
and in every template - routed, it sees the exit's address while the Blaze
servers it hands out see Iran's, and EA refuses the sign-in; and the
installer warns, on an exit, when the exit's address is registered as
Iranian, which Google - and so FC's Ultimate Team - treats as Iran.
"""
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label +
          ((" - " + str(detail)[:400]) if detail and not cond else ""))
    if not cond:
        fails.append(label)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


print("the redirector")
bypass = read("common/bypass.conf")
check("the main resolver asks for it directly", "server=/blazeredirector.ea.com/#" in bypass.splitlines())
services = json.loads(read("domains/services.json"))["services"]
group = next(g for s in services if s["key"] == "bypass" for g in s["groups"] if g["key"] == "ea")
check("and every template does, the group being locked",
      "blazeredirector.ea.com" in group["domains"] and group.get("locked"))
check("next to FC 25's", "gosredirector.ea.com" in group["domains"])

print("an exit registered as Iranian")
logic = read("tools/installer-logic.sh")
body = logic[logic.index("exit_owner_check() {"):logic.index("\n}\n", logic.index("exit_owner_check() {"))]
check("the installer asks the address registry, on an exit only, and only warns",
      'if is_exit; then exit_owner_check "$SELF_IP"; fi' in logic
      and "https://rdap.org/ip/$1" in body and "die" not in body and "|| true" in body)
code = re.search(r"python3 -c '\n(.*?)\n' 2>/dev/null", body, re.S).group(1)


def verdict(rdap):
    r = subprocess.run([sys.executable, "-c", code], input=json.dumps(rdap), capture_output=True,
                       text=True, encoding="utf-8")
    return r.stdout.strip()


def card(role, fn, adr):
    return {"roles": [role], "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                                      ["fn", {}, "text", fn],
                                                      ["adr", {"label": adr}, "text", ["", "", "", "", "", "", ""]]]]}


# What the registry says of the exit in the report, trimmed to what counts.
iranian = {"name": "IR-POB6-20250122", "country": "DE", "entities": [
    card("administrative", "LexaHosting", "uk , london"),
    card("registrant", "Pishgaman Ofogh Barkhat LLC", "No.5 Alboz Alley\nIsfahan\nIRAN, ISLAMIC REPUBLIC OF")]}
german = {"name": "PFCLOUD-NET", "country": "DE", "entities": [
    card("registrant", "Pfcloud UG", "Lilienstrasse 5\n94051 Hauzenberg")]}
got = verdict(iranian)
check("a block named IR-, registered to an Iranian company, is said to be",
      "IR-POB6-20250122" in got and "Pishgaman Ofogh Barkhat LLC" in got, got)
check("a German one is not", verdict(german) == "")
check("nor one that only has Iran in an abuse contact's address",
      verdict({"name": "X-NET", "entities": [card("abuse", "Help", "Tehran, Iran")]}) == "")
check("and an answer that is not the registry's is let be", subprocess.run(
    [sys.executable, "-c", code], input="<html>", capture_output=True, text=True).stdout == "")

print()
print("%d FAILED: %s" % (len(fails), "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
