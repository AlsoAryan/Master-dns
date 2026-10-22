#!/usr/bin/env python3
"""The Persian phrases the panels, the customer page and the bot can show,
as the English translation file keys them.

Each Persian string in the code is cut where a value goes in (%s, %d, ...),
at every HTML tag and quote, and after the page address a message rides on
("users?m=!..."); each piece with a Persian word in it is one phrase. The
pages are translated by swapping those pieces in what they render, so the
pieces here are exactly what domains/i18n-en.json has to hold. Docstrings are
left out: nobody is shown them.

    python3 tools/i18n-extract.py            list the phrases with no English
    python3 tools/i18n-extract.py --all      list every phrase
    python3 tools/i18n-extract.py --json     the same, as a JSON list
"""
import io
import json
import os
import re
import sys
import tokenize

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SOURCES = ["templates/smartdns-admin", "templates/smartdns-sync", "templates/smartdns-panel",
           "examples/telegram-bot/bot.py"]
DICT = os.path.join(ROOT, "domains", "i18n-en.json")
# A Persian word: letters, not the comma, the digits or the percent sign.
PERSIAN_WORD = re.compile("[ء-غف-يپچژکگی]")
CUT = re.compile(
    r"%(?:\([a-z_]+\))?[-+ #0]*\d*(?:\.\d+)?[sdfgxr%]"   # a value going in
    r"|[<>]|&[a-z]+;|&#\d+;"                              # a tag's edge, an entity
    r"|\\n|\n|['\"`]"                                     # a line, a quote
    r"|[a-z-]*\?(?:[a-z]+=[^&]*&)*m=!?|&m=!?"             # page?m=! ahead of a message
    r"|\b[a-z_]+:(?=[؀-ۿ])")                    # routed:... reason codes


def literal_strings(path):
    """Every string constant in a file as Python builds it - adjacent
    literals joined - less docstrings, f-strings and byte strings."""
    src = open(os.path.join(ROOT, path), encoding="utf-8").read()
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    out, run, before = [], [], None
    skip = (tokenize.NL, tokenize.COMMENT)
    for tok in toks + [None]:
        if tok is not None and tok.type == tokenize.STRING:
            run.append(tok.string)
            continue
        if tok is not None and tok.type in skip and run:
            continue
        if run:
            # A string alone on its line is a docstring or a comment in quotes.
            alone = (before is None or before.type in (tokenize.NEWLINE, tokenize.INDENT,
                                                       tokenize.DEDENT)) and \
                (tok is None or tok.type in (tokenize.NEWLINE, tokenize.ENDMARKER))
            if not alone:
                try:
                    out.append("".join(eval(s) for s in run))  # noqa: S307 - literals only
                except Exception:
                    pass
            run = []
        if tok is not None and tok.type not in skip:
            before = tok
    return [s for s in out if isinstance(s, str)]


def phrases(text):
    for piece in CUT.split(text):
        piece = piece.strip(" \t‏‎!")
        if len(piece) >= 2 and PERSIAN_WORD.search(piece):
            yield piece


# The data the pages draw from: the services' and the blocks' names and notes,
# and the games' - whose English names the game index already has.
DATA = ["domains/services.json", "domains/blocks.json", "domains/games.json"]


def data_strings(path):
    data = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("label", "note", "name") and isinstance(v, str):
                    out.append(v)
                elif k not in ("domains", "key", "en", "needs", "section", "kind"):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return out


def game_names():
    """{Persian name: English name} from the game index."""
    data = json.load(open(os.path.join(ROOT, "domains/games.json"), encoding="utf-8"))
    return {g["name"]: g["en"] for g in data.get("games", [])
            if g.get("name") and g.get("en")}


def all_phrases():
    seen = {}
    for path in SOURCES:
        for s in literal_strings(path):
            for p in phrases(s):
                seen.setdefault(p, path)
    for path in DATA:
        for s in data_strings(path):
            for p in phrases(s):
                seen.setdefault(p, path)
    return seen


def main():
    found = all_phrases()
    try:
        have = json.load(open(DICT, encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    want = found if "--all" in sys.argv else {p: f for p, f in found.items() if p not in have}
    sys.stdout.reconfigure(encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(sorted(want, key=lambda x: (want[x], x)), ensure_ascii=False))
    else:
        for p in sorted(want, key=lambda x: (want[x], x)):
            print(p)
    print("%d phrases, %d without English" % (len(found), sum(1 for p in found if p not in have)),
          file=sys.stderr)


if __name__ == "__main__":
    main()
