#!/usr/bin/env python3
"""
Query the Missale Romanum corpus from the command line.

Usage:
  query.py mass <id>                        # full Mass formulary
  query.py mass <id> --field collect        # one field
  query.py mass <id> --lang la              # one language
  query.py preface <id>                     # a Preface from the library
  query.py ep <id>                          # a Eucharistic Prayer
  query.py saints                           # list all saints (id + date + title)
  query.py saints --month 1                 # saints for a given month
  query.py saints --rank solemnity          # saints with a given rank
  query.py search <substring>               # full-text search across plain bodies
  query.py calendar [--season advent]       # list calendar entries
  query.py triduum                          # the 8 Holy Week / Triduum liturgies
  query.py validate                         # run validate.py

Output is JSON unless --text is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"


def load(rel: str) -> Any:
    p = DATA / rel
    if not p.exists():
        sys.exit(f"missing: {p}")
    with p.open() as f:
        return json.load(f)


def iter_items(root: Path):
    """Yield each per-item *.json under root, skipping _index.json sentinels."""
    for f in root.rglob("*.json"):
        if f.name == "_index.json":
            continue
        with f.open() as fp:
            yield json.load(fp)


def all_masses() -> list[dict]:
    return list(iter_items(DATA / "masses"))


def _id_to_path(item_id: str, root: Path, *, suffix: str = ".json") -> Path:
    parts = item_id.split(".")
    return root.joinpath(*parts[:-1], parts[-1] + suffix)


def find_mass(mid: str) -> dict | None:
    p = _id_to_path(mid, DATA / "masses")
    if p.exists():
        with p.open() as f:
            return json.load(f)
    # fallback: walk in case of an unexpected layout
    return next((m for m in all_masses() if m.get("id") == mid), None)


def filter_lang(node: Any, lang: str) -> Any:
    """Recursively keep only the requested language inside Localized fields."""
    if isinstance(node, dict):
        # Heuristic Localized detector
        keys = set(node.keys())
        looks_localized = (
            keys & {"la", "es", "en", "pt-BR", "it", "fr", "de"}
            and all(isinstance(v, str) for v in node.values())
        )
        if looks_localized:
            return {lang: node[lang]} if lang in node else None
        return {k: filter_lang(v, lang) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [filter_lang(v, lang) for v in node]
    return node


def cmd_mass(args):
    m = find_mass(args.id)
    if m is None:
        sys.exit(f"no mass with id {args.id!r}")
    if args.field:
        m = m.get(args.field)
        if m is None:
            sys.exit(f"mass {args.id!r} has no field {args.field!r}")
    if args.lang:
        m = filter_lang(m, args.lang)
    print_output(m, args)


def cmd_preface(args):
    p_path = _id_to_path(f"preface.{args.id}" if not args.id.startswith("preface.") else args.id,
                         DATA / "library")
    if not p_path.exists():
        sys.exit(f"no preface with id {args.id!r}")
    p = json.loads(p_path.read_text())
    if args.lang:
        p = filter_lang(p, args.lang)
    print_output(p, args)


def cmd_ep(args):
    eid = args.id if args.id.startswith("eucharistic-prayer.") else f"eucharistic-prayer.{args.id}"
    e_path = _id_to_path(eid, DATA / "library")
    if not e_path.exists():
        sys.exit(f"no eucharistic prayer with id {args.id!r}")
    e = json.loads(e_path.read_text())
    if args.lang:
        e = filter_lang(e, args.lang)
    print_output(e, args)


def cmd_saints(args):
    saints = list(iter_items(DATA / "saints"))
    if args.month is not None:
        saints = [s for s in saints if (s.get("date") or {}).get("month") == args.month]
    if args.rank is not None:
        saints = [s for s in saints if s.get("rank") == args.rank]
    if args.lang:
        saints = [filter_lang(s, args.lang) for s in saints]
    if args.text:
        for s in saints:
            d = s.get("date") or {}
            mm = d.get("month")
            dd = d.get("day")
            title = (s.get("title") or {}).get(args.lang or "en") or next(iter((s.get("title") or {}).values()), "")
            rank = s.get("rank") or ""
            print(f"  {mm:02d}-{dd:02d} {rank:18} {title[:80]}" if mm and dd else f"  {s['id']:30} {rank:18} {title[:80]}")
        return
    print_output(saints, args)


def cmd_calendar(args):
    entries = list(iter_items(DATA / "calendar"))
    if args.season:
        entries = [e for e in entries if e.get("season") == args.season]
    if args.lang:
        entries = [filter_lang(e, args.lang) for e in entries]
    print_output(entries, args)


def cmd_triduum(args):
    """Resolve the reference list in data/triduum/_index.json against the masses tree."""
    idx = json.loads((DATA / "triduum" / "_index.json").read_text())
    masses = []
    for mid in idx.get("ids", []):
        p = _id_to_path(mid, DATA / "masses")
        if p.exists():
            masses.append(json.loads(p.read_text()))
    out = {"count": len(masses), "masses": masses}
    if args.lang:
        out = filter_lang(out, args.lang)
    print_output(out, args)


def cmd_search(args):
    q = args.substring.lower()
    hits: list[dict] = []
    for m in all_masses():
        title = m.get("title") or {}
        if any(q in v.lower() for v in title.values()):
            hits.append({"id": m["id"], "match": "title", "title": title})
            continue
        for field in ("collect", "postcommunion", "prayerOverOfferings", "communionAntiphon", "entranceAntiphon"):
            f = m.get(field)
            if not isinstance(f, dict):
                continue
            plain = (f.get("body") or {}).get("plain") or {}
            for lang, txt in plain.items():
                if q in txt.lower():
                    hits.append({"id": m["id"], "match": field, "lang": lang, "snippet": _snippet(txt, q)})
                    break
            else:
                continue
            break
    print_output(hits, args)


def _snippet(text: str, q: str, ctx: int = 60) -> str:
    low = text.lower()
    i = low.find(q)
    if i < 0:
        return text[:ctx]
    start = max(0, i - ctx)
    end = min(len(text), i + len(q) + ctx)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def cmd_validate(args):
    import subprocess
    sub = subprocess.run([sys.executable, str(Path(__file__).with_name("validate.py"))], cwd=Path(__file__).resolve().parent.parent)
    sys.exit(sub.returncode)


def print_output(node: Any, args):
    if args.text:
        print(_to_text(node))
    else:
        json.dump(node, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


def _to_text(node: Any, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(node, dict):
        out = []
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                out.append(f"{pad}{k}:")
                out.append(_to_text(v, depth + 1))
            else:
                out.append(f"{pad}{k}: {v}")
        return "\n".join(out)
    if isinstance(node, list):
        return "\n".join(_to_text(v, depth) for v in node)
    return f"{pad}{node}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m_mass = sub.add_parser("mass")
    m_mass.add_argument("id")
    m_mass.add_argument("--field")
    m_mass.add_argument("--lang")
    m_mass.add_argument("--text", action="store_true")
    m_mass.set_defaults(func=cmd_mass)

    m_pref = sub.add_parser("preface")
    m_pref.add_argument("id")
    m_pref.add_argument("--lang")
    m_pref.add_argument("--text", action="store_true")
    m_pref.set_defaults(func=cmd_preface)

    m_ep = sub.add_parser("ep")
    m_ep.add_argument("id")
    m_ep.add_argument("--lang")
    m_ep.add_argument("--text", action="store_true")
    m_ep.set_defaults(func=cmd_ep)

    m_saints = sub.add_parser("saints")
    m_saints.add_argument("--month", type=int)
    m_saints.add_argument("--rank")
    m_saints.add_argument("--lang")
    m_saints.add_argument("--text", action="store_true")
    m_saints.set_defaults(func=cmd_saints)

    m_cal = sub.add_parser("calendar")
    m_cal.add_argument("--season")
    m_cal.add_argument("--lang")
    m_cal.add_argument("--text", action="store_true")
    m_cal.set_defaults(func=cmd_calendar)

    m_tri = sub.add_parser("triduum")
    m_tri.add_argument("--lang")
    m_tri.add_argument("--text", action="store_true")
    m_tri.set_defaults(func=cmd_triduum)

    m_search = sub.add_parser("search")
    m_search.add_argument("substring")
    m_search.add_argument("--text", action="store_true")
    m_search.set_defaults(func=cmd_search)

    m_val = sub.add_parser("validate")
    m_val.set_defaults(func=cmd_validate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
