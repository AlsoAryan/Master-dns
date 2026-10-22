#!/usr/bin/env python3
"""Both panels, dark and light.

Every colour on the two panels is a name, defined once for each theme. A rule
written with a colour of its own would stay dark in the light theme - or light
in the dark one - and the first to notice would be a customer squinting at
grey on grey. So: no colour outside the two palettes, every name used defined
in both, the text readable against what it sits on, and every page carrying
the switch and the line that restores it before anything is drawn.
"""
import importlib.machinery
import importlib.util
import os
import re
import sys

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


def load(name, mod):
    path = os.path.join(HERE, "..", "templates", name)
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, open(path, encoding="utf-8").read()


def palette(block):
    return dict(re.findall(r"--([\w-]+):([^;]+)", block))


def lum(hexcol):
    h = hexcol.strip().lstrip("#")
    def lin(c):
        c = int(c, 16) / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# Text, and what it sits on. 4.5 is the usual line for body text.
PAIRS = [("fg", "bg"), ("fg", "card"), ("muted", "card"), ("dim", "card"),
         ("accent", "card"), ("accent", "bg"), ("warn", "card"),
         ("bad", "card"), ("on-btn", "btn")]

admin, admin_src = load("smartdns-admin", "admin")
sync, sync_src = load("smartdns-sync", "sync")

print("the two panels are one product")
check("the same dark palette on both", admin.DARK == sync.DARK)
check("and the same light one", admin.LIGHT == sync.LIGHT)
check("and the same switch", admin.THEME_BUTTON == sync.THEME_BUTTON and
      admin.THEME_HEAD == sync.THEME_HEAD)

dark, light = palette(admin.DARK), palette(admin.LIGHT)

print("the palettes")
check("both define the same names", set(dark) == set(light),
      str(sorted(set(dark) ^ set(light))))
check("dark is dark", lum(dark["bg"]) < 0.02 and lum(dark["fg"]) > 0.7)
check("light is light", lum(light["bg"]) > 0.85 and lum(light["fg"]) < 0.05)
for name, pal in (("dark", dark), ("light", light)):
    for a, b in PAIRS:
        c = contrast(pal[a], pal[b])
        check("%s: %s on %s is readable (%.1f)" % (name, a, b, c), c >= 4.5)
check("blue is the default accent in both themes",
      dark["accent"] == "#5aa2ff" and light["accent"] == "#1d5fd6")
check("the same accents on both panels", admin.ACCENTS == sync.ACCENTS)
check("pink, green and red are offered besides blue",
      sorted(admin.ACCENTS) == ["green", "pink", "red"])
for acc, pair in admin.ACCENTS.items():
    for theme, base, over in (("dark", dark, pair[0]), ("light", light, pair[1])):
        pal = dict(base, **palette(over))
        check("%s only swaps names the palette has (%s)" % (acc, theme),
              set(palette(over)) <= set(base))
        for a, b in (("accent", "card"), ("accent", "bg"), ("on-btn", "btn")):
            c = contrast(pal[a], pal[b])
            check("%s/%s: %s on %s is readable (%.1f)" % (acc, theme, a, b, c),
                  c >= 4.5)

print("without a choice the browser decides, and a choice beats it")
t = admin.THEME_CSS
check("dark by default", t.startswith(":root{" + admin.DARK))
check("light when the device asks for it, unless dark was picked",
      "@media (prefers-color-scheme: light){:root:not([data-theme=dark]){"
      + admin.LIGHT in t)
check("light when it was picked, whatever the device says",
      ":root[data-theme=light]{" + admin.LIGHT in t)
check("the stored choice is taken only if it is a known one",
      "t=='light'||t=='dark'" in admin.THEME_HEAD and
      "a=='blue'||a=='pink'||a=='green'||a=='red'" in admin.THEME_HEAD)
check("every accent has its own rule, for each theme",
      all((":root[data-accent=%s]{" % n) in t and
          (":root[data-theme=light][data-accent=%s]{" % n) in t
          for n in admin.ACCENTS))
check("and a browser that refuses storage still gets a page",
      admin.THEME_HEAD.count("try{") == 1 and "catch(e){}" in admin.THEME_HEAD
      and "catch(e){}" in admin.THEME_BUTTON)
check("the switch is not a submit button in any form",
      "type='button'" in admin.THEME_BUTTON)

for label, css, src in (("admin", admin.CSS, admin_src),
                        ("users", sync.USER_CSS, sync_src)):
    print("the %s panel" % label)
    rest = src.replace(admin.DARK, "").replace(admin.LIGHT, "")
    loose = re.findall(r"(?:color|background|border)[^;\"'{}\n]*#[0-9a-fA-F]{3,6}\b", rest)
    check("no colour outside the palettes", not loose, str(loose[:3]))
    used = set(re.findall(r"var\(--([\w-]+)\)", src))
    check("every name it uses is defined", used <= set(dark),
          str(sorted(used - set(dark))))
    check("the stylesheet starts with the themes", css.startswith(admin.THEME_CSS))
    ctrl = re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", src)
    check("no control characters in the source", not ctrl, repr(ctrl[:3]))

print("every page has the switch, and opens in the chosen theme")
cfg = {"ADMIN_PATH": "p"}
pages = [("admin page", admin.page("خانه", "<p>x</p>", cfg)),
         ("admin login", admin.login_page(cfg)),
         ("users page", sync.user_page("<p>x</p>"))]
for label, out in pages:
    head, _, body = out.partition("</head>")
    check("%s: the choice is restored in <head>" % label,
          admin.THEME_HEAD in head and head.index(admin.THEME_HEAD) < head.index("<style>"))
    check("%s: the switch is on the page" % label, admin.THEME_BUTTON in body)

print("the password drawer's arrow is an arrow")
# It was '\25b8' in a Python string: an octal escape, so the page got a
# control character followed by the letters b8.
check("closed", "details.pw>summary::before{content:'▸'" in sync.USER_CSS)
check("open", "details.pw[open]>summary::before{content:'▾'}" in sync.USER_CSS)

print()
if fails:
    print("%d FAILED: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all checks passed")
