#!/usr/bin/env python3
"""Audit library + ancillary files (prefaces, eucharistic prayers, ordinary,
saints catalog, calendar, triduum, provenance) for content quality issues."""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
ALLOWED_LANGS = {"la", "es", "en", "pt-BR", "it", "fr", "de"}

HTML_TAG_RE = re.compile(r"<\w+[^>]*>")
TRAILING_RE = re.compile(r"[-_*=\+]+\s*$|(?<=[\.\!\?»\)])\s+\d{2,4}\s*$|\.[a-z]{1,3}\s*$|\s+(div|span|br|p)>\s*$", re.IGNORECASE)
HEADER_LEAK_RE = re.compile(
    r"^(COMUM\s+D[OAES]+|Das Sant[ao]s|Para um[ao]\s+(virgem|santo|santa|mártir)|"
    r"^(Coleta|Collecta|Collect|Prefácio|Postcomunhão|Depois da Comunhão)$|"
    r"^Congedo come)",
    re.IGNORECASE,
)
WEIRD_UNICODE_RE = re.compile("[\u0000-\u0008\u000b-\u001f\u007f\u200b-\u200f\u202a-\u202e\ufeff]")


def report(code, path, detail):
    print(f"  [{code}] {path}: {detail}")


def check_text(text, path, lang, errors):
    if not text:
        return
    if HTML_TAG_RE.search(text):
        errors.append(("H", f"{path}.{lang}", text[:80]))
    if TRAILING_RE.search(text):
        errors.append(("T", f"{path}.{lang}", text[-60:]))
    if WEIRD_UNICODE_RE.search(text):
        errors.append(("U", f"{path}.{lang}", "weird unicode"))


def walk_localized(node, path, errors):
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys & ALLOWED_LANGS and all(isinstance(v, str) for v in node.values()):
            extras = keys - ALLOWED_LANGS
            if extras:
                errors.append(("N", path, sorted(extras)))
            # Skip citation fields — verse-range trailing numbers are valid content,
            # not the page-number/section-marker artifacts the [T] check is for.
            is_citation = path.endswith(".citation") or path.endswith(".reference")
            for lang, t in node.items():
                if not is_citation:
                    check_text(t, path, lang, errors)
                    if HEADER_LEAK_RE.match(t or ""):
                        errors.append(("L", f"{path}.{lang}", t[:80]))
        else:
            for k, v in node.items():
                walk_localized(v, f"{path}.{k}", errors)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_localized(v, f"{path}[{i}]", errors)


def main():
    errors = []
    for f in (ROOT / "library").glob("*.json"):
        d = json.load(f.open())
        walk_localized(d, f.stem, errors)

    walk_localized(json.load((ROOT/"saints.json").open()), "saints", errors)
    walk_localized(json.load((ROOT/"calendar.json").open()), "calendar", errors)
    walk_localized(json.load((ROOT/"triduum.json").open()), "triduum", errors)

    for f in (ROOT/"igmr").glob("*.json"):
        d = json.load(f.open())
        walk_localized(d, f"igmr.{f.stem}", errors)
    for f in (ROOT/"sacerdotale").glob("*.json"):
        d = json.load(f.open())
        walk_localized(d, f"sacerdotale.{f.stem}", errors)

    if not errors:
        print("✓ Library audit: 0 issues")
        return 0

    print(f"⚠ Library audit: {len(errors)} issues")
    by_class = {}
    for code, *rest in errors:
        by_class.setdefault(code, []).append(rest)
    for code in sorted(by_class):
        rows = by_class[code]
        print(f"\n  [{code}] ({len(rows)})")
        for row in rows[:10]:
            print(f"    {row}")
        if len(rows) > 10:
            print(f"    ... and {len(rows)-10} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
