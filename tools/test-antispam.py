#!/usr/bin/env python3
"""The anti-spam in front of the customer panel's public forms.

Three cheap checks no person ever notices: a field only a bot fills in, a stamp
showing the form was open for a moment, and a count per address.
"""
import importlib.machinery
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("sync", os.path.join(HERE, "..", "templates", "smartdns-sync"))
spec = importlib.util.spec_from_loader("sync", loader)
sync = importlib.util.module_from_spec(spec)
loader.exec_module(sync)

failed = []


def check(name, ok, detail=""):
    print("  %s %s" % ("ok  " if ok else "FAIL", name))
    if not ok:
        failed.append(name + (" - " + str(detail) if detail else ""))


NOW = 1_800_000_000.0


def form(age=10, **extra):
    d = {"ft": sync.guard_stamp(NOW - age), "username": "ali"}
    d.update(extra)
    return d


print("what a person sends")
check("a form open for a few seconds passes", sync.guard_check(form(), "1.1.1.1", "login", NOW) is None)

print("the trap field")
check("a filled trap field is refused", sync.guard_check(form(website="http://x"), "1.1.1.2", "login", NOW))

print("the stamp")
check("no stamp is refused", sync.guard_check({"username": "a"}, "1.1.1.3", "login", NOW))
check("a made-up stamp is refused", sync.guard_check({"ft": "%d.deadbeef" % NOW}, "1.1.1.3", "login", NOW))
check("a stamp that is not a number is refused", sync.guard_check({"ft": "x.y"}, "1.1.1.3", "login", NOW))
check("a stamp changed after signing is refused",
      sync.guard_check({"ft": "%d.%s" % (NOW - 100, sync.guard_stamp(NOW).split(".")[1])}, "1.1.1.3", "login", NOW))
check("a form sent in under two seconds is refused", sync.guard_check(form(age=0), "1.1.1.4", "login", NOW))
check("a form a day old is refused", sync.guard_check(form(age=86400), "1.1.1.5", "login", NOW))

print("the count per address")
sync.GUARD_HITS.clear()
ok_n = sum(sync.guard_check(form(), "9.9.9.9", "signup", NOW) is None for _ in range(10))
check("signing up is held to six an hour per address", ok_n == 6, ok_n)
check("another address is not held back by it", sync.guard_check(form(), "9.9.9.8", "signup", NOW) is None)
check("and a different form has its own count", sync.guard_check(form(), "9.9.9.9", "login", NOW) is None)
check("the hour passing frees the address",
      sync.guard_check(form(), "9.9.9.9", "signup", NOW + 3700) is None)
sync.GUARD_HITS.clear()
for i in range(5100):
    sync.GUARD_HITS[("login", "10.%d.%d.1" % (i // 250, i % 250))] = [NOW - 7200]
sync.guard_check(form(), "9.9.9.7", "login", NOW)
check("the table of counts does not grow without bound", len(sync.GUARD_HITS) < 5000, len(sync.GUARD_HITS))

print("the pages carry it")
for name, html in (("sign-up", sync.signup_form()), ("sign-in", sync.login_form()),
                   ("reset step one", sync.forgot_page()), ("reset step two", sync.forgot_code_page("ali"))):
    check("%s has the trap field and a stamp" % name, "name='website'" in html and "name='ft'" in html)
    check("%s hides the trap field from people" % name, "class='hp'" in html and "tabindex='-1'" in html)
check("the trap field is hidden by the stylesheet", ".hp{position:absolute;left:-9999px" in sync.USER_CSS)
check("every stamp is fresh", sync.guard_fields() != "" and "ft" in sync.guard_fields())

print()
if failed:
    print("%d FAILED: %s" % (len(failed), "; ".join(failed)))
    sys.exit(1)
print("all checks passed")
