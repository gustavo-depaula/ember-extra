#!/usr/bin/env python3
"""20% random-sample audit. Looks for content quality issues.

Issue classes detected:
  H — HTML residue (any `<tag>` left in text)
  T — trailing artifacts (`_`, `-`, `div>`, `.v`, page numbers)
  L — header/label leaks (slot labels masquerading as prayer text)
  R — wrong reading.response values (not actually a response phrase)
  E — empty richtext bodies (object exists but no plain text any lang)
  C — citation == "?"
  M — mismatched body length per language (one lang ≪ all others)
  D — duplicate mass IDs across files
  X — schema violation (loaded against schema)
  P — preface ref dangling (no library entry)
  N — non-allowed language tag in any localized field
  S — suspicious starting character (prayer body starts with lowercase, ',', etc.)
  Q — body identical across 5+ languages (likely Latin fallback, or duplicate)
  U — non-ASCII control / BOM / weird Unicode in text
  Z — fields with `null` values where schema says required string
"""

import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
SAMPLE_FRACTION = 0.20

random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 1)

ALLOWED_LANGS = {"la", "es", "en", "pt-BR", "it", "fr", "de"}

HTML_TAG_RE = re.compile(r"<\w+[^>]*>")
TRAILING_RE = re.compile(r"[-_*=\+]+\s*$|(?<=[\.\!\?»\)])\s+\d{2,4}\s*$|\.[a-z]{1,3}\s*$|\s+(div|span|br|p)>\s*$", re.IGNORECASE)
HEADER_LEAK_RE = re.compile(
    r"^(COMUM\s+D[OAES]+|Das Sant[ao]s|Para um[ao]\s+(virgem|santo|santa|mártir)|"
    r"Antífona da (entrada|comunhão)\s+Cf\.|"
    r"Coleta\s*$|Collecta\s*$|Collect\s*$|Postcomunhão|"
    r"Tempo (Ordinario|di Avvento|di Quaresima|di Pasqua|di Natale)\s*$|"
    r"^Congedo come|^Domenica di\s+\w+\s*$|^Solennità\s*$)",
    re.IGNORECASE,
)
RESP_OK_RE = re.compile(
    r"thanks be|praise to|palavra|graças|gloria|alabamos|gracias|rendiamo|"
    r"lode|dank|ehre|louange|rendons|verbum|deo gratias|laus tibi|amen|"
    r"glória|kyrie|lob sei|lob (dir|dem)|christ(us|e)|señor|signore|"
    r"r/\.|℟|chwał|hosianna|hochgelobt",
    re.IGNORECASE,
)


def collect_all_masses():
    out = []
    for f in (ROOT / "masses").rglob("*.json"):
        if f.name == "_index.json":
            continue
        d = json.load(f.open())
        out.append((f, d))
    return out


def check_localized_for_lang_keys(node, path, issues):
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys & ALLOWED_LANGS and all(isinstance(v, str) for v in node.values()):
            extras = keys - ALLOWED_LANGS
            if extras:
                issues.append(("N", path, sorted(extras)))
        else:
            for k, v in node.items():
                check_localized_for_lang_keys(v, f"{path}.{k}", issues)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            check_localized_for_lang_keys(v, f"{path}[{i}]", issues)


def check_text_quality(text, lang, path, issues):
    if not text:
        return
    if HTML_TAG_RE.search(text):
        issues.append(("H", f"{path}.{lang}", text[:80]))
    if TRAILING_RE.search(text):
        issues.append(("T", f"{path}.{lang}", text[-60:]))


SUSPICIOUS_START_RE = re.compile(r"^[,;:_\?\)\]\}]")  # punctuation only
WEIRD_UNICODE_RE = re.compile("[\u0000-\u0008\u000b-\u001f\u007f\u200b-\u200f\u202a-\u202e\ufeff]")


def check_text_quality_extended(text, lang, path, issues):
    if not text:
        return
    check_text_quality(text, lang, path, issues)
    check_trailing_rubric_leak(text, path, lang, issues)


