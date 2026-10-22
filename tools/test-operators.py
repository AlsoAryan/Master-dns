#!/usr/bin/env python3
"""Which operator a customer's address is on, in the admin panel.

smartdns-operators fetches, once a day, the address blocks each Iranian
operator announces; the admin panel matches customers' addresses against them.
Worth checking: that a failed fetch keeps yesterday's list instead of blanking
it, that a small block inside a bigger one goes to whoever announces the small
one, that a page with no list yet says nothing rather than guessing, and that
what gets fetched is only ever the operators' lists - never a customer.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

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
    path = os.path.join(ROOT, "templates", name)
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, open(path, encoding="utf-8").read()


def text(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


ops, ops_src = load("smartdns-operators", "operators")

print("the list of operators")
asns = [a for a, _ in ops.OPERATORS]
names = [n for _, n in ops.OPERATORS]
check("no AS number twice", len(asns) == len(set(asns)))
check("no name twice", len(names) == len(set(names)))
check("the mobile operators and the phone company are on it",
      {197207, 44244, 57218, 58224} <= set(asns))
check("it asks RIPE about operators, never about customers",
      "panel.db" not in ops_src and not re.search(r"\bips\b", ops_src)
      and "resource=AS%d" in ops.URL)

print("reading RIPE's answer")


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


answers = {}


def fake_urlopen(req, timeout=None):
    return Answer(json.dumps(answers[req.full_url]).encode())


real_urlopen = ops.urllib.request.urlopen
ops.urllib.request.urlopen = fake_urlopen
url = ops.URL % 44244
answers[url] = {"status": "ok", "data": {"prefixes": [
    {"prefix": "5.52.0.0/16"}, {"prefix": "2a01:5ec0::/29"},
    {"prefix": "not a prefix"}, {"nope": 1}, {"prefix": "5.52.0.0/16"}]}}
got = ops.fetch(44244)
check("it keeps the blocks, v4 and v6, once each",
      got == ["2a01:5ec0::/29", "5.52.0.0/16"], str(got))
answers[url] = {"status": "error", "data": {}}
check("a refusal from RIPE is a failure", raises(lambda: ops.fetch(44244)))
answers[url] = {"status": "ok", "data": {"prefixes": []}}
check("so is an empty list - an operator does not vanish overnight",
      raises(lambda: ops.fetch(44244)))
ops.urllib.request.urlopen = real_urlopen

print("a day's refresh")
tmp = tempfile.mkdtemp()
out = os.path.join(tmp, "operators.json")
lists = {asn: ["10.%d.0.0/16" % i] for i, asn in enumerate(asns)}


def fake_fetch(asn):
    v = lists[asn]
    if isinstance(v, Exception):
        raise v
    return v


ops.fetch = fake_fetch
rc = ops.refresh(out, pause=0)
data = json.load(open(out, encoding="utf-8"))
check("it succeeds", rc == 0)
check("every operator is in the file",
      set(data["operators"]) == {str(a) for a in asns})
check("under the name it is known by",
      data["operators"]["44244"]["name"] == "ایرانسل")
if os.name == "posix":
    check("the panel can read it", os.stat(out).st_mode & 0o777 == 0o644)

print("a fetch that fails keeps yesterday's list")
irancell_before = data["operators"]["44244"]["prefixes"]
lists[44244] = OSError("timed out")
lists[197207] = ["10.99.0.0/16"]
rc = ops.refresh(out, pause=0)
data = json.load(open(out, encoding="utf-8"))
check("the run still succeeds", rc == 0)
check("Irancell keeps its old blocks",
      data["operators"]["44244"]["prefixes"] == irancell_before)
check("while the others are brought up to date",
      data["operators"]["197207"]["prefixes"] == ["10.99.0.0/16"])

print("one dropped connection is tried again")
tries = {}


def flaky(asn):
    tries[asn] = tries.get(asn, 0) + 1
    if asn == 31549 and tries[asn] == 1:
        raise OSError("EOF occurred in violation of protocol")
    return ["10.77.0.0/16"] if asn == 31549 else lists[asn] if not isinstance(
        lists[asn], Exception) else ["10.0.0.0/8"]


ops.fetch = flaky
rc = ops.refresh(out, pause=0)
data = json.load(open(out, encoding="utf-8"))
check("Shatel's second try is kept",
      data["operators"]["31549"]["prefixes"] == ["10.77.0.0/16"])
check("after exactly two tries", tries[31549] == 2, str(tries[31549]))
check("and the others were asked once", tries[44244] == 1)
ops.fetch = fake_fetch

print("and when nothing can be fetched, the file is left alone")
before = open(out, "rb").read()
for a in asns:
    lists[a] = OSError("down")
rc = ops.refresh(out, pause=0)
check("the run says so", rc == 1)
check("the file is as it was", open(out, "rb").read() == before)
check("and no half-written file is left behind",
      sorted(os.listdir(tmp)) == ["operators.json"], str(os.listdir(tmp)))

print("the panel's lookup")
panel, _ = load("smartdns-panel", "panel")
admin, _ = load("smartdns-admin", "admin")
f = os.path.join(tmp, "ops-test.json")
stamp = [time.time()]


def write(obj, raw=None):
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(raw if raw is not None else json.dumps(obj, ensure_ascii=False))
    # A fresh mtime for every write, however fast the writes come.
    stamp[0] += 5
    os.utime(f, (stamp[0], stamp[0]))


write({"operators": {
    "1": {"name": "بزرگ", "prefixes": ["5.0.0.0/8"]},
    "2": {"name": "کوچک", "prefixes": ["5.52.0.0/16", "2a01:5ec0::/29", "junk"]}}})
o = admin.Operators(f)
check("an address in the small block goes to its owner", o.name("5.52.10.1") == "کوچک")
check("the rest of the big block to the big one", o.name("5.1.2.3") == "بزرگ")
check("IPv6 as well", o.name("2a01:5ec0:1::5") == "کوچک")
check("an address on none of them is on none of them", o.name("8.8.8.8") == "")
check("nonsense is not an address", o.name("x") is None)
write({"operators": {"1": {"name": "تازه", "prefixes": ["5.0.0.0/8"]}}})
check("a new day's file is read again", o.name("5.52.10.1") == "تازه")
write(None, raw="{broken")
check("a broken file keeps the last good list", o.name("5.1.2.3") == "تازه")
check("no file yet, no answer",
      admin.Operators(os.path.join(tmp, "none.json")).name("5.52.10.1") is None)

print("what the users page writes under an address")
write({"operators": {"2": {"name": "<b>x</b>", "prefixes": ["5.52.0.0/16"]}}})
admin.OPERATORS = admin.Operators(f)
check("the name, escaped",
      admin.operator_label("5.52.1.1")
      == "<br><span class='muted'>&lt;b&gt;x&lt;/b&gt;</span>",
      admin.operator_label("5.52.1.1"))
check("«سایر» for an address on none of them", "سایر" in admin.operator_label("8.8.8.8"))
check("nothing for no address",
      admin.operator_label(None) == "" and admin.operator_label("") == "")
admin.OPERATORS = admin.Operators(os.path.join(tmp, "none.json"))
check("nothing at all while there is no list", admin.operator_label("5.52.1.1") == "")

print("on the users page itself")
db = os.path.join(tmp, "panel.db")
store = panel.Store(db)
cat = json.loads(text("domains/services.json"))["services"]
store.ensure_default_template(cat)
store.run("INSERT INTO users (phone, first_name, created_at)"
          " VALUES ('09120000002', 'علی', ?)", (panel.now(),))
store.run("INSERT INTO ips (user_id, ip, added_at)"
          " VALUES ((SELECT id FROM users WHERE phone='09120000002'),"
          " '5.52.1.1', ?)", (panel.now(),))
admin.STORE = admin.Store(db)
admin.CATALOGUE = cat
admin.CFG = {"ADMIN_PATH": "p"}
write({"operators": {"44244": {"name": "ایرانسل", "prefixes": ["5.52.0.0/16"]}}})
admin.OPERATORS = admin.Operators(f)
page = admin.Admin.users(None)
check("the operator is right under the address",
      "<code>5.52.1.1</code><br><span class='muted'>ایرانسل</span>" in page,
      page[page.find("5.52.1.1") - 40:page.find("5.52.1.1") + 80])

print("installed with the admin panel, and only there")
logic = text("tools/installer-logic.sh")
start = logic.index('step "Admin web panel"')
block = logic[start:logic.index("SYNC_TOKEN_OUT=", start)]
check("the admin panel's install puts it in",
      "payload OPERATORS >" in block
      and "enable_service smartdns-operators.timer" in block)
check("and nothing else does", logic.count("payload OPERATORS >") == 1)
check("its first run does not hold up the install",
      "--no-block smartdns-operators.service" in block)
build = text("tools/build-installer.py")
check("the build carries all three",
      all(('"%s"' % n) in build
          for n in ("OPERATORS", "OPERATORS_SERVICE", "OPERATORS_TIMER")))
check("smartdns-logs shows it on the exit",
      "smartdns-operators.timer" in text("templates/smartdns-logs"))
check("once a day", "OnUnitActiveSec=1d" in text("templates/smartdns-operators.timer"))

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
