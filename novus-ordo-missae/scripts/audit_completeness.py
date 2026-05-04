#!/usr/bin/env python3
"""Completeness audit: which source content is missing from data/.

For every source-side entry in out/structure/ (santos, tiempos, comunes_votivas,
plegarias_euc, prefacios, ordinario) that has at least one non-empty padre
anchor, extract the Latin title block and verify that its distinctive phrase
appears somewhere in data/.

Output: TSV to stdout with columns:
  bucket, basename, source_id, slot_count, status, latin_title_excerpt

Status values: FOUND | MISSING | NOTITLE (no x_titulo padre to verify by).

Run: python3.11 scripts/audit_completeness.py [bucket]
"""

import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
DATA = ROOT / "data"

BUCKETS = ("santos", "tiempos", "comunes_votivas", "plegarias_euc", "prefacios", "ordinario")
LANG_BUCKET_FOR_TITLE = "latin"  # canonical language for title disambiguation


def slot_padres(slot):
    yield from (slot.get("padres") or [])
    for g in slot.get("groups") or []:
        if g.get("padre") is not None:
            yield g["padre"]


def has_content(day):
    return any(any(slot_padres(s)) for s in (day.get("slots") or []))


def slot_count(day):
    return sum(1 for s in (day.get("slots") or []) if any(slot_padres(s)))


def first_titulo_padre(day):
    for s in day.get("slots") or []:
        if s.get("type") == "x_titulo":
            for p in slot_padres(s):
                if p is not None:
                    return p
    return None


def load_lang_blocks(bucket, basename):
    fp = OUT / "by-language" / LANG_BUCKET_FOR_TITLE / bucket / f"{basename}.json"
    if not fp.exists():
        return {}
    blocks = json.loads(fp.read_text()).get("blocks") or []
    return {b.get("n"): b for b in blocks if b.get("n") is not None}


def title_excerpt(blocks_by_n, padre):
    blk = blocks_by_n.get(padre)
    if not blk:
        return ""
    return (blk.get("text") or "").strip()


SIGNIFICANT_WORD_RE = re.compile(r"[A-ZÀ-ÝÆŒ][A-Za-zÀ-ÿæœÆŒ]{3,}")
# Latin month names (genitive forms used in source titles)
LATIN_MONTHS = {
    "ianuarii", "februarii", "martii", "aprilis", "maii", "iunii",
    "iulii", "augusti", "septembris", "octobris", "novembris", "decembris",
}
# Words that pad source titles but rarely match data titles distinctively
NOISE_WORDS_LOWER = {
    "tempus", "hebdomada", "feria", "adventus", "quadragesim", "quadragesimæ",
    "paschæ", "paschali", "pentecosten", "sabbato", "dominicam", "dominica",
    "secunda", "tertia", "quarta", "quinta", "sexta", "septima", "octava",
    "maior", "maiore", "sancta", "sanctæ", "sancti", "sancto",
    "memoria", "festum", "sollemnitas", "natus", "natus est", "circa",
    "annum", "anno", "regione", "ecclesiæ", "ecclesia", "patrono", "patrona",
    "doctoris", "presbyteri", "episcopi", "papa", "papæ", "diaconi",
    "martyris", "martyrum", "virginis", "abbatis", "religiosi",
    "missa", "vigilia", "ante", "post", "intra", "infra", "extra",
    "celebrari", "potest", "omnibus", "diebus", "exceptis", "die",
    "anniversario", "electionis", "ordinationis",
    "evangelistæ", "apostoli", "apostolorum",
}


def claim_words(title):
    """Return list of distinctive Latin proper-noun-ish words for verification."""
    candidates = SIGNIFICANT_WORD_RE.findall(title)
    out = []
    for w in candidates:
        wl = w.lower()
        # strip trailing punctuation already excluded by regex
        if wl in LATIN_MONTHS:
            continue
        if wl in NOISE_WORDS_LOWER:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= 4:
            break
    return out


def load_all_data_text():
    """Concatenate all data/ JSON content into one string for greppability."""
    chunks = []
    for fp in DATA.rglob("*.json"):
        try:
            chunks.append(fp.read_text())
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(chunks)


def main():
    only_bucket = sys.argv[1] if len(sys.argv) > 1 else None

    print("Loading data/ corpus...", file=sys.stderr)
    haystack = load_all_data_text()
    print(f"  haystack: {len(haystack):,} chars", file=sys.stderr)
    # Also build per-file index for proximity matching: each file's content
    data_files = []
    for fp in DATA.rglob("*.json"):
        try:
            data_files.append(fp.read_text())
        except (OSError, UnicodeDecodeError):
            continue

    rows = []
    counts = Counter()

    for bucket in BUCKETS:
        if only_bucket and bucket != only_bucket:
            continue
        struct_dir = OUT / "structure" / bucket
        if not struct_dir.exists():
            continue
        for sfp in sorted(struct_dir.glob("*.json")):
            basename = sfp.stem
            blocks_by_n = load_lang_blocks(bucket, basename)
            data = json.loads(sfp.read_text())
            for day in data.get("days") or []:
                did = day.get("id") or ""
                if not has_content(day):
                    continue
                tp = first_titulo_padre(day)
                title = title_excerpt(blocks_by_n, tp) if tp is not None else ""
                words = claim_words(title)
                if not words:
                    status = "NOTITLE"
                else:
                    # Found if at least 2 of the distinctive words co-occur in some data file,
                    # OR if a distinctive single word appears (when only one word survives).
                    if len(words) >= 2:
                        status = "MISSING"
                        for txt in data_files:
                            hits = sum(1 for w in words if w in txt)
                            if hits >= 2:
                                status = "FOUND"
                                break
                    else:
                        status = "FOUND" if words[0] in haystack else "MISSING"
                counts[status] += 1
                rows.append((bucket, basename, did, slot_count(day), status, title[:120]))

    # Print TSV
    print("bucket\tbasename\tsource_id\tslot_count\tstatus\tlatin_title")
    for r in rows:
        print("\t".join(str(x) for x in r))

    # Summary on stderr
    print("\n=== summary ===", file=sys.stderr)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", file=sys.stderr)
    print(f"  TOTAL: {sum(counts.values())}", file=sys.stderr)


if __name__ == "__main__":
    main()