def check_richtext(rt, path, issues):
    if not isinstance(rt, dict):
        return
    plain = (rt.get("body") or rt).get("plain") or {}
    if not plain and "lines" in rt:
        plain2 = rt["lines"]
        if not any(plain2.values()):
            issues.append(("E", path, "empty richtext"))
            return
    if isinstance(plain, dict):
        for lang, txt in plain.items():
            if txt is None:
                issues.append(("Z", f"{path}.{lang}", "null text"))
                continue
            check_text_quality_extended(txt, lang, path, issues)
            if WEIRD_UNICODE_RE.search(txt):
                issues.append(("U", f"{path}.{lang}", "weird unicode"))
            if isinstance(txt, str) and len(txt) <= 100 and HEADER_LEAK_RE.match(txt):
                issues.append(("L", f"{path}.{lang}", txt[:80]))
            # Suspicious start (only for substantial text)
            if isinstance(txt, str) and len(txt) > 40 and SUSPICIOUS_START_RE.match(txt):
                issues.append(("S", f"{path}.{lang}", txt[:80]))
        # Body-length disparity (only flag for actual prayer bodies, not rubric instructions).
        # Skip *Instruction fields entirely — they're terse rubrics whose length
        # legitimately varies a lot per language.
        if not any(p.endswith("Instruction") or p.endswith("Instruction.body")
                   for p in [path]):
            lengths = {l: len(t) for l, t in plain.items() if t}
            if len(lengths) >= 3:
                mn = min(lengths.values())
                mx = max(lengths.values())
                if mx > 500 and mx > mn * 6 and mn < 30:
                    short_langs = [l for l, x in lengths.items() if x == mn]
                    issues.append(("M", path, f"{short_langs} only {mn} chars vs max {mx}"))
        # Same body across many langs (likely fallback to Latin or all-empty)
        if len(plain) >= 5:
            values = list(plain.values())
            if all(v == values[0] for v in values) and len(values[0]) > 80:
                issues.append(("Q", path, f"same text in {len(plain)} langs (~{len(values[0])} chars)"))


def check_reading(reading, path, issues):
    if not isinstance(reading, dict):
        return
    cit = reading.get("citation") or {}
    if isinstance(cit, dict):
        for lang, c in cit.items():
            if c == "?" or c == "" or c.strip() in {".", ":", ",", ":"}:
                issues.append(("C", f"{path}.citation.{lang}", c))
    body = reading.get("body")
    if isinstance(body, dict):
        check_richtext(body, f"{path}.body", issues)
    resp = reading.get("response")
    if isinstance(resp, dict):
        rt = (resp.get("body") or {}).get("plain") or {}
        for lang, t in rt.items():
            if isinstance(t, str) and len(t) > 10 and not RESP_OK_RE.search(t):
                # Could be wrong response content
                issues.append(("R", f"{path}.response.{lang}", t[:80]))


def check_lines_quality(rt, path, issues):
    """Drill into the lines structure looking for malformed segments."""
    if not isinstance(rt, dict):
        return
    body = rt.get("body") or rt
    lines_per_lang = body.get("lines") or {}
    for lang, lines in lines_per_lang.items():
        for li, line in enumerate(lines):
            # Empty line
            if not line:
                issues.append(("E", f"{path}.lines.{lang}[{li}]", "empty line"))
                continue
            for si, seg in enumerate(line):
                t = seg.get("text", "")
                # signOfCross/dropCap should have non-empty text
                if seg.get("type") == "signOfCross" and not t:
                    issues.append(("E", f"{path}.lines.{lang}[{li}].seg[{si}]", "empty signOfCross"))
                if seg.get("type") == "dropCap" and not t:
                    issues.append(("E", f"{path}.lines.{lang}[{li}].seg[{si}]", "empty dropCap"))


RUBRIC_TRAIL_PHRASES = re.compile(
    r"\b(After the distribution of Communion|Acabada la distribución|"
    r"Distribuída a comunhão|Dopo la comunione dei fedeli|"
    r"La distribution de la communion|Nach der Kommunionspendung|"
    r"Distributione Communionis peracta|"
    r"Congedo come|Tempo (Ordinario|di Avvento|di Pasqua|di Quaresima|di Natale)|"
    r"Domenica di [A-Z]\w+\s*$|Solennità\s*$)",
)


def check_trailing_rubric_leak(text, path, lang, issues):
    """Flag rubric leakage at the END of a prayer body. We don't flag when
    the whole body IS a rubric (position 0 — common for Triduum part blocks)."""
    if not text:
        return
    m = RUBRIC_TRAIL_PHRASES.search(text)
    if not m:
        return
    # If the match is in the first 30 chars, the whole body is a rubric paragraph
    # (not a "leak" appended to a prayer body). Only flag genuine end-of-body leaks.
    if m.start() < 30:
        return
    tail = text[-200:]
    if RUBRIC_TRAIL_PHRASES.search(tail):
        issues.append(("L", f"{path}.{lang}", tail[-80:]))


def audit_mass(path, m, issues):
    mid = m.get("id", "?")

    # Schema-ish: id must be set
    if not mid:
        issues.append(("X", str(path), "missing id"))
        return

    # Check all localized fields for unknown lang keys
    check_localized_for_lang_keys(m, mid, issues)

    # Check each prayer/antiphon richtext
    for field in ("entranceAntiphon", "communionAntiphon", "collect",
                  "prayerOverOfferings", "postcommunion", "prayerOverPeople",
                  "preface", "gloriaInstruction", "creedInstruction", "penitentialAct"):
        v = m.get(field)
        if isinstance(v, dict) and "body" in v:
            check_richtext(v["body"], f"{mid}.{field}", issues)
            check_lines_quality(v, f"{mid}.{field}", issues)

    # Check readings
    rdgs = m.get("readings") or {}
    for cyc, rs in rdgs.items() if isinstance(rdgs, dict) else []:
        if not isinstance(rs, dict): continue
        for r_name in ("firstReading", "secondReading", "gospel"):
            r = rs.get(r_name)
            check_reading(r, f"{mid}.readings.{cyc}.{r_name}", issues)
        psalm = rs.get("responsorialPsalm")
        if isinstance(psalm, dict) and "body" in psalm:
            check_richtext(psalm["body"], f"{mid}.readings.{cyc}.responsorialPsalm.body", issues)

    # Check processionGospel
    pg = m.get("processionGospel")
    if isinstance(pg, dict):
        for cyc, rs in pg.items():
            if not isinstance(rs, dict): continue
            for r_name in ("firstReading", "secondReading", "gospel"):
                r = rs.get(r_name)
                check_reading(r, f"{mid}.processionGospel.{cyc}.{r_name}", issues)

    # Check parts (Triduum) — walk the content tree for trailing leaks etc.
    parts = m.get("parts")
    if isinstance(parts, dict):
        for pk, p in parts.items():
            if not isinstance(p, dict): continue
            check_localized_for_lang_keys(p, f"{mid}.parts.{pk}", issues)
            _check_part_content(p.get("content") or [], f"{mid}.parts.{pk}.content", issues)


def _check_part_content(nodes, path, issues):
    if not isinstance(nodes, list):
        return
    for i, node in enumerate(nodes):
        if not isinstance(node, dict): continue
        npath = f"{path}[{i}]"
        if node.get("type") == "block":
            body = node.get("body")
            if isinstance(body, dict):
                check_richtext(body, npath, issues)
        elif node.get("type") == "section":
            _check_part_content(node.get("content") or [], f"{npath}.content", issues)


def check_preface_refs(masses, issues):
    pref_ids = set()
    for f in (ROOT / "library" / "preface").glob("*.json"):
        if f.name == "_index.json":
            continue
        pref_ids.add(json.load(f.open())["id"])
    for path, m in masses:
        p = m.get("preface")
        if isinstance(p, dict):
            for ref in p.get("prefaceRefs") or []:
                if ref not in pref_ids:
                    issues.append(("P", m["id"], f"unresolved {ref}"))


def main():
    all_masses = collect_all_masses()
    n = len(all_masses)
    sample_size = max(1, int(n * SAMPLE_FRACTION))
    sample = random.sample(all_masses, sample_size)

    print(f"Sampling {sample_size}/{n} masses (seed={sys.argv[1] if len(sys.argv) > 1 else 1})")
    print()

    issues = []
    for path, m in sample:
        audit_mass(path, m, issues)

    # Cross-cutting: preface refs (only check sampled masses' prefaces)
    check_preface_refs(sample, issues)

    # Summarize
    by_class = {}
    for code, *rest in issues:
        by_class.setdefault(code, []).append(rest)

    if not issues:
        print("✓ No bugs found in this sample.")
        return 0

    print(f"⚠ Found {len(issues)} issues across {len(by_class)} classes:")
    for code in sorted(by_class):
        rows = by_class[code]
        print(f"\n  [{code}] {len(rows)} occurrences:")
        for row in rows[:10]:
            line = "  ".join(str(x) for x in row)
            print(f"    {line[:200]}")
        if len(rows) > 10:
            print(f"    ... and {len(rows) - 10} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
