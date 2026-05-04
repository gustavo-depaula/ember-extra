#!/usr/bin/env python3
"""
Refine the v1 JSON output (`out/`) into a domain-shaped corpus (`out2/`).

This is a pure refinement pass on top of `out/`. We re-parse v1 HTML where
needed for extra structure (e.g. saint titles split into date / name / rank /
description) but never go back to the source repo.

Design principles (PLAN_V2.md):
- Domain-shaped, not source-shaped. No `x_titulo`, no `padre`, no `legacyId`.
- ISO language codes (la, es, en, pt, it, fr, de).
- Canonical kebab-dotted IDs (`tempore.advent.sunday-1`).
- Empty fields collapsed.
- Each Mass is self-contained; cross-references are by ID.
- A separate `provenance.json` holds the source-path traceback.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, NavigableString, Tag

V1_OUT = Path(__file__).resolve().parent.parent / "out"
V2_OUT = Path(__file__).resolve().parent.parent / "data"

# Schema version. Bump on breaking changes to the output shape.
SCHEMA_VERSION = "1.0.1"

# ISO mapping. Source codes → BCP 47.
LANG_MAP = {
    "latin": "la",
    "cast": "es",
    "engl": "en",
    "port": "pt-BR",
    "ital": "it",
    "fran": "fr",
    "germ": "de",
}
SOURCE_LANGS = list(LANG_MAP.keys())  # for iteration over v1 fields
ISO_LANGS = list(LANG_MAP.values())

# Source repo path (for re-parsing estructura files where regional propers
# are stored with content embedded directly). Project-local under source/
# (gitignored; clone pedropasinn/Missale_romanum here — see README setup).
SOURCE_REPO = Path(__file__).resolve().parent.parent / "source" / "Missale_romanum"


# Regional locale tags found in estructura files. Maps the rubric prefix to
# (canonical region slug, primary ISO language).
REGIONAL_LOCALE_TAGS: list[tuple[str, str, str]] = [
    # (locale tag substring, region slug, ISO language)
    ("In den Diözesen deutscher Sprache",      "german-speaking",   "de"),
    ("en las diócesis de España, Argentina",   "spanish-speaking",  "es"),
    ("en las diócesis de España",              "spain",             "es"),
    ("en las diócesis de Argentina y Chile",   "argentina-chile",   "es"),
    ("en las diócesis de Argentina",           "argentina",         "es"),
    ("en las diócesis de Uruguay",             "uruguay",           "es"),
    ("en las diócesis de Chile",               "chile",             "es"),
    ("In the Dioceses of the United States",   "united-states",     "en"),
    ("In the Dioceses of Africa",              "africa",            "en"),
    ("In the Dioceses of Nigeria",             "nigeria",           "en"),
    ("nas dioceses de Brasil",                 "brazil",            "pt-BR"),
    ("nas dioceses do Brasil",                 "brazil",            "pt-BR"),
]


def detect_locale(tag_text: str) -> Optional[tuple[str, str]]:
    """Match a `<span class="red">[locale tag]</span>` text against known regional
    patterns. Returns (region-slug, iso-lang) or None.
    """
    for sub, slug, iso in REGIONAL_LOCALE_TAGS:
        if sub in tag_text:
            return slug, iso
    return None


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

WS_RE = re.compile(r"\s+")


_TRAILING_ARTIFACT_RE = re.compile(
    r"[-_*=\+]+\s*$"                    # trailing dashes/underscores/asterisks
    r"|(?<=[\.\!\?»\)])\s+\d{2,4}\s*$"   # numeric prefix from next section / page numbers
                                         # AFTER sentence-ender (keeps the period;
                                         # strips " 35" / " 1061" but not "December 30")
    r"|\.[a-z]{1,3}\s*$"                # stray suffix like ".v" or ".com"
    r"|\s+(div|span|br|p)>\s*$"          # leftover HTML closer
    , re.IGNORECASE,
)

# Trailing rubric-text leaks observed in Italian source files: the next-section
# title got concatenated to the end of a prayer body (without HTML separation).
# These are common rubric markers we strip when found at the very end of a
# prayer body, ONLY when preceded by a period/question/exclamation.
_TRAILING_RUBRIC_PHRASES = (
    "Congedo come nel giorno di Pasqua",
    "Congedo come nel Tempo Pasquale",
    "Congedo come nel giorno",
    # Italian section markers
    "Tempo Ordinario",
    "Tempo di Avvento",
    "Tempo di Quaresima",
    "Tempo di Pasqua",
    "Tempo di Natale",
    "Tempo Pasquale",
    "Solennità",
    # Drop the explicit form prefix that occasionally bleeds into prayers
    "div>",
    # Lord's Supper post-Communion rubrics (about the transfer of the Sacrament).
    "Distributione Communionis peracta",
    "After the distribution of Communion",
    "Acabada la distribución de la comunión",
    "Distribuída a comunhão",
    "Dopo la comunione dei fedeli",
    "Al termine della distribuzione della comunione",
    "La distribution de la communion étant achevée",
    "Nach der Kommunionspendung",
    # Good Friday Latin rubric leak inside parts.holyCommunion.
    "ad ecclesiam defertur",
    # Italian section navigation titles
    "Domenica di Pentecoste",
    "Domenica di Avvento",
    "Domenica di Quaresima",
    "Domenica di Pasqua",
    "Domenica di Natale",
    "Domenica del Tempo Ordinario",
    # Italian/Spanish "Oppure: <ref>" / "O bien: <ref>" alternative-scripture markers
    "Oppure:",
    "Or:",
    "O bien:",
    "Ou:",
)


def strip_trailing_rubric(text: str) -> str:
    """Strip known trailing-rubric leakage at the end of a prayer body.

    Only acts when a known rubric phrase appears in the FINAL ~250 chars of
    `text` AND the rubric phrase doesn't appear elsewhere earlier (which would
    suggest it's part of legitimate content). This avoids truncating long
    eucharistic prayers in the middle when a trailing-rubric phrase happens
    to also appear earlier as legitimate text."""
    if not text:
        return text
    s = text
    L = len(s)
    if L < 60:
        return s
    # Look at the trailing window only.
    window_start = max(0, L - 250)
    tail = s[window_start:]
    tail_low = tail.lower()
    full_low = s.lower()

    earliest_in_tail = -1
    for phrase in _TRAILING_RUBRIC_PHRASES:
        plow = phrase.lower()
        starts_alpha = plow[0].isalpha() if plow else False
        if starts_alpha:
            # Word-bounded search: the phrase must start at a word boundary so
            # "Or:" / "Sobre" don't truncate inside "Senh|or:" / "Sobre|do",
            # AND must be preceded by a sentence terminator + whitespace (or be
            # at the very start of the string) so legitimate use of words like
            # "solennità" inside a sentence doesn't get cut.
            pattern = r"(?:(?<=^)|(?<=[.!?»\)]\s)|(?<=[.!?»\)]\n)|(?<=[.!?»\)])\s)" + re.escape(plow)
            m_tail = re.search(pattern, tail_low)
            if not m_tail:
                continue
            idx_in_tail = m_tail.start()
            full_count = len(re.findall(pattern, full_low))
        else:
            idx_in_tail = tail_low.find(plow)
            if idx_in_tail < 0:
                continue
            full_count = full_low.count(plow)
        if full_count > 1:
            continue
        abs_idx = window_start + idx_in_tail
        if abs_idx > 30 and (earliest_in_tail == -1 or abs_idx < earliest_in_tail):
            earliest_in_tail = abs_idx
    if earliest_in_tail < 0:
        return s
    cut = earliest_in_tail
    pre = s[:cut].rstrip()
    m = re.search(r"\s+\d+\.\s*$", pre)
    if m:
        cut = m.start() + 1
    s = s[:cut].rstrip(" .,;:")
    if s and s[-1] not in ".!?»":
        s = s + "."
    return s


_PLACEHOLDER_TEXT_RE = re.compile(r"^[\s.…·•⋯⋮⋰⋱]+$")
# Mid-sentence fragments — text that starts lowercase with a function word
# AND is short. These are usually source-data leaks from wrong-language slots.
_FRAGMENT_RE = re.compile(
    r"^(per|del|della|dello|delle|degli|che|già|qui|dans|dei|"
    r"que|del|los|las|en|y|o|cuando|"
    r"de|do|da|com|dos|das|"
    r"and|of|the|to|that|"
    r"et|en|de|du|des|le|la|les|"
    r"und|die|der|im|den)\s",
    re.IGNORECASE,
)


def _looks_like_fragment(text: str) -> bool:
    """True if text starts mid-sentence (lowercase function word) and is short
    enough to be a fragmentary leak (< 80 chars)."""
    if not text or len(text) >= 80:
        return False
    return bool(_FRAGMENT_RE.match(text))


_INVISIBLE_RE = re.compile(
    "["
    "​‌‍‎‏"   # zero-width / direction marks
    "‪-‮"                     # bidi overrides
    "﻿"                             # BOM
    "]"
)

# (Dropcap rejoin avoided — would over-correct "I am" / "A man" etc.; the
# "T e ígitur" cosmetic artifact is left in place. Could be fixed later by
# rebuilding plain from segments instead of using v1's bs4-joined text.)


_DOUBLED_PUNCT_RE = re.compile(r"::+|,,+|!!+|\?\?+")

# HTML-fragment junk: stray bits like `p>`, `<`, `</p>`, `div>` that survive
# extraction when source files have authoring typos (literal `&gt;` outside a
# tag, etc.). These are NEVER legitimate text content.
_HTML_JUNK_RE = re.compile(
    r"^[<>/]*\s*(?:p|br|span|div|font|i|b|em|strong)?\s*[<>/]*$",
    re.IGNORECASE,
)


def _is_html_junk(text: str) -> bool:
    """True if the entire text is a stray HTML-fragment artifact (e.g. `p>`,
    `<`, `</p>`, `div>`). Returns False for normal text."""
    if not text:
        return False
    s = text.strip()
    if not s or len(s) > 8:
        return False
    if not any(c in s for c in "<>/"):
        return False
    return bool(_HTML_JUNK_RE.match(s))


def clean_text(s: str) -> str:
    """Collapse all whitespace, strip embedded HTML, trailing artifacts, doubled
    punctuation, and known trailing-rubric leaks. Returns "" for placeholder-only
    strings ('...', '…')."""
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace(" ", " ")
    s = _INVISIBLE_RE.sub("", s)
    if "<" in s:
        s = _HTML_TAG_RE.sub(" ", s)
        # Strip stray unmatched `<` left behind from authoring typos (e.g.
        # `<h2>Title<</h2>` in the source — the `<` survives the HTML strip
        # because `<[^>]+>` requires a closing `>`).
        s = s.replace("<", " ")
    s = _DOUBLED_PUNCT_RE.sub(lambda m: m.group(0)[0], s)  # collapse ::: → :, !! → !, etc.
    s = WS_RE.sub(" ", s).strip()
    # Strip a stray leading period or comma when followed immediately by an
    # uppercase word ("..Heute" → "Heute"). Source data has a few such typos.
    if s and s[0] in ".," and len(s) > 1 and s[1].isalpha():
        s = s.lstrip(".,").lstrip()
    while True:
        new = _TRAILING_ARTIFACT_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    s = strip_trailing_rubric(s)
    # Strip stray HTML-fragment artifacts (`p>`, `<`, etc. inside the text)
    # that come from authoring typos — `Text. p> More text` → `Text. More text`.
    s = re.sub(r"(?:(?<=\s)|^)[<>/]+\s*(?:p|span|div|br|font)?\s*[<>/]*(?=\s|$)", " ", s, flags=re.IGNORECASE)
    s = WS_RE.sub(" ", s).strip()
    # Strip a leading prayer-section label ("Prayer over the Offerings ", etc.)
    # that bled into the body of regional-propers entries.
    s = _strip_leading_prayer_label(s)
    # Strip "Antienne N <citation> <body>" prefix where the heading bled into
    # the body for French Holy-Thursday washing-of-feet antiphons.
    s = re.sub(
        r"^(?:Antienne|Antífona|Antiphona|Antiphon|Antifona)\s+\d+\s+"
        r"(?:Cf\.\s*)?(?:[1-3]\s*)?(?:[A-Za-zÀ-ÿ]{1,5})\s*\d+\s*[,:]?\s*[\d\-\.\s,;ab]*\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    # Drop placeholder-only strings (just dots / ellipses) — checked after the
    # prayer-label strip so "Prière sur les offrandes ..." also collapses.
    if _PLACEHOLDER_TEXT_RE.match(s):
        return ""
    return s


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def localized(per_source_lang: dict[str, str]) -> dict[str, str]:
    """Convert {source_lang: text} → {iso_lang: text}, dropping empties."""
    out: dict[str, str] = {}
    for src, txt in per_source_lang.items():
        if src not in LANG_MAP:
            continue
        cleaned = clean_text(txt)
        if cleaned:
            out[LANG_MAP[src]] = cleaned
    return out


# ---------------------------------------------------------------------------
# Rich-text refinement
# ---------------------------------------------------------------------------

# v1 segment types kept as v2 semantic types (mapping)
SEMANTIC_TYPE_MAP = {
    "rubric": "rubric",
    "reference": "reference",
    "italic": "italic",
    "people": "response",
    "cross": "signOfCross",
    "capital": "dropCap",
}

# v1 segment types treated as line/paragraph separators
LINE_BREAKERS = {"break", "paragraph_start", "paragraph_end"}

# v1 types we drop entirely from prayer bodies (extracted into other fields)
DROPPED_TYPES = {
    "heading",
    "bold",
    "reading_title",
    "reading_summary",
    "reading_from",
    "reading_incipit",
    "reading_acclamation",
}

# v1 types that carry real text content (psalm verses, versicles) — convert to plain text segments.
TEXT_LIKE_TYPES = {"verse", "psalm_verse"}


def strip_html_inline(text: str) -> str:
    """Remove any embedded raw HTML tags that leaked into a text segment."""
    if not text:
        return text
    if "<" not in text:
        return text
    return _HTML_TAG_RE.sub(" ", text)


def split_text_on_newlines(text: str) -> list[str]:
    """Split a text segment containing literal newlines into per-line strings.

    Used to recognize implicit line breaks in prayers where the source HTML
    uses raw newlines (no <br/>) for line formatting.
    """
    text = strip_html_inline(text or "")
    parts = re.split(r"\n+", text)
    return [clean_text(p) for p in parts]


def _strip_trailing_rubric_from_lines(lines: list[list[dict]]) -> list[list[dict]]:
    """Remove trailing segments/lines that are a known trailing rubric leak."""
    if not lines:
        return lines
    # Walk backwards from the last line. Find the position of any rubric phrase
    # in the joined text; if found, truncate.
    while lines:
        last_line = lines[-1]
        if not last_line:
            lines.pop()
            continue
        text = " ".join(seg.get("text") or "" for seg in last_line)
        any_match = False
        for phrase in _TRAILING_RUBRIC_PHRASES:
            idx = text.lower().find(phrase.lower())
            if idx >= 0:
                # Truncate segments by computing which segments are after `idx`.
                pos = 0
                new_segs: list[dict] = []
                for seg in last_line:
                    seg_text = seg.get("text") or ""
                    if pos + len(seg_text) <= idx:
                        new_segs.append(seg)
                        pos += len(seg_text) + 1  # +1 for join space
                        continue
                    # Partial — keep up to idx
                    if pos < idx:
                        new_text = seg_text[:idx - pos].rstrip(" .,;:")
                        if new_text:
                            new_seg = dict(seg)
                            new_seg["text"] = new_text
                            new_segs.append(new_seg)
                    break
                lines[-1] = new_segs
                if not new_segs:
                    lines.pop()
                any_match = True
                break
        if not any_match:
            break
    return lines


def refine_segments_to_lines(v1_segments: list[dict]) -> list[list[dict]]:
    """Convert v1 flat segments into v2 lines (a list of segment-lists).

    Lines split on:
    - <br/> equivalents (v1 break / paragraph_start / paragraph_end)
    - literal `\n` boundaries inside `text` segments

    Each segment is normalized to {type, text}.
    """
    lines: list[list[dict]] = []
    current: list[dict] = []

    def flush_line():
        # Drop pure whitespace
        cleaned = [s for s in current if (s.get("text") or "").strip() or s["type"] in {"signOfCross", "dropCap"}]
        if cleaned:
            lines.append(cleaned)
        current.clear()

    for seg in v1_segments:
        t = seg.get("type")
        if t in LINE_BREAKERS:
            flush_line()
            continue
        if t in DROPPED_TYPES:
            continue
        # Treat verse / psalm_verse as plain text — they carry real content
        # (psalm/responsorial verses, alleluia verses).
        if t in TEXT_LIKE_TYPES:
            t = "text"

        text = seg.get("text") or seg.get("value") or ""

        if t == "text":
            # Split on raw newlines — each line is its own line in the prayer.
            parts = split_text_on_newlines(text)
            for i, part in enumerate(parts):
                if i > 0:
                    flush_line()
                if part and not _is_html_junk(part):
                    if current and current[-1]["type"] == "text":
                        current[-1]["text"] = clean_text(current[-1]["text"] + " " + part)
                    else:
                        current.append({"type": "text", "text": part})
            continue

        if t in SEMANTIC_TYPE_MAP:
            sem = SEMANTIC_TYPE_MAP[t]
            cleaned = clean_text(text) if t != "cross" else (text or "✠")
            if cleaned and t != "cross" and _is_html_junk(cleaned):
                continue
            current.append({"type": sem, "text": cleaned})
            continue

        # Unknown type — silently drop.

    flush_line()
    # Strip known trailing-rubric leaks from line segments themselves.
    lines = _strip_trailing_rubric_from_lines(lines)
    # Merge mid-sentence line breaks: if a line ends with no terminator (just a
    # plain word), merge it into the next line. Preserves comma/semicolon
    # poetic-chant breaks but stitches together cases where the source HTML
    # has a stray <br/> mid-sentence (e.g. "nómina | autem eórum vivent...").
    lines = _merge_mid_sentence_lines(lines)
    return lines


_LINE_TERMINATORS = ".!?:;,»)]\"›』」"


def _merge_mid_sentence_lines(lines: list[list[dict]]) -> list[list[dict]]:
    """If a line ends without a sentence terminator (no `.,;:!?` etc.), merge
    the next line into it — that's a stray mid-sentence `<br/>` in the source.
    Lines that end with `,` `;` `:` are preserved as legitimate poetic-chant
    breaks."""
    if len(lines) < 2:
        return lines
    merged: list[list[dict]] = []
    for line in lines:
        if not line:
            continue
        first_type = next((s.get("type") for s in line if (s.get("text") or "").strip()), "text")
        # Check if previous line ends without a terminator → merge current into prev
        if merged and first_type == "text":
            prev = merged[-1]
            prev_last_text = ""
            for seg in prev:
                if seg.get("type") in ("text", "dropCap") and (seg.get("text") or "").strip():
                    prev_last_text = seg["text"].rstrip()
            prev_ends_term = bool(prev_last_text) and prev_last_text[-1] in _LINE_TERMINATORS
            if not prev_ends_term and prev_last_text:
                if prev and line and prev[-1].get("type") == "text" and line[0].get("type") == "text":
                    prev[-1] = dict(prev[-1])
                    prev[-1]["text"] = clean_text(prev[-1]["text"] + " " + line[0]["text"])
                    prev.extend(line[1:])
                else:
                    prev.extend(line)
                continue
        merged.append(list(line))
    return merged


_LEADING_LABEL_RUBRIC_RE = re.compile(
    r"^(?:"
    r"(?:antiphona|antífona|antifona|antiphon|antíphone|antiphone|antienne|canto)\s*\d+"
    r"|\d+[ºo°]\s*canto"
    r")\s*$",
    re.IGNORECASE,
)

# Prayer-section labels that occasionally leak into the start of `body.plain`
# in regional propers when the source HTML uses a non-standard markup. Strip
# them when they appear as a prefix immediately followed by the prayer body.
_LEADING_PRAYER_LABEL_RE = re.compile(
    r"^(?:"
    r"Prayer over the Offerings|Prayer after Communion|Prayer after communion|"
    r"Oración sobre las ofrendas|Oración colecta|Oración después de la comunión|"
    r"Oración después de la Comunión|"
    r"Pri[èe]re sur les offrandes|Pri[èe]re apr[èe]s la communion|"
    r"Pri[èe]re d'ouverture|Pri[èe]re après la communion|Collecte|"
    r"Or[aá][cç][aã]o sobre as oferendas|Or[aá][cç][aã]o ap[oó]s a comunh[aã]o|"
    r"Or[aá][cç][aã]o ap[oó]s a Comunh[aã]o|Or[aá][cç][aã]o do dia|"
    r"Sulle offerte|Dopo la comunione|Colletta|Orazione sulle offerte|"
    r"Orazione dopo la comunione|"
    r"Schlussgebet|Gabengebet|Tagesgebet|Eingangsgebet|Schlußgebet"
    r")\s+(?=\S)"
)


def _strip_leading_prayer_label(text: str) -> str:
    """Strip a leading prayer-section label (e.g. 'Prayer over the Offerings ',
    'Prière sur les offrandes ') from the start of a body. The match requires
    the label to be followed by a space and at least one more character — never
    matches when the label is the entire content."""
    if not text:
        return text
    m = _LEADING_PRAYER_LABEL_RE.match(text)
    if m:
        rest = text[m.end():]
        if rest:
            return rest
    return text


def _plain_from_lines(lines: list[list[dict]]) -> str:
    """Reconstruct plain text from a Line[] list, joining dropCap+text without
    space (so "T e ígitur" → "Te ígitur"), and otherwise space-joining.
    Applies strip_trailing_rubric at the end.

    Strips a leading "Antiphona N + reference" preamble when the body opens
    with a label-style rubric followed by a citation — these are structural
    metadata for the antiphon, not part of its plain text."""
    if lines and lines[0]:
        first_line = lines[0]
        if (
            len(first_line) >= 2
            and isinstance(first_line[0], dict)
            and first_line[0].get("type") == "rubric"
            and _LEADING_LABEL_RUBRIC_RE.match((first_line[0].get("text") or "").strip())
        ):
            drop_count = 1
            if (
                len(first_line) > drop_count
                and isinstance(first_line[drop_count], dict)
                and first_line[drop_count].get("type") == "reference"
            ):
                drop_count += 1
            new_first = first_line[drop_count:]
            if new_first:
                lines = [new_first] + list(lines[1:])
            else:
                lines = list(lines[1:])

    out_lines: list[str] = []
    for line in lines:
        parts: list[str] = []
        prev_drop = False
        for seg in line:
            t = seg.get("text") or ""
            if not t:
                continue
            if seg.get("type") == "dropCap":
                parts.append(t)
                prev_drop = True
            else:
                if prev_drop and parts:
                    parts[-1] = parts[-1] + t
                    prev_drop = False
                else:
                    parts.append(t)
                    prev_drop = False
        if parts:
            out_lines.append(" ".join(parts))
    s = " ".join(out_lines).strip()
    # Apply full clean_text to strip trailing artifacts, rubric leaks, etc.
    s = clean_text(s)
    return s


def make_rich_text(content_per_source_lang: dict[str, dict]) -> Optional[dict]:
    """Produce a v2 RichText {plain: Localized, lines: {iso_lang: Line[]}}.

    `plain` is reconstructed from the segment lines (so dropCap+text join
    correctly) when segments are available; otherwise we fall back to v1's
    bs4-joined text field.

    Returns None when nothing is present in any language.
    """
    plain: dict[str, str] = {}
    lines_per_lang: dict[str, list[list[dict]]] = {}

    for src in SOURCE_LANGS:
        c = content_per_source_lang.get(src)
        if not c:
            continue
        iso = LANG_MAP[src]
        segs = c.get("segments") or []
        refined = refine_segments_to_lines(segs) if segs else []
        if refined:
            lines_per_lang[iso] = refined
            rebuilt = _plain_from_lines(refined)
            if rebuilt:
                plain[iso] = rebuilt
        else:
            # Only fall back to raw text when there were no structured segments.
            # If segments were present but produced empty plain (e.g. a label-only
            # block whose preamble was stripped), leave plain empty for this lang.
            text = clean_text(c.get("text") or "")
            if text:
                plain[iso] = text

    _drop_latin_leak(plain, lines_per_lang)

    if not plain and not lines_per_lang:
        return None
    return {"plain": plain, "lines": lines_per_lang}


def _drop_latin_leak(plain: dict[str, str], lines_per_lang: dict[str, list]) -> None:
    """Drop non-Latin language keys whose plain text is byte-identical (after
    whitespace/case normalization) to the Latin plain text. Indicates the
    source HTML carries the untranslated Latin in the vernacular slot.

    Only applied for bodies of real length (>= 30 chars) — short antiphons may
    legitimately match across languages."""
    la_norm = WS_RE.sub(" ", (plain.get("la") or "")).strip().lower()
    if not la_norm or len(la_norm) < 30:
        return
    for iso in list(plain.keys()):
        if iso == "la":
            continue
        other_norm = WS_RE.sub(" ", plain[iso] or "").strip().lower()
        if other_norm == la_norm:
            del plain[iso]
            lines_per_lang.pop(iso, None)


def merge_blocks_to_rich_text(blocks: list[dict[str, dict]]) -> Optional[dict]:
    """Concatenate multiple per-source-lang content blocks into a single RichText."""
    if not blocks:
        return None
    if len(blocks) == 1:
        return make_rich_text(blocks[0])
    merged: dict[str, dict] = {}
    for b in blocks:
        for src, c in (b or {}).items():
            if src not in LANG_MAP:
                continue
            existing = merged.setdefault(src, {"text": "", "segments": []})
            existing["text"] = (existing["text"] + " " + (c.get("text") or "")).strip()
            existing["segments"].extend(c.get("segments") or [])
    return make_rich_text(merged)


# ---------------------------------------------------------------------------
# HTML re-parsing for title structure (saints)
# ---------------------------------------------------------------------------


def parse_title_html(html_per_source: dict[str, str]) -> dict[str, Any]:
    """Parse a saint/Mass title hijo HTML across languages.

    Returns:
      {
        title: {iso_lang: "Saints Basil and Gregory, bishops and doctors of the Church"},
        date: {iso_lang: "January 2"},
        rank: {iso_lang: "Memorial"},
        description: {iso_lang: "Basil (Cesarea of Cappadocia, ...)"}
      }

    Any field is omitted if empty.
    """
    out: dict[str, dict[str, str]] = {"title": {}, "date": {}, "rank": {}, "description": {}}

    for src, html in html_per_source.items():
        if src not in LANG_MAP or not html:
            continue
        iso = LANG_MAP[src]
        soup = BeautifulSoup(html, "lxml")

        # Title: prefer h2, fall back to h1 (used in some Holy Week files)
        h2 = soup.find("h2") or soup.find("h1")
        if h2:
            # Split on the first <br/> to separate date and name (sanctorale).
            parts = []
            current_part = []
            for child in h2.children:
                if isinstance(child, Tag) and child.name == "br":
                    parts.append("".join(current_part))
                    current_part = []
                else:
                    current_part.append(child.get_text() if isinstance(child, Tag) else str(child))
            parts.append("".join(current_part))
            cleaned = [clean_text(p) for p in parts if clean_text(p)]
            if len(cleaned) >= 2 and looks_like_date(cleaned[0]):
                out["date"][iso] = cleaned[0]
                out["title"][iso] = " ".join(cleaned[1:])
            else:
                out["title"][iso] = " ".join(cleaned) if cleaned else clean_text(h2.get_text(" ", strip=True))

        # Rank from h3 (Memória, Festa, Solenidade, ...) — first h3 only.
        # If h3 looks like a numbered subtitle (e.g. "1. PRO ECCLESIA B" in
        # various-needs masses) instead of a rank, append it to the title
        # so we don't lose it. The post-processor strips redundant section
        # prefixes from the merged title.
        h3 = soup.find("h3")
        if h3:
            rank_text = clean_text(h3.get_text(" ", strip=True))
            if rank_text:  # skip empty <h3></h3>
                # Numbered subtitle pattern: "1. X", "I. X", "A. X", or just
                # a single letter (e.g. "A", "B", "C") with optional sub-text.
                # Used in commons + ritual + votive masses to identify which
                # formula/option this mass represents.
                if re.match(r"^(?:\d+|[IVX]+|[A-Z])(?:\.\s+|\s+)[A-ZÀ-Ýa-zà-ÿ]", rank_text) or re.match(r"^[A-Z]$", rank_text):
                    base_title = out["title"].get(iso, "")
                    if base_title and rank_text not in base_title:
                        out["title"][iso] = f"{base_title} {rank_text}"
                    elif not base_title:
                        out["title"][iso] = rank_text
                else:
                    out["rank"][iso] = rank_text

        # Description from italic divs (`<div style="font-style: italic">`)
        bio_paragraphs = []
        for div in soup.find_all("div"):
            style = div.get("style") or ""
            if "italic" in style.lower():
                bio_paragraphs.append(clean_text(div.get_text(" ", strip=True)))
        bio_text = "\n\n".join(p for p in bio_paragraphs if p)
        # Filter rank-info masquerading as description ("En Uruguay: Memoria libre",
        # "In Italia: Memoria obbligatoria") and obvious placeholder strings.
        bio_low = bio_text.lower().strip()
        is_rank_info = bool(re.match(
            r"^(?:in|en|na|nas?|im|au|aux|aux?)\s+\w+(?:\s+\w+)?:\s*"
            r"(?:memori[ao]|memoria|memória|festa|fest|solenidade|solennit|"
            r"solemnit|gedenkt|festtag|f[èe]te|fiesta)",
            bio_low,
        ))
        is_placeholder = bool(re.match(r"^(descri[çc][ãa]o|description|descripcion)\s+\w+\s*[!?.]?\s*$", bio_low))
        if bio_text.strip() and not is_rank_info and not is_placeholder:
            out["description"][iso] = bio_text

    # Drop empty
    return {k: v for k, v in out.items() if v}


_DATE_PATTERNS = [
    re.compile(r"^\d{1,2}\.?\s+(de\s+\w+|\w+|de\w+)", re.IGNORECASE),  # "2 de janeiro", "2 January", "2. Januar"
    re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December|"
               r"Ianuary|Iuly|Iune|"  # Latin-style English typos in upstream source
               r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|"
               r"janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|"
               r"gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|"
               r"janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|"
               r"Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|"
               r"ianuarii|februarii|martii|aprilis|maii|iunii|iulii|augusti|septembris|octobris|novembris|decembris)\s+\d",
               re.IGNORECASE),
    re.compile(r"^(\w+)\s+\d{1,2}$"),
    re.compile(r"^\d{1,2}\.?\s+\w+$"),
    re.compile(r"^Die\s+\d", re.IGNORECASE),
]


def looks_like_date(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    return any(p.match(s) for p in _DATE_PATTERNS)


# ---------------------------------------------------------------------------
# Preface reference extraction
# ---------------------------------------------------------------------------

_PREFACE_HREF_RE = re.compile(r"#(pf\d+[A-Za-z]?)")


# --- Library-preface label matcher --------------------------------------------

_PREFACE_LIB_INDEX: Optional[dict[str, str]] = None  # built once on first use


def _build_preface_lib_index() -> dict[str, str]:
    """Build a normalized-title → preface-id map from the split library layout."""
    out: dict[str, str] = {}
    pref_dir = V2_OUT / "library" / "preface"
    if not pref_dir.exists():
        return out
    for p_path in pref_dir.glob("*.json"):
        if p_path.name == "_index.json":
            continue
        p = json.loads(p_path.read_text())
        for lang, t in (p.get("title") or {}).items():
            key = _normalize_pref_label(t)
            if key:
                out.setdefault(key, p["id"])
    return out


_LABEL_TRAIL_RE = re.compile(r"\.?\s*(Quando adhibetur|When|Cuando|Quand|Wenn|Quando si).*$", re.IGNORECASE | re.DOTALL)
_PREFACE_PREFIX_RE = re.compile(
    r"^((?:P[rR][aæ]fatio|Preface|Prefacio|Prefácio|Prefazio|Préface|Präfation)\s+)+",
    re.IGNORECASE,
)


def _normalize_pref_label(text: str) -> str:
    """Normalize a preface title for matching."""
    if not text:
        return ""
    s = clean_text(text)
    s = _LABEL_TRAIL_RE.sub("", s)
    s = _PREFACE_PREFIX_RE.sub("", s).strip(". ")
    # Strip parenthetical content "(in hac potíssimum die)", "(Et te in maternitáte)" etc.
    s = re.sub(r"\([^)]*\)", "", s)
    s = s.replace("æ", "ae").replace("Æ", "ae").replace("œ", "oe").replace("Œ", "oe")
    s = s.lower()
    s = re.sub(r"\b(\w+)\s+\1\b", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip(" .,:;")
    if len(s) > 50:
        s = s[:50]
    return s


def _resolve_preface_label(text: str) -> Optional[str]:
    """Try to match a label-only preface body to a library preface id."""
    global _PREFACE_LIB_INDEX
    if _PREFACE_LIB_INDEX is None:
        _PREFACE_LIB_INDEX = _build_preface_lib_index()
    if not _PREFACE_LIB_INDEX:
        return None
    key = _normalize_pref_label(text)
    if not key:
        return None
    # Try exact match first
    if key in _PREFACE_LIB_INDEX:
        return _PREFACE_LIB_INDEX[key]
    # Substring match — find the longest library key that's a prefix of our key
    matches = [(k, v) for k, v in _PREFACE_LIB_INDEX.items() if key.startswith(k) or k.startswith(key)]
    if matches:
        # Prefer longest match
        matches.sort(key=lambda x: -len(x[0]))
        return matches[0][1]
    return None


def label_only_preface_to_ref(prayer: dict) -> Optional[dict]:
    """If `prayer` is a Prayer whose body is only a preface label (no actual
    preface text), resolve it to a PrefaceRef. Returns the new ref dict or None."""
    body = prayer.get("body") if isinstance(prayer, dict) else None
    if not isinstance(body, dict):
        return None
    plain = body.get("plain") or {}
    if not plain:
        return None
    # Are all texts short and label-shaped?
    label_texts = {}
    for lang, t in plain.items():
        if not t.strip():
            continue
        if len(t) > 200:
            return None
        if not _PREFACE_PREFIX_RE.match(t):
            return None
        label_texts[lang] = t
    if not label_texts:
        return None
    # Try to resolve via Latin first, then any other lang.
    pref_id = None
    for pref_lang in ("la", "es", "en", "pt-BR", "it", "fr", "de"):
        if pref_lang in label_texts:
            pref_id = _resolve_preface_label(label_texts[pref_lang])
            if pref_id:
                break
    if not pref_id:
        return None
    # Keep the localized labels for display.
    return strip_empty({
        "prefaceRef": pref_id,
        "label": {lang: t.strip(". ") for lang, t in label_texts.items()},
    })


def extract_preface_ref(html_per_source: dict[str, str]) -> Optional[dict[str, Any]]:
    """If the preface slot is a link to a library preface, return a structured ref.

    Pattern: <a class="enlacepref" href="...m_estructura_prefacios.html#pf056">
    Also captures the preceding label text and the inline excerpt.
    """
    refs: list[str] = []
    label: dict[str, str] = {}
    excerpt: dict[str, str] = {}

    for src, html in html_per_source.items():
        if src not in LANG_MAP:
            continue
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a.enlacepref"):
            m = _PREFACE_HREF_RE.search(a.get("href") or "")
            if m:
                refs.append(m.group(1))
                excerpt[LANG_MAP[src]] = clean_text(a.get_text(" ", strip=True)).strip(". ").strip()
        # Label = parent indicepref text minus the anchor text
        idx = soup.select_one(".indicepref")
        if idx:
            full = clean_text(idx.get_text(" ", strip=True))
            anchor_txt = idx.find("a")
            if anchor_txt:
                anchor_full = clean_text(anchor_txt.get_text(" ", strip=True))
                label_text = clean_text(full.replace(anchor_full, "")).strip(" .")
                if label_text:
                    label[LANG_MAP[src]] = label_text

    if not refs:
        return None

    # Canonical preface ID
    seen = []
    for r in refs:
        if r not in seen:
            seen.append(r)
    out: dict[str, Any] = {"prefaceRef": f"preface.{seen[0]}"}
    if len(seen) > 1:
        out["alternativeRefs"] = [f"preface.{r}" for r in seen[1:]]
    if label:
        out["label"] = label
    if excerpt:
        out["excerpt"] = excerpt
    return out


# ---------------------------------------------------------------------------
# Day-ID parsing
# ---------------------------------------------------------------------------

# Letter prefixes and the season they encode (in tempore source).
TEMPORE_SEASON_BY_LETTER = {
    "A": "advent_christmas",   # advnav file: covers both advent and christmas; we'll refine below
    "Q": "lent",               # quadragesima
    "OT": "ordinary-time",
    "P": "easter",             # pascua
    "SS": "holy-week",
}


def parse_temporal_day_id(day_id: str) -> dict[str, Any]:
    """Decode a temporal day_id into {seasonGroup, weekIndex?, weekday?, raw}.

    Examples:
      A010 → {seasonGroup: "advent_christmas", weekIndex: 1, weekday: "sunday"}
      A046 → {seasonGroup: "advent_christmas", weekIndex: 4, weekday: "saturday"}
      OT01 → {seasonGroup: "ordinary-time", weekIndex: 1}
      Q010 → {seasonGroup: "lent", weekIndex: 1, weekday: "sunday"}
      P064B → {seasonGroup: "easter", weekIndex: 6, weekday: "thursday", suffix: "B"}
      SS04A → {seasonGroup: "holy-week", suffix: "04A"}
    """
    out: dict[str, Any] = {"raw": day_id}

    # Try OT prefix first
    if day_id.startswith("OT"):
        out["seasonGroup"] = "ordinary-time"
        rest = day_id[2:]
        m = re.match(r"^(\d{2})$", rest)
        if m:
            week = int(m.group(1))
            out["weekIndex"] = week
            # OT01..OT34 are the Sunday formularies of Ordinary Time. The
            # OT solemnities (OT51..OT54: Trinity, Corpus Christi, Sacred
            # Heart, Christ the King) are remapped to tempore.solemnity.*
            # downstream and don't surface this weekday tag.
            if 1 <= week <= 34:
                out["weekday"] = "sunday"
        return out

    # Lecturas-style OT ferial day-id: O0WD[A-Z]? where WW=week (01..34)
    # and D=weekday (1..6 = Mon..Sat). The trailing alpha is a structural
    # suffix from the source HTML id, not a year-cycle marker — those
    # live inside the day's slots as cicloI / cicloII.
    if day_id.startswith("O") and not day_id.startswith("OT"):
        m_otf = re.match(r"^O(\d{2})(\d)([A-Z]?)$", day_id)
        if m_otf:
            wd = int(m_otf.group(2))
            out["seasonGroup"] = "ordinary-time"
            out["weekIndex"] = int(m_otf.group(1))
            if 1 <= wd <= 6:
                out["weekday"] = WEEKDAY_NAMES[wd]
            return out

    if day_id.startswith("SS"):
        out["seasonGroup"] = "holy-week"
        out["suffix"] = day_id[2:]
        return out

    # Standard 3-digit pattern, optional uppercase letter suffix.
    m = re.match(r"^([A-Z])(\d)(\d)(\d)([A-Z]?)$", day_id)
    if m:
        letter, w_hi, w_lo, day, suf = m.groups()
        out["seasonGroup"] = TEMPORE_SEASON_BY_LETTER.get(letter, "unknown")
        block = int(w_hi)
        week = int(w_lo)
        weekday_idx = int(day)
        out["block"] = block
        out["weekIndex"] = week if block == 0 else None
        out["weekday"] = WEEKDAY_NAMES[weekday_idx] if weekday_idx in WEEKDAY_NAMES else None
        if suf:
            out["suffix"] = suf
        out = {k: v for k, v in out.items() if v is not None}
        return out

    # Looser fallback: classify by leading letter, keep the rest as opaque code.
    m = re.match(r"^([A-Z])([A-Za-z0-9]+)$", day_id)
    if m:
        letter, code = m.groups()
        season = TEMPORE_SEASON_BY_LETTER.get(letter, "unknown")
        if season != "unknown":
            out["seasonGroup"] = season
            out["code"] = code
            return out

    return out


WEEKDAY_NAMES = {
    0: "sunday",
    1: "monday",
    2: "tuesday",
    3: "wednesday",
    4: "thursday",
    5: "friday",
    6: "saturday",
}


def split_advent_christmas(day_id: str) -> str:
    """For A-prefixed days, split into 'advent' or 'christmas' based on the first
    numeric digit (block: 0 = Advent proper; 1+ = Christmas season).
    """
    m = re.match(r"^A(\d)", day_id)
    if not m:
        return "advent_christmas"
    return "advent" if m.group(1) == "0" else "christmas"


def parse_sanctorale_day_id(day_id: str) -> Optional[dict[str, Any]]:
    """MMDD or MMDDS where S is a single-letter suffix; or 000X for undated.

    Days > 31 are encoded markers for movable feasts (e.g. "Saturday after the
    Second Sunday after Pentecost") — surfaced as `movableCode` instead of `date`.
    """
    m = re.match(r"^(\d{2})(\d{2})([A-Za-z])?$", day_id)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        suffix = m.group(3)
        if month == 0:
            return {"undated": True, "ordinal": suffix or m.group(2)}
        if day > 31:
            return {"movableCode": day_id, "movableMonthAnchor": month, **({"suffix": suffix} if suffix else {})}
        return {"month": month, "day": day, **({"suffix": suffix} if suffix else {})}
    return None


# ---------------------------------------------------------------------------
# Item / slot helpers
# ---------------------------------------------------------------------------


def items_collect_content(items: list[dict], roles: tuple[str, ...] = ("post", "main")) -> list[dict]:
    """Pull v1 content dicts from items matching the given roles."""
    return [it.get("content") or {} for it in items if it.get("role") in roles]


def items_collect_html(items: list[dict], roles: tuple[str, ...] = ("post", "main")) -> dict[str, str]:
    """Concatenated raw HTML per source language for items matching given roles."""
    by_src: dict[str, list[str]] = {}
    for it in items:
        if it.get("role") not in roles:
            continue
        for src, c in (it.get("content") or {}).items():
            if src not in LANG_MAP:
                continue
            by_src.setdefault(src, []).append(c.get("html") or "")
    return {src: "\n".join(parts) for src, parts in by_src.items()}


def labeled_localized(items: list[dict]) -> Optional[dict[str, str]]:
    """Pull the localized 'label' (e.g., 'Collect' / 'Coleta') from `ant`-role items."""
    label_blocks = items_collect_content(items, roles=("ant", "ante"))
    if not label_blocks:
        return None
    out: dict[str, str] = {}
    for b in label_blocks:
        for src, c in b.items():
            if src not in LANG_MAP:
                continue
            iso = LANG_MAP[src]
            txt = clean_text(c.get("text") or "")
            if txt:
                # Strip any reference suffix (e.g. "Antiphona de Introitu Cf. Ps 24")
                # by removing trailing reference-only fragments — done downstream.
                out[iso] = (out.get(iso, "") + " " + txt).strip()
                out[iso] = WS_RE.sub(" ", out[iso])
    return out or None


_RUBRIC_ALT_ONLY = re.compile(
    # "Or:", "Ou:", "Vel:" optionally followed by a short citation (with or
    # without "Cf."). Total length kept short to avoid swallowing real content.
    r"^(or|ou|oppure|vel|oder|ó)\s*:?\s*(cf\.?\s*)?[\w\s.,;:\-]{0,40}$",
    re.IGNORECASE,
)


_SLOT_LABEL_WORDS = {
    # Common short slot-label words across all 7 languages — these are headers,
    # not prayer bodies. Lowercase comparison.
    "collect", "collecta", "coleta", "colecta", "colletta", "kollektengebet", "tagesgebet",
    "antífona", "antiphona", "antiphone", "antifona", "antienne",
    "offerings", "oblata", "ofrendas", "oferendas", "oblazione",
    "communion", "comunión", "comunhão", "comunione",
    "preface", "prefacio", "prefácio", "prefazio", "präfation",
    "postcommunion", "depois", "después", "dopo", "nach",
    "blessing", "bendición", "bênção", "benedizione", "ségen",
    "people", "pueblo", "povo", "popolo", "peuple", "volk",
    "rito", "rite", "ritus",
    # Common-mass section title vocabulary (header leaks). Avoid common
    # function words like "para"/"uma" that appear in legitimate prayer text.
    "comum", "común", "common", "commune", "commun",
    "ou:", "or:", "vel:", "oppure:", "oder:",
}

_LABEL_HEAD_RE = re.compile(r"^[\s'\"\(]*(\w+(?:\s+\w+){0,2})", re.UNICODE)


def _looks_like_slot_label(text: str) -> bool:
    """True if `text` is a short slot label (e.g. 'Coleta', 'Antífona da entrada
    Cf. Mt 25, 4', 'COMUM DAS VIRGENS II', 'Das Santas Virgens e Religiosos…')
    rather than a prayer body. Matches when:
      - total length ≤ 60 chars and any word is a known label, OR
      - the first 1–3 words match label vocabulary (catches longer headers
        with appended citation like 'Antífona da comunhão Cf. Mt 25, 4. 6')."""
    s = clean_text(text or "").strip()
    if not s:
        return False
    # Short-and-contains-label
    if len(s) <= 60:
        words = re.findall(r"[\wáéíóúàèìòùâêîôûäëïöüãñç]+", s.lower())
        if any(w in _SLOT_LABEL_WORDS for w in words):
            return True
    # Header pattern: text starts with 1–3 label words.
    head_match = _LABEL_HEAD_RE.match(s)
    if head_match:
        head_words = re.findall(r"[\wáéíóúàèìòùâêîôûäëïöüãñç]+", head_match.group(1).lower())
        if any(w in _SLOT_LABEL_WORDS for w in head_words[:3]):
            # Only treat as label if the full text is reasonably short (< 100 chars)
            # — a real prayer body that happens to start with the word "Common" should not be filtered.
            if len(s) <= 100:
                return True
    # Specific bleed-through patterns: the source's section navigation strings
    return bool(re.match(r"^(comum|común|commune|common)\s", s.lower()))


def _looks_like_rubric_only(text: str) -> bool:
    """True if `text` is an alternative marker, a slot label / header, fragment, or empty."""
    s = clean_text(text or "").strip()
    if not s:
        return True
    if _RUBRIC_ALT_ONLY.match(s):
        return True
    if _looks_like_slot_label(s):
        return True
    if _looks_like_fragment(s):
        return True
    return False


_HEADER_PATTERNS = [
    # Bleed-through navigation strings observed in source files.
    re.compile(r"^Das\s+Sant[ao]s\s+Virgens?\s+e\s+Religiosos?", re.IGNORECASE),
    re.compile(r"^COMUM\s+D[OAE]S?\s+", re.IGNORECASE),
    re.compile(r"^Para\s+um[ao]\s+(virgem|santa|santo|mártir)\b", re.IGNORECASE),
]


def _looks_like_header_leak(text: str) -> bool:
    s = (text or "").strip()
    return any(p.match(s) for p in _HEADER_PATTERNS)


def antiphon_from_items(items: list[dict]) -> Optional[dict]:
    """Build an Antiphon. Per language we look at role=post (or =main).

    If the post-role content for a language looks like a label/rubric only
    (the source data is broken for that language), we OMIT that language
    rather than risk pulling in misaligned content from role=ant.

    Special case: when EVERY language has only role=ant items (the source
    didn't separate label and body — see `comunes_mart.mart4`), use ant items
    [1:] as body since item[0] is conventionally the label.
    """
    out: dict[str, Any] = {}

    has_post_lang = any(
        ((it.get("content") or {}).get(src) or {}).get("text", "").strip()
        for it in items if it.get("role") in ("post", "main")
        for src in SOURCE_LANGS
    )

    body_per_src: dict[str, dict] = {}
    for src_lang in SOURCE_LANGS:
        roles_content: dict[str, dict] = {}
        roles_text: dict[str, str] = {}
        for it in items:
            content = (it.get("content") or {}).get(src_lang)
            if not content:
                continue
            role = it.get("role") or "main"
            roles_content[role] = content
            roles_text[role] = (content.get("text") or "").strip()

        chosen: Optional[dict] = None
        # Try post / main first.
        for role in ("post", "main"):
            content = roles_content.get(role)
            if content is None:
                continue
            text = roles_text.get(role, "")
            if _looks_like_rubric_only(text) or _looks_like_header_leak(text):
                continue
            chosen = content
            break

        if chosen is None and not has_post_lang:
            # Source uses role=ant for everything (mart4-style). Use ant[1:].
            ant_items = [it for it in items if it.get("role") in ("ant", "ante")]
            if len(ant_items) >= 2:
                ant_contents = [
                    (it.get("content") or {}).get(src_lang)
                    for it in ant_items[1:]
                ]
                ant_contents = [c for c in ant_contents if c]
                if ant_contents:
                    chosen = {
                        "text": " ".join(c.get("text") or "" for c in ant_contents),
                        "segments": [s for c in ant_contents for s in (c.get("segments") or [])],
                    }

        if chosen:
            body_per_src[src_lang] = chosen

    if body_per_src:
        body = make_rich_text(body_per_src)
        if body:
            out["body"] = body

    # Citation lives inside the label (`alindcha` / reference segment).
    # Filter prose-length entries (>40 chars or no digits) — those are antiphon
    # body fragments mis-tagged as references, not real citations.
    citation: dict[str, str] = {}
    for it in items:
        if it.get("role") not in ("ant", "ante"):
            continue
        for src, c in (it.get("content") or {}).items():
            if src not in LANG_MAP:
                continue
            for seg in c.get("segments") or []:
                if seg.get("type") == "reference":
                    txt = clean_text(seg.get("text") or "")
                    if not txt or len(txt) > 40 or not any(ch.isdigit() for ch in txt):
                        continue
                    # Normalize Cf prefix and strip cosmetic trailing punctuation.
                    txt = re.sub(r"^(?:Cf\.?|cf\.?|Cfr\.?)(\s+)", r"Cf.\1", txt).rstrip(".,;:")
                    if txt:
                        citation[LANG_MAP[src]] = txt
    if citation:
        out["citation"] = citation
    return out or None


def prayer_from_items(items: list[dict]) -> Optional[dict]:
    """A Prayer is just a body.

    If the post-role content for a language is empty / looks like a label,
    we OMIT that language (rather than fall back to role=ant which can be
    misaligned in some source files).

    Special case: if NO language has any post/main content (mart4-style
    source where all items use role=ant), use ant items [1:] as body since
    item[0] is conventionally the label.
    """
    has_post_lang = any(
        ((it.get("content") or {}).get(src) or {}).get("text", "").strip()
        for it in items if it.get("role") in ("post", "main")
        for src in SOURCE_LANGS
    )

    body_per_src: dict[str, dict] = {}
    for src_lang in SOURCE_LANGS:
        roles_content: dict[str, list[dict]] = {}
        roles_text: dict[str, str] = {}
        for it in items:
            content = (it.get("content") or {}).get(src_lang)
            if not content:
                continue
            role = it.get("role") or "main"
            roles_content.setdefault(role, []).append(content)
            roles_text[role] = (roles_text.get(role, "") + " " + (content.get("text") or "")).strip()

        chosen: Optional[dict] = None
        # Try post / main first; concatenate multiple posts.
        post_or_main: list[dict] = []
        for r in ("post", "main"):
            if r in roles_content and not _looks_like_rubric_only(roles_text.get(r, "")) and not _looks_like_header_leak(roles_text.get(r, "")):
                post_or_main.extend(roles_content[r])
        if post_or_main:
            chosen = {
                "text": " ".join(c.get("text") or "" for c in post_or_main),
                "segments": [seg for c in post_or_main for seg in (c.get("segments") or [])],
            }
        elif not has_post_lang:
            ant_items = [it for it in items if it.get("role") in ("ant", "ante")]
            if len(ant_items) >= 2:
                ant_contents = [
                    (it.get("content") or {}).get(src_lang)
                    for it in ant_items[1:]
                ]
                ant_contents = [c for c in ant_contents if c]
                if ant_contents:
                    chosen = {
                        "text": " ".join(c.get("text") or "" for c in ant_contents),
                        "segments": [s for c in ant_contents for s in (c.get("segments") or [])],
                    }

        if chosen:
            body_per_src[src_lang] = chosen

    if not body_per_src:
        return None
    body = make_rich_text(body_per_src)
    if not body:
        return None
    return {"body": body}


# ---------------------------------------------------------------------------
# Reading extraction (lecturas)
# ---------------------------------------------------------------------------

READING_FIELD_BY_SEG_TYPE = {
    "reading_title": "label",
    "reading_summary": "summary",
    "reading_from": "introduction",
    "reading_incipit": "body",
    "reading_acclamation": "conclusion",
}


_READING_CLASS_FIELD_MAP = {
    "ReadingGospelTitle": "label",
    "Summary": "summary",
    "Areadingfrom": "introduction",
    "Incipit-oneline": "body",
    "TheWordoftheLord": "conclusion",
}


# UI toggle-button text used by the source HTML to switch between brevior
# and longior forms. These items are noise — they should be dropped, not
# treated as reading content.
_READING_FORM_TOGGLE_TEXTS = frozenset({
    'brevior', 'longior',
    'shorter', 'longer',
    'shorter form', 'longer form',
    'forma breve', 'forma larga', 'forma corta',
    'forme brève', 'forme longue',
    'forma più breve',
    'kürzere form', 'längere form',
})


def _is_form_toggle_item(item: dict) -> bool:
    """An item is a UI form-toggle button if its content (in any language)
    is just one of the known toggle labels."""
    for src, c in (item.get("content") or {}).items():
        text = (c.get("text") or "").strip().lower()
        if text and text in _READING_FORM_TOGGLE_TEXTS:
            return True
    return False


def _is_reading_title_item(item: dict) -> bool:
    """An item starts a new reading if its HTML carries one of the
    reading-title classes (`Areadingfrom`, `ReadingGospelTitle`)."""
    for src, c in (item.get("content") or {}).items():
        html = c.get("html") or ""
        if 'class="Areadingfrom"' in html or 'class="ReadingGospelTitle"' in html:
            return True
    return False


def _split_reading_items(items: list[dict]) -> list[list[dict]]:
    """Split items into groups when they contain multiple complete readings
    (the source's brevior/longior toggle pattern). Drops UI toggle-button
    items as noise. Returns a list of item-lists, one per form."""
    sorted_items = sorted(items, key=lambda it: it.get("n", 0))
    content = [it for it in sorted_items if not _is_form_toggle_item(it)]
    title_indices = [i for i, it in enumerate(content) if _is_reading_title_item(it)]
    if len(title_indices) <= 1:
        return [content]
    groups: list[list[dict]] = []
    prev = 0
    for idx in title_indices[1:]:
        groups.append(content[prev:idx])
        prev = idx
    groups.append(content[prev:])
    return groups


def _reading_has_response(reading: dict) -> bool:
    """True if a reading (single or alternatives-shape) already carries
    a response somewhere."""
    if "alternatives" in reading:
        return any("response" in alt for alt in reading["alternatives"])
    return "response" in reading


def _attach_response(reading: dict, response: Optional[dict]) -> None:
    """Attach the people's response (`R/. Thanks be to God` etc.) to a
    reading. When the reading is a multi-form `{alternatives: [...]}`
    shape, attach the response to each alternative — the response is
    identical across long/short forms."""
    if not response:
        return
    if "alternatives" in reading:
        for alt in reading["alternatives"]:
            alt["response"] = response
    else:
        reading["response"] = response


def reading_with_alternatives_from_items(items: list[dict]) -> Optional[dict]:
    """Wrapper over reading_from_items that handles multi-form readings:
    when the source structure carries both a longior and a brevior gospel
    (or first reading) under one slot, emit the two as
    `{alternatives: [Reading, Reading]}` instead of collapsing them.

    For single-form readings (the common case), delegate directly to
    reading_from_items so the slot keeps its simple shape."""
    groups = _split_reading_items(items)
    if len(groups) <= 1:
        return reading_from_items(items)
    readings = [r for r in (reading_from_items(g) for g in groups) if r]
    if not readings:
        return None
    if len(readings) == 1:
        return readings[0]
    return {"alternatives": readings}


def reading_from_items(items: list[dict]) -> Optional[dict]:
    """Build a structured Reading by re-parsing the hijo HTML across all items.

    Most readings come as one item per slot, so the typical case is items=[one].
    Some readings (Palm Sunday's Passion narratives, Easter Vigil) span multiple
    items in the same slot — e.g. a `shorter` form alongside a `longer` form,
    or an introduction + body + acclamation as separate items. We concatenate
    the HTML of all items per language and re-parse the combined document, so
    the structured fields capture the union.
    """
    if not items:
        return None

    # Combine HTML of all items per source language.
    combined_html: dict[str, str] = {}
    for it in items:
        for src, c in (it.get("content") or {}).items():
            if src not in LANG_MAP:
                continue
            html = c.get("html") or ""
            if not html:
                continue
            combined_html[src] = (combined_html.get(src, "") + "\n" + html).strip()

    fields: dict[str, dict[str, str]] = {f: {} for f in dict.fromkeys(_READING_CLASS_FIELD_MAP.values())}
    citation: dict[str, str] = {}

    known_reading_classes = set(_READING_CLASS_FIELD_MAP.keys())
    # Phrases used as the "Word of the Lord" / Gospel conclusion across languages.
    conclusion_phrases_re = re.compile(
        r"^\s*(verbum (d|D)ómini|the word of the lord|palabra de dios|"
        r"palavra do senhor|parola di dio|parole du seigneur|"
        r"wort des lebendigen gottes|wort des herrn|"
        r"verbum christi)\s*\.?\s*$",
        re.IGNORECASE,
    )

    for src, html in combined_html.items():
        iso = LANG_MAP[src]
        soup = BeautifulSoup(html, "lxml")

        # 1) Extract structured reading_* paragraph classes (label, summary,
        #    introduction, body via Incipit-oneline, conclusion via TheWordoftheLord).
        for cls, field in _READING_CLASS_FIELD_MAP.items():
            ps = soup.find_all("p", class_=cls)
            if not ps:
                continue
            parts: list[str] = []
            for p in ps:
                cite_span = p.find("span", class_="alindcha")
                if cite_span is not None and field == "introduction" and iso not in citation:
                    cite_text = clean_text(cite_span.get_text(" ", strip=True))
                    # Reject citations that are just punctuation residue.
                    if cite_text and not re.fullmatch(r"[\s.,;:\-]+", cite_text):
                        # Source quirk #1: when alindcha is ", 11-20" (starts
                        # with comma), the chapter number is in the intro text
                        # just before the span. Try to prepend it.
                        if cite_text.startswith(",") or cite_text.startswith("."):
                            prev = cite_span.previous_sibling
                            prev_text = clean_text(str(prev)) if prev else ""
                            m_chap = re.search(r"\b(\d+)\s*$", prev_text)
                            if m_chap:
                                cite_text = m_chap.group(1) + cite_text
                            else:
                                # Source quirk #2: leading comma is decorative —
                                # the chapter is already inside the alindcha.
                                cite_text = cite_text.lstrip(",.").strip()
                        # Strip cosmetic trailing punctuation: citations end
                        # with a verse range, never a sentence terminator.
                        cite_text = cite_text.rstrip(".,;:").strip()
                        # Normalize Cf./cf./Cfr./Cf prefix to canonical "Cf."
                        cite_text = re.sub(r"^(?:Cf\.?|cf\.?|Cfr\.?)(\s+)", r"Cf.\1", cite_text)
                        citation[iso] = cite_text
                    cite_span.extract()
                text = clean_text(p.get_text(" ", strip=True))
                if text:
                    parts.append(text)
            if parts:
                if field == "body":
                    # Latin readings sometimes mis-class "Verbum Dómini." as
                    # Incipit-oneline; if the only body text is the conclusion
                    # phrase, route it to conclusion and leave body for the
                    # bare-<p> fallback below.
                    body_parts = [p for p in parts if not conclusion_phrases_re.match(p)]
                    conclusion_in_body = [p for p in parts if conclusion_phrases_re.match(p)]
                    if body_parts:
                        fields["body"][iso] = "\n\n".join(body_parts)
                    if conclusion_in_body and iso not in fields["conclusion"]:
                        fields["conclusion"][iso] = conclusion_in_body[0]
                else:
                    fields[field][iso] = parts[0]

        # 2) Always also collect bare <p> tags (no class) and merge into body.
        #    Used for Passion narratives, Latin gospels where the actual body
        #    isn't tagged with `Incipit-oneline`, etc.  We accept short opening
        #    words like "Fratres,"/"Frères,"/"Hermanos:" — they're part of the
        #    reading.  Conclusion phrases ("Verbum Dómini.") get routed to
        #    `conclusion` instead of body.
        bare_paragraphs: list[str] = []
        for p in soup.find_all("p"):
            cls = p.get("class") or []
            if any(c in known_reading_classes for c in cls):
                continue
            text = clean_text(p.get_text(" ", strip=True))
            if not text:
                continue
            if conclusion_phrases_re.match(text):
                if iso not in fields["conclusion"]:
                    fields["conclusion"][iso] = text
                continue
            bare_paragraphs.append(text)
        if bare_paragraphs:
            existing = fields["body"].get(iso, "")
            # Merge; if existing was empty, just use bare. Otherwise concat.
            if existing:
                fields["body"][iso] = existing + "\n\n" + "\n\n".join(bare_paragraphs)
            else:
                fields["body"][iso] = "\n\n".join(bare_paragraphs)

    # Source quirk: the Spanish "Incipit-oneline" sometimes starts with the
    # introduction phrase ("Lectura del libro...") that's already in the
    # introduction field. Strip that duplicate prefix from body.
    intro_prefix_re = re.compile(
        r"^(A reading from|Léctio|Lectura del|Leitura\s|Dal libro|Lecture du|Lesung aus)[^\.]{1,80}\.?\s*",
        re.IGNORECASE,
    )
    for iso, body_text in list(fields["body"].items()):
        if intro_prefix_re.match(body_text):
            stripped = intro_prefix_re.sub("", body_text, count=1).strip()
            if stripped:
                fields["body"][iso] = stripped

    # Drop reading body entries whose only content is the response acclamation
    # (e.g. `R/. Gloria tibi, Domine.`) — these come from sanctorale memorials
    # whose source HTML lacks the actual pericope and only contains acclamations.
    # When ALL languages of body are acclamation-only, we drop the whole reading.
    _ACCLAMATION_ONLY_RE = re.compile(
        r"^(?:R[/.]?\.?|℟\.?|A:)?\s*"
        r"(?:Gloria tibi[^.!?]*|Laus tibi[^.!?]*|Gloria a ti[^.!?]*|"
        r"Glory to you[^.!?]*|Praise to you[^.!?]*|Lode a te[^.!?]*|"
        r"Gloire à toi[^.!?]*|Louange à toi[^.!?]*|Glória a vós[^.!?]*|"
        r"Ehre sei dir[^.!?]*|Lob sei dir[^.!?]*|Gloria a te[^.!?]*|"
        r"Christus[^.!?]*)"
        r"\s*[.!?]?\s*"
        r"(?:R[/.]?\.?|℟\.?|A:)?\s*"
        r"(?:Gloria tibi[^.!?]*|Laus tibi[^.!?]*|Gloria a ti[^.!?]*|"
        r"Glory to you[^.!?]*|Praise to you[^.!?]*|Lode a te[^.!?]*|"
        r"Gloire à toi[^.!?]*|Louange à toi[^.!?]*|Glória a vós[^.!?]*|"
        r"Ehre sei dir[^.!?]*|Lob sei dir[^.!?]*|Gloria a te[^.!?]*|"
        r"Christus[^.!?]*)?"
        r"\s*[.!?]?\s*$",
        re.IGNORECASE,
    )
    if fields.get("body"):
        body_isos = list(fields["body"].keys())
        if body_isos and all(
            _ACCLAMATION_ONLY_RE.match(fields["body"][iso] or "")
            for iso in body_isos
        ):
            fields["body"] = {}

    out: dict[str, Any] = {}
    for f, m in fields.items():
        m = {iso: t for iso, t in m.items() if t}
        if m:
            out[f] = m
    if citation:
        out["citation"] = citation

    if not out:
        # Last resort: richtext body from combined items.
        all_blocks = [it.get("content") or {} for it in items]
        body = merge_blocks_to_rich_text(all_blocks)
        if body:
            plain = body.get("plain") or {}
            if plain and all(_ACCLAMATION_ONLY_RE.match(t or "") for t in plain.values()):
                return None
            return {"body": body}
        return None

    if "body" in out:
        body_plain = dict(out["body"])
        body_lines: dict[str, list] = {}
        _drop_latin_leak(body_plain, body_lines)
        out["body"] = {"plain": body_plain, "lines": body_lines}
        # Also strip leaked Latin from sibling reading fields (label, summary,
        # introduction, conclusion) for consistency.
    for f in ("label", "summary", "introduction", "conclusion"):
        if f in out and isinstance(out[f], dict):
            la = WS_RE.sub(" ", out[f].get("la", "")).strip().lower()
            if la and len(la) >= 30:
                for iso in list(out[f].keys()):
                    if iso == "la":
                        continue
                    other = WS_RE.sub(" ", out[f][iso] or "").strip().lower()
                    if other == la:
                        del out[f][iso]
    return out


_PSALM_TITLE_PREFIX_RE = re.compile(
    r"^(?:Psalmus\s+Responsorius|Salmo\s+Responsorial(?:e)?|Responsorial\s+Psalm|"
    r"Psaume\s+Responsoriel|Antwortpsalm)\s*",
    re.IGNORECASE,
)
_ALLELUIA_TITLE_PREFIX_RE = re.compile(
    r"^(?:Alleluia\s*[,.]?\s*Versus\s+ad\s+Evangelium|"
    r"Aleluya\s*[,.]?\s*Versículo\s+antes\s+del\s+Evangelio|"
    r"Alleluia\s*[,.]?\s*Verse\s+before\s+the\s+Gospel|"
    r"Aleluia\s*[,.]?\s*Vers[ií]culo\s+antes\s+do\s+Evangelho|"
    r"Alleluia\s*[,.]?\s*Versetto\s+al\s+Vangelo|"
    r"Alléluia\s*[,.]?\s*Acclamation|"
    r"Halleluja\s*[,.]?\s*Ruf\s+vor\s+dem\s+Evangelium|"
    r"Aclama[cç][aã]o\s+ao\s+Evangelho|"
    r"Aleluya|Alleluia|Aleluia|Alléluia|Halleluja"
    r")\s*[,.]?\s*",
    re.IGNORECASE,
)
# Citation pattern: book abbrev (1+ words/digits) + verse range — used to pull
# the Ps/Sl/Ps citation out of a title-style segment text. Allows comma/period/
# semicolon separators and "et" / "y" / "and" / "und" connectors between
# verse runs ("14 et 17", "5 y 6").
_CITATION_IN_TITLE_RE = re.compile(
    r"\b((?:[1-3]\s*)?(?:Ps|Sal|Sl|Cant|Lam|Ct|Sg|Jdt|Tb|Sir|Dan|Is|Os|Hab|Jl|Mi|Mt|Mc|Mk|Lc|Lk|Jn|Joh|Gv|Hbr|Heb|Ap|Rev|Apoc)\.?\s+"
    r"\d+[a-z]?"
    r"(?:(?:\s*[,:;.]\s*|\s+(?:et|y|and|und)\s+)\d+[a-z]?(?:\s*[-–]\s*\d+[a-z]?)?)*)"
    r"(?:\s*\(|\s*$|\s+R/\.|\s+\")",
    re.IGNORECASE,
)


def _extract_citation_and_strip_from_text(text: str, lang: str) -> tuple[Optional[str], str]:
    """For a psalm or gospel-acclamation reading, the v1 segments embed the
    citation inside a `reading_title` segment like:
        "Psalmus Responsorius Ps 50, 3-4. 5-6a. 12-13. 14 et 17 (: cf. 3a)"
    Extract the citation (book + verse range, before any parenthetical refrain
    indicator), and return (citation, text_with_title_prefix_removed)."""
    if not text:
        return None, text or ""
    s = text.strip()
    # Strip leading title prefix (Psalmus Responsorius / Alleluia, ...)
    m = _PSALM_TITLE_PREFIX_RE.match(s) or _ALLELUIA_TITLE_PREFIX_RE.match(s)
    if not m:
        return None, text
    rest = s[m.end():].strip()
    # Citation = first chunk before "(:" (refrain marker) or end
    citation_match = _CITATION_IN_TITLE_RE.match(rest)
    if not citation_match:
        return None, rest
    citation = citation_match.group(1).strip().rstrip(".,;:")
    return citation, rest[citation_match.end():].lstrip(" :")


def psalm_from_items(items: list[dict]) -> Optional[dict]:
    """Responsorial psalm: extract citation from the title segment when present
    (e.g. "Psalmus Responsorius Ps 50, 3-4...")."""
    result = prayer_from_items(items)
    if not result:
        return result
    # Pull citation per language from the v1 first item's text/segments.
    citations: dict[str, str] = {}
    for it in items:
        for src, c in (it.get("content") or {}).items():
            if src not in LANG_MAP:
                continue
            iso = LANG_MAP[src]
            if iso in citations:
                continue
            # Look at the first reading_title segment, falling back to text
            title_text = ""
            for seg in c.get("segments") or []:
                if seg.get("type") in ("reading_title", "heading"):
                    title_text = seg.get("text") or ""
                    break
            if not title_text:
                title_text = c.get("text") or ""
            cit, _ = _extract_citation_and_strip_from_text(title_text, iso)
            if cit:
                citations[iso] = cit
    if citations:
        result["citation"] = citations
    return result


# ---------------------------------------------------------------------------
# Lecturas → readings tree
# ---------------------------------------------------------------------------


def split_lecturas_by_cycle(day: dict) -> dict[str, list[dict]]:
    """Split slots into per-cycle slot-lists.

    Common slots before any cycle marker are propagated to every cycle.
    """
    cycles: dict[str, list[dict]] = {}
    pre_cycle: list[dict] = []
    current: Optional[str] = None

    for slot in day["slots"]:
        t = slot.get("type")
        if t == "cycle_start":
            current = slot.get("cycle")
            cycles.setdefault(current, [])
        elif t == "cycle_end":
            current = None
        else:
            (cycles[current] if current else pre_cycle).append(slot)

    if not cycles:
        return {"default": pre_cycle}
    if pre_cycle:
        for c in cycles:
            cycles[c] = pre_cycle + cycles[c]
    return cycles


CYCLE_NAME_MAP = {"cicloA": "A", "cicloB": "B", "cicloC": "C", "cicloI": "I", "cicloII": "II", "default": "default"}


_RESPONSE_MARKERS = (
    "thanks be to god", "praise to you, lord", "palavra do senhor",
    "graças a deus", "gloria a ti, señor", "gloria a te, o signore",
    "lode a te, o cristo", "rendiamo grazie a dio",
    "louvor a vós, ó cristo", "te alabamos, señor",
    "te glorifiquen, señor", "ehre sei dir, herr",
    "lob sei dir christus", "dank sei gott dem herrn",
    "louange à toi, seigneur", "rendons gráce à dieu",
    "rendons grâce à dieu", "rendons grace à dieu",
    "verbum domini", "deo gratias", "laus tibi", "amen.",
    "r/.", "r.", "℟",
)


def _looks_like_reading_response(items: list[dict]) -> bool:
    """A 'people's response' generic slot is short, contains 'R/.', and matches
    a known response phrase ('Thanks be to God', 'Praise to you, Lord').
    Heading/title slots and other generics should NOT be treated as responses."""
    if not items:
        return False
    item = items[0]
    content = item.get("content") or {}
    for src, c in content.items():
        if src not in LANG_MAP:
            continue
        text = (c.get("text") or "").strip().lower()
        if not text or len(text) > 80:
            continue
        if any(marker in text for marker in _RESPONSE_MARKERS):
            return True
    return False


def _citation_from_item(item: dict) -> dict[str, str]:
    """Extract a localized verse-before-the-Gospel citation from a single
    aleluya item. Looks for a leading `reference` segment, falls back to
    parsing one out of the title/text."""
    out: dict[str, str] = {}
    for src, c in (item.get("content") or {}).items():
        if src not in LANG_MAP:
            continue
        iso = LANG_MAP[src]
        if iso in out:
            continue
        for seg in c.get("segments") or []:
            if seg.get("type") == "reference":
                ref_text = clean_text(seg.get("text") or "")
                if ref_text and re.search(r"\d", ref_text):
                    out[iso] = ref_text
                    break
        if iso in out:
            continue
        title_text = ""
        for seg in c.get("segments") or []:
            if seg.get("type") in ("reading_title", "heading"):
                title_text = seg.get("text") or ""
                break
        if not title_text:
            title_text = c.get("text") or ""
        cit, _ = _extract_citation_and_strip_from_text(title_text, iso)
        if cit:
            out[iso] = cit
    return out


def cycle_slots_to_reading_set(slots: list[dict]) -> dict[str, Any]:
    """Convert a cycle's slot list into a ReadingSet."""
    rs: dict[str, Any] = {}
    pending_response: Optional[dict] = None

    for slot in slots:
        t = slot.get("type")
        items = slot.get("items") or []
        if t == "x_prim_lect":
            r = reading_with_alternatives_from_items(items)
            if r:
                _attach_response(r, pending_response)
                pending_response = None
                rs["firstReading"] = r
        elif t == "x_salmo":
            r = psalm_from_items(items)
            if r:
                rs["responsorialPsalm"] = r
        elif t == "x_seg_lect":
            r = reading_with_alternatives_from_items(items)
            if r:
                _attach_response(r, pending_response)
                pending_response = None
                rs["secondReading"] = r
        elif t == "x_aleluya":
            # When the source lists multiple alternative gospel acclamations
            # (e.g. All Souls Day's 11 options, Sacred Heart B/C's 2 options),
            # each item is a separate `<div class="hijo">` carrying one
            # citation + one verse. Emit the slot as `{alternatives: [...]}`
            # with all options inside (no primary at the root). For
            # single-option days, keep the simple body+citation shape.
            options: list[dict] = []
            if len(items) > 1:
                for item in items:
                    opt = prayer_from_items([item])
                    if not opt:
                        continue
                    cit = _citation_from_item(item)
                    if cit:
                        opt["citation"] = cit
                    options.append(opt)
            if len(options) >= 2:
                rs["gospelAcclamation"] = {"alternatives": options}
            elif options:
                rs["gospelAcclamation"] = options[0]
            else:
                r = prayer_from_items(items)
                if r:
                    cit = _citation_from_item(items[0]) if items else {}
                    if cit:
                        r["citation"] = cit
                    rs["gospelAcclamation"] = r
        elif t == "x_evangelio":
            r = reading_with_alternatives_from_items(items)
            if r:
                _attach_response(r, pending_response)
                pending_response = None
                rs["gospel"] = r
        elif t == "generic":
            # Only treat as a response if the content looks like an actual
            # liturgical response. Skip title/header slots.
            if not _looks_like_reading_response(items):
                continue
            response = prayer_from_items(items)
            if response is None:
                continue
            for key in ("gospel", "secondReading", "firstReading"):
                if key in rs and not _reading_has_response(rs[key]):
                    _attach_response(rs[key], response)
                    break
            else:
                pending_response = response

    # Reorder cycle slots to canonical liturgical sequence.
    canonical_order = [
        "firstReading",
        "responsorialPsalm",
        "secondReading",
        "gospelAcclamation",
        "gospel",
    ]
    return {
        k: rs[k] for k in canonical_order if k in rs
    } | {k: v for k, v in rs.items() if k not in canonical_order}


def build_readings(lecturas_day: dict) -> dict[str, Any]:
    cycles_raw = split_lecturas_by_cycle(lecturas_day)
    out: dict[str, dict] = {}
    for cyc, slots in cycles_raw.items():
        name = CYCLE_NAME_MAP.get(cyc, cyc)
        rs = cycle_slots_to_reading_set(slots)
        if rs:
            out[name] = rs
    return out


# ---------------------------------------------------------------------------
# Mass assembly
# ---------------------------------------------------------------------------


def load_v1(category: str, basename: str, day_id: str) -> Optional[dict]:
    p = V1_OUT / "days" / category / basename / f"{day_id}.json"
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def list_basenames(category: str) -> list[str]:
    d = V1_OUT / "days" / category
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def list_day_ids(category: str, basename: str) -> list[str]:
    d = V1_OUT / "days" / category / basename
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def index_lecturas() -> dict[str, str]:
    """Build {day_id: basename}."""
    idx: dict[str, str] = {}
    for base in list_basenames("lecturas"):
        for d_id in list_day_ids("lecturas", base):
            if d_id == "_root":
                continue
            idx.setdefault(d_id, base)
    return idx


# Slot-class → field mapping for ordinary Mass parts
SLOT_TO_FIELD = {
    "x_ant_ent": "entranceAntiphon",
    "x_acto_penit": "penitentialAct",
    "x_gloria": "gloriaInstruction",
    "x_colecta": "collect",
    "x_credo": "creedInstruction",
    "x_or_ofrend": "prayerOverOfferings",
    "x_prefacio": "preface",
    "x_ant_com": "communionAntiphon",
    "x_post_com": "postcommunion",
    "x_or_pueblo": "prayerOverPeople",
}


def extract_special_rites(day: dict) -> list[dict]:
    """Capture content from `generic` slots that the standard Mass schema doesn't model.

    Used for the Triduum (Easter Vigil, Good Friday Liturgy of the Passion,
    Holy Thursday's Mass of the Lord's Supper, Palm Sunday Procession), where
    most of the liturgy is structured as headings + free-form blocks rather
    than as the standard ant_ent / collect / readings / preface layout.

    The output is a tree:
      [
        { type: "section", level, heading: Localized, content: [...] },
        { type: "block", body: RichText },
        ...
      ]

    Sections nest by the source heading level (h1..h6). Blocks are paragraphs
    of liturgical text or rubric in source order.

    Returns [] for days with no generic content (most ordinary Masses).
    """
    root: list[dict] = []
    stack: list[dict] = []  # active section stack (deepest last)

    def append(node: dict) -> None:
        if stack:
            stack[-1]["content"].append(node)
        else:
            root.append(node)

    for slot in day.get("slots", []):
        if slot.get("type") != "generic":
            continue
        items = slot.get("items") or []
        if not items:
            continue
        # Only the first item per generic slot in this corpus.
        content = items[0].get("content") or {}

        heading_level: Optional[int] = None
        heading_loc: dict[str, str] = {}
        for src in SOURCE_LANGS:
            c = content.get(src)
            if not c:
                continue
            for seg in c.get("segments") or []:
                if seg.get("type") == "heading":
                    if heading_level is None:
                        heading_level = seg.get("level") or 5
                    heading_loc[LANG_MAP[src]] = clean_text(seg.get("text") or "")
                    break

        # Fallback: detect "soft heading" patterns where the source uses
        # short rubric-only slots as section markers:
        #   <big><span class="red">First Form: The Procession</span></big>     (Palm Sunday)
        #   <span class="red">Renewal of Priestly Promises</span>             (Chrism Mass)
        #
        # A slot is a soft heading when:
        # - Its only segment is one short rubric (≤ 80 chars).
        # - Same in at least 2 of the major languages.
        # - Doesn't end with ':' (continuation rubric like "And all reply:").
        # - Doesn't start with "\d+\." (numbered rubric paragraph).
        # - Doesn't match a known continuation-rubric pattern (e.g. "After the
        #   Nth reading…" / "Post Nth lectionem…" — those introduce a unit, not
        #   a section break).
        if not heading_loc:
            soft_loc: dict[str, str] = {}
            for src in SOURCE_LANGS:
                c = content.get(src)
                if not c:
                    continue
                segs = [s for s in (c.get("segments") or []) if s.get("type") != "break"]
                if len(segs) != 1:
                    continue
                seg = segs[0]
                if seg.get("type") != "rubric":
                    continue
                text = clean_text(seg.get("text") or "")
                if not text or len(text) > 80:
                    continue
                if text.endswith(":") or text.endswith(": "):
                    continue
                if re.match(r"^\d+\.", text):
                    continue
                soft_loc[LANG_MAP[src]] = text
            # Reject if the slot is a known continuation-rubric pattern across
            # any language (OT-reading-and-collect introducer, "Or:" alternative).
            if soft_loc:
                anyhit = False
                for txt in soft_loc.values():
                    low = txt.lower()
                    if any(low.startswith(p) for p in (
                        "post ", "after the ", "tras la ", "após a ", "dopo la ",
                        "après la ", "nach der ",
                    )):
                        anyhit = True
                        break
                    # Also reject pure "Or:" alternative markers
                    if low.rstrip(".:") in {"or", "or,", "ou", "oppure", "o", "oder"}:
                        anyhit = True
                        break
                if anyhit:
                    soft_loc = {}
            if len(soft_loc) >= 2:
                heading_loc = soft_loc
                heading_level = 5

        if heading_level is not None and heading_loc:
            section = {
                "type": "section",
                "level": heading_level,
                "heading": heading_loc,
                "content": [],
            }
            # Pop deeper or sibling sections off the stack.
            while stack and stack[-1]["level"] >= heading_level:
                stack.pop()
            append(section)
            stack.append(section)
            # Continue: the slot may also have non-heading body text after the heading.
            body = _rich_text_excluding_headings(content)
            if body:
                section["content"].append({"type": "block", "body": body})
            continue

        # Plain content block — attaches to current open section or the root.
        body = make_rich_text(content)
        if body:
            block: dict[str, Any] = {"type": "block", "body": body}
            # Stash the per-language source HTML so typed extractors can re-parse.
            # Stripped out before final write.
            block["_sourceHtml"] = {
                src: c.get("html") or ""
                for src, c in content.items()
                if src in LANG_MAP
            }
            append(block)

    return root


def _rich_text_excluding_headings(content: dict[str, dict]) -> Optional[dict]:
    """Build a RichText from a content dict, skipping heading segments.

    Used when a generic slot has both a heading AND body text — the heading
    starts a new section, and the remaining segments become the section's
    first block.
    """
    filtered: dict[str, dict] = {}
    for src, c in content.items():
        if src not in LANG_MAP:
            continue
        non_heading = [s for s in (c.get("segments") or []) if s.get("type") != "heading"]
        if not non_heading:
            continue
        filtered[src] = {
            "text": c.get("text") or "",  # may include heading text but cleaning is best-effort
            "segments": non_heading,
        }
    if not filtered:
        return None
    return make_rich_text(filtered)


# ---------------------------------------------------------------------------
# Special-rite classification (Triduum + Palm Sunday)
# ---------------------------------------------------------------------------

# Title patterns (case-insensitive, any language). First match wins.
RITE_TITLE_PATTERNS = [
    ("mass-with-procession", [
        "palm sunday", "dominica in palmis", "domingo de ramos",
        "domenica delle palme", "dimanche des rameaux", "palmsonntag",
    ]),
    ("chrism-mass", [
        "chrism mass", "missa chrismatis", "misa crismal",
        "messa crismale", "messe chrismale", "chrisam-messe",
        "missa do crisma",
    ]),
    ("lords-supper", [
        "lord's supper", "of the lord's supper",
        "in cena domini", "missa vespertina",
        "misa vespertina de la cena", "santa ceia do senhor",
        "messa nella cena del signore", "messe en mémoire de la cène",
        "abendmahl",
    ]),
    ("celebration-of-the-passion", [
        "good friday", "friday of the passion",
        "viernes santo", "sexta-feira santa", "venerdì santo",
        "vendredi saint", "karfreitag",
        "in passione domini", "celebratio passionis",
    ]),
    ("easter-vigil", [
        "easter vigil", "vigilia paschalis",
        "vigilia pascual", "vigília pascal",
        "veglia pasquale", "veillée pascale",
        "osternacht",
        "holy saturday",
        # NOTE: "easter sunday of the resurrection" is shared by P010 (morning
        # Mass) and SS06 (Vigil). Detection by title alone is ambiguous — the
        # actual Easter Vigil is recognized via SPECIAL_DAY_ID_OVERRIDES (SS06
        # → tempore.holy-week.easter-vigil) and gets `rite` set explicitly in
        # `assemble_mass`.
    ]),
]


def detect_rite(*haystack_dicts: dict[str, str]) -> str:
    """Return the rite label, scanning each provided localized dict for keywords.

    Order matters: pass the `title` first, then `rankLocalized` (which often
    carries the rite-identifying subtitle like "The Chrism Mass").
    """
    parts = []
    for d in haystack_dicts:
        if not d:
            continue
        parts.extend(t.lower() for t in d.values())
    haystack = " | ".join(parts)
    if not haystack:
        return "mass"
    for rite, kws in RITE_TITLE_PATTERNS:
        for kw in kws:
            if kw in haystack:
                return rite
    return "mass"


# Per-rite, the canonical ordered part keys and the heading patterns that
# identify each part in the source text. Patterns are matched case-insensitively
# against any language's heading text.
RITE_PART_PATTERNS: dict[str, list[tuple[str, list[str]]]] = {
    "mass-with-procession": [
        ("commemorationOfTheLordsEntrance", [
            "commemoration of the lord's entrance",
            "the commemoration of the lord",
            "first form: the procession", "second form: the solemn entrance",
            "third form: the simple entrance",
            "first form: solemn procession", "second form: solemn entrance",
            "third form: simple entrance",
            "blessing and procession of palms",
            "conmemoración de la entrada",
            "procissão de ramos", "procissão dos ramos",
            "commemorazione dell'ingresso",
            "commémoration de l'entrée",
            "gedenken des einzugs",
        ]),
        ("mass", [
            "at the mass", "en la misa", "na missa", "alla messa",
            "à la messe", "in der messe",
        ]),
    ],
    "chrism-mass": [
        ("renewalOfPriestlyPromises", [
            "renewal of priestly promises", "renovación de las promesas",
            "renovação das promessas sacerdotais",
            "rinnovazione delle promesse",
            "renouvellement des engagements",
            "erneuerung der priesterlichen versprechen",
        ]),
        ("blessingOfTheOils", [
            "blessing of the oil", "consecration of the chrism",
            "bendición de los óleos", "bênção dos óleos",
            "benedizione degli oli", "bénédiction des huiles",
            "ölweihe", "weihe des chrisams",
        ]),
    ],
    "lords-supper": [
        ("washingOfFeet", [
            "washing of feet", "lavanda de los pies",
            "lava-pés", "lavanda dei piedi",
            "lavement des pieds", "fußwaschung", "mandatum",
        ]),
        ("transferOfTheBlessedSacrament", [
            "transfer of the blessed sacrament",
            "translado del santísimo", "translação do santíssimo",
            "trasferimento del santissimo",
            "translation du saint-sacrement",
            "übertragung des allerheiligsten",
        ]),
    ],
    "celebration-of-the-passion": [
        ("liturgyOfTheWord", [
            "first part: liturgy of the word",
            "primera parte: liturgia de la palabra",
            "primeira parte: liturgia da palavra",
            "prima parte: liturgia della parola",
            "première partie: liturgie de la parole",
            "erster teil: wortgottesdienst",
        ]),
        ("adorationOfTheCross", [
            "second part: the adoration of the holy cross",
            "second part: adoration",
            "segunda parte: la adoración",
            "segunda parte: adoração",
            "seconda parte: adorazione",
            "deuxième partie: adoration",
            "zweiter teil: kreuzverehrung",
        ]),
        ("holyCommunion", [
            "third part: holy communion",
            "tercera parte: sagrada comunión",
            "terceira parte: sagrada comunhão",
            "terza parte: santa comunione",
            "troisième partie: communion",
            "dritter teil: heilige kommunion",
        ]),
    ],
    "easter-vigil": [
        ("serviceOfLight", [
            "first part: the service of light",
            "the solemn beginning of the vigil",
            "lucernarium",
            "primera parte: liturgia de la luz",
            "primeira parte: liturgia da luz",
            "prima parte: liturgia della luce",
            "première partie: liturgie de la lumière",
            "erster teil: lichtfeier",
        ]),
        ("liturgyOfTheWord", [
            "second part: the liturgy of the word",
            "segunda parte: liturgia de la palabra",
            "segunda parte: liturgia da palavra",
            "seconda parte: liturgia della parola",
            "deuxième partie: liturgie de la parole",
            "zweiter teil: wortgottesdienst",
        ]),
        ("baptismalLiturgy", [
            "third part: baptismal liturgy",
            "third part: the baptismal liturgy",
            "tercera parte: liturgia bautismal",
            "terceira parte: liturgia batismal",
            "terza parte: liturgia battesimale",
            "troisième partie: liturgie baptismale",
            "dritter teil: tauffeier",
        ]),
        ("liturgyOfTheEucharist", [
            "fourth part: liturgy of the eucharist",
            "cuarta parte: liturgia eucarística",
            "quarta parte: liturgia eucarística",
            "quarta parte: liturgia eucaristica",
            "quatrième partie: liturgie eucharistique",
            "vierter teil: eucharistiefeier",
        ]),
    ],
}


def assign_section_to_part(rite: str, heading_localized: dict[str, str]) -> Optional[str]:
    """Match a section heading against the rite's known parts. Return part key or None."""
    if rite not in RITE_PART_PATTERNS:
        return None
    haystack = " | ".join(h.lower() for h in heading_localized.values())
    for part_key, patterns in RITE_PART_PATTERNS[rite]:
        for pat in patterns:
            if pat in haystack:
                return part_key
    return None


def split_rites_into_parts(rite: str, rites_tree: list[dict]) -> dict[str, dict]:
    """Walk the top-level sections of a rites tree and route each into a known part key.

    Sections that don't match any known part go under `preamble` (before the
    first matched part) or `appendix` (after the last matched part).
    """
    parts: dict[str, dict] = {}
    appendix_buf: list[dict] = []
    preamble_buf: list[dict] = []
    seen_any = False
    last_part_key: Optional[str] = None

    for node in rites_tree:
        if node.get("type") != "section":
            # A loose block at the top of the file → preamble.
            (appendix_buf if seen_any else preamble_buf).append(node)
            continue
        part_key = assign_section_to_part(rite, node.get("heading") or {})
        if part_key:
            seen_any = True
            last_part_key = part_key
            existing = parts.get(part_key)
            if existing is None:
                parts[part_key] = {
                    "heading": node["heading"],
                    "content": list(node.get("content") or []),
                }
            else:
                # Already routed: nest this section under existing as a sub-section
                # (preserves the form/subsection's heading rather than dropping it).
                existing["content"].append(node)
            appendix_buf = []
        else:
            # Section that doesn't match a part key. If we've seen a part already,
            # it's an addition to the most recent part; otherwise it's preamble.
            if last_part_key is not None and last_part_key in parts:
                parts[last_part_key]["content"].append(node)
            elif seen_any:
                appendix_buf.append(node)
            else:
                preamble_buf.append(node)

    if preamble_buf:
        parts["preamble"] = {
            "heading": {LANG_MAP[s]: "Preamble" for s in ("engl",) if s in LANG_MAP} or {"en": "Preamble"},
            "content": preamble_buf,
        }
    if appendix_buf:
        parts["appendix"] = {
            "heading": {"en": "Appendix"},
            "content": appendix_buf,
        }
    return parts


# ---------------------------------------------------------------------------
# Typed extractors for special-rite blocks
# ---------------------------------------------------------------------------

# Localized "Let us pray" markers for splitting forWhom from the deacon's invitation.
LET_US_PRAY_MARKERS = [
    "let us pray",
    "oremos",
    "preghiamo",
    "prions",
    "lasset uns beten",
    "let us pray, dearly beloved",
]

# Localized "Through Christ our Lord" closing of a collect.
COLLECT_CLOSING_MARKERS = [
    "through christ our lord",
    "por nuestro señor jesucristo",
    "por nosso senhor jesus cristo",
    "per cristo nostro signore",
    "par jésus, le christ, notre seigneur",
    "durch christus, unseren herrn",
    "per christum dominum nostrum",
]


def _split_intercession_html(html: str) -> Optional[dict[str, str]]:
    """Parse one intercession's HTML into {forWhom, invitation, silenceRubric, collect, response}.

    Source pattern:
      I. <span class="red">For Holy Church</span><br/>
      Let us pray... <br/>
      <span class="red">Silent prayer. Then the priest sings or says:</span><br/>
      Almighty ever-living God,...<br/>
      Through Christ our Lord.<br/>
      <span class="red">R/. </span>Amen.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    # Use string accumulation: walk children, splitting on <br/>.
    lines: list[tuple[str, str]] = []  # (kind, text) where kind is 'rubric'|'text'
    buffer_rubric = ""
    buffer_text = ""

    def push_line():
        nonlocal buffer_rubric, buffer_text
        if buffer_rubric or buffer_text:
            kind = "rubric" if (buffer_rubric and not buffer_text) else "text"
            lines.append((kind, clean_text((buffer_rubric + " " + buffer_text).strip())))
        buffer_rubric = ""
        buffer_text = ""

    body_root = soup.body or soup
    for el in body_root.descendants:
        # We use simple top-level walk instead.
        pass

    # Simpler: rebuild line-by-line using a manual walker.
    lines = []
    cur: list[tuple[str, str]] = []  # [(kind, text), ...]

    def flush():
        nonlocal cur
        if cur:
            text = " ".join(t for _, t in cur).strip()
            kinds = {k for k, _ in cur}
            kind = "rubric" if kinds == {"rubric"} else "text"
            if text:
                lines.append((kind, clean_text(text)))
        cur = []

    def walk(node):
        from bs4 import NavigableString, Tag
        if isinstance(node, NavigableString):
            t = clean_text(str(node))
            if t:
                cur.append(("text", t))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "br":
            flush()
            return
        cls = node.get("class") or []
        if "red" in cls:
            t = clean_text(node.get_text(" ", strip=True))
            if t:
                cur.append(("rubric", t))
            return
        for child in node.children:
            walk(child)

    for child in (soup.body or soup).children:
        walk(child)
    flush()

    if not lines:
        return None

    # First line: ordinal + forWhom title (a red rubric).
    # The forWhom title can be wrapped in <span class="red"> on its own line,
    # or merged with subsequent Latin rubric text. Take just the first rubric
    # token after the ordinal.
    out: dict[str, str] = {}
    # Match Roman or Arabic ordinal (German source uses "3." for intercession III).
    m = re.match(r"^(?:[IVX]+|\d{1,2})\.\s*(.+?)$", lines[0][1])
    inline_invitation: Optional[str] = None
    if m:
        # Truncate at the start of the deacon's invitation if it's mashed in
        # (e.g. Portuguese "Pela santa Igreja Oremos, irmãos...").
        fw = m.group(1).strip()
        invitation_starters = [
            " Oremos", " Oremus", " Let us pray",
            " Preghiamo", " Prions",
            " Lasset uns beten", " Lasst uns beten",
            " Roguemos", " Suppliquons",
            " Oratio dicitur", " Praeces dicuntur", " Preces dicuntur",
        ]
        for sep in invitation_starters:
            idx = fw.find(sep)
            if idx > 0:
                # Capture the inline invitation portion for extraction below.
                inline_invitation = fw[idx:].strip()
                fw = fw[:idx].rstrip(" ,;:")
                break
        out["forWhom"] = fw

    # Find silence rubric line. Match either explicit rubric lines, or text
    # lines that contain the silence/kneeling marker (German source has the
    # "Beuget die Knie. - Stille - Erhebet euch." rubric in mixed-kind lines).
    silence_idx: Optional[int] = None
    silence_keywords = (
        "silent prayer", "silen", "still", "in silenzio",
        "reza-se em silêncio", "oración en silencio",
        "prière silencieuse", "stilles gebet",
        "beuget die knie", "flectamus genua", "flectámus",
    )
    for i, (kind, txt) in enumerate(lines):
        low = txt.lower()
        if any(k in low for k in silence_keywords):
            silence_idx = i
            out["silenceRubric"] = txt
            break

    # Identify the response line — last line containing "Amen" or starting with "R/."
    response_idx: Optional[int] = None
    response_text: Optional[str] = None
    for i in range(len(lines) - 1, max(silence_idx or 0, 0), -1):
        kind, txt = lines[i]
        # Strip "R/." prefix from response
        m2 = re.match(r"^\s*R\s*/?\.?\s*(.+)$", txt, flags=re.IGNORECASE)
        if m2 and ("amen" in m2.group(1).lower() or len(m2.group(1).strip()) <= 16):
            response_text = clean_text(m2.group(1))
            response_idx = i
            break
        if "amen" in txt.lower() and len(txt) < 60:
            response_text = clean_text(txt.lstrip("R/. ").lstrip("R. "))
            response_idx = i
            break

    # Build invitation and collect
    invitation_lines: list[str] = []
    collect_lines: list[str] = []

    # Inline invitation (mashed into line[0] after the forWhom title) — picked
    # out by the truncation above. Add as the first line of invitation.
    if inline_invitation:
        invitation_lines.append(inline_invitation)

    inv_end = silence_idx if silence_idx is not None else (response_idx or len(lines))
    coll_end = response_idx if response_idx is not None else len(lines)

    for i in range(1, inv_end):
        kind, txt = lines[i]
        if kind == "text":
            invitation_lines.append(txt)

    if silence_idx is not None:
        for i in range(silence_idx + 1, coll_end):
            kind, txt = lines[i]
            if kind == "text":
                collect_lines.append(txt)

    if invitation_lines:
        out["invitation"] = " ".join(invitation_lines)
    if collect_lines:
        out["collect"] = " ".join(collect_lines)
    if response_text:
        out["response"] = response_text

    return out


def try_extract_solemn_intercession(block: dict) -> Optional[dict]:
    """If a block looks like a Solemn Intercession (ordinal + title + invitation + collect),
    return a typed object; otherwise None.
    """
    body_plain = (block.get("body") or {}).get("plain") or {}
    # Always check English first — English consistently uses Roman numerals,
    # while German rubrics use Arabic numerals that conflict with the
    # intercession Arabic numerals. Roman match is the authoritative signal.
    sample = body_plain.get("en") or body_plain.get("la") or ""
    m = re.match(r"^([IVX]+)\.\s", sample.strip())
    if not m:
        return None
    ordinal = m.group(1)
    src = block.get("_sourceHtml") or {}

    forWhom: dict[str, str] = {}
    invitation: dict[str, str] = {}
    silenceRubric: dict[str, str] = {}
    collect: dict[str, str] = {}
    response: dict[str, str] = {}

    for src_lang, html in src.items():
        iso = LANG_MAP.get(src_lang)
        if not iso:
            continue
        parts = _split_intercession_html(html)
        if not parts:
            continue
        if "forWhom" in parts:
            forWhom[iso] = parts["forWhom"]
        if "invitation" in parts:
            invitation[iso] = parts["invitation"]
        if "silenceRubric" in parts:
            silenceRubric[iso] = parts["silenceRubric"]
        if "collect" in parts:
            collect[iso] = parts["collect"]
        if "response" in parts:
            response[iso] = parts["response"]

    if not forWhom:
        return None

    return strip_empty({
        "type": "solemn-intercession",
        "ordinal": ordinal,
        "forWhom": forWhom,
        "invitation": invitation,
        "silenceRubric": silenceRubric,
        "collect": collect,
        "response": response,
    })


def try_extract_baptismal_qa(block: dict) -> Optional[dict]:
    """Detect 'Priest: ...' / 'All: ...' alternation typical of the Renewal of Baptismal
    Promises. Returns a typed Q&A pair, or None if pattern doesn't match.
    """
    body_plain = (block.get("body") or {}).get("plain") or {}
    sample = body_plain.get("en") or next(iter(body_plain.values()), "")
    if not sample:
        return None
    # Must contain at least one role-marker pattern.
    role_markers = ["priest:", "all:", "celebrant:", "presiding minister:",
                    "sacerdote:", "todos:", "tutti:", "prêtre:", "tous:",
                    "priester:", "alle:", "celebrante:", "celebrans:"]
    low = sample.lower()
    if not any(m in low for m in role_markers):
        return None

    src = block.get("_sourceHtml") or {}
    qa_per_lang: dict[str, list[dict]] = {}

    for src_lang, html in src.items():
        iso = LANG_MAP.get(src_lang)
        if not iso:
            continue
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        # Split into role-prefixed lines.
        # Pattern: <Role>: <text> until next role marker
        pairs = re.findall(
            r"(Priest|All|Celebrant|Presiding minister|Sacerdote|Todos|Tutti|Prêtre|Tous|Priester|Alle|Celebrante|Celebrans)\s*:\s*([^:]+?)(?=(?:Priest|All|Celebrant|Sacerdote|Todos|Tutti|Prêtre|Tous|Priester|Alle|Celebrante)\s*:|$)",
            text,
            flags=re.IGNORECASE,
        )
        if pairs:
            qa_per_lang[iso] = [{"role": r.strip().rstrip(":"), "text": clean_text(t)} for r, t in pairs]

    if not qa_per_lang:
        return None

    return strip_empty({
        "type": "qa-exchange",
        "exchanges": qa_per_lang,
    })


_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "seventh": 7,
    # Spanish
    "primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4,
    "quinta": 5, "sexta": 6, "séptima": 7, "septima": 7,
    # Portuguese
    "primeira": 1, "segunda": 2, "terceira": 3, "quarta": 4,
    "quinta": 5, "sexta": 6, "sétima": 7, "setima": 7,
    # Italian
    "prima": 1, "seconda": 2, "terza": 3, "quarta": 4,
    "quinta": 5, "sesta": 6, "settima": 7,
    # French
    "première": 1, "premiere": 1, "deuxième": 2, "deuxieme": 2,
    "troisième": 3, "troisieme": 3, "quatrième": 4, "quatrieme": 4,
    "cinquième": 5, "cinquieme": 5, "sixième": 6, "sixieme": 6,
    "septième": 7, "septieme": 7,
    # German
    "erste": 1, "zweite": 2, "dritte": 3, "vierte": 4,
    "fünfte": 5, "fuenfte": 5, "sechste": 6, "siebte": 7,
    # Latin
    "prima": 1, "secunda": 2, "tertia": 3, "quarta": 4,
    "quinta": 5, "sexta": 6, "septima": 7,
}

_OT_RUBRIC_PATTERNS = [
    re.compile(r"^(\d+)\.\s*After the (\w+) reading", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s*Tras la (\w+) lectura", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s*Após a (\w+) leitura", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s*Dopo la (\w+) lettura", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s*Après la (\w+) lecture", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s*Nach der (\w+) Lesung", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s*Post (\w+) lectionem", re.IGNORECASE),
]


def _is_ot_rubric_block(block: dict) -> Optional[int]:
    """Return the OT reading ordinal (1..7) if the block is the rubric heading
    of an OT-reading + collect unit; else None."""
    body_plain = (block.get("body") or {}).get("plain") or {}
    for txt in body_plain.values():
        s = (txt or "").strip()
        for pat in _OT_RUBRIC_PATTERNS:
            m = pat.match(s)
            if m:
                ord_word = m.group(2).lower()
                if ord_word in _ORDINAL_WORDS:
                    return _ORDINAL_WORDS[ord_word]
    return None


_ALTERNATIVE_PRAYER_MARKERS = {"or:", "or,", "ou:", "o:", "oppure:", "ou bien:", "oder:"}


_ALT_PREFIX_HINTS = (
    "or, on", "or:", "or,", "ou,", "ou:", "oppure", "oppure,", "oppure:",
    "o, sobre", "o:", "ou bien", "o bien", "oder",
)


def _is_alternative_marker(block: dict) -> bool:
    """True if the block is a short marker introducing an alternative prayer."""
    body_plain = (block.get("body") or {}).get("plain") or {}
    for txt in body_plain.values():
        s = (txt or "").strip().lower()
        if not s or len(s) > 60:
            continue
        if s.rstrip(".:") in _ALTERNATIVE_PRAYER_MARKERS or s in _ALTERNATIVE_PRAYER_MARKERS:
            return True
        for prefix in _ALT_PREFIX_HINTS:
            if s.startswith(prefix):
                return True
    return False


def consume_ot_readings(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Walk blocks; replace OT-reading rubric+collect groups with typed units.

    Returns (remaining_blocks, ot_readings).
    """
    remaining: list[dict] = []
    units: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.get("type") != "block":
            remaining.append(b)
            i += 1
            continue
        ordinal = _is_ot_rubric_block(b)
        if ordinal is None:
            remaining.append(b)
            i += 1
            continue

        # Consume the rubric block and following block(s) until the next OT rubric
        # or until exiting the run of related blocks (a section break or a clearly
        # unrelated block).
        unit: dict[str, Any] = {
            "type": "ot-reading-unit",
            "ordinal": ordinal,
            "rubric": b.get("body"),
            "collect": None,
            "alternativeCollect": None,
        }

        j = i + 1
        # First following block is the collect.
        if j < len(blocks) and blocks[j].get("type") == "block":
            unit["collect"] = blocks[j].get("body")
            j += 1

        # Optional: an alternative collect after an "Or:" marker.
        while j < len(blocks):
            nb = blocks[j]
            if nb.get("type") != "block":
                break
            if _is_alternative_marker(nb):
                if j + 1 < len(blocks) and blocks[j + 1].get("type") == "block":
                    unit["alternativeCollect"] = blocks[j + 1].get("body")
                    j += 2
                    continue
                j += 1
                continue
            # If the next block is another OT rubric, stop here.
            if _is_ot_rubric_block(nb) is not None:
                break
            # If it's a free-floating block before the next rubric, leave it
            # in remaining (don't grab too much).
            break

        units.append(strip_empty(unit))
        i = j
    return remaining, units


# ---------------------------------------------------------------------------
# Apply typed extractors per rite
# ---------------------------------------------------------------------------


def _walk_and_replace(node: dict, predicate, replacement_fn):
    """Walk a section/block tree, replacing matching blocks with the result of replacement_fn.

    `predicate(block) -> bool`. `replacement_fn(block) -> dict | None`.
    If replacement_fn returns None, the original block is kept.
    """
    if node.get("type") == "section":
        new_content = []
        for child in node.get("content") or []:
            if child.get("type") == "block" and predicate(child):
                replaced = replacement_fn(child)
                new_content.append(replaced if replaced is not None else child)
            elif child.get("type") == "section":
                _walk_and_replace(child, predicate, replacement_fn)
                new_content.append(child)
            else:
                new_content.append(child)
        node["content"] = new_content


def apply_typed_extractors(rite: str, parts: dict) -> None:
    """Run rite-specific typed extractors that promote `block` nodes into typed
    fields hanging off the relevant `Part`. The originating section is removed
    from the content tree (its content has been moved to the typed array)."""
    if rite == "celebration-of-the-passion":
        if "liturgyOfTheWord" in parts:
            new_top: list[dict] = []
            intercessions: list[dict] = []
            for child in parts["liturgyOfTheWord"].get("content", []):
                if (child.get("type") == "section"
                        and "solemn intercessions" in (child.get("heading") or {}).get("en", "").lower()):
                    section_content_kept: list[dict] = []
                    for c in child.get("content") or []:
                        if c.get("type") == "block":
                            si = try_extract_solemn_intercession(c)
                            if si is not None:
                                intercessions.append(si)
                                continue
                        section_content_kept.append(c)
                    if section_content_kept:
                        # Keep the section if there's residual non-intercession content.
                        child["content"] = section_content_kept
                        new_top.append(child)
                    # else: section consumed entirely — drop.
                else:
                    new_top.append(child)
            parts["liturgyOfTheWord"]["content"] = new_top
            if intercessions:
                parts["liturgyOfTheWord"]["solemnIntercessions"] = intercessions
    elif rite == "easter-vigil":
        if "liturgyOfTheWord" in parts:
            blocks = parts["liturgyOfTheWord"].get("content") or []
            remaining, ot_units = consume_ot_readings(blocks)
            parts["liturgyOfTheWord"]["content"] = remaining
            if ot_units:
                parts["liturgyOfTheWord"]["oldTestamentReadings"] = ot_units

        if "baptismalLiturgy" in parts:
            # Walk all blocks under baptismalLiturgy looking for Q&A pairs, then
            # consolidate consecutive Q&A blocks into one `renewalOfBaptismalPromises`
            # field with an ordered `questions` list.
            collected: list[dict] = []

            def walk(scope: list[dict]) -> list[dict]:
                out = []
                for c in scope:
                    if c.get("type") == "block":
                        qa = try_extract_baptismal_qa(c)
                        if qa is not None:
                            collected.append(qa)
                            continue
                    if c.get("type") == "section":
                        c["content"] = walk(c.get("content") or [])
                    out.append(c)
                return out

            parts["baptismalLiturgy"]["content"] = walk(parts["baptismalLiturgy"].get("content") or [])
            if collected:
                # Merge per-language exchanges across all collected blocks.
                merged: dict[str, list[dict]] = {}
                for qa in collected:
                    for lang, pairs in qa.get("exchanges", {}).items():
                        merged.setdefault(lang, []).extend(pairs)
                parts["baptismalLiturgy"]["renewalOfBaptismalPromises"] = {
                    "questions": merged,
                }


# ---------------------------------------------------------------------------
# Strip the temporary _sourceHtml field everywhere
# ---------------------------------------------------------------------------


def strip_source_html(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("_sourceHtml", None)
        for v in node.values():
            strip_source_html(v)
    elif isinstance(node, list):
        for v in node:
            strip_source_html(v)


# ---------------------------------------------------------------------------
# ID overrides for special days
# ---------------------------------------------------------------------------

LECTURAS_ID_OVERRIDES: dict[str, str] = {
    # Holy Week proper day-ids (SSxx) → lecturas day-ids (S00xD)
    "SS00":  "S000D",   # Palm Sunday — Mass readings (Passion Gospel)
    "SS01":  "S001D",   # Holy Monday
    "SS02":  "S002D",   # Holy Tuesday
    "SS03":  "S003D",   # Holy Wednesday
    "SS04":  "S004D",   # Holy Thursday Evening (Lord's Supper)
    "SS04A": "S004DA",  # Holy Thursday morning (Chrism Mass)
    "SS05":  "S005D",   # Good Friday
    "SS06":  "S006D",   # Easter Vigil
    # Solemnities of the Lord during Ordinary Time (OT5x) → lecturas (O5x0I)
    "OT51":  "O510I",   # Most Holy Trinity
    "OT52":  "O520I",   # Corpus Christi
    "OT53":  "O530I",   # Sacred Heart of Jesus
    "OT54":  "O540I",   # Christ the King
}

# Additional lecturas day-ids that should be attached as procession-Gospel
# rather than as the main `readings` of a Mass.
PROCESSION_LECTURAS: dict[str, str] = {
    "SS00": "S000DA",   # Palm Sunday Procession Gospel (entry into Jerusalem)
}


SPECIAL_DAY_ID_OVERRIDES: dict[str, str] = {
    # Holy Week (tiempos_semanasta*)
    "SS00": "tempore.holy-week.palm-sunday",
    "SS01": "tempore.holy-week.monday",
    "SS02": "tempore.holy-week.tuesday",
    "SS03": "tempore.holy-week.wednesday",
    "SS04A": "tempore.holy-week.chrism-mass",
    "SS04":  "tempore.holy-week.lords-supper",
    "SS05":  "tempore.holy-week.good-friday",
    "SS06":  "tempore.holy-week.easter-vigil",
    # Solemnities of the Lord during Ordinary Time
    "OT51":  "tempore.solemnity.most-holy-trinity",
    "OT52":  "tempore.solemnity.corpus-christi",
    "OT53":  "tempore.solemnity.sacred-heart-of-jesus",
    "OT54":  "tempore.solemnity.christ-the-king",
    # The four Christmas Day Masses (Vigil, Night, Dawn, Day)
    "A1251": "tempore.christmas.nativity-vigil",      # "annua exspectatione"
    "A1252": "tempore.christmas.nativity-night",      # "hanc sacratíssimam noctem"
    "A1253": "tempore.christmas.nativity-dawn",       # "nova incarnati Verbi tui luce"
    "A125":  "tempore.christmas.nativity-day",        # "humanae substantiae dignitatem"
    # Epiphany alternative
    "A170b": "tempore.christmas.epiphany-alt",
}

# Day-ids for which we should drop the (inferred) weekIndex/weekday metadata
# because the day is a movable solemnity, not a regular week of Ordinary Time.
DROP_WEEK_FOR_DAY_IDS = {"OT51", "OT52", "OT53", "OT54"}

# Day-id → rite override. Some special days share their title with another day
# (e.g. SS06 Easter Vigil and P010 Easter Sunday morning Mass), so we can't
# detect rite from title alone.
_SPECIAL_DAY_RITE: dict[str, str] = {
    "SS00":  "mass-with-procession",
    "SS04A": "chrism-mass",
    "SS04":  "lords-supper",
    "SS05":  "celebration-of-the-passion",
    "SS06":  "easter-vigil",
}


def extract_embedded_regional_saints() -> list[dict]:
    """Walk every santos estructura file and extract saints whose Mass content
    is embedded directly inside the estructura `<div class="dia">` (instead of
    being stored as `padre_N` placeholders pointing to language-file `hijo_N`).

    These are regional propers (USA, Brazil, Argentina, Germany, Spain, France,
    Africa, Nigeria, Uruguay, Chile) and a few extra Latin American saints.
    Returns a list of v2 Mass objects ready to be added to the corpus.
    """
    out: list[dict] = []
    estr_dir = SOURCE_REPO / "misal_v2" / "m_estructura" / "santos"
    if not estr_dir.exists():
        return out

    for f in sorted(estr_dir.glob("m_estructura_santos_*.html")):
        basename = f.stem.replace("m_estructura_santos_", "")
        html = f.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "lxml")
        for dia in soup.select("div.dia"):
            day_id = dia.get("id")
            x_titulo = dia.find("div", class_="x_titulo")
            if x_titulo is None:
                continue
            h2 = x_titulo.find("h2")
            if h2 is None:
                continue  # No embedded content — handled by regular flow.

            # Locale tag determines region + language.
            locale_tag = ""
            for span in x_titulo.find_all("span", class_="red"):
                t = clean_text(span.get_text(" ", strip=True))
                if t and (t.startswith("[") or t.startswith("(")):
                    locale_tag = t
                    break
            loc = detect_locale(locale_tag)
            if loc is None:
                # Fall back to file basename for known regional files.
                loc = _basename_locale_fallback(basename)
            if loc is None:
                continue  # Can't determine language — skip rather than guess wrong.
            region, iso_lang = loc

            mass = _build_embedded_mass(dia, day_id, basename, region, iso_lang)
            if mass and is_real_mass(mass):
                out.append(mass)
    return out


_BASENAME_FALLBACK = {
    "africa": ("africa", "en"),
    "arg":    ("argentina", "es"),
    "brasil": ("brazil", "pt-BR"),
    "fran":   ("france", "fr"),
    "obra":   ("religious-orders", "la"),
}


def _basename_locale_fallback(basename: str) -> Optional[tuple[str, str]]:
    return _BASENAME_FALLBACK.get(basename)


def _build_embedded_mass(dia: Tag, day_id: str, basename: str,
                         region: str, iso_lang: str) -> Optional[dict]:
    """Construct a v2 Mass object from an estructura `<div class="dia">` with
    embedded content."""
    mass: dict[str, Any] = {"id": "", "group": "sanctorale", "scope": region}

    # Parse the date from day_id (MMDD with optional letter suffix).
    parsed = parse_sanctorale_day_id(day_id)
    if parsed and "month" in parsed:
        mass["date"] = {"month": parsed["month"], "day": parsed["day"]}
        if parsed.get("suffix"):
            mass["dateSuffix"] = parsed["suffix"].lower()
    elif parsed and parsed.get("movableCode"):
        mass["movable"] = True
        mass["movableMonthAnchor"] = parsed["movableMonthAnchor"]
        mass["movableCode"] = parsed["movableCode"]

    # Pass the entire x_titulo HTML so parse_title_html can find h2 + h3 + italic divs.
    x_titulo = dia.find("div", class_="x_titulo")
    title_parsed = parse_title_html({_iso_to_src(iso_lang): str(x_titulo)})
    if "title" in title_parsed:
        mass["title"] = title_parsed["title"]
    if "date" in title_parsed and "date" not in mass:
        # If we couldn't get date from day_id, infer from title text — risky but safer than nothing.
        pass  # Skip this for now.
    if "rank" in title_parsed:
        rank = normalize_rank(title_parsed["rank"])
        if rank:
            mass["rank"] = rank
            mass["rankLocalized"] = title_parsed["rank"]
    if "description" in title_parsed:
        mass["description"] = title_parsed["description"]

    # Walk the typed slots (x_ant_ent, x_colecta, etc.) and build their content.
    slot_to_field = {
        "x_ant_ent": ("entranceAntiphon", "antiphon"),
        "x_ant_com": ("communionAntiphon", "antiphon"),
        "x_colecta": ("collect", "prayer"),
        "x_or_ofrend": ("prayerOverOfferings", "prayer"),
        "x_post_com": ("postcommunion", "prayer"),
        "x_or_pueblo": ("prayerOverPeople", "prayer"),
        "x_prefacio": ("preface", "preface"),
    }
    for slot_class, (field, kind) in slot_to_field.items():
        slot_div = dia.find("div", class_=slot_class)
        if slot_div is None:
            continue
        result = _build_field_from_embedded(slot_div, kind, iso_lang)
        if result:
            mass[field] = result

    # Canonical ID
    if parsed and "month" in parsed:
        # Date suffix joins the date segment with no separator, so dots-as-slashes
        # doesn't produce single-letter sub-folders (sanctorale.07-20.z.<scope>
        # would otherwise nest as saints/07-20/z/<scope>.json).
        date_seg = f"{parsed['month']:02d}-{parsed['day']:02d}"
        if parsed.get("suffix"):
            date_seg += parsed["suffix"].lower()
        mass["id"] = f"sanctorale.{date_seg}.{region}"
    elif parsed and parsed.get("movableCode"):
        mass["id"] = f"sanctorale.movable.{parsed['movableMonthAnchor']:02d}-{parsed['movableCode'][2:]}.{region}"
    else:
        mass["id"] = f"sanctorale.{region}.{day_id.lower()}"

    return mass


def _iso_to_src(iso: str) -> str:
    for src, target in LANG_MAP.items():
        if target == iso:
            return src
    return "engl"


def _build_lines_from_div(div: Tag) -> list[list[dict]]:
    """Build lines (list of segment lists) from an HTML div, splitting on <br/>
    and <p> boundaries, with embedded `<span class="red">` recognized as rubric
    and `<span class="alindcha">` as reference."""
    if div is None:
        return []
    lines: list[list[dict]] = []
    current: list[dict] = []

    def flush():
        if any((s.get("text") or "").strip() for s in current):
            lines.append(list(current))
        current.clear()

    def append_text(s: str):
        s = clean_text(s)
        if not s or _is_html_junk(s):
            return
        if current and current[-1]["type"] == "text":
            current[-1]["text"] = clean_text(current[-1]["text"] + " " + s)
        else:
            current.append({"type": "text", "text": s})

    def walk(node):
        if isinstance(node, str):
            append_text(node)
            return
        if not isinstance(node, Tag):
            return
        name = (node.name or "").lower()
        cls = " ".join(node.get("class") or [])
        if name == "br":
            flush()
            return
        if name == "p":
            flush()
            for child in node.children:
                walk(child)
            flush()
            return
        if name == "span" and "red" in cls:
            txt = clean_text(node.get_text(" ", strip=True))
            if txt:
                current.append({"type": "rubric", "text": txt})
            return
        if name == "span" and "alindcha" in cls:
            txt = clean_text(node.get_text(" ", strip=True))
            if txt:
                current.append({"type": "reference", "text": txt})
            return
        if name in ("h2", "h3", "h4"):
            # Headings inside body content are skipped — they're already
            # handled by parse_title_html.
            return
        # Other tags: recurse
        for child in node.children:
            walk(child)

    for child in div.children:
        walk(child)
    flush()
    return lines


def _build_field_from_embedded(slot_div: Tag, kind: str, iso_lang: str) -> Optional[dict]:
    """Build an Antiphon / Prayer / Preface from an embedded estructura slot.

    The slot may contain `agrupado_ant` / `agrupado_post` subdivisions, or be a
    flat container.  Some prayers reference a library preface via
    `<a class="enlacepref" href="…#pfNNN">` — for the `preface` kind, return a
    PrefaceRef instead of an inline body.
    """
    # Preface ref check
    if kind == "preface":
        ref = extract_preface_ref({_iso_to_src(iso_lang): str(slot_div)})
        if ref:
            return ref

    # For Antiphon: separate citation (label) from body.
    if kind == "antiphon":
        label_div = slot_div.find("div", class_="agrupado_ant")
        body_div = slot_div.find("div", class_="agrupado_post")
        out: dict[str, Any] = {}
        if label_div is not None:
            citation = _extract_alindcha(label_div)
            if citation:
                out["citation"] = {iso_lang: citation}
        if body_div is not None:
            body_text = clean_text(body_div.get_text(" ", strip=True))
            if body_text:
                lines_built = _build_lines_from_div(body_div)
                out["body"] = {
                    "plain": {iso_lang: body_text},
                    "lines": {iso_lang: lines_built} if lines_built else {},
                }
        elif slot_div.find("div") is None:
            text = clean_text(slot_div.get_text(" ", strip=True))
            if text:
                lines_built = _build_lines_from_div(slot_div)
                out["body"] = {
                    "plain": {iso_lang: text},
                    "lines": {iso_lang: lines_built} if lines_built else {},
                }
        return out or None

    # For Prayer / Preface body
    body_div = slot_div.find("div", class_="agrupado_post") or slot_div
    text = clean_text(body_div.get_text(" ", strip=True))
    if not text:
        return None
    lines_built = _build_lines_from_div(body_div)
    return {
        "body": {
            "plain": {iso_lang: text},
            "lines": {iso_lang: lines_built} if lines_built else {},
        }
    }


def _extract_alindcha(scope: Tag) -> Optional[str]:
    span = scope.find("span", class_="alindcha")
    if span is not None:
        text = clean_text(span.get_text(" ", strip=True))
        if text:
            # Normalize Cf prefix variants to canonical "Cf."
            text = re.sub(r"^(?:Cf\.?|cf\.?|Cfr\.?)(\s+)", r"Cf.\1", text)
            text = text.rstrip(".,;:").strip()
        return text or None
    return None


def is_real_mass(mass: dict) -> bool:
    """Filter out scaffolding 'masses' that contain only rubric instructions.

    A real mass has at least one of: title, description, collect,
    entranceAntiphon, communionAntiphon, postcommunion, prayerOverOfferings,
    or substantive readings. Empty/stub readings (only acclamation responses,
    no actual scripture body) don't count.
    """
    for f in ("title", "description", "collect", "entranceAntiphon",
              "communionAntiphon", "postcommunion", "prayerOverOfferings"):
        if f in mass and mass[f]:
            return True
    # Readings only count if any reading has substantial body (>100 chars).
    for cyc, rs in (mass.get("readings") or {}).items():
        if not isinstance(rs, dict):
            continue
        for r_name in ("firstReading", "secondReading", "gospel"):
            r = rs.get(r_name)
            if not isinstance(r, dict):
                continue
            body = (r.get("body") or {}).get("plain") or {}
            if any(len(t) > 100 for t in body.values()):
                return True
    return False


def assemble_mass(category: str, basename: str, day: dict, lecturas_idx: dict[str, str]) -> dict:
    day_id = day.get("id")

    # Identify what kind of mass this is (group + season + date metadata)
    group, group_meta = classify_mass_group(category, basename, day_id)

    mass: dict[str, Any] = {
        "id": canonical_id(category, basename, day_id),
        "group": group,
    }
    mass.update(group_meta)

    # Title + saint metadata extraction
    title_slot = next((s for s in day["slots"] if s.get("type") == "x_titulo"), None)
    if title_slot and title_slot.get("items"):
        # Title slot may use role="main" (typical), "post" (some Holy Week files),
        # or "ant" (Gaudete Sunday and similar where the title comes BEFORE a
        # rubric note about color in the same slot).
        html_per_src = items_collect_html(title_slot["items"], roles=("main", "post", "ant"))
        if html_per_src:
            structured = parse_title_html(html_per_src)
            if "title" in structured:
                mass["title"] = structured["title"]
            if "rank" in structured:
                # The "rank" slot (h3 in source) holds either:
                #   1) an actual celebration class (Solemnity/Feast/Memorial)
                #   2) a Mass-time variant label ("At Mass during the Day",
                #      "Vigil Mass", "Messa della notte") — useful, not a rank
                #   3) a rubric sentence about how the day is celebrated
                # We only store rankLocalized when we can normalize to one of
                # the four canonical ranks; otherwise dropping it avoids
                # polluting the catalog with non-rank text in a rank field.
                values = list(structured["rank"].values())
                avg_len = sum(len(v) for v in values) / max(len(values), 1)
                looks_rubric = avg_len > 30 or any(v.endswith((".", "!")) for v in values if v)
                if not looks_rubric:
                    rank_norm = normalize_rank(structured["rank"])
                    if rank_norm:
                        mass["rank"] = rank_norm
                        mass["rankLocalized"] = structured["rank"]
            if "description" in structured:
                mass["description"] = structured["description"]

    # Mass parts
    for slot in day["slots"]:
        st = slot.get("type")
        items = slot.get("items") or []
        if st in ("x_ant_ent", "x_ant_com"):
            ant = antiphon_from_items(items)
            if ant:
                mass[SLOT_TO_FIELD[st]] = ant
        elif st == "x_prefacio":
            # First check if this is a reference to a library preface.
            html_per_src = items_collect_html(items, roles=("post", "main"))
            ref = extract_preface_ref(html_per_src) if html_per_src else None
            if ref:
                mass["preface"] = ref
            else:
                p = prayer_from_items(items)
                if p:
                    # Try to resolve label-only preface bodies into prefaceRefs.
                    label_ref = label_only_preface_to_ref(p)
                    if label_ref:
                        mass["preface"] = label_ref
                    else:
                        mass["preface"] = p
        elif st in ("x_colecta", "x_or_ofrend", "x_post_com", "x_or_pueblo"):
            p = prayer_from_items(items)
            if p:
                mass[SLOT_TO_FIELD[st]] = p
        elif st in ("x_gloria", "x_credo", "x_acto_penit"):
            p = prayer_from_items(items)
            if p:
                mass[SLOT_TO_FIELD[st]] = p

    # Readings — joined by day-id, with Holy Week id remapping where applicable.
    if day_id and category in ("tiempos", "santos"):
        # Resolve the lecturas day-id (some Holy Week days use a different code).
        lec_day_id = LECTURAS_ID_OVERRIDES.get(day_id, day_id)
        # General fallback: tempore mass IDs are Q010/P010/etc. while lecturas
        # use the trailing-`D` form (Q010D/P010D). Try the `D`-suffixed form
        # when an exact match is not in the index.
        if lec_day_id not in lecturas_idx and (lec_day_id + "D") in lecturas_idx:
            lec_day_id = lec_day_id + "D"
        # Variant masses (e.g. `P064A` Easter Thursday Year-A formulary) share
        # readings with the base day (`P064D`). Try stripping the trailing
        # alpha-suffix and adding `D`.
        if lec_day_id not in lecturas_idx:
            m_var = re.match(r"^([A-Z]\d{3})[A-Z]$", lec_day_id)
            if m_var and (m_var.group(1) + "D") in lecturas_idx:
                lec_day_id = m_var.group(1) + "D"
        # Ordinary Time Sundays: mass `OT0N` (or `OT34`) → lecturas `O0N0I`
        # (Sunday formulary, weekday cycle I; the file holds A/B/C cycles inside).
        if lec_day_id not in lecturas_idx:
            m_ot = re.match(r"^OT(\d{2})$", lec_day_id)
            if m_ot:
                ot_candidate = f"O{m_ot.group(1)}0I"
                if ot_candidate in lecturas_idx:
                    lec_day_id = ot_candidate
        if lec_day_id in lecturas_idx:
            lec_day = load_v1("lecturas", lecturas_idx[lec_day_id], lec_day_id)
            if lec_day:
                r = build_readings(lec_day)
                if r:
                    mass["readings"] = r

        # Palm Sunday additionally has a procession Gospel under a separate ID.
        proc_id = PROCESSION_LECTURAS.get(day_id)
        if proc_id and proc_id in lecturas_idx:
            proc_day = load_v1("lecturas", lecturas_idx[proc_id], proc_id)
            if proc_day:
                proc_readings = build_readings(proc_day)
                if proc_readings:
                    mass["processionGospel"] = proc_readings

    # Detect rite, populate parts, override ID for known special days.
    # Some special days (notably Easter Vigil SS06, whose title is identical to
    # Easter Sunday morning P010) require an explicit override by day-id.
    rite = _SPECIAL_DAY_RITE.get(day_id) or detect_rite(mass.get("title") or {}, mass.get("rankLocalized") or {})
    if rite != "mass":
        mass["rite"] = rite
        rites_tree = extract_special_rites(day)
        if rites_tree:
            parts = split_rites_into_parts(rite, rites_tree)
            if parts:
                apply_typed_extractors(rite, parts)
                strip_source_html(parts)
                mass["parts"] = parts

    if day_id in SPECIAL_DAY_ID_OVERRIDES:
        mass["id"] = SPECIAL_DAY_ID_OVERRIDES[day_id]

    if day_id in DROP_WEEK_FOR_DAY_IDS:
        mass.pop("weekIndex", None)
        mass.pop("weekday", None)
        # Tag as a solemnity in the season grouping
        mass["season"] = "solemnity"

    return strip_empty(mass)


# ---------------------------------------------------------------------------
# Mass grouping & canonical IDs
# ---------------------------------------------------------------------------


def classify_mass_group(category: str, basename: str, day_id: Optional[str]) -> tuple[str, dict[str, Any]]:
    """Return (group, metadata) for a Mass.

    Group is one of: tempore, sanctorale, common, votive, ritual, ordinary,
                     eucharistic-prayer, preface
    """
    if category == "tiempos":
        meta: dict[str, Any] = {}
        if day_id:
            parsed = parse_temporal_day_id(day_id)
            sg = parsed.get("seasonGroup")
            if sg == "advent_christmas":
                meta["season"] = split_advent_christmas(day_id)
            elif sg:
                meta["season"] = sg
            if "weekIndex" in parsed and parsed["weekIndex"] is not None:
                meta["weekIndex"] = parsed["weekIndex"]
            if "weekday" in parsed and parsed["weekday"] is not None:
                meta["weekday"] = parsed["weekday"]
        return "tempore", meta

    if category == "santos":
        meta: dict[str, Any] = {}
        if day_id:
            d = parse_sanctorale_day_id(day_id)
            if d and "month" in d:
                meta["date"] = {"month": d["month"], "day": d["day"]}
                if d.get("suffix"):
                    meta["dateSuffix"] = d["suffix"].lower()
            elif d and d.get("movableCode"):
                meta["movable"] = True
                meta["movableMonthAnchor"] = d["movableMonthAnchor"]
                meta["movableCode"] = d["movableCode"]
                if d.get("suffix"):
                    meta["dateSuffix"] = d["suffix"].lower()
            elif d and d.get("undated"):
                meta["undated"] = True
                meta["ordinal"] = d["ordinal"]
        scope = SANCTORALE_SCOPE.get(basename)
        if scope:
            meta["scope"] = scope
        return "sanctorale", meta

    if category == "comunes_votivas":
        # Distinguish commons / votives / ritual / various-needs / dead by basename
        base_meta = COMMON_VOTIVE_BASE.get(basename, {"group": "common", "subgroup": basename})
        return base_meta["group"], {"subgroup": base_meta["subgroup"]}

    if category == "ordinario":
        return "ordinary", {"part": basename}
    if category == "plegarias_euc":
        return "eucharistic-prayer", {}
    if category == "prefacios":
        return "preface", {}

    return category, {}


SANCTORALE_SCOPE = {
    "santos_brasil": "brazil",
    "santos_arg": "argentina",
    "santos_africa": "africa",
    "santos_fran": "france",
    "santos_obra": "religious-orders",
}


COMMON_VOTIVE_BASE = {
    "comunes_bmv": {"group": "common", "subgroup": "blessed-virgin-mary"},
    "comunes_ded": {"group": "common", "subgroup": "dedication-of-church"},
    "comunes_doct": {"group": "common", "subgroup": "doctors-of-the-church"},
    "comunes_mart": {"group": "common", "subgroup": "martyrs"},
    "comunes_past": {"group": "common", "subgroup": "pastors"},
    "comunes_sant": {"group": "common", "subgroup": "saints"},
    "comunes_virg": {"group": "common", "subgroup": "virgins"},
    "difuntos": {"group": "ritual", "subgroup": "for-the-dead"},
    "diversas": {"group": "ritual", "subgroup": "various-needs"},
    "votivas": {"group": "votive", "subgroup": "votive-masses"},
}


def canonical_id(category: str, basename: str, day_id: Optional[str]) -> str:
    """Build a clean canonical Mass ID."""
    if category == "tiempos":
        if day_id:
            parsed = parse_temporal_day_id(day_id)
            season = parsed.get("seasonGroup", "unknown")
            if season == "advent_christmas":
                season = split_advent_christmas(day_id)
            week = parsed.get("weekIndex")
            weekday = parsed.get("weekday")
            block = parsed.get("block")
            suf = parsed.get("suffix")
            parts = [f"tempore", season]
            code = parsed.get("code")
            if season == "ordinary-time" and week is not None:
                parts.append(f"week-{week}")
            elif season in ("advent", "lent", "easter") and week is not None:
                parts.append(f"week-{week}")
            elif season == "christmas" and not code:
                parts.append(f"day-{day_id[1:]}")
            if code:
                parts.append(code.lower())
            if weekday:
                parts.append(weekday)
            if suf:
                parts.append(suf.lower())
            return ".".join(parts)
        return f"tempore.{basename}"
    if category == "santos":
        scope_slug = (SANCTORALE_SCOPE.get(basename, "") or "").lower().replace(" ", "-")
        if day_id:
            d = parse_sanctorale_day_id(day_id)
            if d and "month" in d:
                # Date suffix (e.g. alternative observance "z") joins the
                # date segment with a hyphen so dots-as-slashes doesn't
                # produce single-letter sub-folders like sanctorale/07-20/z/.
                date_seg = f"{d['month']:02d}-{d['day']:02d}"
                if d.get("suffix"):
                    date_seg += d["suffix"].lower()
                base = f"sanctorale.{date_seg}"
                if scope_slug:
                    return f"{base}.{scope_slug}"
                return base
            if d and d.get("movableCode"):
                base = f"sanctorale.movable.{d['movableMonthAnchor']:02d}-{d['movableCode'][2:]}"
                if scope_slug:
                    return f"{base}.{scope_slug}"
                return base
            if d and d.get("undated"):
                base = f"sanctorale.undated.{d['ordinal']}".lower()
                if scope_slug:
                    return f"{base}.{scope_slug}"
                return base
            if scope_slug:
                return f"sanctorale.{scope_slug}.{day_id.lower()}"
            return f"sanctorale.{day_id.lower()}"
        return f"sanctorale.{basename}"
    if category == "comunes_votivas":
        meta = COMMON_VOTIVE_BASE.get(basename, {"group": "common", "subgroup": basename})
        if day_id:
            return f"{meta['group']}.{meta['subgroup']}.{day_id}"
        return f"{meta['group']}.{meta['subgroup']}"
    if category == "ordinario":
        return f"ordinary.{basename.replace('_', '-')}"
    if category == "plegarias_euc":
        return f"eucharistic-prayer.{basename.replace('plegaria_euc_', '').replace('_', '-')}"
    if category == "prefacios":
        return f"preface.{day_id}" if day_id else f"preface.{basename}"
    return f"{category}.{basename}.{day_id}" if day_id else f"{category}.{basename}"


# Localized rank names → canonical
RANK_KEYWORDS = {
    "solemnity": ["solemnitas", "sollemnitas", "solemnidad", "solemnity", "solenidade",
                  "solennità", "solennita", "solennité", "hochfest"],
    "feast": ["festum", "fiesta", "feast", "festa", "fête", "fest"],
    "memorial": ["memoria", "memorial", "memória", "mémoire", "gedenktag"],
    "optional-memorial": [
        "memoria ad libitum",
        "memoria libera",
        "memoria opcional",
        "optional memorial",
        "memória facultativa",
        "memoria facoltativa",
        "mémoire facultative",
        "nicht gebotener gedenktag",
    ],
}


def normalize_rank(rank_localized: dict[str, str]) -> Optional[str]:
    """Return the canonical rank slug given any localized rank."""
    # Check optional-memorial first (more specific)
    keys = ["optional-memorial", "solemnity", "feast", "memorial"]
    for canonical in keys:
        kws = RANK_KEYWORDS[canonical]
        for txt in rank_localized.values():
            low = txt.lower()
            for kw in kws:
                if kw in low:
                    return canonical
    return None


# ---------------------------------------------------------------------------
# Library builders
# ---------------------------------------------------------------------------


def build_prefaces() -> list[dict]:
    out: list[dict] = []
    for d_id in list_day_ids("prefacios", "prefacios"):
        d = load_v1("prefacios", "prefacios", d_id)
        if not d:
            continue
        title_loc: dict[str, str] = {}
        body_blocks: list[dict] = []
        for slot in d["slots"]:
            for it in slot.get("items", []):
                content = it.get("content") or {}
                # The first segment in any language with type heading level 2 is the title.
                for src, c in content.items():
                    if src not in LANG_MAP:
                        continue
                    iso = LANG_MAP[src]
                    for seg in c.get("segments") or []:
                        if seg.get("type") == "heading" and seg.get("level") == 2 and iso not in title_loc:
                            title_loc[iso] = clean_text(seg.get("text") or "")
                body_blocks.append(content)
        body = merge_blocks_to_rich_text(body_blocks)
        out.append(strip_empty({
            "id": f"preface.{d_id}",
            "ordinal": d_id,
            "title": title_loc,
            "body": body,
        }))
    return out


def build_eucharistic_prayers() -> list[dict]:
    out: list[dict] = []
    for base in list_basenames("plegarias_euc"):
        title_loc: dict[str, str] = {}
        body_blocks: list[dict] = []
        for d_id in list_day_ids("plegarias_euc", base):
            d = load_v1("plegarias_euc", base, d_id)
            if not d:
                continue
            for slot in d["slots"]:
                for it in slot.get("items", []):
                    content = it.get("content") or {}
                    for src, c in content.items():
                        if src not in LANG_MAP:
                            continue
                        iso = LANG_MAP[src]
                        for seg in c.get("segments") or []:
                            if seg.get("type") == "heading" and seg.get("level") == 2 and iso not in title_loc:
                                title_loc[iso] = clean_text(seg.get("text") or "")
                    body_blocks.append(content)
        body = merge_blocks_to_rich_text(body_blocks)
        # Drop language entries with stub content — < 30% of the longest body
        # (e.g. German 5-i…rec-ii sources are 1.4K stub files containing only
        # a Sanctus + memorial acclamation, not the actual prayer).
        if body:
            plain = body.get("plain") or {}
            lines_map = body.get("lines") or {}
            if plain:
                max_len = max((len(t or "") for t in plain.values()), default=0)
                if max_len >= 1500:
                    threshold = int(max_len * 0.3)
                    for iso in list(plain.keys()):
                        if len(plain[iso] or "") < threshold:
                            plain.pop(iso, None)
                            lines_map.pop(iso, None)
        out.append(strip_empty({
            "id": f"eucharistic-prayer.{base.replace('plegaria_euc_', '').replace('_', '-')}",
            "title": title_loc,
            "body": body,
        }))
    return out


_ORDINARY_PART_TITLES = {
    "ordinario": {
        "la": "ORDO MISSÆ",
        "es": "ORDINARIO DE LA MISA",
        "en": "ORDER OF MASS",
        "pt-BR": "ORDINÁRIO DA MISSA",
        "it": "ORDINARIO DELLA MESSA",
        "fr": "ORDINAIRE DE LA MESSE",
        "de": "ORDNUNG DER MESSE",
    },
    "bendiciones": {
        "la": "Benedictiones sollemnes",
        "es": "Bendiciones solemnes",
        "en": "Solemn Blessings",
        "pt-BR": "Bênçãos solenes",
        "it": "Benedizioni solenni",
        "fr": "Bénédictions solennelles",
        "de": "Feierliche Schlusssegen",
    },
    "oraciones_pueblo": {
        "la": "Orationes super populum",
        "es": "Oraciones sobre el pueblo",
        "en": "Prayers over the People",
        "pt-BR": "Orações sobre o povo",
        "it": "Orazioni sul popolo",
        "fr": "Prières sur le peuple",
        "de": "Gebete über das Volk",
    },
    "oracion_fieles": {
        "la": "Oratio universalis",
        "es": "Oración de los fieles",
        "en": "Universal Prayer (Prayer of the Faithful)",
        "pt-BR": "Oração universal",
        "it": "Preghiera universale (dei fedeli)",
        "fr": "Prière universelle",
        "de": "Allgemeines Gebet (Fürbitten)",
    },
}


def build_ordinary() -> list[dict]:
    out: list[dict] = []
    for base in list_basenames("ordinario"):
        body_blocks: list[dict] = []
        title_loc: dict[str, str] = {}
        for d_id in list_day_ids("ordinario", base):
            d = load_v1("ordinario", base, d_id)
            if not d:
                continue
            for slot in d["slots"]:
                for it in slot.get("items", []):
                    content = it.get("content") or {}
                    for src, c in content.items():
                        if src not in LANG_MAP:
                            continue
                        iso = LANG_MAP[src]
                        for seg in c.get("segments") or []:
                            if seg.get("type") == "heading" and seg.get("level") == 1 and iso not in title_loc:
                                title_loc[iso] = clean_text(seg.get("text") or "")
                    body_blocks.append(content)
        body = merge_blocks_to_rich_text(body_blocks)
        # Override with canonical localized titles where the source repeated the
        # Latin title across non-Latin languages (e.g. fr title was "ORDO MISSÆ").
        if base in _ORDINARY_PART_TITLES:
            canonical = _ORDINARY_PART_TITLES[base]
            for iso, canonical_t in canonical.items():
                src_t = title_loc.get(iso, "")
                # Replace if missing OR if src is the Latin title in a non-Latin slot.
                if not src_t or (iso != "la" and src_t == canonical.get("la", "ORDO MISSÆ")):
                    title_loc[iso] = canonical_t
        out.append(strip_empty({
            "id": f"ordinary.{base.replace('_', '-')}",
            "title": title_loc,
            "body": body,
        }))
    return out


# ---------------------------------------------------------------------------
# OT ferial synthesis
# ---------------------------------------------------------------------------


_OT_FERIAL_DAY_RE = re.compile(r"^O(\d{2})(\d)([A-Z]?)$")


def _title_from_lecturas_day(lec_day: dict) -> Optional[dict[str, str]]:
    """Extract a localized title from the first level-2 heading segment of the
    lecturas day's first slot, per language."""
    slots = lec_day.get("slots") or []
    if not slots:
        return None
    items = slots[0].get("items") or []
    if not items:
        return None
    content = items[0].get("content") or {}
    per_src: dict[str, str] = {}
    for src, c in content.items():
        for seg in c.get("segments") or []:
            if seg.get("type") == "heading":
                text = seg.get("text") or ""
                if text:
                    per_src[src] = text
                    break
    return localized(per_src) or None


def synthesize_ot_ferial_masses(lec_idx: dict[str, str]) -> list[dict]:
    """Emit mass shells for Ordinary Time ferials (Mon-Sat × 34 weeks).

    The Roman Missal has no proper Mass formulary per OT ferial day, so the
    tiempos source has nothing to extract. But the lecturas source has Year
    I + Year II readings per day. We emit one mass shell per lecturas day,
    populated with title + readings + season metadata. Prayer slots are
    intentionally absent (consumers fall back to the Sunday formulary).
    """
    out: list[dict] = []
    for day_id in sorted(lec_idx):
        if day_id.startswith("OT"):
            continue
        m = _OT_FERIAL_DAY_RE.match(day_id)
        if not m:
            continue
        wd_int = int(m.group(2))
        if not (1 <= wd_int <= 6):
            continue
        week = int(m.group(1))
        if not (1 <= week <= 34):
            continue
        weekday = WEEKDAY_NAMES[wd_int]
        base = lec_idx[day_id]
        lec_day = load_v1("lecturas", base, day_id)
        if not lec_day:
            continue
        mass: dict[str, Any] = {
            "id": f"tempore.ordinary-time.week-{week}.{weekday}",
            "group": "tempore",
            "season": "ordinary-time",
            "weekIndex": week,
            "weekday": weekday,
        }
        title_loc = _title_from_lecturas_day(lec_day)
        if title_loc:
            mass["title"] = title_loc
        readings = build_readings(lec_day)
        if readings:
            mass["readings"] = readings
        out.append(mass)
    return out


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def build_calendar(masses_by_group: dict[str, list[dict]]) -> dict[str, Any]:
    cal: dict[str, Any] = {"tempore": [], "sanctorale": []}
    for m in masses_by_group.get("tempore", []):
        entry: dict[str, Any] = {"id": m["id"]}
        if m.get("title"):
            entry["title"] = m["title"]
        for k in ("season", "weekIndex", "weekday"):
            if k in m:
                entry[k] = m[k]
        # Propagate rank from the underlying mass so the calendar can be
        # used for sorting/filtering without a second lookup.
        if m.get("rank"):
            entry["rank"] = m["rank"]
        if m.get("liturgicalColor"):
            entry["liturgicalColor"] = m["liturgicalColor"]
        cal["tempore"].append(entry)
    for m in masses_by_group.get("sanctorale", []):
        # Primary entry from the parent mass.
        primary = _build_sanctorale_calendar_entry(m["id"], m)
        cal["sanctorale"].append(primary)
        # Each alternative becomes its own calendar entry, id = parent.<key>.
        for alt in m.get("alternatives") or []:
            alt_entry = _build_sanctorale_calendar_entry(
                f"{m['id']}.{alt['key']}", alt,
                date=m.get("date"), scope=m.get("scope"),
            )
            cal["sanctorale"].append(alt_entry)
    return cal


def _build_sanctorale_calendar_entry(
    entry_id: str,
    src: dict,
    *,
    date: Optional[dict] = None,
    scope: Optional[str] = None,
) -> dict:
    """Project either a parent mass or an alternative into a calendar entry."""
    entry: dict[str, Any] = {"id": entry_id}
    if src.get("title"):
        entry["title"] = src["title"]
    if "date" in src:
        entry["date"] = src["date"]
    elif date is not None:
        entry["date"] = date
    if "scope" in src:
        entry["scope"] = src["scope"]
    elif scope is not None:
        entry["scope"] = scope
    if src.get("rank"):
        entry["rank"] = src["rank"]
    if src.get("liturgicalColor"):
        entry["liturgicalColor"] = src["liturgicalColor"]
    return entry


# ---------------------------------------------------------------------------
# Sanctorale catalog
# ---------------------------------------------------------------------------


def build_saints_catalog(sanctorale_masses: list[dict]) -> list[dict]:
    """A focused catalog of saints with date / name / rank / description.

    Excludes the prayer text — for the calendar/catalog use case.

    Saints in the General Roman Calendar without an explicit rank in the source
    default to `optional-memorial` (the lowest celebration class) — that's the
    convention when the missal lists a saint without a higher rank tag.
    Regional / placeholder entries without a title are skipped.

    When a mass has `alternatives`, each alternative whose title differs from
    the parent's becomes its own catalog entry (different saint, same date).
    Alternatives with the SAME title as the parent (All Souls' three
    formularies) collapse into a single catalog entry — they're the same
    celebration with multiple prayer choices, not separate saints.
    """
    catalog: list[dict] = []
    for m in sanctorale_masses:
        title = m.get("title") or {}
        description = m.get("description") or {}
        if title or description:
            catalog.append(_saint_entry_from_mass(m["id"], m))
        for alt in m.get("alternatives") or []:
            alt_title = alt.get("title") or {}
            if not alt_title:
                continue
            # Same celebration with a different prayer formulary → no new
            # saint entry. (All Souls.)
            if alt_title.get("la") == title.get("la"):
                continue
            entry_id = f"{m['id']}.{alt['key']}"
            entry = _saint_entry_from_mass(
                entry_id, alt,
                date=m.get("date"), scope=m.get("scope"),
            )
            catalog.append(entry)
    catalog.sort(key=lambda e: (e.get("date", {}).get("month", 13), e.get("date", {}).get("day", 32), e.get("scope", "")))
    return catalog


def _saint_entry_from_mass(
    entry_id: str,
    src: dict,
    *,
    date: Optional[dict] = None,
    scope: Optional[str] = None,
) -> dict:
    entry: dict[str, Any] = {"id": entry_id}
    if "date" in src:
        entry["date"] = src["date"]
    elif date is not None:
        entry["date"] = date
    if "scope" in src:
        entry["scope"] = src["scope"]
    elif scope is not None:
        entry["scope"] = scope
    for k in ("title", "rank", "rankLocalized", "description"):
        if k in src:
            entry[k] = src[k]
    if not entry.get("rank"):
        entry["rank"] = "optional-memorial"
    return entry


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def build_provenance(all_masses: list[dict], category_basename_dayid: list[tuple[str, str, Optional[str]]]) -> dict:
    out: dict[str, str] = {}
    for (cat, base, did), m in zip(category_basename_dayid, all_masses):
        if not m:
            continue
        out[m["id"]] = f"misal_v2/m_<lang>/{cat}/m_<lang>_{base}.html#{did or '_root'}"
    return out


# ---------------------------------------------------------------------------
# strip_empty
# ---------------------------------------------------------------------------


def strip_empty(d: Any) -> Any:
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            v = strip_empty(v)
            if v is None:
                continue
            if isinstance(v, (dict, list)) and not v:
                continue
            if isinstance(v, str):
                if not v.strip():
                    continue
                v = v
            out[k] = v
        return out or None
    if isinstance(d, list):
        out = [strip_empty(v) for v in d]
        out = [
            v for v in out
            if v is not None
            and not (isinstance(v, (dict, list)) and not v)
            and not (isinstance(v, str) and not v.strip())
        ]
        return out
    if isinstance(d, str):
        return d if d.strip() else None
    return d


# ---------------------------------------------------------------------------
# Post-processing cleanups
#
# These run on every payload right before write_json serializes it. They fix
# defects surfaced by the audit suite: trailing `..`, invisible unicode,
# Latin OCR scannos, missing terminal periods, title prefix pollution from
# the source's hierarchy headers, bare verse-number leaks in lines arrays,
# stranded Lectio labels, French rubric latin-leak, and rank gaps on
# tempore solemnities. Do NOT add aggressive content rewrites here — only
# defects that are unambiguously wrong in the rendered missal.
# ---------------------------------------------------------------------------

# Match trailing 2+ periods (with any preceding whitespace) so that
# `verehren wir ...` collapses to `verehren wir.` (no orphan space-period).
_DOUBLE_PERIOD_END_RE = re.compile(r'\s*\.{2,}$')
_INVISIBLE_CHARS_RE = re.compile(r'[­​‌‍﻿]')
# Comma immediately followed by an uppercase letter — bug pattern from the
# source where a space was lost. Numeric "5,1-2" is preserved because we
# require an alpha char after the comma.
_MISSING_SPACE_AFTER_COMMA_RE = re.compile(r',(?=[A-ZÀ-ÝÆŒ][a-zà-ÿæœ])')
# Source HTML indentation noise: newline followed by run of tabs/spaces.
# Collapses to a single space (matches HTML's whitespace semantics anyway).
_HTML_INDENT_NOISE_RE = re.compile(r'\n[\t ]+')
# Ogonek combining-diacritic OCR artifact: "Ke˛ty" → "Kęty". The ˛ (U+02DB)
# is a spacing version of the combining ogonek; the precomposed letter is
# what readers expect.
_OGONEK_FIXES = [
    ('e˛', 'ę'), ('a˛', 'ą'), ('i˛', 'į'), ('u˛', 'ų'),
    ('E˛', 'Ę'), ('A˛', 'Ą'), ('I˛', 'Į'), ('U˛', 'Ų'),
]
# Trailing double close-quote artifact: `».»` (with or without period after) →
# single closing quote with terminal period. From OCR/source where the
# closing guillemet was duplicated.
_TRAILING_DOUBLE_QUOTE_RE = re.compile(r'»\.»\.?$|»»\.?$')
# Doubled-word OCR scannos like "from from heaven", "venit venit saliens",
# "will will pull". Only collapses when the word is short (≤6 chars) and
# all-lowercase (avoids touching Eucharistic acclamations like
# "Sanctus, Sanctus, Sanctus" which are uppercase).
_DOUBLED_WORD_RE = re.compile(r'\b([a-zà-ÿæœ]{2,6})\s+\1\b(?!\s+\1\b)')
# Leading hyphen-dash artifact: "- Féries Lundi" / "-Il est..." — source
# HTML used a hyphen as a list marker that shouldn't appear in text. Match
# only ASCII `-`; preserve em-dash (—) and en-dash (–) which are legitimate.
_LEADING_HYPHEN_RE = re.compile(r'^-\s*(?=[A-Za-zÀ-ÿ])')

# Latin OCR scannos: where the source PDF/HTML mis-read æ/ǽ ligatures.
# Only applied to la-tagged strings to avoid touching legitimate vernacular
# words that happen to share a substring.
_LA_OCR_FIXES = [
    (re.compile(r'\bvitre\b', re.I), 'vitæ'),
    (re.compile(r'\bQuœsumus\b'), 'Quǽsumus'),
    (re.compile(r'\bquœsumus\b'), 'quǽsumus'),
    (re.compile(r'\bsœculi\b', re.I), 'sǽculi'),
    (re.compile(r'\bsœculo\b', re.I), 'sǽculo'),
    (re.compile(r'\bsœcula\b', re.I), 'sǽcula'),
    (re.compile(r'\bPrœsta\b'), 'Præsta'),
    (re.compile(r'\bprœsta\b'), 'præsta'),
    (re.compile(r'\bPrœstantíssimum\b'), 'Præstantíssimum'),
    (re.compile(r'\bprœstantíssimum\b'), 'præstantíssimum'),
    (re.compile(r'\bprœces\b', re.I), 'preces'),
    (re.compile(r'\btuœ\b'), 'tuæ'),
    (re.compile(r'\bsuœ\b'), 'suæ'),
    (re.compile(r'\bmeœ\b'), 'meæ'),
    # Triple-f scanno (audit cycle 23):
    (re.compile(r'\bdifffícile\b'), 'diffícile'),
    # Cycle 24 — single-occurrence OCR scannos.
    # `1Omaii` is OCR'd `10 Maii` (May 10) — digit-zero glued to capital-M.
    (re.compile(r'\b1Omaii\b'), '10 Maii'),
    # `gratìs` (grave accent) — Latin uses acute only. The word is `grátis`
    # (adverb, "freely"); the grave is an OCR misread of the acute.
    (re.compile(r'\bgratìs\b'), 'grátis'),
]


# Cycle 23 — Latin diacritic word-list. Restores diacritic-marked forms in
# Latin liturgical words that were OCR'd as plain ASCII. Operates only when
# `lang == 'la'`. Each entry: (unaccented_form, accented_form). Word boundaries
# matched via `\b...\b`. Already-accented forms don't match the ASCII pattern,
# making the table idempotent.
_LA_DIACRITIC_WORDS = [
    # Dominus paradigm (most common)
    ('Dominus', 'Dóminus'), ('Dominum', 'Dóminum'), ('Domine', 'Dómine'),
    ('Domino', 'Dómino'), ('Domini', 'Dómini'),
    # Filius paradigm (avoid "filium" mid-word — careful boundary)
    ('Filius', 'Fílius'), ('Filium', 'Fílium'), ('Filii', 'Fílii'),
    ('Filio', 'Fílio'),
    # Spiritus
    ('Spiritus', 'Spíritus'), ('Spiritum', 'Spíritum'),
    ('Spiritu', 'Spíritu'), ('Spiritui', 'Spirítui'),
    # Ecclesia
    ('Ecclesia', 'Ecclésia'), ('Ecclesiam', 'Ecclésiam'),
    ('Ecclesiæ', 'Ecclésiæ'), ('Ecclesiae', 'Ecclésiæ'),
    # Gloria (both cases — title-line and mid-sentence)
    ('Gloria', 'Glória'), ('gloria', 'glória'),
    ('Gloriam', 'Glóriam'), ('gloriam', 'glóriam'),
    # Other high-frequency
    ('anima', 'ánima'), ('animam', 'ánimam'), ('animæ', 'ánimæ'),
    ('populi', 'pópuli'), ('populus', 'pópulus'), ('populum', 'pópulum'),
    ('tempus', 'témpus'), ('tempore', 'témpore'),
    ('gratia', 'grátia'), ('gratiam', 'grátiam'), ('gratiæ', 'grátiæ'),
    ('caritas', 'cáritas'), ('caritate', 'caritáte'), ('caritatem', 'caritátem'),
    ('benedictio', 'benedíctio'), ('benedictus', 'benedíctus'),
    ('mysterium', 'mystérium'), ('mysteria', 'mystéria'),
    ('apostoli', 'apóstoli'), ('apostolus', 'apóstolus'),
    ('apostolorum', 'apostolórum'), ('apostolus', 'apóstolus'),
    ('misericordia', 'misericórdia'), ('misericordiam', 'misericórdiam'),
    ('omnipotens', 'omnípotens'), ('omnipotentem', 'omnipoténtem'),
    # ae→æ ligature stragglers in Latin context (high-confidence whole-word)
    ('quaesumus', 'quǽsumus'), ('Quaesumus', 'Quǽsumus'),
    ('praesta', 'præsta'), ('Praesta', 'Præsta'),
    ('aeternam', 'ætérnam'), ('aeterna', 'ætérna'),
    ('aeternum', 'ætérnum'), ('aeternus', 'ætérnus'),
    ('beatae', 'beátæ'), ('Beatae', 'Beátæ'),
    ('Mariae', 'Maríæ'), ('mariae', 'maríæ'),
    ('sanctae', 'sánctæ'), ('Sanctae', 'Sánctæ'),
    # Cycle 24 — caelum forms. Cross-checked against the 2002 Missale Romanum
    # Pater Noster ("qui es in cælis", "sicut in cælo"). Corpus already has
    # 1500+ ligated occurrences vs ~22 plain holdouts.
    ('caeli', 'cæli'), ('caelis', 'cælis'), ('caelo', 'cælo'),
    ('caelum', 'cælum'), ('caelos', 'cælos'),
    ('caelórum', 'cælórum'), ('caelorum', 'cælórum'),
    ('caeléstis', 'cæléstis'), ('caelestis', 'cæléstis'),
    ('Caelestis', 'Cæléstis'), ('Caeléstis', 'Cæléstis'),
    # Cycle 25 — high-frequency liturgical words missing accents.
    # All cross-checked against the 2002 Missale Romanum text (cf. pages
    # 14, 26 for "per ómnia sǽcula"; page 9 for "in nómine Dómini";
    # page 6 for "óperis"; standard Latin antepenult-stress orthography).
    ('omnia', 'ómnia'), ('omnium', 'ómnium'), ('omnibus', 'ómnibus'),
    ('nomine', 'nómine'), ('nominis', 'nóminis'),
    ('opera', 'ópera'),
    ('gentibus', 'géntibus'), ('gentium', 'géntium'),
    # ae → æ ligature + accent for sǽculum forms (when the OCR flattened
    # both at once: `saecula` → `sǽcula`). Equivalent ligated-only forms
    # `sǽcula/sǽculi/sǽculo/sǽculórum` already dominate the corpus.
    ('saecula', 'sǽcula'), ('saeculi', 'sǽculi'),
    ('saeculo', 'sǽculo'), ('saeculorum', 'sæculórum'),
    ('saeculórum', 'sæculórum'),
    # Cycle 27 additions: corpus-dominant accented forms.
    ('fidelium', 'fidélium'), ('fidelibus', 'fidélibus'),
    ('orationem', 'oratiónem'), ('orationes', 'oratiónes'),
    ('orationis', 'oratiónis'), ('oratione', 'oratióne'),
    # Cycle 29: more ae→æ ligature stragglers (corpus-dominant ratio ≥40:1).
    # Cross-checked: `quae` (relative pronoun fem. sg/pl), `tuae` (gen),
    # `terrae`, `meae`, `vitae` — all canonically ligated in modern missals.
    ('quae', 'quæ'),
    ('tuae', 'tuæ'),  # already in list as direct OCR fix; redundant entries are
                       # safe (dict.update keeps last) but listed here for the
                       # corpus-survey-driven justification trail.
    ('terrae', 'terræ'),
    ('meae', 'meæ'),
    ('vitae', 'vitæ'),
    ('haec', 'hæc'),
    # Cycle 34: more accent-dominant pairs from audit (ratio >=10:1).
    # Mostly responsorial-psalm response stragglers next to accented verse text.
    ('hominis', 'hóminis'), ('Hominis', 'Hóminis'),
    ('eorum', 'eórum'),
    ('Ierusalem', 'Ierúsalem'),
    ('quoniam', 'quóniam'), ('Quoniam', 'Quóniam'),
    ('secundum', 'secúndum'), ('Secundum', 'Secúndum'),
    ('faciem', 'fáciem'),
    ('medio', 'médio'),
    ('facere', 'fácere'), ('Facere', 'Fácere'),
    ('mortuis', 'mórtuis'),
    ('filium', 'fílium'), ('Filium', 'Fílium'),
    ('faciet', 'fáciet'), ('Faciet', 'Fáciet'),
    ('Beatus', 'Beátus'), ('beatus', 'beátus'),
    ('Benedictus', 'Benedíctus'), ('benedictus', 'benedíctus'),
]

_LA_DIACRITIC_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w, _ in _LA_DIACRITIC_WORDS) + r')\b'
)
_LA_DIACRITIC_MAP = dict(_LA_DIACRITIC_WORDS)


def _fix_la_diacritics(text: str, lang: str) -> str:
    if lang != 'la' or not isinstance(text, str) or not text:
        return text
    return _LA_DIACRITIC_RE.sub(lambda m: _LA_DIACRITIC_MAP[m.group(1)], text)

# Title-prefix pollution from document hierarchy headers. Match-only patterns
# (no captures); we strip the matched prefix and any trailing whitespace.
_TITLE_PREFIX_POLLUTION = [
    re.compile(r'^Hebdomada Sancta\s+', re.I),
    re.compile(r'^IN SOLLEMNITATIBUS DOMINI\s+«[^»]*»\s+OCCURRENTIBUS\s+', re.I),
    re.compile(r'^IN SOLLEMNITATIBUS DOMINI\s+OCCURRENTIBUS\s+', re.I),
    re.compile(r'^SACRUM TRIDUUM PASCHALE\s+', re.I),
    re.compile(r'^SAGRADO TR[IÍ]DUO PASCAL\s+', re.I),
    re.compile(r'^SAGRADO\s+TRÍDUO\s+PASCAL\s+', re.I),
    re.compile(r'^Tempus\s+(?:Paschale|Adventus|Quadragesim(?:æ|ae|e)|«?\s*per\s+annum\s*»?|Nativitatis)\s+', re.I),
    re.compile(r'^Tempo\s+(?:Pascal|do\s+Advento|da\s+Quaresma|do\s+Natal|Comum)\s+', re.I),
    re.compile(r'^TEMPO\s+(?:DI|DELLA|DEL|PASQUALE)\s+', re.I),
    re.compile(r'^TIEMPO\s+(?:DE|DEL|PASCUAL|ORDINARIO)\s+', re.I),
    # Longer alternatives first so 'de l'Avent' isn't shadowed by bare 'DE'
    re.compile(r"^TEMPS\s+(?:de\s+l'Avent|de\s+Carême|de\s+Noël|ordinaire|PASCAL|DES|DU|DE)\s+", re.I),
    re.compile(r'^OSTERZEIT\s+', re.I),
    re.compile(r'^ADVENTSZEIT\s+', re.I),
    re.compile(r'^FASTENZEIT\s+', re.I),
    re.compile(r'^WEIHNACHTSZEIT\s+', re.I),
    # Holy Family date-rubric prefix in many langs:
    re.compile(r'^DOMINICA\s+infra\s+octavam\s+Nativitatis[^.]*?(?:decembris|deficiente)\s+', re.I),
    re.compile(r"^Domingo\s+(?:dentro\s+de\s+la\s+octava|na\s+oitava)\s+d[eo]\s+(?:Natal|Navidad).*?(?:diciembre|dezembro)\)?\s+(?=[A-ZÀ-Ý])", re.I),
    re.compile(r"^Domenica\s+fra\s+l'ottava\s+di\s+Natale.*?dicembre\s+(?=[A-ZÀ-Ý])", re.I),
    # Holy Family French: strip the date-rubric. NOT re.I (the lookahead
    # `[A-ZÀ-Ý]` would otherwise match lowercase). Two variants:
    re.compile(r"^Dimanche\s+dans\s+l'Octave\s+de\s+la\s+Nativité\s+ou\s+\d+\s+décembre\s+en\s+l'absence\s+de\s+ce\s+dimanche\s+(?=[A-ZÀ-Ý])"),
    re.compile(r"^Dimanche\s+dans\s+l'Octave\s+de\s+la\s+Nativité\s+ou\s+\d+\s+décembre\s+(?=[A-ZÀ-Ý])"),
    re.compile(r"^Dimanche\s+dans\s+l'Octave\s+de\s+la\s+Nativité\s+(?=[A-ZÀ-Ý])"),
    re.compile(r'^SONNTAG\s+in\s+der\s+Weihnachtsoktav.*?Dezember\.\s+(?=[A-ZÄÖÜ])', re.I),
    # Mary, Mother of God (Jan 1) — Octave-Day-of-Nativity rubric prefix:
    re.compile(r'^In\s+octava\s+Nativitatis\s+Domini\s+(?=[A-ZÆŒ])', re.I),
    re.compile(r'^The\s+Octave\s+Day\s+of\s+the\s+Nativity\s+of\s+the\s+Lord\s+\[Christmas\]\s+(?=[A-Z])', re.I),
    re.compile(r'^1[ºo°]?\s+de\s+janeiro\s+Oitava\s+do\s+Natal\s+do\s+Senhor\s+(?=[A-ZÀ-Ý])', re.I),
    re.compile(r"^Nell'ottava\s+di\s+Natale\s+(?=[A-ZÀ-Ý])", re.I),
    # German Christmas weekday section-headers glued to per-day titles:
    re.compile(r'^DIE\s+WOCHENTAGE\s+VOM\s+\d+\.\s+BIS\s+\d+\.\s+DEZEMBER\s+(?=\d)', re.I),
    re.compile(r'^AN\s+DEN\s+WOCHENTAGEN\s+DER\s+WEIHNACHTSZEIT\s+vom\s+\d+\.\s+Januar\s+bis\s+zum\s+Samstag\s+vor\s+dem\s+Fest\s+der\s+Taufe\s+Jesu\s+(?=[A-Z])', re.I),
    # Solemnity-of-the-Lord section headers in vernacular missals.
    # NOT case-insensitive: the lookahead detects where the actual title
    # (a CAPS run of 4+ chars) starts after the (optional) date-rubric phrase.
    re.compile(r'^SOLEMNIDADES\s+DEL\s+SEÑOR\s+DURANTE\s+EL\s+TIEMPO\s+ORDINARIO\s+.*?(?=[A-ZÀ-Ý]{4,})'),
    re.compile(r'^SOLEMNIDADES\s+DEL\s+SEÑOR\s+DURANTE\s+EL\s+TIEMPO\s+ORDINARIO\s+(?=[A-ZÀ-Ý]{4,})'),
    re.compile(r'^SOLENNITÀ\s+DEL\s+SIGNORE\s+NEL\s+TEMPO\s+ORDINARIO\s+.*?(?=[A-ZÀ-Ý]{4,})'),
    re.compile(r'^SOLENNITÀ\s+DEL\s+SIGNORE\s+NEL\s+TEMPO\s+ORDINARIO\s+(?=[A-ZÀ-Ý]{4,})'),
    re.compile(r'^HERRENFESTE\s+IM\s+JAHRESKREIS\s+.*?(?=[A-ZÄÖÜ]{4,})'),
    re.compile(r'^HERRENFESTE\s+IM\s+JAHRESKREIS\s+(?=[A-ZÄÖÜ]{4,})'),
    # Italian "Christmas weekdays" section header:
    re.compile(r'^FERIE\s+DEL\s+TEMPO\s+DI\s+NATALE\s+', re.I),
    # Italian variant with apostrophe instead of grave accent:
    re.compile(r"^SOLENNITA'\s+DEL\s+SIGNORE\s+NEL\s+TEMPO\s+ORDINARIO\s+.*?(?=[A-ZÀ-Ý]{4,})"),
    re.compile(r"^SOLENNITA'\s+DEL\s+SIGNORE\s+NEL\s+TEMPO\s+ORDINARIO\s+(?=[A-ZÀ-Ý]{4,})"),
    # pt-BR section headers (with or without intermediate date-rubric phrase):
    re.compile(r'^SOLENIDADES\s+DO\s+SENHOR\s+NO\s+TEMPO\s+COMUM\s+.*?(?=[A-ZÀ-Ý]{4,})'),
    re.compile(r'^SOLENIDADES\s+DO\s+SENHOR\s+NO\s+TEMPO\s+COMUM\s+(?=[A-ZÀ-Ý]{4,})'),
    re.compile(r'^Dias\s+de\s+Semana\s+do\s+Tempo\s+do\s+Natal\s+', re.I),
    # English parenthetical date prefix (e.g. US Day of Prayer for the Unborn):
    re.compile(r'^\([\*\s]*[A-Z][a-z]+\s+\d+[^)]*\)\s+', re.I),
    # Spanish:
    re.compile(r'^FERIAS\s+DEL\s+TIEMPO\s+DE\s+NAVIDAD\s+', re.I),
    # English:
    re.compile(r'^Weekdays\s+of\s+Christmas\s+Time\s+(?:from\s+\w+\s+\d+\s+)?(?=[A-Z][a-z])', re.I),
    # Latin date-rubric prefixes for Lord's solemnities in Ordinary Time:
    re.compile(r'^Feria\s+[IVX]+\s+post\s+(?:Ss\.\s*mam\s+Trinitatem|Dominicam\s+[IVX]+\s+post\s+Pentecosten|Pentecosten)\s+', re.I),
    # Latin late-Advent (Dec 17-24) section header:
    re.compile(r'^IN\s+FERIIS\s+ADVENTUS\s+a\s+Die\s+\d+\s+ad\s+diem\s+\d+\s+decembris\s+', re.I),
    # Latin Christmas-octave weekdays section header:
    re.compile(r'^IN\s+FERIIS\s+TEMPORIS\s+NATIVITATIS\s+', re.I),
    # Late-Advent (Dec 17-24) section headers in vernacular missals:
    re.compile(r'^Weekdays\s+of\s+Advent\s+December\s+\d+\s+to\s+December\s+\d+\s+', re.I),
    re.compile(r'^FERIAS\s+DE\s+ADVIENTO\s+desde\s+el\s+\d+\s+al\s+\d+\s+de\s+diciembre\s+', re.I),
    re.compile(r'^FERIE\s+DI\s+AVVENTO\s+dal\s+\d+\s+al\s+\d+\s+dicembre\s+', re.I),
    re.compile(r'^PARA\s+OS\s+DIAS\s+DE\s+SEMANA\s+DO\s+ADVENTO\s+de\s+\d+\s+a\s+\d+\s+de\s+dezembro\s+', re.I),
    re.compile(r'^IN\s+FERIIS\s+ADVENTUS\s+Du\s+\d+\s+au\s+\d+\s+décembre\s+', re.I),
    # German equivalent (placeholder — German source uses different format):
    re.compile(r'^WERKTAGE\s+IM\s+ADVENT\s+vom\s+\d+\.\s+bis\s+\d+\.\s+Dezember\s+', re.I),
    # French date prefixes on saint titles (e.g. "1er octobre Sainte Thérèse..."):
    re.compile(r'^\d+(?:er)?\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+', re.I),
]

# Trailing pollution patterns at end of prayer bodies — section-header
# fragments that bled across boundaries.
_TRAILING_POLLUTION_RE = re.compile(
    r'\.\s+(?:[IVX]+\s+(?:settimana|semana|domingo|s[ée]ttimana|secci[oó]n))\s.*$',
    re.I,
)
_LEAKED_RUBRIC_AT_END_RE = re.compile(
    r'\.\s+(?:Si\s+ha\s+de\s+decir\s+misa|si\s+celebra|se\s+utilizan|consegna\s+l)[^.]*$',
    re.I,
)


def _scrub_string(s: str, lang: Optional[str]) -> str:
    if not isinstance(s, str) or not s:
        return s
    s = _INVISIBLE_CHARS_RE.sub('', s)
    # Apply liturgical-character substitution AFTER invisible-char strip so
    # source artifacts like "R/﻿." (BOM hiding inside the marker) are
    # converted too.
    s = _liturgical_markers(s)
    s = _HTML_INDENT_NOISE_RE.sub(' ', s)
    s = _LEADING_HYPHEN_RE.sub('', s)
    for ogonek_pair, replacement in _OGONEK_FIXES:
        if ogonek_pair in s:
            s = s.replace(ogonek_pair, replacement)
    if _DOUBLE_PERIOD_END_RE.search(s):
        s = _DOUBLE_PERIOD_END_RE.sub('.', s)
    s = _TRAILING_DOUBLE_QUOTE_RE.sub('».', s)
    s = _MISSING_SPACE_AFTER_COMMA_RE.sub(', ', s)
    s = _DOUBLED_WORD_RE.sub(r'\1', s)
    s = _balance_parens(s)
    if lang == 'la':
        for pat, rep in _LA_OCR_FIXES:
            s = pat.sub(rep, s)
        s = _fix_la_diacritics(s, 'la')
    return s


def _balance_parens(s: str) -> str:
    """Conservative paren cleanup for prose strings:
    - Collapse `( (` and `) )` (duplicated brackets from parser artifacts)
    - Strip `()` empty pairs
    - Strip orphan trailing `)` at end of string when there's no opener
    - Strip orphan leading `(` at start when there's no closer
    Does NOT rewrite mid-string brackets that look balanced overall."""
    if not isinstance(s, str) or '(' not in s and ')' not in s:
        return s
    # Collapse duplicates
    s = re.sub(r'\(\s+\(', '(', s)
    s = re.sub(r'\)\s+\)', ')', s)
    # Strip empty parens (with optional whitespace)
    s = re.sub(r'\(\s*\)', '', s)
    # Trim doubled spaces created by the strip
    s = re.sub(r'\s{2,}', ' ', s)
    # Net imbalance handling — only at the boundaries
    opens = s.count('(')
    closes = s.count(')')
    if opens > closes:
        # Try to remove trailing/leading orphan `(`
        # If there's an orphan `(` not followed by content+`)`, drop it
        # (only safe when count diff is small)
        diff = opens - closes
        # Drop the rightmost `(` that has no `)` after it
        while diff > 0:
            # find last `(` with no `)` after it
            idx = s.rfind('(')
            if idx == -1: break
            if ')' not in s[idx:]:
                s = (s[:idx] + s[idx+1:]).strip()
                # Clean up double spaces
                s = re.sub(r'\s{2,}', ' ', s)
                diff -= 1
            else:
                # Lone `(` with `)` after — try to find an unbalanced one
                # Walk left-to-right tracking depth
                depth = 0
                last_unmatched_open = -1
                for i, ch in enumerate(s):
                    if ch == '(':
                        depth += 1
                        last_unmatched_open = i
                    elif ch == ')':
                        depth -= 1
                        if depth >= 0:
                            last_unmatched_open = -1  # reset since they balance
                if last_unmatched_open >= 0:
                    s = (s[:last_unmatched_open] + s[last_unmatched_open+1:]).strip()
                    s = re.sub(r'\s{2,}', ' ', s)
                    diff -= 1
                else:
                    break
    elif closes > opens:
        diff = closes - opens
        while diff > 0:
            # Drop the leftmost orphan `)` (one with no `(` before it)
            depth = 0
            removed = False
            for i, ch in enumerate(s):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    if depth == 0:
                        s = (s[:i] + s[i+1:]).strip()
                        s = re.sub(r'\s{2,}', ' ', s)
                        diff -= 1
                        removed = True
                        break
                    depth -= 1
            if not removed:
                break
    return s


_TITLE_TRAILING_RUBRIC = [
    # Italian Christmas-weekday rubric trailing the day name:
    # "lunedi Dal 2 gennaio fino alla vigilia della solennità dell'Epifania del Signore"
    re.compile(r"^((?:lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica))\s+Dal\s+\d+\s+\w+\s+fino\s+alla\s+vigilia.*$", re.I),
]


def _strip_title_pollution(title: str) -> str:
    if not isinstance(title, str) or not title:
        return title
    for _ in range(3):  # iterate in case multiple prefixes stack
        before = title
        for pat in _TITLE_PREFIX_POLLUTION:
            title = pat.sub('', title)
        if title == before:
            break
    for pat in _TITLE_TRAILING_RUBRIC:
        title = pat.sub(r'\1', title)
    return title.strip()


def _ensure_terminal_period(s: str) -> str:
    if not isinstance(s, str):
        return s
    stripped = s.rstrip()
    if len(stripped) < 20:
        return s
    last = stripped[-1]
    if last.isalpha():
        return stripped + '.'
    return s


def _strip_trailing_pollution(s: str) -> str:
    if not isinstance(s, str) or len(s) < 30:
        return s
    s = _TRAILING_POLLUTION_RE.sub('.', s)
    s = _LEAKED_RUBRIC_AT_END_RE.sub('.', s)
    return s


def _scrub_tree(node: Any, lang_hint: Optional[str] = None) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            new_lang = k if k in ISO_LANGS else lang_hint
            out[k] = _scrub_tree(v, new_lang)
        return out
    if isinstance(node, list):
        return [_scrub_tree(v, lang_hint) for v in node]
    if isinstance(node, str):
        return _scrub_string(node, lang_hint)
    return node


_PRAYER_BODY_SLOTS = (
    'collect', 'prayerOverOfferings', 'postcommunion', 'prayerOverPeople',
    'entranceAntiphon', 'communionAntiphon', 'preface',
)


def _fix_prayer_terminations(mass: dict) -> None:
    """For each prayer body slot, strip trailing pollution then ensure a
    terminal period."""
    for slot in _PRAYER_BODY_SLOTS:
        p = mass.get(slot)
        if not isinstance(p, dict):
            continue
        body = p.get('body')
        if not isinstance(body, dict):
            continue
        plain = body.get('plain')
        if isinstance(plain, dict):
            for L, v in list(plain.items()):
                if isinstance(v, str):
                    v = _strip_trailing_pollution(v)
                    v = _ensure_terminal_period(v)
                    plain[L] = v
        lines = body.get('lines')
        if isinstance(lines, dict):
            for L, langlines in lines.items():
                if not isinstance(langlines, list) or not langlines:
                    continue
                for i in range(len(langlines) - 1, -1, -1):
                    line = langlines[i]
                    if isinstance(line, list) and line:
                        last_seg = line[-1]
                        if isinstance(last_seg, dict) and isinstance(last_seg.get('text'), str):
                            t = _strip_trailing_pollution(last_seg['text'])
                            t = _ensure_terminal_period(t)
                            last_seg['text'] = t
                        break


def _strip_bare_number_segments(mass: dict) -> None:
    """Remove text segments inside body.lines that are just bare digits —
    these are page/verse markers that leaked into the prayer text stream.
    Also drops lines that become empty as a result, so the schema's
    non-empty-list invariant for line entries holds. Cycle 35 also strips
    bare-digit `plain.<L>` orphans (one-character section numbers that
    became the only content for a language while siblings carry full
    paragraphs)."""
    # Bare digit optionally trailed by 1-3 dots — covers verse markers ("21"),
    # source-PDF paragraph numbers ("15."), and OCR'd doubled periods ("19..").
    bare_digits = re.compile(r'^\d{1,4}\.{0,3}$')
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == 'lines' and isinstance(v, dict):
                    for L, langlines in v.items():
                        if not isinstance(langlines, list):
                            continue
                        for line in langlines:
                            if not isinstance(line, list):
                                continue
                            line[:] = [seg for seg in line
                                       if not (isinstance(seg, dict)
                                               and seg.get('type') == 'text'
                                               and isinstance(seg.get('text'), str)
                                               and bare_digits.match(seg['text'].strip()))]
                        v[L] = [line for line in langlines
                                if isinstance(line, list) and line]
                elif k == 'plain' and isinstance(v, dict):
                    # Cycle 35: drop plain.<L> entries that are just a bare
                    # digit (section-number residue, no real content).
                    for L in list(v.keys()):
                        val = v[L]
                        if isinstance(val, str) and bare_digits.match(val.strip()):
                            v.pop(L, None)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(mass)


# ---------------------------------------------------------------------------
# Reading-citation enrichment from `introduction` field
# ---------------------------------------------------------------------------
# Bare verse refs like "23, 8-12" gain the book abbreviation by parsing the
# Latin lectionary intro ("✠ Léctio sancti Evangélii secúndum Matthǽum").
# Map keys are normalized (NFD-stripped, lowercased) so accent marks don't
# break matching.
import unicodedata

def _norm(s: str) -> str:
    s = unicodedata.normalize('NFD', s)
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()


# Latin abbreviation table — book-name token (normalized) → abbreviation.
# Multi-word names match by ordered substring. Order matters: longer/more-
# specific patterns first to avoid mid-match shadowing.
_LA_BOOK_ABBREVS_ORDERED: list[tuple[str, str]] = [
    # Gospels (must come before generic "evangelii") + Passion narratives:
    ('evangelii secundum matthaeum', 'Mt'),
    ('evangelii secundum matthæum', 'Mt'),
    ('evangelii secundum marcum', 'Mc'),
    ('evangelii secundum lucam', 'Lc'),
    ('evangelii secundum ioannem', 'Io'),
    ('passio domini nostri iesu christi secundum matthaeum', 'Mt'),
    ('passio domini nostri iesu christi secundum matthæum', 'Mt'),
    ('passio domini nostri iesu christi secundum marcum', 'Mc'),
    ('passio domini nostri iesu christi secundum lucam', 'Lc'),
    ('passio domini nostri iesu christi secundum ioannem', 'Io'),
    ('secundum matthaeum', 'Mt'),
    ('secundum matthæum', 'Mt'),
    ('secundum marcum', 'Mc'),
    ('secundum lucam', 'Lc'),
    ('secundum ioannem', 'Io'),
    # Acts:
    ('actuum apostolorum', 'Act'),
    # Pauline epistles (longest first to avoid bare "ad" matches):
    ('primae beati pauli apostoli ad corinthios', '1 Cor'),
    ('primæ beati pauli apostoli ad corinthios', '1 Cor'),
    ('primae beati pauli apostoli ad thessalonicenses', '1 Thess'),
    ('primæ beati pauli apostoli ad thessalonicenses', '1 Thess'),
    ('primae beati pauli apostoli ad timotheum', '1 Tim'),
    ('primæ beati pauli apostoli ad timotheum', '1 Tim'),
    ('secundae beati pauli apostoli ad corinthios', '2 Cor'),
    ('secundæ beati pauli apostoli ad corinthios', '2 Cor'),
    ('secundae beati pauli apostoli ad thessalonicenses', '2 Thess'),
    ('secundæ beati pauli apostoli ad thessalonicenses', '2 Thess'),
    ('secundae beati pauli apostoli ad timotheum', '2 Tim'),
    ('secundæ beati pauli apostoli ad timotheum', '2 Tim'),
    ('beati pauli apostoli ad romanos', 'Rom'),
    ('beati pauli apostoli ad galatas', 'Gal'),
    ('beati pauli apostoli ad ephesios', 'Eph'),
    ('beati pauli apostoli ad philippenses', 'Phil'),
    ('beati pauli apostoli ad colossenses', 'Col'),
    ('beati pauli apostoli ad titum', 'Tit'),
    ('beati pauli apostoli ad philemonem', 'Phlm'),
    ('beati pauli apostoli ad thessalonicenses', 'Thess'),
    ('beati pauli apostoli ad timotheum', 'Tim'),
    ('beati pauli apostoli ad corinthios', 'Cor'),
    ('beati pauli apostoli ad hebraeos', 'Heb'),
    ('beati pauli apostoli ad hebræos', 'Heb'),
    ('ad romanos', 'Rom'),
    ('ad galatas', 'Gal'),
    ('ad ephesios', 'Eph'),
    ('ad philippenses', 'Phil'),
    ('ad colossenses', 'Col'),
    ('ad titum', 'Tit'),
    ('ad philemonem', 'Phlm'),
    ('ad hebraeos', 'Heb'),
    ('ad hebræos', 'Heb'),
    # Catholic / general epistles:
    ('primae beati ioannis apostoli', '1 Io'),
    ('primæ beati ioannis apostoli', '1 Io'),
    ('secundae beati ioannis apostoli', '2 Io'),
    ('secundæ beati ioannis apostoli', '2 Io'),
    ('tertiae beati ioannis apostoli', '3 Io'),
    ('tertiæ beati ioannis apostoli', '3 Io'),
    ('beati ioannis apostoli', 'Io'),
    ('primae beati petri apostoli', '1 Pe'),
    ('primæ beati petri apostoli', '1 Pe'),
    ('secundae beati petri apostoli', '2 Pe'),
    ('secundæ beati petri apostoli', '2 Pe'),
    ('beati petri apostoli', '1 Pe'),
    ('beati iacobi apostoli', 'Iac'),
    ('beati iudae apostoli', 'Iud'),
    ('beati iudæ apostoli', 'Iud'),
    # Apocalypse:
    ('apocalypsis beati ioannis apostoli', 'Apoc'),
    ('apocalypsis', 'Apoc'),
    # OT books — historical:
    ('libri primi paralipomenon', '1 Par'),
    ('libri primi paralipómenon', '1 Par'),
    ('libri secundi paralipomenon', '2 Par'),
    ('libri secundi paralipómenon', '2 Par'),
    ('libri primi samuelis', '1 Sam'),
    ('libri secundi samuelis', '2 Sam'),
    ('libri primi regum', '1 Reg'),
    ('libri secundi regum', '2 Reg'),
    ('libri primi machabaeorum', '1 Mac'),
    ('libri primi machabæorum', '1 Mac'),
    ('libri secundi machabaeorum', '2 Mac'),
    ('libri secundi machabæorum', '2 Mac'),
    ('libri esdrae', 'Esd'),
    ('libri esdræ', 'Esd'),
    ('libri nehemiae', 'Neh'),
    ('libri nehemiæ', 'Neh'),
    ('libri tobiae', 'Tb'),
    ('libri tobiæ', 'Tb'),
    ('libri iudith', 'Idt'),
    ('libri esther', 'Est'),
    ('libri iob', 'Iob'),
    ('libri ruth', 'Rt'),
    ('libri iosue', 'Ios'),
    ('libri iudicum', 'Iud'),
    # OT books — pentateuch:
    ('libri genesis', 'Gn'),
    ('libri exodi', 'Ex'),
    ('libri levitici', 'Lv'),
    ('libri numerorum', 'Num'),
    ('libri numerórum', 'Num'),
    ('libri deuteronomii', 'Dt'),
    ('libri deuteronómii', 'Dt'),
    # OT books — wisdom:
    ('libri sapientiae', 'Sap'),
    ('libri sapiéntiæ', 'Sap'),
    ('libri ecclesiastici', 'Sir'),
    ('libri ecclesiástici', 'Sir'),
    ('libri ecclesiastes', 'Eccle'),
    ('libri ecclesiástes', 'Eccle'),
    ('libri proverbiorum', 'Prov'),
    ('libri proverbiórum', 'Prov'),
    ('libri psalmorum', 'Ps'),
    ('libri psalmórum', 'Ps'),
    ('libri canticum canticorum', 'Cant'),
    # OT books — prophets:
    ('libri isaiae prophetae', 'Is'),
    ('libri isaíæ prophétæ', 'Is'),
    ('libri ieremiae prophetae', 'Ier'),
    ('libri ieremíæ prophétæ', 'Ier'),
    ('libri ieremiae', 'Ier'),
    ('libri lamentationum', 'Lam'),
    ('libri ezechielis prophetae', 'Ez'),
    ('libri ezechiélis prophétæ', 'Ez'),
    ('libri danielis prophetae', 'Dan'),
    ('libri daniélis prophétæ', 'Dan'),
    ('libri osee prophetae', 'Os'),
    ('libri osée prophétæ', 'Os'),
    ('libri ioel prophetae', 'Ioel'),
    ('libri ioél prophétæ', 'Ioel'),
    ('libri amos prophetae', 'Am'),
    ('libri amos prophétæ', 'Am'),
    ('libri abdiae prophetae', 'Abd'),
    ('libri ionae prophetae', 'Ion'),
    ('libri ionæ prophetæ', 'Ion'),
    ('libri michaeae prophetae', 'Mi'),
    ('libri michææ prophétæ', 'Mi'),
    ('libri michææ', 'Mi'),
    ('libri nahum prophetae', 'Nah'),
    ('libri habacuc prophetae', 'Hab'),
    ('libri hábacuc prophétæ', 'Hab'),
    ('libri sophoniae prophetae', 'Soph'),
    ('libri sophoníæ prophétæ', 'Soph'),
    ('libri aggaei prophetae', 'Agg'),
    ('libri zachariae prophetae', 'Zach'),
    ('libri zacharíæ prophétæ', 'Zach'),
    ('libri malachiae prophetae', 'Mal'),
    ('libri malachíæ prophétæ', 'Mal'),
    ('libri baruch prophetae', 'Bar'),
    ('libri baruch prophétæ', 'Bar'),
    ('libri ioelis prophetae', 'Ioel'),
    ('libri ioélis prophétæ', 'Ioel'),
    ('libri thobis', 'Tb'),
    # Variants for OT books with alternative Latin spellings:
    ('libri numeri', 'Num'),
    ('libri secundi maccabaeorum', '2 Mac'),
    ('libri secundi maccabæorum', '2 Mac'),
    ('libri primi maccabaeorum', '1 Mac'),
    ('libri primi maccabæorum', '1 Mac'),
    ('cantici canticorum', 'Cant'),
    ('cántici canticórum', 'Cant'),
]
_LA_BOOK_PATTERNS = [(_norm(k), v) for k, v in _LA_BOOK_ABBREVS_ORDERED]

# Per-language intro patterns. The value is a canonical book ID (matches the
# Latin abbreviation column above); per-lang abbreviations resolve through
# _BOOK_ABBREV_BY_LANG below.
_EN_BOOK_PATTERNS_ORDERED: list[tuple[str, str]] = [
    ('holy gospel according to matthew', 'Mt'),
    ('holy gospel according to mark', 'Mc'),
    ('holy gospel according to luke', 'Lc'),
    ('holy gospel according to john', 'Io'),
    ('the acts of the apostles', 'Act'),
    ('first letter of saint paul to the corinthians', '1 Cor'),
    ('first letter of saint paul the apostle to the corinthians', '1 Cor'),
    ('second letter of saint paul to the corinthians', '2 Cor'),
    ('second letter of saint paul the apostle to the corinthians', '2 Cor'),
    ('first letter of saint paul to the thessalonians', '1 Thess'),
    ('second letter of saint paul to the thessalonians', '2 Thess'),
    ('first letter of saint paul to timothy', '1 Tim'),
    ('second letter of saint paul to timothy', '2 Tim'),
    ('letter of saint paul to the romans', 'Rom'),
    ('letter of saint paul to the galatians', 'Gal'),
    ('letter of saint paul to the ephesians', 'Eph'),
    ('letter of saint paul to the philippians', 'Phil'),
    ('letter of saint paul to the colossians', 'Col'),
    ('letter of saint paul to titus', 'Tit'),
    ('letter of saint paul to philemon', 'Phlm'),
    ('letter of saint paul to the hebrews', 'Heb'),
    ('letter to the romans', 'Rom'),
    ('letter to the galatians', 'Gal'),
    ('letter to the ephesians', 'Eph'),
    ('letter to the philippians', 'Phil'),
    ('letter to the colossians', 'Col'),
    ('letter to titus', 'Tit'),
    ('letter to philemon', 'Phlm'),
    ('letter to the hebrews', 'Heb'),
    ('first letter of saint john', '1 Io'),
    ('second letter of saint john', '2 Io'),
    ('third letter of saint john', '3 Io'),
    ('letter of saint james', 'Iac'),
    ('first letter of saint peter', '1 Pe'),
    ('second letter of saint peter', '2 Pe'),
    ('letter of saint jude', 'Iud'),
    ('book of revelation', 'Apoc'),
    ('book of genesis', 'Gn'),
    ('book of exodus', 'Ex'),
    ('book of leviticus', 'Lv'),
    ('book of numbers', 'Num'),
    ('book of deuteronomy', 'Dt'),
    ('book of joshua', 'Ios'),
    ('book of judges', 'Iud'),
    ('book of ruth', 'Rt'),
    ('first book of samuel', '1 Sam'),
    ('second book of samuel', '2 Sam'),
    ('first book of kings', '1 Reg'),
    ('second book of kings', '2 Reg'),
    ('first book of chronicles', '1 Par'),
    ('second book of chronicles', '2 Par'),
    ('book of ezra', 'Esd'),
    ('book of nehemiah', 'Neh'),
    ('book of tobit', 'Tb'),
    ('book of judith', 'Idt'),
    ('book of esther', 'Est'),
    ('first book of maccabees', '1 Mac'),
    ('second book of maccabees', '2 Mac'),
    ('book of job', 'Iob'),
    ('book of psalms', 'Ps'),
    ('book of proverbs', 'Prov'),
    ('book of ecclesiastes', 'Eccle'),
    ('song of songs', 'Cant'),
    ('book of wisdom', 'Sap'),
    ('book of ben sira', 'Sir'),
    ('book of sirach', 'Sir'),
    ('book of isaiah', 'Is'),
    ('prophet isaiah', 'Is'),
    ('prophecy of isaiah', 'Is'),
    ('book of jeremiah', 'Ier'),
    ('book of lamentations', 'Lam'),
    ('book of baruch', 'Bar'),
    ('book of ezekiel', 'Ez'),
    ('book of daniel', 'Dan'),
    ('book of hosea', 'Os'),
    ('book of joel', 'Ioel'),
    ('book of amos', 'Am'),
    ('book of obadiah', 'Abd'),
    ('book of jonah', 'Ion'),
    ('book of micah', 'Mi'),
    ('book of nahum', 'Nah'),
    ('book of habakkuk', 'Hab'),
    ('book of zephaniah', 'Soph'),
    ('book of haggai', 'Agg'),
    ('book of zechariah', 'Zach'),
    ('book of malachi', 'Mal'),
    # Lowercase variants (some intros use lowercase 'book of')
    ('prophecy of ezekiel', 'Ez'),
    ('prophecy of jeremiah', 'Ier'),
]
_EN_BOOK_PATTERNS = [(_norm(k), v) for k, v in _EN_BOOK_PATTERNS_ORDERED]

_ES_BOOK_PATTERNS_ORDERED: list[tuple[str, str]] = [
    ('santo evangelio segun san mateo', 'Mt'),
    ('santo evangelio segun san marcos', 'Mc'),
    ('santo evangelio segun san lucas', 'Lc'),
    ('santo evangelio segun san juan', 'Io'),
    ('libro de los hechos', 'Act'),
    ('hechos de los apostoles', 'Act'),
    ('primera carta del apostol san pablo a los corintios', '1 Cor'),
    ('segunda carta del apostol san pablo a los corintios', '2 Cor'),
    ('primera carta del apostol san pablo a los tesalonicenses', '1 Thess'),
    ('segunda carta del apostol san pablo a los tesalonicenses', '2 Thess'),
    ('primera carta del apostol san pablo a timoteo', '1 Tim'),
    ('segunda carta del apostol san pablo a timoteo', '2 Tim'),
    ('carta del apostol san pablo a los romanos', 'Rom'),
    ('carta del apostol san pablo a los galatas', 'Gal'),
    ('carta del apostol san pablo a los efesios', 'Eph'),
    ('carta del apostol san pablo a los filipenses', 'Phil'),
    ('carta del apostol san pablo a los colosenses', 'Col'),
    ('carta del apostol san pablo a tito', 'Tit'),
    ('carta del apostol san pablo a filemon', 'Phlm'),
    ('carta a los hebreos', 'Heb'),
    ('carta a los romanos', 'Rom'),
    ('carta a los galatas', 'Gal'),
    ('carta a los efesios', 'Eph'),
    ('carta a los filipenses', 'Phil'),
    ('carta a los colosenses', 'Col'),
    ('primera carta de san juan', '1 Io'),
    ('segunda carta de san juan', '2 Io'),
    ('tercera carta de san juan', '3 Io'),
    ('carta de santiago', 'Iac'),
    ('primera carta de san pedro', '1 Pe'),
    ('segunda carta de san pedro', '2 Pe'),
    ('carta de san judas', 'Iud'),
    ('libro del apocalipsis', 'Apoc'),
    ('libro del genesis', 'Gn'),
    ('libro del exodo', 'Ex'),
    ('libro del levitico', 'Lv'),
    ('libro de los numeros', 'Num'),
    ('libro del deuteronomio', 'Dt'),
    ('libro de josue', 'Ios'),
    ('libro de los jueces', 'Iud'),
    ('libro de rut', 'Rt'),
    ('libro primero de samuel', '1 Sam'),
    ('libro segundo de samuel', '2 Sam'),
    ('libro primero de los reyes', '1 Reg'),
    ('libro segundo de los reyes', '2 Reg'),
    ('libro primero de las cronicas', '1 Par'),
    ('libro segundo de las cronicas', '2 Par'),
    ('libro de tobias', 'Tb'),
    ('libro de judit', 'Idt'),
    ('libro de ester', 'Est'),
    ('libro primero de los macabeos', '1 Mac'),
    ('libro segundo de los macabeos', '2 Mac'),
    ('libro de job', 'Iob'),
    ('libro de los salmos', 'Ps'),
    ('libro de los proverbios', 'Prov'),
    ('libro del eclesiastes', 'Eccle'),
    ('libro del cantar de los cantares', 'Cant'),
    ('libro de la sabiduria', 'Sap'),
    ('libro del eclesiastico', 'Sir'),
    ('libro del siracide', 'Sir'),
    ('libro del profeta isaias', 'Is'),
    ('libro de isaias', 'Is'),
    ('libro del profeta jeremias', 'Ier'),
    ('libro de jeremias', 'Ier'),
    ('libro de las lamentaciones', 'Lam'),
    ('libro de baruc', 'Bar'),
    ('libro del profeta ezequiel', 'Ez'),
    ('libro de ezequiel', 'Ez'),
    ('libro del profeta daniel', 'Dan'),
    ('libro de daniel', 'Dan'),
    ('libro de oseas', 'Os'),
    ('libro de joel', 'Ioel'),
    ('libro de amos', 'Am'),
    ('libro de abdias', 'Abd'),
    ('libro de jonas', 'Ion'),
    ('libro de miqueas', 'Mi'),
    ('libro de nahum', 'Nah'),
    ('libro de habacuc', 'Hab'),
    ('libro de sofonias', 'Soph'),
    ('libro de ageo', 'Agg'),
    ('libro de zacarias', 'Zach'),
    ('libro de malaquias', 'Mal'),
]
_ES_BOOK_PATTERNS = [(_norm(k), v) for k, v in _ES_BOOK_PATTERNS_ORDERED]

_PT_BR_BOOK_PATTERNS_ORDERED: list[tuple[str, str]] = [
    ('proclamacao do evangelho de jesus cristo segundo mateus', 'Mt'),
    ('proclamacao do evangelho de jesus cristo segundo marcos', 'Mc'),
    ('proclamacao do evangelho de jesus cristo segundo lucas', 'Lc'),
    ('proclamacao do evangelho de jesus cristo segundo joao', 'Io'),
    ('atos dos apostolos', 'Act'),
    ('primeira carta de sao paulo aos corintios', '1 Cor'),
    ('segunda carta de sao paulo aos corintios', '2 Cor'),
    ('primeira carta de sao paulo aos tessalonicenses', '1 Thess'),
    ('segunda carta de sao paulo aos tessalonicenses', '2 Thess'),
    ('primeira carta de sao paulo a timoteo', '1 Tim'),
    ('segunda carta de sao paulo a timoteo', '2 Tim'),
    ('carta de sao paulo aos romanos', 'Rom'),
    ('carta de sao paulo aos galatas', 'Gal'),
    ('carta de sao paulo aos efesios', 'Eph'),
    ('carta de sao paulo aos filipenses', 'Phil'),
    ('carta de sao paulo aos colossenses', 'Col'),
    ('carta de sao paulo a tito', 'Tit'),
    ('carta de sao paulo a filemon', 'Phlm'),
    ('carta aos hebreus', 'Heb'),
    ('carta aos romanos', 'Rom'),
    ('primeira carta de sao joao', '1 Io'),
    ('segunda carta de sao joao', '2 Io'),
    ('terceira carta de sao joao', '3 Io'),
    ('carta de sao tiago', 'Iac'),
    ('primeira carta de sao pedro', '1 Pe'),
    ('segunda carta de sao pedro', '2 Pe'),
    ('carta de sao judas', 'Iud'),
    ('livro do apocalipsis', 'Apoc'),
    ('livro do apocalipse', 'Apoc'),
    ('livro do genesis', 'Gn'),
    ('livro do genesis', 'Gn'),
    ('livro do exodo', 'Ex'),
    ('livro do levitico', 'Lv'),
    ('livro dos numeros', 'Num'),
    ('livro do deuteronomio', 'Dt'),
    ('livro de josue', 'Ios'),
    ('livro dos juizes', 'Iud'),
    ('livro de rute', 'Rt'),
    ('primeiro livro de samuel', '1 Sam'),
    ('segundo livro de samuel', '2 Sam'),
    ('primeiro livro dos reis', '1 Reg'),
    ('segundo livro dos reis', '2 Reg'),
    ('primeiro livro das cronicas', '1 Par'),
    ('segundo livro das cronicas', '2 Par'),
    ('livro de tobias', 'Tb'),
    ('livro de judite', 'Idt'),
    ('livro de ester', 'Est'),
    ('primeiro livro dos macabeus', '1 Mac'),
    ('segundo livro dos macabeus', '2 Mac'),
    ('livro de jo', 'Iob'),
    ('livro dos salmos', 'Ps'),
    ('livro dos proverbios', 'Prov'),
    ('livro do eclesiastes', 'Eccle'),
    ('livro do cantico dos canticos', 'Cant'),
    ('livro da sabedoria', 'Sap'),
    ('livro do eclesiastico', 'Sir'),
    ('livro do siracida', 'Sir'),
    ('livro do profeta isaias', 'Is'),
    ('livro de isaias', 'Is'),
    ('livro do profeta jeremias', 'Ier'),
    ('livro de jeremias', 'Ier'),
    ('profecia de jeremias', 'Ier'),
    ('livro das lamentacoes', 'Lam'),
    ('livro do profeta baruc', 'Bar'),
    ('livro de baruc', 'Bar'),
    ('livro do profeta ezequiel', 'Ez'),
    ('livro de ezequiel', 'Ez'),
    ('profecia de ezequiel', 'Ez'),
    ('livro do profeta daniel', 'Dan'),
    ('livro de daniel', 'Dan'),
    ('livro de oseias', 'Os'),
    ('livro de joel', 'Ioel'),
    ('livro de amos', 'Am'),
    ('livro de abdias', 'Abd'),
    ('livro de jonas', 'Ion'),
    ('livro de miqueias', 'Mi'),
    ('livro de naum', 'Nah'),
    ('livro de habacuc', 'Hab'),
    ('livro de sofonias', 'Soph'),
    ('livro de ageu', 'Agg'),
    ('livro de zacarias', 'Zach'),
    ('livro de malaquias', 'Mal'),
]
_PT_BR_BOOK_PATTERNS = [(_norm(k), v) for k, v in _PT_BR_BOOK_PATTERNS_ORDERED]

_IT_BOOK_PATTERNS_ORDERED: list[tuple[str, str]] = [
    ('vangelo secondo matteo', 'Mt'),
    ('vangelo secondo marco', 'Mc'),
    ('vangelo secondo luca', 'Lc'),
    ('vangelo secondo giovanni', 'Io'),
    ('atti degli apostoli', 'Act'),
    ('prima lettera di san paolo apostolo ai corinzi', '1 Cor'),
    ('seconda lettera di san paolo apostolo ai corinzi', '2 Cor'),
    ('prima lettera di san paolo apostolo ai tessalonicesi', '1 Thess'),
    ('seconda lettera di san paolo apostolo ai tessalonicesi', '2 Thess'),
    ('prima lettera di san paolo apostolo a timoteo', '1 Tim'),
    ('seconda lettera di san paolo apostolo a timoteo', '2 Tim'),
    ('lettera di san paolo apostolo ai romani', 'Rom'),
    ('lettera di san paolo apostolo ai galati', 'Gal'),
    ('lettera di san paolo apostolo agli efesini', 'Eph'),
    ('lettera di san paolo apostolo ai filippesi', 'Phil'),
    ('lettera di san paolo apostolo ai colossesi', 'Col'),
    ('lettera di san paolo apostolo a tito', 'Tit'),
    ('lettera di san paolo apostolo a filemone', 'Phlm'),
    ('lettera agli ebrei', 'Heb'),
    ('lettera ai romani', 'Rom'),
    ('prima lettera di san giovanni', '1 Io'),
    ('seconda lettera di san giovanni', '2 Io'),
    ('terza lettera di san giovanni', '3 Io'),
    ('lettera di san giacomo', 'Iac'),
    ('prima lettera di san pietro', '1 Pe'),
    ('seconda lettera di san pietro', '2 Pe'),
    ("dell'apocalisse di san giovanni", 'Apoc'),
    ("libro dell'apocalisse", 'Apoc'),
    ('libro della genesi', 'Gn'),
    ("libro dell'esodo", 'Ex'),
    ('libro del levitico', 'Lv'),
    ('libro dei numeri', 'Num'),
    ('libro del deuteronomio', 'Dt'),
    ('libro di giosue', 'Ios'),
    ('libro dei giudici', 'Iud'),
    ('libro di rut', 'Rt'),
    ('primo libro di samuele', '1 Sam'),
    ('secondo libro di samuele', '2 Sam'),
    ('primo libro dei re', '1 Reg'),
    ('secondo libro dei re', '2 Reg'),
    ('libro di tobia', 'Tb'),
    ('libro di giuditta', 'Idt'),
    ('libro di ester', 'Est'),
    ('primo libro dei maccabei', '1 Mac'),
    ('secondo libro dei maccabei', '2 Mac'),
    ('libro di giobbe', 'Iob'),
    ('libro dei salmi', 'Ps'),
    ('libro dei proverbi', 'Prov'),
    ("libro del qoelet", 'Eccle'),
    ('cantico dei cantici', 'Cant'),
    ('libro della sapienza', 'Sap'),
    ('libro del siracide', 'Sir'),
    ('libro del profeta isaia', 'Is'),
    ('libro di isaia', 'Is'),
    ('libro del profeta geremia', 'Ier'),
    ('libro di geremia', 'Ier'),
    ('libro delle lamentazioni', 'Lam'),
    ('libro di baruc', 'Bar'),
    ('libro del profeta ezechiele', 'Ez'),
    ('libro di ezechiele', 'Ez'),
    ('libro del profeta daniele', 'Dan'),
    ('libro di daniele', 'Dan'),
    ('libro di osea', 'Os'),
    ('libro di gioele', 'Ioel'),
    ('libro di amos', 'Am'),
    ('libro di abdia', 'Abd'),
    ('libro di giona', 'Ion'),
    ('libro di michea', 'Mi'),
    ('libro di naum', 'Nah'),
    ('libro di abacuc', 'Hab'),
    ('libro di sofonia', 'Soph'),
    ('libro di aggeo', 'Agg'),
    ('libro di zaccaria', 'Zach'),
    ('libro di malachia', 'Mal'),
]
_IT_BOOK_PATTERNS = [(_norm(k), v) for k, v in _IT_BOOK_PATTERNS_ORDERED]

_FR_BOOK_PATTERNS_ORDERED: list[tuple[str, str]] = [
    ('evangile de jesus-christ selon saint matthieu', 'Mt'),
    ('evangile de jesus christ selon saint matthieu', 'Mt'),
    ('evangile de jesus-christ selon saint marc', 'Mc'),
    ('evangile de jesus christ selon saint marc', 'Mc'),
    ('evangile de jesus-christ selon saint luc', 'Lc'),
    ('evangile de jesus christ selon saint luc', 'Lc'),
    ('evangile de jesus-christ selon saint jean', 'Io'),
    ('evangile de jesus christ selon saint jean', 'Io'),
    ('actes des apotres', 'Act'),
    ('premiere lettre de saint paul apotre aux corinthiens', '1 Cor'),
    ('deuxieme lettre de saint paul apotre aux corinthiens', '2 Cor'),
    ('premiere lettre de saint paul apotre aux thessaloniciens', '1 Thess'),
    ('deuxieme lettre de saint paul apotre aux thessaloniciens', '2 Thess'),
    ('premiere lettre de saint paul apotre a timothee', '1 Tim'),
    ('deuxieme lettre de saint paul apotre a timothee', '2 Tim'),
    ('lettre de saint paul apotre aux romains', 'Rom'),
    ('lettre de saint paul apotre aux galates', 'Gal'),
    ('lettre de saint paul apotre aux ephesiens', 'Eph'),
    ('lettre de saint paul apotre aux philippiens', 'Phil'),
    ('lettre de saint paul apotre aux colossiens', 'Col'),
    ('lettre de saint paul apotre a tite', 'Tit'),
    ('lettre de saint paul apotre a philemon', 'Phlm'),
    ('lettre aux hebreux', 'Heb'),
    ('lettre aux romains', 'Rom'),
    ('premiere lettre de saint jean', '1 Io'),
    ('deuxieme lettre de saint jean', '2 Io'),
    ('troisieme lettre de saint jean', '3 Io'),
    ('lettre de saint jacques', 'Iac'),
    ('premiere lettre de saint pierre', '1 Pe'),
    ('deuxieme lettre de saint pierre', '2 Pe'),
    ("livre de l'apocalypse de saint jean", 'Apoc'),
    ("livre de l'apocalypse", 'Apoc'),
    ('livre de la genese', 'Gn'),
    ("livre de l'exode", 'Ex'),
    ('livre du levitique', 'Lv'),
    ('livre des nombres', 'Num'),
    ('livre du deuteronome', 'Dt'),
    ('livre de josue', 'Ios'),
    ('livre des juges', 'Iud'),
    ('livre de ruth', 'Rt'),
    ('premier livre de samuel', '1 Sam'),
    ('deuxieme livre de samuel', '2 Sam'),
    ('premier livre des rois', '1 Reg'),
    ('deuxieme livre des rois', '2 Reg'),
    ('livre de tobie', 'Tb'),
    ('livre de judith', 'Idt'),
    ('livre d esther', 'Est'),
    ('premier livre des maccabees', '1 Mac'),
    ('deuxieme livre des maccabees', '2 Mac'),
    ('livre de job', 'Iob'),
    ('livre des psaumes', 'Ps'),
    ('livre des proverbes', 'Prov'),
    ("livre de l'ecclesiaste", 'Eccle'),
    ('cantique des cantiques', 'Cant'),
    ('livre de la sagesse', 'Sap'),
    ('livre de ben sirac le sage', 'Sir'),
    ("livre de l'ecclesiastique", 'Sir'),
    ("livre d'isaie", 'Is'),
    ('livre de jeremie', 'Ier'),
    ('livre des lamentations', 'Lam'),
    ('livre de baruch', 'Bar'),
    ("livre d'ezechiel", 'Ez'),
    ('livre de daniel', 'Dan'),
    ("livre d'osee", 'Os'),
    ('livre de joel', 'Ioel'),
    ("livre d'amos", 'Am'),
    ("livre d'abdias", 'Abd'),
    ('livre de jonas', 'Ion'),
    ('livre de michee', 'Mi'),
    ('livre de nahum', 'Nah'),
    ('livre d habacuc', 'Hab'),
    ('livre de sophonie', 'Soph'),
    ("livre d'aggee", 'Agg'),
    ('livre de zacharie', 'Zach'),
    ('livre de malachie', 'Mal'),
]
_FR_BOOK_PATTERNS = [(_norm(k), v) for k, v in _FR_BOOK_PATTERNS_ORDERED]

_DE_BOOK_PATTERNS_ORDERED: list[tuple[str, str]] = [
    ('heiligen evangelium nach matthaus', 'Mt'),
    ('heiligen evangelium nach matthäus', 'Mt'),
    ('heiligen evangelium nach markus', 'Mc'),
    ('heiligen evangelium nach lukas', 'Lc'),
    ('heiligen evangelium nach johannes', 'Io'),
    ('apostelgeschichte', 'Act'),
    ('ersten brief des apostels paulus an die korinther', '1 Cor'),
    ('zweiten brief des apostels paulus an die korinther', '2 Cor'),
    ('ersten brief des apostels paulus an die thessalonicher', '1 Thess'),
    ('zweiten brief des apostels paulus an die thessalonicher', '2 Thess'),
    ('ersten brief des apostels paulus an timotheus', '1 Tim'),
    ('zweiten brief des apostels paulus an timotheus', '2 Tim'),
    ('brief des apostels paulus an die romer', 'Rom'),
    ('brief des apostels paulus an die römer', 'Rom'),
    ('brief des apostels paulus an die galater', 'Gal'),
    ('brief des apostels paulus an die epheser', 'Eph'),
    ('brief des apostels paulus an die philipper', 'Phil'),
    ('brief des apostels paulus an die kolosser', 'Col'),
    ('brief des apostels paulus an titus', 'Tit'),
    ('brief des apostels paulus an philemon', 'Phlm'),
    ('hebraerbrief', 'Heb'),
    ('hebräerbrief', 'Heb'),
    ('ersten johannesbrief', '1 Io'),
    ('zweiten johannesbrief', '2 Io'),
    ('dritten johannesbrief', '3 Io'),
    ('jakobusbrief', 'Iac'),
    ('ersten petrusbrief', '1 Pe'),
    ('zweiten petrusbrief', '2 Pe'),
    ('judasbrief', 'Iud'),
    ('offenbarung des johannes', 'Apoc'),
    ('buch genesis', 'Gn'),
    ('buch génesis', 'Gn'),
    ('buch exodus', 'Ex'),
    ('buch éxodus', 'Ex'),
    ('buch levitikus', 'Lv'),
    ('buch numeri', 'Num'),
    ('buch deuteronomium', 'Dt'),
    ('buch deuteronómium', 'Dt'),
    ('buch josua', 'Ios'),
    ('buch der richter', 'Iud'),
    ('buch rut', 'Rt'),
    ('ersten buch samuel', '1 Sam'),
    ('zweiten buch samuel', '2 Sam'),
    ('ersten buch der konige', '1 Reg'),
    ('ersten buch der könige', '1 Reg'),
    ('zweiten buch der konige', '2 Reg'),
    ('zweiten buch der könige', '2 Reg'),
    ('buch tobit', 'Tb'),
    ('buch judit', 'Idt'),
    ('buch ester', 'Est'),
    ('ersten buch der makkabaer', '1 Mac'),
    ('ersten buch der makkabäer', '1 Mac'),
    ('zweiten buch der makkabaer', '2 Mac'),
    ('zweiten buch der makkabäer', '2 Mac'),
    ('buch ijob', 'Iob'),
    ('buch der psalmen', 'Ps'),
    ('buch der spruche', 'Prov'),
    ('buch der sprüche', 'Prov'),
    ('buch kohelet', 'Eccle'),
    ('hohelied', 'Cant'),
    ('buch der weisheit', 'Sap'),
    ('buch jesus sirach', 'Sir'),
    ('buch jesaja', 'Is'),
    ('buch jesája', 'Is'),
    ('buch jeremia', 'Ier'),
    ('buch jeremía', 'Ier'),
    ('klagelieder', 'Lam'),
    ('buch baruch', 'Bar'),
    ('buch ezechiel', 'Ez'),
    ('buch ezéchiel', 'Ez'),
    ('buch daniel', 'Dan'),
    ('buch daniél', 'Dan'),
    ('buch hosea', 'Os'),
    ('buch joel', 'Ioel'),
    ('buch amos', 'Am'),
    ('buch obadja', 'Abd'),
    ('buch jona', 'Ion'),
    ('buch micha', 'Mi'),
    ('buch nahum', 'Nah'),
    ('buch habakuk', 'Hab'),
    ('buch zefanja', 'Soph'),
    ('buch haggai', 'Agg'),
    ('buch sacharja', 'Zach'),
    ('buch maleachi', 'Mal'),
]
_DE_BOOK_PATTERNS = [(_norm(k), v) for k, v in _DE_BOOK_PATTERNS_ORDERED]

_LANG_BOOK_PATTERNS = {
    'la': _LA_BOOK_PATTERNS,
    'en': _EN_BOOK_PATTERNS,
    'es': _ES_BOOK_PATTERNS,
    'pt-BR': _PT_BR_BOOK_PATTERNS,
    'it': _IT_BOOK_PATTERNS,
    'fr': _FR_BOOK_PATTERNS,
    'de': _DE_BOOK_PATTERNS,
}

# Per-language abbreviation table — each book id maps to lang-specific abbrev.
# Default falls back to the canonical (Latin) abbrev when a lang isn't listed.
_BOOK_ABBREV_BY_LANG: dict[str, dict[str, str]] = {
    'Mt': {'la': 'Mt', 'en': 'Mt', 'es': 'Mt', 'pt-BR': 'Mt', 'it': 'Mt', 'fr': 'Mt', 'de': 'Mt'},
    'Mc': {'la': 'Mc', 'en': 'Mk', 'es': 'Mc', 'pt-BR': 'Mc', 'it': 'Mc', 'fr': 'Mc', 'de': 'Mk'},
    'Lc': {'la': 'Lc', 'en': 'Lk', 'es': 'Lc', 'pt-BR': 'Lc', 'it': 'Lc', 'fr': 'Lc', 'de': 'Lk'},
    'Io': {'la': 'Io', 'en': 'Jn', 'es': 'Jn', 'pt-BR': 'Jo', 'it': 'Gv', 'fr': 'Jn', 'de': 'Joh'},
    'Act': {'la': 'Act', 'en': 'Acts', 'es': 'Hch', 'pt-BR': 'At', 'it': 'At', 'fr': 'Ac', 'de': 'Apg'},
    'Rom': {'la': 'Rom', 'en': 'Rom', 'es': 'Rom', 'pt-BR': 'Rom', 'it': 'Rm', 'fr': 'Rm', 'de': 'Röm'},
    '1 Cor': {'la': '1 Cor', 'en': '1 Cor', 'es': '1 Cor', 'pt-BR': '1 Cor', 'it': '1 Cor', 'fr': '1 Co', 'de': '1 Kor'},
    '2 Cor': {'la': '2 Cor', 'en': '2 Cor', 'es': '2 Cor', 'pt-BR': '2 Cor', 'it': '2 Cor', 'fr': '2 Co', 'de': '2 Kor'},
    'Gal': {'la': 'Gal', 'en': 'Gal', 'es': 'Gal', 'pt-BR': 'Gal', 'it': 'Gal', 'fr': 'Ga', 'de': 'Gal'},
    'Eph': {'la': 'Eph', 'en': 'Eph', 'es': 'Ef', 'pt-BR': 'Ef', 'it': 'Ef', 'fr': 'Ep', 'de': 'Eph'},
    'Phil': {'la': 'Phil', 'en': 'Phil', 'es': 'Flp', 'pt-BR': 'Fl', 'it': 'Fil', 'fr': 'Ph', 'de': 'Phil'},
    'Col': {'la': 'Col', 'en': 'Col', 'es': 'Col', 'pt-BR': 'Cl', 'it': 'Col', 'fr': 'Col', 'de': 'Kol'},
    '1 Thess': {'la': '1 Thess', 'en': '1 Thess', 'es': '1 Tes', 'pt-BR': '1 Ts', 'it': '1 Ts', 'fr': '1 Th', 'de': '1 Thess'},
    '2 Thess': {'la': '2 Thess', 'en': '2 Thess', 'es': '2 Tes', 'pt-BR': '2 Ts', 'it': '2 Ts', 'fr': '2 Th', 'de': '2 Thess'},
    '1 Tim': {'la': '1 Tim', 'en': '1 Tim', 'es': '1 Tim', 'pt-BR': '1 Tim', 'it': '1 Tm', 'fr': '1 Tm', 'de': '1 Tim'},
    '2 Tim': {'la': '2 Tim', 'en': '2 Tim', 'es': '2 Tim', 'pt-BR': '2 Tim', 'it': '2 Tm', 'fr': '2 Tm', 'de': '2 Tim'},
    'Tit': {'la': 'Tit', 'en': 'Ti', 'es': 'Tit', 'pt-BR': 'Tt', 'it': 'Tt', 'fr': 'Tt', 'de': 'Tit'},
    'Phlm': {'la': 'Phlm', 'en': 'Phlm', 'es': 'Flm', 'pt-BR': 'Fm', 'it': 'Fm', 'fr': 'Phm', 'de': 'Phlm'},
    'Heb': {'la': 'Heb', 'en': 'Heb', 'es': 'Heb', 'pt-BR': 'Heb', 'it': 'Eb', 'fr': 'He', 'de': 'Hebr'},
    '1 Pe': {'la': '1 Pe', 'en': '1 Pet', 'es': '1 Pe', 'pt-BR': '1 Pd', 'it': '1 Pt', 'fr': '1 P', 'de': '1 Petr'},
    '2 Pe': {'la': '2 Pe', 'en': '2 Pet', 'es': '2 Pe', 'pt-BR': '2 Pd', 'it': '2 Pt', 'fr': '2 P', 'de': '2 Petr'},
    '1 Io': {'la': '1 Io', 'en': '1 Jn', 'es': '1 Jn', 'pt-BR': '1 Jo', 'it': '1 Gv', 'fr': '1 Jn', 'de': '1 Joh'},
    '2 Io': {'la': '2 Io', 'en': '2 Jn', 'es': '2 Jn', 'pt-BR': '2 Jo', 'it': '2 Gv', 'fr': '2 Jn', 'de': '2 Joh'},
    '3 Io': {'la': '3 Io', 'en': '3 Jn', 'es': '3 Jn', 'pt-BR': '3 Jo', 'it': '3 Gv', 'fr': '3 Jn', 'de': '3 Joh'},
    'Iac': {'la': 'Iac', 'en': 'Jas', 'es': 'St', 'pt-BR': 'Tg', 'it': 'Gc', 'fr': 'Jc', 'de': 'Jak'},
    'Iud': {'la': 'Iud', 'en': 'Jude', 'es': 'Jds', 'pt-BR': 'Jd', 'it': 'Gd', 'fr': 'Jude', 'de': 'Jud'},
    'Apoc': {'la': 'Apoc', 'en': 'Rev', 'es': 'Ap', 'pt-BR': 'Ap', 'it': 'Ap', 'fr': 'Ap', 'de': 'Offb'},
    # OT books
    'Gn': {'la': 'Gn', 'en': 'Gn', 'es': 'Gn', 'pt-BR': 'Gn', 'it': 'Gn', 'fr': 'Gn', 'de': 'Gen'},
    'Ex': {'la': 'Ex', 'en': 'Ex', 'es': 'Ex', 'pt-BR': 'Ex', 'it': 'Es', 'fr': 'Ex', 'de': 'Ex'},
    'Lv': {'la': 'Lv', 'en': 'Lv', 'es': 'Lv', 'pt-BR': 'Lv', 'it': 'Lv', 'fr': 'Lv', 'de': 'Lev'},
    'Num': {'la': 'Num', 'en': 'Nm', 'es': 'Núm', 'pt-BR': 'Nm', 'it': 'Nm', 'fr': 'Nb', 'de': 'Num'},
    'Dt': {'la': 'Dt', 'en': 'Dt', 'es': 'Dt', 'pt-BR': 'Dt', 'it': 'Dt', 'fr': 'Dt', 'de': 'Dtn'},
    'Ios': {'la': 'Ios', 'en': 'Jos', 'es': 'Jos', 'pt-BR': 'Js', 'it': 'Gs', 'fr': 'Jos', 'de': 'Jos'},
    'Rt': {'la': 'Rt', 'en': 'Ru', 'es': 'Rut', 'pt-BR': 'Rt', 'it': 'Rt', 'fr': 'Rt', 'de': 'Rut'},
    '1 Sam': {'la': '1 Sam', 'en': '1 Sm', 'es': '1 Sam', 'pt-BR': '1 Sm', 'it': '1 Sam', 'fr': '1 S', 'de': '1 Sam'},
    '2 Sam': {'la': '2 Sam', 'en': '2 Sm', 'es': '2 Sam', 'pt-BR': '2 Sm', 'it': '2 Sam', 'fr': '2 S', 'de': '2 Sam'},
    '1 Reg': {'la': '1 Reg', 'en': '1 Kgs', 'es': '1 Re', 'pt-BR': '1 Rs', 'it': '1 Re', 'fr': '1 R', 'de': '1 Kön'},
    '2 Reg': {'la': '2 Reg', 'en': '2 Kgs', 'es': '2 Re', 'pt-BR': '2 Rs', 'it': '2 Re', 'fr': '2 R', 'de': '2 Kön'},
    '1 Par': {'la': '1 Par', 'en': '1 Chr', 'es': '1 Cr', 'pt-BR': '1 Cr', 'it': '1 Cr', 'fr': '1 Ch', 'de': '1 Chr'},
    '2 Par': {'la': '2 Par', 'en': '2 Chr', 'es': '2 Cr', 'pt-BR': '2 Cr', 'it': '2 Cr', 'fr': '2 Ch', 'de': '2 Chr'},
    'Esd': {'la': 'Esd', 'en': 'Ezr', 'es': 'Esd', 'pt-BR': 'Esd', 'it': 'Esd', 'fr': 'Esd', 'de': 'Esra'},
    'Neh': {'la': 'Neh', 'en': 'Neh', 'es': 'Neh', 'pt-BR': 'Ne', 'it': 'Ne', 'fr': 'Né', 'de': 'Neh'},
    'Tb': {'la': 'Tb', 'en': 'Tb', 'es': 'Tob', 'pt-BR': 'Tb', 'it': 'Tb', 'fr': 'Tb', 'de': 'Tob'},
    'Idt': {'la': 'Idt', 'en': 'Jdt', 'es': 'Jdt', 'pt-BR': 'Jt', 'it': 'Gdt', 'fr': 'Jdt', 'de': 'Jdt'},
    'Est': {'la': 'Est', 'en': 'Est', 'es': 'Est', 'pt-BR': 'Est', 'it': 'Est', 'fr': 'Est', 'de': 'Est'},
    '1 Mac': {'la': '1 Mac', 'en': '1 Mc', 'es': '1 Mac', 'pt-BR': '1 Mac', 'it': '1 Mac', 'fr': '1 M', 'de': '1 Makk'},
    '2 Mac': {'la': '2 Mac', 'en': '2 Mc', 'es': '2 Mac', 'pt-BR': '2 Mac', 'it': '2 Mac', 'fr': '2 M', 'de': '2 Makk'},
    'Iob': {'la': 'Iob', 'en': 'Jb', 'es': 'Job', 'pt-BR': 'Jó', 'it': 'Gb', 'fr': 'Jb', 'de': 'Ijob'},
    'Ps': {'la': 'Ps', 'en': 'Ps', 'es': 'Sal', 'pt-BR': 'Sl', 'it': 'Sal', 'fr': 'Ps', 'de': 'Ps'},
    'Prov': {'la': 'Prov', 'en': 'Prv', 'es': 'Prov', 'pt-BR': 'Pr', 'it': 'Pr', 'fr': 'Pr', 'de': 'Spr'},
    'Eccle': {'la': 'Eccle', 'en': 'Eccl', 'es': 'Ecl', 'pt-BR': 'Ecl', 'it': 'Qo', 'fr': 'Qo', 'de': 'Koh'},
    'Cant': {'la': 'Cant', 'en': 'Sg', 'es': 'Cant', 'pt-BR': 'Ct', 'it': 'Ct', 'fr': 'Ct', 'de': 'Hld'},
    'Sap': {'la': 'Sap', 'en': 'Wis', 'es': 'Sab', 'pt-BR': 'Sb', 'it': 'Sap', 'fr': 'Sg', 'de': 'Weish'},
    'Sir': {'la': 'Sir', 'en': 'Sir', 'es': 'Sir', 'pt-BR': 'Eclo', 'it': 'Sir', 'fr': 'Si', 'de': 'Sir'},
    'Is': {'la': 'Is', 'en': 'Is', 'es': 'Is', 'pt-BR': 'Is', 'it': 'Is', 'fr': 'Is', 'de': 'Jes'},
    'Ier': {'la': 'Ier', 'en': 'Jer', 'es': 'Jer', 'pt-BR': 'Jer', 'it': 'Ger', 'fr': 'Jr', 'de': 'Jer'},
    'Lam': {'la': 'Lam', 'en': 'Lam', 'es': 'Lam', 'pt-BR': 'Lam', 'it': 'Lam', 'fr': 'Lm', 'de': 'Klgl'},
    'Bar': {'la': 'Bar', 'en': 'Bar', 'es': 'Bar', 'pt-BR': 'Br', 'it': 'Bar', 'fr': 'Ba', 'de': 'Bar'},
    'Ez': {'la': 'Ez', 'en': 'Ez', 'es': 'Ez', 'pt-BR': 'Ez', 'it': 'Ez', 'fr': 'Ez', 'de': 'Ez'},
    'Dan': {'la': 'Dan', 'en': 'Dn', 'es': 'Dn', 'pt-BR': 'Dn', 'it': 'Dn', 'fr': 'Dn', 'de': 'Dan'},
    'Os': {'la': 'Os', 'en': 'Hos', 'es': 'Os', 'pt-BR': 'Os', 'it': 'Os', 'fr': 'Os', 'de': 'Hos'},
    'Ioel': {'la': 'Ioel', 'en': 'Jl', 'es': 'Jl', 'pt-BR': 'Jl', 'it': 'Gl', 'fr': 'Jl', 'de': 'Joël'},
    'Am': {'la': 'Am', 'en': 'Am', 'es': 'Am', 'pt-BR': 'Am', 'it': 'Am', 'fr': 'Am', 'de': 'Am'},
    'Abd': {'la': 'Abd', 'en': 'Ob', 'es': 'Abd', 'pt-BR': 'Abd', 'it': 'Abd', 'fr': 'Ab', 'de': 'Obd'},
    'Ion': {'la': 'Ion', 'en': 'Jon', 'es': 'Jon', 'pt-BR': 'Jn', 'it': 'Gn', 'fr': 'Jon', 'de': 'Jona'},
    'Mi': {'la': 'Mi', 'en': 'Mi', 'es': 'Miq', 'pt-BR': 'Mq', 'it': 'Mi', 'fr': 'Mi', 'de': 'Mi'},
    'Nah': {'la': 'Nah', 'en': 'Na', 'es': 'Nah', 'pt-BR': 'Na', 'it': 'Na', 'fr': 'Na', 'de': 'Nah'},
    'Hab': {'la': 'Hab', 'en': 'Hb', 'es': 'Hab', 'pt-BR': 'Hab', 'it': 'Ab', 'fr': 'Ha', 'de': 'Hab'},
    'Soph': {'la': 'Soph', 'en': 'Zep', 'es': 'Sof', 'pt-BR': 'Sf', 'it': 'Sof', 'fr': 'So', 'de': 'Zef'},
    'Agg': {'la': 'Agg', 'en': 'Hg', 'es': 'Ag', 'pt-BR': 'Ag', 'it': 'Ag', 'fr': 'Ag', 'de': 'Hag'},
    'Zach': {'la': 'Zach', 'en': 'Zec', 'es': 'Zac', 'pt-BR': 'Zc', 'it': 'Zc', 'fr': 'Za', 'de': 'Sach'},
    'Mal': {'la': 'Mal', 'en': 'Mal', 'es': 'Mal', 'pt-BR': 'Ml', 'it': 'Ml', 'fr': 'Ml', 'de': 'Mal'},
}


def _book_id_from_intro(intro_text: str, lang: str) -> Optional[str]:
    """Parse a per-language lectionary introduction and return the canonical
    book id. Returns None if no pattern matches."""
    if not isinstance(intro_text, str) or not intro_text:
        return None
    patterns = _LANG_BOOK_PATTERNS.get(lang)
    if not patterns:
        return None
    n = _norm(intro_text)
    for needle, book_id in patterns:
        if needle in n:
            return book_id
    return None


def _book_abbrev_from_intro(intro_text: str, lang: str) -> Optional[str]:
    """Resolve the language-specific abbreviation for the book referenced in
    the per-language introduction."""
    book_id = _book_id_from_intro(intro_text, lang)
    if not book_id:
        return None
    abbrev_map = _BOOK_ABBREV_BY_LANG.get(book_id)
    if not abbrev_map:
        return None
    # Per-lang abbrev with Latin fallback if a lang isn't listed
    return abbrev_map.get(lang) or abbrev_map.get('la')


_NUMBERED_BOOK_PREFIX_RE = re.compile(r'^\d+\s+[A-Za-zÀ-ÿ]')


def _enrich_reading_citation(reading: dict) -> None:
    """If `citation.<lang>` is bare (no book token at start) and the matching
    `introduction.<lang>` reveals the book, prepend the language-appropriate
    abbreviation. Falls back to using the Latin intro to identify the book if
    a vernacular intro doesn't pattern-match — the same canonical book id
    feeds the per-lang abbrev lookup."""
    if not isinstance(reading, dict):
        return
    intro = reading.get('introduction')
    cit = reading.get('citation')
    if not isinstance(intro, dict) or not isinstance(cit, dict):
        return
    # First derive a canonical book id from whichever intro language has a
    # pattern hit. Latin is preferred (most stable patterns), then vernaculars.
    canonical_id: Optional[str] = None
    for try_lang in ('la', 'en', 'es', 'pt-BR', 'it', 'fr', 'de'):
        intro_text = intro.get(try_lang)
        if not isinstance(intro_text, str):
            continue
        bid = _book_id_from_intro(intro_text, try_lang)
        if bid:
            canonical_id = bid
            break
    for lang in ('la', 'en', 'es', 'pt-BR', 'it', 'fr', 'de'):
        cit_text = cit.get(lang)
        if not isinstance(cit_text, str) or not cit_text.strip():
            continue
        first = cit_text.lstrip()
        # Already has a book token? (alpha-start, OR digit-prefix-then-alpha
        # like "1 Cor 5, 1-2")
        if first[:1].isalpha() or _NUMBERED_BOOK_PREFIX_RE.match(first):
            continue
        # Try the lang's own intro first
        abbrev = _book_abbrev_from_intro(intro.get(lang) or '', lang)
        # Fallback to canonical id from any intro lang
        if not abbrev and canonical_id:
            abbrev_map = _BOOK_ABBREV_BY_LANG.get(canonical_id)
            if abbrev_map:
                abbrev = abbrev_map.get(lang) or abbrev_map.get('la')
        if abbrev:
            cit[lang] = f"{abbrev} {cit_text}"


def _enrich_mass_reading_citations(mass: dict) -> None:
    """Walk all reading slots in the mass and enrich their citations."""
    readings = mass.get('readings')
    if not isinstance(readings, dict):
        return
    for cyc, slots in readings.items():
        if not isinstance(slots, dict):
            continue
        for slot_name in ('firstReading', 'secondReading', 'gospel'):
            r = slots.get(slot_name)
            if isinstance(r, dict):
                _enrich_reading_citation(r)
        rp = slots.get('responsorialPsalm')
        if isinstance(rp, dict):
            _backfill_responsorial_psalm_citation(rp)


# Per-language Psalm book abbreviations.
_PSALM_ABBREV = {
    'la': 'Ps', 'en': 'Ps', 'es': 'Sal', 'pt-BR': 'Sl',
    'it': 'Sal', 'fr': 'Ps', 'de': 'Ps',
}
# Recognize any of these as the leading book token in a responsorialPsalm
# citation, regardless of source lang.
_PSALM_LEADING_TOKEN_RE = re.compile(
    r'^\s*(?:Cf\.\s+)?(?:Ps|Sal|Sl|Salmo|Salm|Psalm)\s*\.?\s+',
    re.IGNORECASE,
)


# Known Vigil/Day pairs in the sanctorale — base id is the Vigil, `.z` is
# the Day mass. Distinguished here by appending a per-lang "Vigil" suffix
# to the base entry's title.
_KNOWN_VIGIL_BASE_IDS = {
    'sanctorale.08-15',  # Assumption Vigil (Aug 14 evening)
}
_VIGIL_SUFFIX_BY_LANG = {
    'la': 'IN VIGILIA',
    'en': '(Vigil Mass)',
    'es': '(Misa de la vigilia)',
    'pt-BR': '(Missa da Vigília)',
    'it': '(Messa Vigiliare)',
    'fr': '(Messe de la vigile)',
    'de': '(Vigilmesse)',
}


def _mark_known_vigil_masses(mass: dict) -> None:
    """For known Vigil/Day pairs (Assumption, etc.), append a 'Vigil' marker
    to the title of the base entry so it's distinguishable from the Day mass
    sibling (`.z`)."""
    if mass.get('id') not in _KNOWN_VIGIL_BASE_IDS:
        return
    title = mass.get('title')
    if not isinstance(title, dict):
        return
    for lang, suffix in _VIGIL_SUFFIX_BY_LANG.items():
        v = title.get(lang)
        if isinstance(v, str) and v.strip():
            up = v.upper()
            if 'VIGIL' not in up and 'VIGILIA' not in up:
                # For Latin: prefix "IN VIGILIA" before assumption.
                if lang == 'la':
                    title[lang] = suffix + ' ' + v
                else:
                    title[lang] = v + ' ' + suffix


def _backfill_responsorial_psalm_citation(rp: dict) -> None:
    """If responsorialPsalm.citation has some langs but not others, replicate
    the verse-portion across missing langs with each lang's psalm-abbrev.
    Also normalizes mismatched abbrevs (e.g. "Sl" appearing in la → "Ps")."""
    cit = rp.get('citation')
    if not isinstance(cit, dict):
        return
    # Find a source lang with a populated citation.
    src_text = None
    cf_prefix = ''
    verses = None
    for L in ('la', 'pt-BR', 'en', 'es', 'it', 'fr', 'de'):
        v = cit.get(L)
        if isinstance(v, str) and v.strip():
            src_text = v
            # Strip the leading Cf. + book token, keep the verse portion.
            stripped = src_text.strip()
            cf_match = re.match(r'^(Cf\.\s+)', stripped, re.I)
            cf_prefix = cf_match.group(1) if cf_match else ''
            without_cf = stripped[len(cf_prefix):]
            tok_match = _PSALM_LEADING_TOKEN_RE.match('Cf. ' + without_cf if cf_prefix else without_cf)
            if tok_match:
                # If the whole leading "Cf. Ps " was matched, strip it.
                verses = (without_cf if cf_prefix else stripped)
                # Now strip the Ps/Sal/Sl token from `verses`
                verses = re.sub(r'^(?:Ps|Sal|Sl|Salmo|Salm|Psalm)\s*\.?\s+', '', verses, flags=re.I)
            else:
                # No book token found — assume the citation is already bare verses
                verses = without_cf if cf_prefix else stripped
            break
    if verses is None:
        return
    # Apply per-lang abbrev for any missing or mismatched lang.
    for lang, abbrev in _PSALM_ABBREV.items():
        existing = cit.get(lang)
        target = f"{cf_prefix}{abbrev} {verses}".strip()
        if not isinstance(existing, str) or not existing.strip():
            cit[lang] = target
        else:
            # If existing has a different psalm abbrev for this lang, fix it.
            cur_match = _PSALM_LEADING_TOKEN_RE.match(existing.strip())
            if cur_match:
                cur_abbrev = re.match(r'^(?:Cf\.\s+)?(\S+)', existing.strip(), re.I).group(1)
                if cur_abbrev.lower() not in (abbrev.lower(),):
                    rest = re.sub(r'^(?:Cf\.\s+)?(?:Ps|Sal|Sl|Salmo|Salm|Psalm)\s*\.?\s+', '', existing.strip(), flags=re.I)
                    cit[lang] = f"{cf_prefix}{abbrev} {rest}".strip() if rest else f"{cf_prefix}{abbrev}".strip()


def _drop_stranded_lectio_labels(mass: dict) -> None:
    """If a reading has only `label` (no body), clear the label since it's a
    structural ghost. Concentrated in /readings/{default,A,B,C}/firstReading|
    secondReading/."""
    label_only_re = re.compile(r'^Lectio\b', re.I)
    def is_empty_body(body):
        if not isinstance(body, dict):
            return True
        plain = body.get('plain')
        if isinstance(plain, dict) and any(isinstance(v, str) and v.strip() for v in plain.values()):
            return False
        return True
    readings = mass.get('readings')
    if not isinstance(readings, dict):
        return
    for cycle, slots in readings.items():
        if not isinstance(slots, dict):
            continue
        for slot in ('firstReading', 'secondReading', 'gospel'):
            r = slots.get(slot)
            if not isinstance(r, dict):
                continue
            label = r.get('label')
            body = r.get('body')
            if isinstance(label, dict) and is_empty_body(body):
                # Drop label entries where the localized value is just "Lectio …"
                cleaned = {L: v for L, v in label.items()
                           if isinstance(v, str) and not label_only_re.match(v.strip())}
                if cleaned:
                    r['label'] = cleaned
                else:
                    r.pop('label', None)


_WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def _set_weekday_from_id(mass: dict) -> None:
    """Backfill `weekday` from the id's trailing segment (e.g. id ends with
    `...monday` → weekday='monday'). Skips if already set or id doesn't end
    in a known weekday."""
    if mass.get('weekday'):
        return
    mid = mass.get('id') or ''
    last = mid.rsplit('.', 1)[-1].lower()
    if last in _WEEKDAYS:
        mass['weekday'] = last


def _collapse_duplicate_cycles(mass: dict) -> None:
    """If readings has only `A`/`B`/`C` keys and ALL are byte-identical,
    collapse to a single `default` entry. Triggered by the Lent week-5
    weekday case where the parser duplicated the Year-A reading 3×."""
    readings = mass.get('readings')
    if not isinstance(readings, dict):
        return
    keys = set(readings.keys())
    if not keys.issubset({'A', 'B', 'C'}) or not keys:
        return
    values = list(readings.values())
    first = json.dumps(values[0], ensure_ascii=False, sort_keys=True)
    if all(json.dumps(v, ensure_ascii=False, sort_keys=True) == first for v in values[1:]):
        mass['readings'] = {'default': values[0]}


_PLACEHOLDER_TITLES = {
    'san fulano',          # es "John Doe"
    'fulano de tal',
    'lorem ipsum',
    'tbd', 'tba', 'todo',
    'placeholder',
    'sin nombre', 'no name',
    # Slug fragments that leaked from id paths into title.la (cycle 23):
    'africa', 'chile', 'spain', 'germany', 'argentina', 'uruguay',
    'france', 'italy', 'usa', 'brazil',
    # Cycle 27: complete the regional-scope token list.
    'nigeria', 'argentina-chile', 'argentina chile',
    'german-speaking', 'german speaking',
    'spanish-speaking', 'spanish speaking',
    'religious-orders', 'religious orders',
    'united-states', 'united states',
}

# Slug-pattern detection: very short tokens like `z`, `y`, or `000c` that
# came from id segments rather than real Latin titles. These are too short
# to be real titles in any language.
_SLUG_PATTERN_RE = re.compile(r'^[a-z]$|^\d{3,}[a-z]?$')


def _drop_placeholder_titles(mass: dict) -> None:
    """Drop title-language entries whose value is a placeholder (San Fulano
    style — Spanish "John Doe" leaked from the source template). If the
    title becomes entirely empty, delete the title key (the schema requires
    Localized to have at least one language)."""
    title = mass.get('title')
    if not isinstance(title, dict):
        return
    for L, v in list(title.items()):
        if not isinstance(v, str):
            continue
        s = v.strip()
        low = s.lower()
        if low in _PLACEHOLDER_TITLES or _SLUG_PATTERN_RE.match(s):
            title.pop(L, None)
    if not title:
        mass.pop('title', None)


# Cycle 23 — `Prefacio Prefacio:` / `Prefácio Prefácio:` doubled-label scanno.
_DOUBLED_PREFACE_LABEL_RE = re.compile(
    r'\b(Prefacio|Prefácio|Préface|Prefazio|Praefatio|Preface|Vorbereitungsgebet)\s+\1\b\s*[:.]?',
    re.I,
)


def _fix_doubled_preface_label(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    out = re.sub(
        r'\b(Prefacio|Prefácio)\s+\1\b\s*:',
        lambda m: f"{m.group(1)}:",
        text,
    )
    return out


# Cycle 23 — `difffícile` (triple-f) scanno surfaced in audit. Limited to
# specific spelling. Other triple-letter scannos handled separately.
def _fix_difficile(text: str) -> str:
    if not isinstance(text, str) or 'difffí' not in text:
        return text
    return text.replace('difffícile', 'diffícile')


# Cycle 23 — `N.[` → `N. [` (placeholder followed by bracket without space).
_N_BRACKET_RE = re.compile(r'\bN\.\[')


def _fix_n_bracket_spacing(text: str) -> str:
    if not isinstance(text, str) or 'N.[' not in text:
        return text
    return _N_BRACKET_RE.sub('N. [', text)


# Cycle 23 — clean trailing empty `rubric` segments at end of `lines.<lang>[i]`,
# and drop entirely-empty rubric lines.
def _clean_empty_rubric_segments(body: dict) -> None:
    if not isinstance(body, dict):
        return
    lines = body.get('lines')
    if not isinstance(lines, dict):
        return
    for lang, line_arr in list(lines.items()):
        if not isinstance(line_arr, list):
            continue
        new_lines = []
        for line in line_arr:
            if not isinstance(line, list):
                new_lines.append(line)
                continue
            # Drop EVERY empty rubric segment (cycle 27): leading, terminal,
            # and separators between text segments. Empty rubrics are
            # residual markers from stripped italic/parenthetical formatting.
            cleaned = [
                seg for seg in line
                if not (
                    isinstance(seg, dict)
                    and seg.get('type') == 'rubric'
                    and not (seg.get('text') or '').strip()
                )
            ]
            # Drop entirely-empty lines (no content after trim)
            if not cleaned:
                continue
            new_lines.append(cleaned)
        lines[lang] = new_lines


def _merge_adjacent_segments(body: dict) -> None:
    """Merge adjacent same-type segments inside each Line[]. Cycle-27 audit
    found 700+ punctuation-only segments (`","`, `";"`, etc.) sandwiched
    between text segments after italic-marker cleanup, plus 13 cases of
    legitimate adjacent same-type text segments. Joining produces the
    natural reading order.

    Joining rules:
    - Adjacent segs of the same `type` merge.
    - If the right side starts with a punctuation char, no inserted space.
    - If left ends with whitespace OR right starts with whitespace,
      simple concat (no extra space).
    - Otherwise insert a single space (avoids gluing word-tokens).
    """
    if not isinstance(body, dict):
        return
    lines = body.get('lines')
    if not isinstance(lines, dict):
        return
    PUNCT_CONTINUE = {',', '.', ';', ':', '!', '?', ')', ']', '»', "'", '"', '…'}
    for lang, line_arr in list(lines.items()):
        if not isinstance(line_arr, list):
            continue
        for li, line in enumerate(line_arr):
            if not isinstance(line, list) or len(line) < 2:
                continue
            merged = [line[0]]
            for seg in line[1:]:
                last = merged[-1]
                if (
                    isinstance(last, dict)
                    and isinstance(seg, dict)
                    and last.get('type') == seg.get('type')
                    and isinstance(last.get('text'), str)
                    and isinstance(seg.get('text'), str)
                ):
                    a = last['text']
                    b = seg['text']
                    if not a:
                        last['text'] = b
                        continue
                    if not b:
                        continue
                    first_b = b.lstrip()[:1]
                    last_a = a.rstrip()[-1:]
                    if first_b in PUNCT_CONTINUE:
                        sep = ''
                    elif a.endswith(' ') or b.startswith(' '):
                        sep = ''
                    elif last_a in '([«' or last_a == '':
                        sep = ''
                    else:
                        sep = ' '
                    last['text'] = a + sep + b
                else:
                    merged.append(seg)
            line_arr[li] = merged


def _merge_adjacent_segments_in_mass(mass: dict) -> None:
    """Walk a tree applying _merge_adjacent_segments to every body block."""
    def walk(node):
        if isinstance(node, dict):
            if 'lines' in node and isinstance(node.get('lines'), dict):
                _merge_adjacent_segments(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(mass)


def _clean_empty_rubric_segments_in_mass(mass: dict) -> None:
    """Walk a tree applying _clean_empty_rubric_segments to every body block
    (anything with a `lines` field of lang->[lines] shape)."""
    def walk(node):
        if isinstance(node, dict):
            if 'lines' in node and isinstance(node.get('lines'), dict):
                _clean_empty_rubric_segments(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(mass)


def _backfill_missing_title(mass: dict) -> None:
    """If a mass has body content but no title at all, synthesize a minimal
    title from the id. Better than rendering a `?` placeholder."""
    title = mass.get('title')
    has_title = isinstance(title, dict) and any(
        isinstance(v, str) and v.strip() for v in title.values())
    if has_title:
        return
    # Has any body content?
    has_body = any(mass.get(slot) for slot in (
        'collect', 'prayerOverOfferings', 'postcommunion', 'entranceAntiphon',
        'communionAntiphon', 'preface', 'parts', 'readings'))
    if not has_body:
        return
    mid = mass.get('id') or ''
    # Use the trailing id segment as a title fragment, prettified
    last = mid.rsplit('.', 1)[-1]
    pretty = last.replace('-', ' ').replace('_', ' ').strip()
    # Skip if pretty is purely numeric/separators — that's a placeholder, not
    # a meaningful name. Better to leave the title absent.
    if pretty and not re.match(r'^[\d\s-]+$', pretty):
        # Cycle 27: don't backfill scope-token tails (`africa`, `chile`, etc.)
        # — these come from regional `sanctorale.04-04.africa`-style ids and
        # would re-introduce the placeholder pollution that `_drop_placeholder_titles`
        # just cleaned up.
        if pretty.lower() in _PLACEHOLDER_TITLES or _SLUG_PATTERN_RE.match(pretty):
            return
        mass['title'] = {'la': pretty}


def _drop_vernacular_la_leak(node: dict, field: str, min_length: int = 30) -> None:
    """If `node[field]` is a Localized dict and a vernacular value byte-equals
    the Latin value (and is at least `min_length` chars long), drop the
    vernacular entry. Only applies to known import-leak languages where the
    Latin column was used as a fallback when the translation row was empty."""
    loc = node.get(field)
    if not isinstance(loc, dict):
        return
    la = loc.get('la')
    if not isinstance(la, str) or len(la.strip()) < min_length:
        return
    for v_lang in ('en', 'es', 'pt-BR', 'it', 'fr', 'de'):
        v = loc.get(v_lang)
        if isinstance(v, str) and v.strip() == la.strip():
            loc.pop(v_lang, None)


_TRAILING_RUBRIC_MARKER_RE = re.compile(r'\s*[℟℣]\s*$|\s*[RV]/\s*\.?\s*$')
_LEADING_RUBRIC_MARKER_RE = re.compile(r'^\s*[℟℣]\s*|^\s*[RV]/\s*')


# Parenthetical conditional rubrics in lectionary stage-directions across all
# 7 languages. These wrap the entire segment text and indicate when something
# applies (e.g. "(Si catechumeni adsunt)" = "if catechumens are present").
_CONDITIONAL_RUBRIC_RE = re.compile(
    r"^\s*\((?:Si\s|If\s|Se\s|Wenn\s|Quand\s|Quando\s|Caso\s|"
    r"Sin?\s+(?:catechumeni|hay|se)|Lorsque|Lorsqu'|S'il\s|N\.\s|"
    r"Em\s+caso|Em\s+seguida|Em\s+caso\s+contrário)",
    re.I,
)


def _retype_parenthetical_conditions(rt: dict) -> None:
    """A `text` segment whose entire content is a parenthetical condition
    ('(Si catechumeni adsunt)') is a stage direction — re-type as rubric."""
    if not isinstance(rt, dict):
        return
    lines = rt.get('lines')
    if not isinstance(lines, dict):
        return
    for L, langlines in lines.items():
        if not isinstance(langlines, list): continue
        for line in langlines:
            if not isinstance(line, list): continue
            for seg in line:
                if (isinstance(seg, dict)
                        and seg.get('type') == 'text'
                        and isinstance(seg.get('text'), str)):
                    t = seg['text'].strip()
                    if t.startswith('(') and t.endswith(')') and _CONDITIONAL_RUBRIC_RE.match(t):
                        seg['type'] = 'rubric'


def _retype_vel_alleluia_as_response(rt: dict) -> None:
    """Inside body.lines, when a `vel:` rubric (or `or:`/`oder:` etc.) is
    immediately followed by a lone `Allelúia.` text segment, the Alleluia is
    actually the alternative response refrain — re-type as `response`."""
    if not isinstance(rt, dict):
        return
    lines = rt.get('lines')
    if not isinstance(lines, dict):
        return
    vel_re = re.compile(r'^(?:vel|or|oder|ou|o)[:.]?\s*$', re.I)
    # Allelúia / Alleluia / Hallelujah / Aleluya / Aleluia — Latin/vernacular
    # variants. Matches with optional accent on the `u` and trailing period.
    alleluia_re = re.compile(r'^(?:Al+el+[uúù](?:ia|ya|ja)|Hal+el+u(?:ja|jah))\.?$', re.I)
    for L, langlines in lines.items():
        if not isinstance(langlines, list): continue
        for line in langlines:
            if not isinstance(line, list): continue
            for i, seg in enumerate(line):
                if not isinstance(seg, dict): continue
                if seg.get('type') != 'rubric': continue
                t = (seg.get('text') or '').strip()
                if not vel_re.match(t):
                    continue
                # Next segment in same line should be the Alleluia text
                if i + 1 < len(line):
                    nxt = line[i + 1]
                    if (isinstance(nxt, dict)
                            and nxt.get('type') == 'text'
                            and alleluia_re.match((nxt.get('text') or '').strip())):
                        nxt['type'] = 'response'


def _retype_alleluia_in_mass(mass: dict) -> None:
    """Walk all rich-text bodies in the mass and apply rubric retypings."""
    def walk(node):
        if isinstance(node, dict):
            if 'lines' in node and 'plain' in node and isinstance(node.get('lines'), dict):
                _retype_vel_alleluia_as_response(node)
                _retype_parenthetical_conditions(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(mass)


def _strip_rubric_markers_from_text(mass: dict) -> None:
    """Remove `R/`/`R/.`/`V/.`/`℟`/`℣` markers that bled into `text` segments
    inside `body.lines`. Trailing markers are stripped; leading markers are
    stripped (the surrounding response semantics already live in the segment
    type or the next line). Intentionally narrow: only operates on segments
    explicitly typed `text`."""
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == 'lines' and isinstance(v, dict):
                    for L, langlines in v.items():
                        if not isinstance(langlines, list):
                            continue
                        for line in langlines:
                            if not isinstance(line, list):
                                continue
                            for seg in line:
                                if (isinstance(seg, dict)
                                        and seg.get('type') == 'text'
                                        and isinstance(seg.get('text'), str)):
                                    t = seg['text']
                                    t = _TRAILING_RUBRIC_MARKER_RE.sub('', t)
                                    t = _LEADING_RUBRIC_MARKER_RE.sub('', t)
                                    seg['text'] = t
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(mass)


def _fix_empty_lines(mass: dict) -> None:
    """If a body has populated `plain` but `lines` is an empty dict — or if
    `plain.<L>` is set but `lines.<L>` is missing — build minimal lines
    (a single text segment per missing language) so plain/lines stay in
    sync per language."""
    def walk(node):
        if isinstance(node, dict):
            plain = node.get('plain')
            lines = node.get('lines')
            if isinstance(plain, dict) and isinstance(lines, dict):
                # Case 1: empty `lines` dict, populated plain.
                if not lines and any(isinstance(v, str) and v.strip()
                                     for v in plain.values()):
                    rebuilt = {}
                    for L, txt in plain.items():
                        if isinstance(txt, str) and txt.strip():
                            rebuilt[L] = [[{"type": "text", "text": txt}]]
                    node['lines'] = rebuilt
                # Cycle 35: case 2 — per-lang gap (plain.es set but no lines.es).
                else:
                    for L, txt in plain.items():
                        if (isinstance(txt, str) and txt.strip()
                                and L not in lines):
                            lines[L] = [[{"type": "text", "text": txt}]]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(mass)


# ---------------------------------------------------------------------------
# Liturgical color assignment (Roman Rite OF)
# ---------------------------------------------------------------------------
# Single source of truth: rank + season + sanctorale rubric (martyr, apostle,
# Marian, etc.). Used for app-side color coding. Preserves any value already
# set on the mass.

_MARTYR_KEYWORDS = (
    'martyris', 'martyrum', 'martyr', 'martyre', 'mártires',
    'mártir', 'mártires', 'martire', 'martiri', 'märtyrer', 'martyrs',
    'martirio',
)
_APOSTLE_KEYWORDS = (
    'apostolorum', 'apostoli', 'apóstol', 'apóstoles', 'apostolo',
    'apostoli', 'apostle', 'apostles', 'apostel', 'apôtre', 'apôtres',
)
_PENTECOST_KEYWORDS = (
    'pentecostes', 'pentecost', 'pentecôte', 'pingstdag', 'pfingsten',
)
_GOOD_FRIDAY_KEYWORDS = (
    'good friday', 'in passione domini', "passion du seigneur",
    'sexta-feira santa', 'venerdì santo', 'viernes santo', 'karfreitag',
    'feria vi in passione',
)
_ALL_SOULS_KEYWORDS = (
    'all the faithful departed', 'commemoratione omnium fidelium defunctorum',
    'fiéis defuntos', 'fieles difuntos', 'fedeli defunti',
    'fidèles défunts', 'allerseelen',
)
_PASSION_KEYWORDS = (
    'passione domini', 'passion du seigneur', 'pasión del señor',
    'paixão do senhor', 'passione del signore', 'passion of our lord',
    'leiden des herrn',
)
# Marian / Joseph / "of the Lord" keywords that mark a feast as WHITE even
# when other (red-flagging) words appear in the same title.
_WHITE_OVERRIDE_KEYWORDS = (
    'beatæ mariæ', 'beatae mariae', 'mariæ virginis', 'mariae virginis',
    'virgen maría', 'virgen maria', 'virgine maria', 'vergine maria',
    'vierge marie', 'jungfrau maria', 'virgem maria',
    'cordis beatæ', 'cordis beatae', 'inmaculado corazón', 'imaculado coração',
    'cuore immacolato', 'cœur immaculé', 'unbeflecktes herz',
    'beata vergine', 'bienaventurada virgen', 'bem-aventurada virgem',
    'mãe da igreja', 'madre de la iglesia', 'madre della chiesa',
    "mère de l'église", 'mutter der kirche', 'mother of the church',
    'sacratissimi cordis', 'sacratissimo cuore', 'sacratísimo corazón',
    'sacratíssimo coração', 'sacré-cœur', 'sacred heart', 'heiligstes herz',
    'sancti ioseph', 'san josé', 'são josé', 'san giuseppe', 'saint joseph',
    'heiligen joseph', 'most holy eucharist', 'sanctissimi corporis',
    'santísima eucaristía', 'santíssima eucaristia', 'eucharistie',
    'eucaristia', 'eucharistia', 'sanctissimæ trinitatis',
    'most holy trinity', 'santísima trinidad', 'santíssima trindade',
    'santissima trinità', 'sainte trinité', 'heiligste dreifaltigkeit',
    'all saints', 'tous les saints', 'todos los santos', 'todos os santos',
    'tutti i santi', 'allerheiligen', 'omnium sanctorum',
    # Christ-feast votives → white:
    'most holy name of jesus', 'santíssimo nome de jesus',
    'santísimo nombre de jesús', 'santissimo nome di gesù',
    'saint nom de jésus', 'heiliger name jesu',
    'eternal high priest', 'sumo e eterno sacerdote', 'sommo ed eterno sacerdote',
    'sumo y eterno sacerdote', 'souverain et éternel prêtre',
    'ewiger hohepriester', 'aeterni summi sacerdotis',
    # Mercy of God / Divine Mercy → white:
    'mercy of god', 'misericordia de dios', 'misericordia di dio',
    'misericórdia de deus', 'miséricorde de dieu', 'barmherzigkeit gottes',
    'divinae misericordiae',
    # Holy Angels → white:
    'holy angels', 'santos ángeles', 'santos anjos', 'santi angeli',
    'saints anges', 'heilige engel', 'sanctorum angelorum',
    # John the Baptist (non-martyr feast) → white:
    'john the baptist', 'juan bautista', 'juan el bautista',
    'joão batista', 'giovanni battista', 'jean baptiste', 'jean-baptiste',
    'johannes der täufer', 'ioannis baptistæ', 'ioannis baptistae',
)
# Cross feasts and Holy Cross votives → red (Christ's Passion symbol).
_CROSS_RED_KEYWORDS = (
    'exaltatione sanctæ crucis', 'exaltatione sanctae crucis',
    'exaltation of the holy cross', 'esaltazione della santa croce',
    'exaltación de la santa cruz', 'exaltação da santa cruz',
    'la croix glorieuse', 'kreuzerhöhung', 'inventione sanctæ crucis',
    'finding of the holy cross', 'invención de la santa cruz',
    'invenção da santa cruz',
    # Votive Masses of the Holy Cross / Mystery of the Cross:
    'mystery of the holy cross', 'mystério da santa cruz',
    'misterio de la santa cruz', 'mistero della santa croce',
    'mystère de la sainte croix', 'mysterium des heiligen kreuzes',
    'sanctæ crucis', 'sanctae crucis', 'holy cross', 'santa cruz',
    'santa croce', 'sainte croix', 'heiliges kreuz',
)
# Holy Spirit / Precious Blood / Apostles votives → red.
_VOTIVE_RED_KEYWORDS = (
    'holy spirit', 'spiritus sancti', 'espíritu santo', 'espírito santo',
    'spirito santo', 'esprit saint', 'heiligen geist',
    'precious blood', 'pretiosissimi sanguinis', 'sangue preziosissimo',
    'sangre preciosísima', 'sangue preciosíssimo', 'sang précieux',
    'kostbares blut',
)


def _title_blob(mass: dict) -> str:
    t = mass.get('title') or {}
    return ' '.join(v for v in t.values() if isinstance(v, str)).lower()


_LATE_ADVENT_DAY_IDS = {
    'tempore.christmas.day-117',
    'tempore.christmas.day-118',
    'tempore.christmas.day-119',
    'tempore.christmas.day-120.sunday',
    'tempore.christmas.day-121.monday',
    'tempore.christmas.day-122.tuesday',
    'tempore.christmas.day-123.wednesday',
    'tempore.christmas.day-124.thursday',
}


def _reclassify_late_advent_season(mass: dict) -> None:
    """Dec 17-24 weekdays are still Advent (violet), not Christmas (white).
    The upstream HTML grouped them under `tempore.christmas.*` for its own
    pagination but liturgically they remain in Advent."""
    if mass.get('id') in _LATE_ADVENT_DAY_IDS:
        mass['season'] = 'advent'


def _clear_late_advent_weekday(mass: dict) -> None:
    """Dec 17-24 are fixed-date ferias whose weekday changes year-to-year.
    The id suffix (`.sunday`, `.monday`...) is from one specific year of the
    upstream HTML and is calendrically wrong as a stable property — clear it
    so consumers don't trust a stale weekday."""
    if mass.get('id') in _LATE_ADVENT_DAY_IDS:
        mass.pop('weekday', None)


def _assign_liturgical_color(mass: dict) -> None:
    if mass.get('liturgicalColor'):
        return
    season = (mass.get('season') or '').lower()
    rank = (mass.get('rank') or '').lower()
    blob = _title_blob(mass)
    mid = mass.get('id') or ''

    # Order: most specific → least specific. Apostles/martyrs/cross are red
    # regardless of any "Mary" mention (e.g. "Sanctorum Petri et Pauli, ad
    # Beatam Mariam" is still about apostles). Conversely, Marian feasts
    # whose titles include date rubrics like "Pentecosten" should NOT match
    # red on the date-mention.
    if any(k in blob for k in _ALL_SOULS_KEYWORDS):
        mass['liturgicalColor'] = 'violet'
        return
    if any(k in blob for k in _GOOD_FRIDAY_KEYWORDS) or 'good-friday' in mid:
        mass['liturgicalColor'] = 'red'
        return
    # Ritual: For the Dead → violet (funeral, memorial, anniversary)
    if mid.startswith('ritual.for-the-dead'):
        mass['liturgicalColor'] = 'violet'
        return
    # Common of: pastors / saints / virgins / doctors / dedication → white
    # (martyrs handled by martyr-keyword check below)
    if mid.startswith('common.') and not mid.startswith('common.martyrs'):
        mass['liturgicalColor'] = 'white'
        return
    if any(k in blob for k in _CROSS_RED_KEYWORDS):
        mass['liturgicalColor'] = 'red'
        return
    if any(k in blob for k in _APOSTLE_KEYWORDS):
        mass['liturgicalColor'] = 'red'
        return
    if any(k in blob for k in _MARTYR_KEYWORDS):
        mass['liturgicalColor'] = 'red'
        return
    if any(k in blob for k in _VOTIVE_RED_KEYWORDS):
        mass['liturgicalColor'] = 'red'
        return

    # WHITE override — Marian/Joseph/Trinity/Eucharist/All Saints/Sacred Heart/etc.
    # Runs AFTER apostle/martyr so a feast that's both Marian and martyr (rare)
    # stays red. Catches Marian feasts where "Pentecosten" appears as a date.
    if any(k in blob for k in _WHITE_OVERRIDE_KEYWORDS):
        mass['liturgicalColor'] = 'white'
        return

    if any(k in blob for k in _PENTECOST_KEYWORDS):
        mass['liturgicalColor'] = 'red'
        return
    if any(k in blob for k in _PASSION_KEYWORDS) and 'palm' not in blob:
        mass['liturgicalColor'] = 'red'
        return

    # Holy Week special cases (after the rite-specific overrides above):
    # weekdays Mon-Wed and Holy Saturday morning → violet (Lent continues);
    # Chrism Mass + Lord's Supper → white; Easter Vigil already handled.
    if season == 'holy-week' or 'holy-week' in mid:
        if 'chrism' in mid or 'chrism' in blob:
            mass['liturgicalColor'] = 'white'
            return
        if 'lords-supper' in mid or "lord's supper" in blob or 'cena domini' in blob:
            mass['liturgicalColor'] = 'white'
            return
        if 'easter-vigil' in mid:
            mass['liturgicalColor'] = 'white'
            return
        if 'palm-sunday' in mid:
            mass['liturgicalColor'] = 'red'
            return
        # Mon/Tue/Wed of Holy Week — Lenten violet
        mass['liturgicalColor'] = 'violet'
        return

    # Season-driven defaults
    if season in ('advent', 'lent', 'quadragesima'):
        mass['liturgicalColor'] = 'violet'
        return
    if season in ('easter', 'christmas'):
        mass['liturgicalColor'] = 'white'
        return

    # Rank-driven for sanctorale + ordinary
    if rank in ('solemnity', 'feast'):
        mass['liturgicalColor'] = 'white'
        return

    # Sanctorale default: white for most saints (non-martyr non-apostle)
    if mid.startswith('sanctorale.'):
        mass['liturgicalColor'] = 'white'
        return

    # Ordinary time default
    mass['liturgicalColor'] = 'green'


def _normalize_weekday_reading_cycle(mass: dict) -> None:
    """A weekday with a SINGLE Sunday-style cycle key (`A` alone) is a
    parser mis-label — rename to `default`. Weekdays with all three
    Sunday cycles (A/B/C) are intentional Lent-week-5 alternates and are
    left untouched."""
    if mass.get('weekday') in (None, 'sunday'):
        return
    readings = mass.get('readings')
    if not isinstance(readings, dict):
        return
    keys = list(readings.keys())
    if keys == ['A']:
        readings['default'] = readings.pop('A')


def _drop_french_rubric_latin_leak(mass: dict) -> None:
    """If creedInstruction or gloriaInstruction has fr == la, drop fr."""
    for slot in ('creedInstruction', 'gloriaInstruction'):
        p = mass.get(slot)
        if not isinstance(p, dict):
            continue
        body = p.get('body')
        if not isinstance(body, dict):
            continue
        plain = body.get('plain')
        if not isinstance(plain, dict):
            continue
        la = plain.get('la')
        fr = plain.get('fr')
        if isinstance(la, str) and isinstance(fr, str) and la.strip() == fr.strip():
            plain.pop('fr', None)
            lines = body.get('lines')
            if isinstance(lines, dict):
                lines.pop('fr', None)


# Tempore solemnities that should always carry rank=solemnity — explicit
# whitelist rather than heuristic so we don't accidentally promote ferials.
_SOLEMNITY_IDS = {
    'tempore.christmas.day-141.monday',           # Mary, Mother of God (Jan 1)
    'tempore.christmas.nativity-vigil',
    'tempore.easter.week-1.sunday',               # Easter Sunday
    'tempore.holy-week.easter-vigil',             # Easter Vigil (Triduum apex)
    'tempore.easter.week-6.thursday.a',           # alt Ascension
    'tempore.easter.week-8.sunday',               # Pentecost (canonically a solemnity)
    'sanctorale.11-02',                           # All Souls
    'sanctorale.11-02y',
    'sanctorale.11-02z',
}

_SOLEMNITY_LOCALIZED = {
    'la': 'Sollemnitas',
    'es': 'Solemnidad',
    'en': 'Solemnity',
    'pt-BR': 'Solenidade',
    'it': 'SOLENNITÀ',
    'fr': 'Solennité',
    'de': 'Hochfest',
}

_OPTIONAL_MEMORIAL_LOCALIZED = {
    'la': 'Memoria ad libitum',
    'es': 'Memoria libre',
    'en': 'Optional Memorial',
    'pt-BR': 'Memória facultativa',
    'it': 'Memoria facoltativa',
    'fr': 'Mémoire facultative',
    'de': 'Nicht gebotener Gedenktag',
}

_MEMORIAL_LOCALIZED = {
    'la': 'Memoria',
    'es': 'Memoria',
    'en': 'Memorial',
    'pt-BR': 'Memória',
    'it': 'Memoria',
    'fr': 'Mémoire',
    'de': 'Gebotener Gedenktag',
}

_FEAST_LOCALIZED = {
    'la': 'Festum',
    'es': 'Fiesta',
    'en': 'Feast',
    'pt-BR': 'Festa',
    'it': 'Festa',
    'fr': 'Fête',
    'de': 'Fest',
}

_RANK_LOCALIZED = {
    'optional-memorial': _OPTIONAL_MEMORIAL_LOCALIZED,
    'memorial': _MEMORIAL_LOCALIZED,
    'feast': _FEAST_LOCALIZED,
    'solemnity': _SOLEMNITY_LOCALIZED,
}


def _backfill_rank_localized(node: dict) -> None:
    """If `rank` is set but `rankLocalized` is missing/empty, fill from
    the canonical map. Used both for masses and for saints."""
    rank = node.get('rank')
    if not isinstance(rank, str) or rank not in _RANK_LOCALIZED:
        return
    rl = node.get('rankLocalized') or {}
    if isinstance(rl, dict) and any(isinstance(v, str) and v.strip() for v in rl.values()):
        return
    node['rankLocalized'] = dict(_RANK_LOCALIZED[rank])


def _backfill_sanctorale_rank(mass: dict) -> None:
    """Sanctorale entries with no explicit rank default to optional-memorial
    (the canonical default rank in the General Roman Calendar). Skip masses
    with no title — those are placeholders that get dropped elsewhere."""
    mid = mass.get('id') or ''
    if not mid.startswith('sanctorale.'):
        return
    if mass.get('rank'):
        # Backfill rankLocalized if missing (covers all rank types)
        _backfill_rank_localized(mass)
        return
    title = mass.get('title') or {}
    if not isinstance(title, dict) or not any(
        isinstance(v, str) and v.strip() for v in title.values()
    ):
        return
    mass['rank'] = 'optional-memorial'
    _backfill_rank_localized(mass)


def _promote_known_solemnities(mass: dict) -> None:
    if mass.get('id') in _SOLEMNITY_IDS and mass.get('rank') in (None, '', 'feast'):
        mass['rank'] = 'solemnity'
        if not mass.get('rankLocalized'):
            mass['rankLocalized'] = dict(_SOLEMNITY_LOCALIZED)


def _drop_empty_mass(mass: dict) -> bool:
    """Return True if mass has no usable content and should be dropped from
    output. Empty title + no rank + no collect + no body of any kind."""
    title = mass.get('title') or {}
    if isinstance(title, dict) and any(isinstance(v, str) and v.strip() for v in title.values()):
        return False
    for slot in ('collect', 'prayerOverOfferings', 'postcommunion', 'entranceAntiphon',
                 'communionAntiphon', 'parts', 'rite', 'readings', 'preface'):
        if mass.get(slot):
            return False
    return True


def _normalize_titles(mass: dict) -> None:
    title = mass.get('title')
    if isinstance(title, dict):
        for L, v in list(title.items()):
            if isinstance(v, str):
                cleaned = _strip_title_pollution(v)
                # Fix votive missing-space-after-numeric-prefix: "8.THE MOST" → "8. THE MOST"
                cleaned = re.sub(r'^(\d+)\.([A-Z])', r'\1. \2', cleaned)
                # Fix mid-word capitalization typos like "ChristI" → "Christi"
                cleaned = re.sub(r'\b([A-Z][a-zà-ÿæœ]+)([A-Z])\b',
                                 lambda m: m.group(1) + m.group(2).lower(), cleaned)
                # Fix mid-word capital like "BeaTa" / "SanTi" / "beaTa" / "MonTe"
                # → lowercase the inner uppercase. The first letter may be
                # upper- or lower-case (covers "beaTa" = lowercase start).
                cleaned = re.sub(
                    r'\b([A-Za-zÀ-ÿæœ][a-zà-ÿæœ]+)([A-Z])([a-zà-ÿæœ]+)\b',
                    lambda m: m.group(1) + m.group(2).lower() + m.group(3),
                    cleaned,
                )
                # Insert space if a digit-period-letter cluster like "7.AlTre"
                cleaned = re.sub(r'(\d)\.([A-Za-z])', r'\1. \2', cleaned)
                title[L] = cleaned


_LEADING_ROMAN_LEAK_RE = re.compile(
    r'(^|(?<=[.!?»"\'\)]\s))([IVX]{1,4})([A-ZÉÈÀÎÔÛÄÖÜÇ][a-zàáâäãéèêëíîïóôöõúûüçñ])'
)


def _strip_leading_roman_leak(text: str, lang: str) -> str:
    """Strip leading roman-numeral verse-marker that got concatenated to the
    next word: 'IEn ce jour-là' -> 'En ce jour-là', 'IIn comunione' -> 'In
    comunione', 'IVoici' -> 'Voici'. Operates at start-of-string OR after
    sentence-end punctuation. Will NOT strip when the numeral is followed by
    a lowercase letter (preserving 'IVe siècle', 'IIIe siècle' French
    ordinals)."""
    if not isinstance(text, str) or not text:
        return text
    return _LEADING_ROMAN_LEAK_RE.sub(lambda m: f"{m.group(1)}{m.group(3)}", text)


def _walk_lang_strings(mass: dict, fn) -> None:
    """Walk the mass tree applying `fn(text, lang)` to every string under a
    language-keyed branch. Handles both `plain.<lang>` (string) and
    `lines.<lang>` (nested array of segment dicts whose `text` field is the
    string)."""
    LANGS = ('la', 'en', 'es', 'pt-BR', 'it', 'fr', 'de')

    def descend(node, lang):
        if isinstance(node, dict):
            # If we are inside a lang-keyed subtree and we hit a `text` field
            # (string), apply fn.
            if lang and isinstance(node.get('text'), str):
                node['text'] = fn(node['text'], lang)
            for k, v in list(node.items()):
                if k == 'text' and lang:
                    continue  # already handled above
                if k in LANGS:
                    if isinstance(v, str):
                        node[k] = fn(v, k)
                    else:
                        descend(v, k)
                else:
                    descend(v, lang)
        elif isinstance(node, list):
            for item in node:
                descend(item, lang)

    descend(mass, None)


def _strip_leading_roman_leak_in_mass(mass: dict) -> None:
    _walk_lang_strings(mass, _strip_leading_roman_leak)


_DOUBLED_ALLELUIA_RE = re.compile(
    r'\b(Allelúia|Alléluia|Alleluia|Aleluia|Aleluya)\s+(Allelúia|Alléluia|Alleluia|Aleluia|Aleluya)\b'
)


def _fix_doubled_alleluia(text: str, lang: str) -> str:
    """`Aleluya Aleluya. Esta` -> `Aleluya, aleluya. Esta`. Adds the missing
    comma between two repetitions of the acclamation and lowercases the
    second instance per liturgical typesetting convention."""
    if not isinstance(text, str) or not text:
        return text

    def repl(m):
        first = m.group(1)
        second = m.group(2)
        return f"{first}, {second.lower()}"

    out = _DOUBLED_ALLELUIA_RE.sub(repl, text)
    # If the result has `Aleluya, aleluya Veni` (no period after second), insert a period
    out = re.sub(
        r'(Aleluya|Alleluia|Aleluia|Allelúia|Alléluia),\s+(aleluya|alleluia|aleluia|allelúia|alléluia)\s+(?=[A-ZÀ-Ý])',
        lambda m: f"{m.group(1)}, {m.group(2)}. ",
        out,
    )
    return out


def _fix_doubled_alleluia_in_mass(mass: dict) -> None:
    _walk_lang_strings(mass, _fix_doubled_alleluia)


def _fix_double_period_before_marker(text: str) -> str:
    """`X.. Aleluia` / `N .. Por` / `Senhor.. Ou:` / `Popolo.. Cantori:` —
    collapse exactly two adjacent periods to one. Does NOT touch ellipses
    (three or more dots)."""
    if not isinstance(text, str) or not text:
        return text
    # Match exactly `..` not preceded or followed by another `.` (so ellipsis
    # stays untouched), with optional whitespace before the second period.
    out = re.sub(r'(?<!\.)\s?\.\s*\.(?!\.)', '.', text)
    # Re-tighten: ensure single space after the resulting `.` if a word follows
    out = re.sub(r'\.\s{2,}', '. ', out)
    return out


def _fix_double_period_before_marker_in_mass(mass: dict) -> None:
    _walk_lang_strings(mass, lambda t, _lang: _fix_double_period_before_marker(t))


# One-off OCR/text scannos surfaced by the audit. Each entry is (pattern,
# replacement, lang_filter). Lang filter is None for any-lang or a tuple of
# allowed lang codes.
_TEXT_SCANNOS = [
    # `nada do qu me deu` -> `nada do que me deu` (sanctorale.11-02 pt-BR)
    (re.compile(r'\bdo qu me deu\b'), 'do que me deu', ('pt-BR',)),
    # Latin: `dignátus esinter labóres` -> `dignátus es inter labóres`
    (re.compile(r'\besinter\b'), 'es inter', ('la',)),
    # `o .seu amor` -> `o seu amor` (stray period mid-text in pt-BR)
    (re.compile(r'\bo \.seu amor\b'), 'o seu amor', ('pt-BR',)),
    # `(E . alleluia )` -> `(E. T. alleluia)` (Easter Time abbreviation)
    (re.compile(r'\(E \. alleluia \)'), '(E. T. alleluia)', ('en',)),
    # `Buried with christ` (and similar) — capitalize Christ after `with `
    (re.compile(r'\bwith christ\b'), 'with Christ', ('en',)),
    (re.compile(r'\blive with christ\b'), 'live with Christ', ('en',)),
    # `aSim` -> `assim` (OCR of doubled `ss` rendered as capital `S` in pt-BR)
    (re.compile(r'\baSim\b'), 'assim', ('pt-BR',)),
    # `rnJentras` -> `mientras` (Spanish OCR `m` → `rn` and `i` → `J`)
    (re.compile(r'\brnJentras\b'), 'mientras', ('es', 'pt-BR')),
    # `sacerdoTali` -> `sacerdotali` (already covered by midword cap, but
    # explicit here for safety in cases regex misses)
    (re.compile(r'\bsacerdoTali\b'), 'sacerdotali', ('la',)),
    # Latin `sacerdótesDeo` -> `sacerdótes Deo`
    (re.compile(r'\bsacerdótesDeo\b'), 'sacerdótes Deo', ('la',)),
    # `beatárumNous` -> `beatárum Nous` (concatenated Latin+French)
    (re.compile(r'\bbeatárumNous\b'), 'beatárum Nous', ('fr',)),
    # `tuórumSeigneur` -> `tuórum Seigneur` (Latin+French)
    (re.compile(r'\btuórumSeigneur\b'), 'tuórum Seigneur', ('fr',)),
    # `sanAccogli` -> `san Accogli` (san + verb)
    (re.compile(r'\bsanAccogli\b'), 'san Accogli', ('it',)),
    # `Procesion` -> `Procession` (palm-sunday metadata typo)
    (re.compile(r'\bProcesion\b'), 'Procession', ('en',)),
    # `Comemoration` -> `Commemoration` (lord's-supper section heading typo)
    (re.compile(r'\bComemoration\b'), 'Commemoration', ('en',)),
]


def _fix_text_scannos(text: str, lang: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat, rep, langs in _TEXT_SCANNOS:
        if langs is not None and lang not in langs:
            continue
        out = pat.sub(rep, out)
    return out


def _fix_text_scannos_in_mass(mass: dict) -> None:
    _walk_lang_strings(mass, _fix_text_scannos)


# English title-case rules: words ≤4 chars that are articles/prepositions/
# conjunctions stay lowercase except when first or last word.
_EN_LOWERCASE_WORDS = {
    'a', 'an', 'the', 'of', 'in', 'on', 'at', 'by', 'for', 'and', 'or', 'but',
    'with', 'to', 'as', 'from', 'into', 'over', 'upon',
}

# Pt-BR: articles, prepositions, conjunctions stay lowercase except when first.
# Descriptor nouns (virgem, bispo, etc.) live in _PT_DESCRIPTOR_WORDS so we can
# context-switch them to Title Case when followed by a proper-name continuation.
_PT_LOWERCASE_WORDS = {
    'de', 'da', 'do', 'dos', 'das', 'e', 'em', 'na', 'no', 'nas', 'nos',
    'a', 'o', 'as', 'os', 'pelo', 'pela', 'pelos', 'pelas', 'para', 'por',
}


def _split_word_punct(word: str) -> tuple[str, str, str]:
    """Decompose a token into (leading_punct, core, trailing_punct)."""
    m = re.match(r"^([\"'(]*)(.*?)([\"')]?[,.;:!?]?)$", word)
    leading = m.group(1)
    core = m.group(2)
    trailing = m.group(3)
    # If core ends with a period (e.g. "ST."), pull that period into trailing
    while core.endswith('.') and not trailing.startswith('.'):
        core = core[:-1]
        trailing = '.' + trailing
    return leading, core, trailing


def _en_titlecase_word(word: str, is_first: bool, is_last: bool) -> str:
    """Title-case a single English word handling ST./SS./SAINT preserving
    abbrev style and roman numerals."""
    if not word:
        return word
    leading, core, trailing = _split_word_punct(word)
    # Roman numeral preserved
    if re.match(r'^[IVX]+$', core):
        return word
    # Saint abbreviations: ST or ST. -> St. (single period)
    if core in ('ST', 'ST.'):
        new_trailing = trailing.lstrip('.') if trailing.startswith('.') else trailing
        return f"{leading}St.{new_trailing}"
    if core in ('SS', 'SS.'):
        new_trailing = trailing.lstrip('.') if trailing.startswith('.') else trailing
        return f"{leading}Ss.{new_trailing}"
    if core == 'SAINT':
        return f"{leading}Saint{trailing}"
    if core == 'SAINTS':
        return f"{leading}Saints{trailing}"
    if core == 'BLESSED':
        return f"{leading}Blessed{trailing}"
    lower = core.lower()
    if not is_first and not is_last and lower in _EN_LOWERCASE_WORDS:
        return f"{leading}{lower}{trailing}"
    # Default Title Case: first letter upper, rest lower, but preserve
    # internal capitalization for hyphenated words (Bem-Aventurada style)
    if '-' in core:
        parts = core.split('-')
        new_parts = [p.capitalize() if p else p for p in parts]
        return f"{leading}{'-'.join(new_parts)}{trailing}"
    return f"{leading}{core.capitalize()}{trailing}"


# Words that are descriptor-position-only (lowercase mid-title, BUT capitalized
# when followed by a proper noun like 'Maria'). Used by the PT-BR titlecaser.
_PT_DESCRIPTOR_WORDS = {
    'virgem', 'virgens', 'bispo', 'bispos', 'papa', 'doutor', 'doutora',
    'doutores', 'mártir', 'mártires', 'apóstolo', 'apóstolos',
    'evangelista', 'evangelistas', 'religioso', 'religiosa',
    'religiosos', 'religiosas', 'presbítero', 'presbíteros',
    'diácono', 'diáconos', 'abade', 'abades', 'abadessa', 'abadessas',
    'monge', 'monges', 'fundador', 'fundadora', 'fundadores',
    'rei', 'rainha', 'imperador', 'imperatriz', 'profeta', 'profetas',
    'patriarca', 'patriarcas',
}

# Proper-name continuations that signal the previous word is part of a name
# (e.g., 'Virgem Maria' -> capitalize Virgem)
_PT_PROPER_NAME_CONTINUATIONS = {'maria', 'jesus', 'cristo', 'senhor', 'senhora'}


def _pt_titlecase_word(word: str, is_first: bool, next_core_lower: str = '') -> str:
    """Title-case a single pt-BR word — keeping articles/prepositions
    lowercase, capitalizing proper nouns and saint titles. Descriptors
    (virgem, bispo, etc.) stay lowercase UNLESS followed by a proper name."""
    if not word:
        return word
    leading, core, trailing = _split_word_punct(word)
    # Roman numeral preserved
    if re.match(r'^[IVX]+$', core):
        return word
    upper_map = {'SÃO': 'São', 'SANTO': 'Santo', 'SANTA': 'Santa',
                 'SANTOS': 'Santos', 'SANTAS': 'Santas',
                 'BEATO': 'Beato', 'BEATA': 'Beata',
                 'BEATOS': 'Beatos', 'BEATAS': 'Beatas'}
    if core in upper_map:
        return f"{leading}{upper_map[core]}{trailing}"
    lower = core.lower()
    # Lower-case articles/prepositions stay lowercase mid-title
    if not is_first and lower in _PT_LOWERCASE_WORDS:
        return f"{leading}{lower}{trailing}"
    # Descriptors lowercase EXCEPT when followed by a proper name
    if not is_first and lower in _PT_DESCRIPTOR_WORDS:
        if next_core_lower in _PT_PROPER_NAME_CONTINUATIONS:
            # Part of a proper-name phrase (e.g., 'Virgem Maria') -> Title case
            pass  # fall through to capitalize
        else:
            return f"{leading}{lower}{trailing}"
    if '-' in core:
        parts = core.split('-')
        new_parts = [p.capitalize() if p else p for p in parts]
        return f"{leading}{'-'.join(new_parts)}{trailing}"
    return f"{leading}{core.capitalize()}{trailing}"


def _is_all_caps(s: str) -> bool:
    """True if string has ≥6 alphabetic chars and all are uppercase."""
    if not isinstance(s, str) or not s:
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 6:
        return False
    return all(c.isupper() for c in letters)


def _titlecase_saint_title(title: str, lang: str) -> str:
    """Convert ALL-CAPS saint titles to Title Case for EN/PT-BR. Other langs
    are returned unchanged (LA/IT use ALL-CAPS by liturgical convention).
    Already-mixed-case titles pass through unchanged."""
    if not isinstance(title, str) or not title:
        return title
    if lang not in ('en', 'pt-BR'):
        return title
    if not _is_all_caps(title):
        return title
    # Tokenize on whitespace, preserving hyphens within tokens
    tokens = title.split()
    if not tokens:
        return title
    out = []
    for i, tok in enumerate(tokens):
        is_first = (i == 0)
        is_last = (i == len(tokens) - 1)
        if lang == 'en':
            out.append(_en_titlecase_word(tok, is_first, is_last))
        else:
            # Look ahead for next-word context (used to detect proper-name
            # continuations like 'Virgem Maria').
            next_core_lower = ''
            if i + 1 < len(tokens):
                _, nxt_core, _ = _split_word_punct(tokens[i + 1])
                next_core_lower = nxt_core.lower()
            out.append(_pt_titlecase_word(tok, is_first, next_core_lower))
    return ' '.join(out)


def _titlecase_sanctorale_titles(mass: dict) -> None:
    """Apply Title-Case conversion only to sanctorale entries' EN/PT-BR titles."""
    mid = mass.get('id') or ''
    if not isinstance(mid, str) or not mid.startswith('sanctorale.'):
        return
    title = mass.get('title')
    if not isinstance(title, dict):
        return
    for L in ('en', 'pt-BR'):
        v = title.get(L)
        if isinstance(v, str):
            title[L] = _titlecase_saint_title(v, L)


def _normalize_en_st_abbrev(text: str) -> str:
    """`St ` -> `St.` when followed by a capitalized name (avoid touching
    words like 'Stephen' or 'Stanislaus'). Targets the abbreviation only."""
    if not isinstance(text, str) or not text:
        return text
    # Match `St ` (no period) followed by a capitalized name OR `Saint`.
    # The pattern: word boundary, `St`, space, then [A-Z][a-z] (a name).
    return re.sub(r'\bSt (?=[A-Z][a-zàáâäéèêëíîïóôöúûüçñ])', 'St. ', text)


def _normalize_en_st_abbrev_in_mass(mass: dict) -> None:
    title = mass.get('title')
    if isinstance(title, dict):
        v = title.get('en')
        if isinstance(v, str):
            title['en'] = _normalize_en_st_abbrev(v)


_DOUBLED_QUOTE_RE = re.compile(r'»{2,}|«{2,}')


def _collapse_doubled_quotes(text: str) -> str:
    """Collapse runs of `»»` or `««` to a single quote (OCR doubling)."""
    if not isinstance(text, str) or not text:
        return text
    return _DOUBLED_QUOTE_RE.sub(lambda m: m.group(0)[0], text)


def _fix_tilde_nbsp(text: str) -> str:
    """Replace `letter~letter` with `letter letter` (tilde used as
    non-breaking space). Avoid touching URL-like `~user`."""
    if not isinstance(text, str) or not text:
        return text
    return re.sub(r'(?<=[a-zà-ÿA-ZÀ-Ý])~(?=[a-zà-ÿA-ZÀ-Ý])', ' ', text)


def _fix_colon_no_space(text: str) -> str:
    """Insert space after `:` when followed immediately by a Capital letter
    word. Skips URLs and time-of-day patterns."""
    if not isinstance(text, str) or not text:
        return text
    # Match `:` after a word, no space, then Capital letter starting a word.
    # Negative-lookbehind for `://` (URL) and digit (time).
    return re.sub(
        r'(?<![/:0-9])(?<=[a-zà-ÿA-ZÀ-Ý]):(?=[A-ZÀ-Ý][a-zà-ÿ])',
        ': ',
        text,
    )


# Mid-word capital fixes. For these specific known proper-noun continuations,
# split with a space (e.g., 'deJesus' -> 'de Jesus'). Otherwise lowercase the
# inner cap (e.g., 'vesperTina' -> 'vespertina').
_MIDWORD_CAP_RIGHT_NAMES = {
    'Jesus', 'Jesús', 'Cristo', 'Christ', 'Cristóbal', 'Maria', 'María',
    'Senhor', 'Señor', 'Seigneur', 'Vós', 'Carta', 'Liturgia',
    'Unterweisung', 'Apóstolos', 'Apóstolo', 'Gesù', 'Marie', 'Herr',
    'Sehnor',
}

# Per-lang prefix words that legitimately concatenate with a capitalized noun
# when fused (de+Jesus, da+Carta, etc.). Used as a heuristic.
_MIDWORD_CAP_LEFT_PREFIXES = {
    'pt-BR': {'de', 'da', 'do', 'dos', 'das', 'a', 'o', 'e', 'em', 'na',
              'no', 'às', 'aos', 'pela', 'pelo'},
    'es': {'de', 'del', 'la', 'el', 'los', 'las', 'en', 'a', 'mi', 'tu', 'su'},
    'it': {'di', 'da', 'del', 'della', 'dello', 'dei', 'degli', 'delle',
           'la', 'il', 'lo', 'i', 'gli', 'le', 'a', 'in', 'con'},
    'fr': {'de', 'du', 'la', 'le', 'les', 'aux', 'un', 'une', 'mes', 'ton',
           'son', 'sa', 'ses', 'ta'},
    'de': {'der', 'die', 'das', 'dem', 'den', 'des', 'und', 'ein', 'eine',
           'einer', 'einem', 'einen'},
    'la': set(),
    'en': set(),
}

_MIDWORD_CAP_RE = re.compile(r'\b([a-zà-ÿA-ZÀ-Ý][a-zà-ÿ]*)([A-ZÀ-Ý])([a-zà-ÿ]+)\b')


def _fix_midword_cap_scannos(text: str, lang: str) -> str:
    """Heuristic fix for mid-word capital OCR scannos:
    - If the right-side word (cap + tail) matches a known proper noun, split
      with a space: 'deJesus' -> 'de Jesus'
    - Otherwise lowercase the inner cap: 'vesperTina' -> 'vespertina',
      'aSim' -> 'assim', 'ChrisTum' -> 'Christum'.
    Note: prefix-only splitting (e.g., 'a' is a pt-BR article) is too noisy
    because words like 'aSim' (OCR of 'assim') would get split incorrectly.
    """
    if not isinstance(text, str) or not text:
        return text

    def repl(m):
        left, cap, right = m.group(1), m.group(2), m.group(3)
        right_word = cap + right
        # Split when right-side is a known proper noun
        if right_word in _MIDWORD_CAP_RIGHT_NAMES:
            return f"{left} {right_word}"
        # Otherwise lowercase the inner cap (default OCR-fix)
        return f"{left}{cap.lower()}{right}"

    return _MIDWORD_CAP_RE.sub(repl, text)


_ALLELUIA_END_TAIL_RE = re.compile(
    r'(Allelúia|Alléluia|Alleluia|Aleluia|Aleluya|Halleluja|Hallelujah)\s*$'
)


def _append_period_to_alleluia_end(text: str) -> str:
    """If a Gospel acclamation body ends with a bare `Alleluia`-form (no
    terminal punctuation), append `.`. Leave alone if already terminated
    with `.`, `!`, `?`, `:` — and ignore trailing whitespace."""
    if not isinstance(text, str) or not text:
        return text
    s = text.rstrip()
    if not s:
        return text
    last_char = s[-1]
    if last_char in '.!?:':
        return text
    if _ALLELUIA_END_TAIL_RE.search(s):
        return s + '.'
    return text


def _english_citation_style(text: str) -> str:
    """Normalize English citation punctuation. The corpus stores English
    citations using Latin convention (comma + period). Convert to English
    style:
      `Ps 121, 1-2. 4-5. 6-7. 8-9` -> `Ps 121:1-2, 4-5, 6-7, 8-9`
      `Heb 4, 14-16; 5, 7-9`       -> `Heb 4:14-16; 5:7-9`
      `Lk 24, 46.`                 -> `Lk 24:46`
      `Ps 89, 21-22.25 et 27`      -> `Ps 89:21-22, 25 and 27`
    """
    if not isinstance(text, str) or not text:
        return text
    s = text.strip()
    if not s:
        return text
    # Already English (uses `:`)?
    # If string already has `:` between book and verse, just normalize tail.
    has_colon = bool(re.search(r'\d:\d', s))
    if has_colon:
        # Already English-style — just strip trailing period, replace ` et ` -> ` and `
        s = re.sub(r'\.$', '', s)
        s = re.sub(r'\s+et\s+', ' and ', s)
        return s
    # Step 1: replace `<book> <ch>, <verse>` with `<book> <ch>:<verse>`
    # `Ps 121, 1-2` -> `Ps 121:1-2`
    s = re.sub(r'(\d+),\s+(\d)', r'\1:\2', s)
    # Step 2: between fragments, replace `. <num>` with `, <num>`
    # `Ps 121:1-2. 4-5. 6-7. 8-9` -> `Ps 121:1-2, 4-5, 6-7, 8-9`
    s = re.sub(r'\.\s+(\d)', r', \1', s)
    # Step 3: replace `.<num>` (no space) with `, <num>`
    # `Ps 89:21-22.25` -> `Ps 89:21-22, 25`
    s = re.sub(r'\.(\d)', r', \1', s)
    # Step 4: replace ` et ` with ` and `
    s = re.sub(r'\s+et\s+', ' and ', s)
    # Step 5: strip trailing period
    s = re.sub(r'\.$', '', s)
    return s


# English-only book abbreviations that should be Latin in `la` field.
_EN_TO_LA_BOOK_ABBREV = {
    'Sir': 'Eccli',
    'Heb': 'Hebr',
    'Gn': 'Gen',
    'Ex': 'Ex',  # same
    'Lv': 'Lev',
    'Nm': 'Num',
    'Dt': 'Deut',
    'Mk': 'Mc',
    'Lk': 'Lc',
    'Jn': 'Io',
    'Rev': 'Apoc',
    'Jas': 'Iac',
    'Phil': 'Phil',  # same
    'Eph': 'Eph',  # same
    'Col': 'Col',  # same
    'Rom': 'Rom',  # same
    'Tit': 'Tit',  # same
    'Phlm': 'Philem',
    'Acts': 'Act',
}


def _normalize_la_book_abbrev(text: str) -> str:
    """Latin citation cleanup:
    - Replace English-only book abbrevs at start with Latin equivalents.
    - Insert space after comma (e.g., `Col 3,1` -> `Col 3, 1`).
    - Strip trailing period.
    """
    if not isinstance(text, str) or not text:
        return text
    s = text.strip()
    if not s:
        return text
    # Step 1: book abbrev at start (with optional digit prefix like `1 Cor`)
    m = re.match(r'^(\d+\s+)?([A-Za-z]+)(\b)', s)
    if m:
        digit_prefix = m.group(1) or ''
        book = m.group(2)
        rest = s[m.end():]
        if book in _EN_TO_LA_BOOK_ABBREV:
            la_book = _EN_TO_LA_BOOK_ABBREV[book]
            s = f"{digit_prefix}{la_book}{rest}"
    # Step 2: insert space after comma between digits (`3,1` -> `3, 1`)
    s = re.sub(r'(\d),(\d)', r'\1, \2', s)
    # Step 3: insert space after period between digit and digit (`4.9` -> `4. 9`)
    # Only if not already followed by space, and not part of a real number like `4.5`
    # Actually liturgical citations don't use decimals so always normalize
    s = re.sub(r'(\d)\.(\d)', r'\1. \2', s)
    # Step 4: strip trailing period
    s = re.sub(r'\.$', '', s)
    return s


def _strip_citation_trailing_period(text: str) -> str:
    """Strip a single trailing `.` from a citation."""
    if not isinstance(text, str):
        return text
    return re.sub(r'\.$', '', text.rstrip())


def _normalize_citation_styles_in_mass(mass: dict) -> None:
    """Walk all reading citations in the mass and apply per-lang citation
    normalization."""
    readings = mass.get('readings')
    if not isinstance(readings, dict):
        return
    for cyc_name, cyc in readings.items():
        if not isinstance(cyc, dict):
            continue
        for slot_name, slot in cyc.items():
            if not isinstance(slot, dict):
                continue
            cit = slot.get('citation')
            if not isinstance(cit, dict):
                continue
            for lang, val in list(cit.items()):
                if not isinstance(val, str) or not val.strip():
                    continue
                if lang == 'en':
                    cit[lang] = _english_citation_style(val)
                elif lang == 'la':
                    cit[lang] = _normalize_la_book_abbrev(val)
                else:
                    # Other langs: just strip trailing period for cleanliness
                    cit[lang] = _strip_citation_trailing_period(val)


def _append_period_to_alleluia_end_in_mass(mass: dict) -> None:
    """Append terminal period to Gospel acclamation bodies that end with
    bare 'Alléluia' (no period). Apply only to gospelAcclamation slots."""
    readings = mass.get('readings')
    if not isinstance(readings, dict):
        return
    for cyc_name, cyc in readings.items():
        if not isinstance(cyc, dict):
            continue
        ga = cyc.get('gospelAcclamation')
        if not isinstance(ga, dict):
            continue
        body = ga.get('body')
        if not isinstance(body, dict):
            continue
        plain = body.get('plain')
        if isinstance(plain, dict):
            for lang, v in list(plain.items()):
                if isinstance(v, str):
                    plain[lang] = _append_period_to_alleluia_end(v)
        # Also fix lines structure: last segment in last line of each lang
        lines = body.get('lines')
        if isinstance(lines, dict):
            for lang, lang_lines in lines.items():
                if not isinstance(lang_lines, list) or not lang_lines:
                    continue
                last_line = lang_lines[-1]
                if not isinstance(last_line, list) or not last_line:
                    continue
                last_seg = last_line[-1]
                if isinstance(last_seg, dict) and isinstance(last_seg.get('text'), str):
                    last_seg['text'] = _append_period_to_alleluia_end(last_seg['text'])


# Triduum masses that have a legitimate `preamble` part containing genuine
# introductory rubrics (not misclassified mid-Mass content).
_TRIDUUM_REORDER_PREAMBLE_FIRST_IDS = {
    'tempore.holy-week.palm-sunday',
    'tempore.holy-week.easter-vigil',
    'tempore.holy-week.good-friday',
}


def _reorder_triduum_parts_preamble_first(mass: dict) -> None:
    """For specific triduum masses, move `preamble` to the first position so
    its introductory rubrics appear before the liturgical action."""
    mid = mass.get('id')
    if mid not in _TRIDUUM_REORDER_PREAMBLE_FIRST_IDS:
        return
    parts = mass.get('parts')
    if not isinstance(parts, dict):
        return
    if 'preamble' not in parts:
        return
    # Rebuild dict with preamble first
    new_parts = {'preamble': parts['preamble']}
    for k, v in parts.items():
        if k != 'preamble':
            new_parts[k] = v
    mass['parts'] = new_parts


# Canonical translations for the well-known `Preamble` section heading used
# in liturgical books across all 7 supported languages.
_PREAMBLE_HEADING_TRANSLATIONS = {
    'la': 'Praenotanda',
    'en': 'Preamble',
    'es': 'Preámbulo',
    'pt-BR': 'Preâmbulo',
    'it': 'Preambolo',
    'fr': 'Préambule',
    'de': 'Vorbemerkung',
}


def _backfill_preamble_heading(mass: dict) -> None:
    """Backfill the standard 7-lang heading translations for the `preamble`
    part of a mass. Does not overwrite any non-empty existing value."""
    parts = mass.get('parts')
    if not isinstance(parts, dict):
        return
    pre = parts.get('preamble')
    if not isinstance(pre, dict):
        return
    h = pre.get('heading')
    if not isinstance(h, dict):
        h = {}
        pre['heading'] = h
    for lang, val in _PREAMBLE_HEADING_TRANSLATIONS.items():
        if not isinstance(h.get(lang), str) or not h[lang].strip():
            h[lang] = val


def _backfill_heading_from_latin(block: dict) -> None:
    """For a block with a `heading` dict that has the `la` key but is missing
    other langs, copy the LA value into the missing lang slots. This is a
    fallback strategy — better to render the Latin name than render an empty
    title. Does not invent translations."""
    if not isinstance(block, dict):
        return
    h = block.get('heading')
    if not isinstance(h, dict):
        return
    la = h.get('la')
    # Primary fallback: use LA if available
    if isinstance(la, str) and la.strip():
        for lang in ('en', 'es', 'pt-BR', 'it', 'fr', 'de'):
            if not isinstance(h.get(lang), str) or not h[lang].strip():
                h[lang] = la
        return
    # Secondary fallback: pick the first available value across the 7 langs.
    # Iterates in priority order: en > fr > es > pt-BR > it > de (latin first
    # already handled above). Used for cases where LA is missing — better to
    # render *something* than show a blank.
    fallback_value = None
    for lang in ('en', 'fr', 'es', 'pt-BR', 'it', 'de'):
        v = h.get(lang)
        if isinstance(v, str) and v.strip():
            fallback_value = v
            break
    if not fallback_value:
        return
    for lang in ('la', 'en', 'es', 'pt-BR', 'it', 'fr', 'de'):
        if not isinstance(h.get(lang), str) or not h[lang].strip():
            h[lang] = fallback_value


def _backfill_subsection_headings_in_mass(mass: dict) -> None:
    """Walk all parts and content blocks; backfill heading lang gaps using
    Latin as fallback."""
    parts = mass.get('parts')
    if not isinstance(parts, dict):
        return

    def walk(node):
        if isinstance(node, dict):
            _backfill_heading_from_latin(node)
            content = node.get('content')
            if isinstance(content, list):
                for sub in content:
                    walk(sub)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for p in parts.values():
        walk(p)


# Cycle 21 — additional text-quality fixes surfaced by the audit.

# `$anto` / `$anta` — `$` is an OCR misread of capital `S`. Limited to the
# specific `$` + lowercase `S<word>` pattern occurring at word start so we
# don't touch legitimate `$` in prices / markdown.
_DOLLAR_S_SCANNO_WORDS = ('anto', 'anta', 'ão', 'antos', 'antas', 'aint', 'aints')


def _fix_dollar_s_scanno(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    if '$' not in text:
        return text

    def repl(m):
        return 'S' + m.group(1)

    pattern = r'(?<![A-Za-zÀ-ÿ0-9])\$(' + '|'.join(_DOLLAR_S_SCANNO_WORDS) + r')\b'
    return re.sub(pattern, repl, text)


# Italian `E\`` for `È` — backtick used as ASCII grave accent. Only fire
# when `E\`` is followed by a space so that legitimate code-quote `E\`x...`
# (rare here but possible) is left alone.
_IT_E_BACKTICK_RE = re.compile(r"(?<![A-Za-zÀ-ÿ])([Ee])`(?=\s)")


def _fix_italian_e_backtick(text: str) -> str:
    if not isinstance(text, str) or '`' not in text:
        return text

    def repl(m):
        return 'È' if m.group(1) == 'E' else 'è'

    return _IT_E_BACKTICK_RE.sub(repl, text)


# Italian `baTTez*` / `seTTim*` / `baTTesim*` / `BaTTisT*` — OCR over-cased
# the doubled `tt` in specific stems. Limit to the known stems to avoid
# touching ALL-CAPS Latin like `IL POPOLO`.
_IT_DOUBLED_TT_STEMS = (
    'baTTez', 'baTTesim', 'seTTim', 'BaTTisT',
)


def _fix_italian_doubled_tt(text: str) -> str:
    if not isinstance(text, str) or 'TT' not in text:
        return text
    out = text
    # `BaTTisT*` -> `Battist*` (proper-noun: cap T after `is` lowered too)
    out = re.sub(r'\bBaTTisT([a-zà-ÿ]*)', lambda m: 'Battist' + m.group(1), out)
    # `baTTez*` -> `battez*`
    out = re.sub(r'\bbaTTez([a-zà-ÿA-ZÀ-Ý]*)', lambda m: 'battez' + m.group(1).lower(), out)
    # `baTTesim*` -> `battesim*`
    out = re.sub(r'\bbaTTesim([a-zà-ÿA-ZÀ-Ý]*)', lambda m: 'battesim' + m.group(1).lower(), out)
    # `seTTim*` -> `settim*` (e.g., settimana, settima)
    out = re.sub(r'\bseTTim([a-zà-ÿA-ZÀ-Ý]*)', lambda m: 'settim' + m.group(1).lower(), out)
    return out


# Spanish `engeridró` -> `engendró` (he begot). 2 hits in pastors.json.
def _fix_spanish_engeridro(text: str) -> str:
    if not isinstance(text, str) or 'engeridró' not in text:
        return text
    return text.replace('engeridró', 'engendró')


# Latin `In illo it Iesus` -> `In illo tempore: Dixit Iesus`. The corruption
# is `tempore: D` getting dropped between `illo` and `it`.
def _fix_in_illo_it_scanno(text: str) -> str:
    if not isinstance(text, str) or 'In illo it' not in text:
        return text
    return re.sub(r'\bIn illo it\b', 'In illo tempore: Dixit', text)


# Citation `Cf.<Bookname>` (no space) — normalize to `Cf. <Bookname>`.
# Also handles `cf.<Capital>` (lowercase variant) and missing-space-between-
# bookname-and-chapter (`Cf.Sab3,6` -> `Cf. Sab 3,6`).
_CF_NO_SPACE_RE = re.compile(r'\b([Cc]f)\.([A-ZÀ-Ý][A-Za-zÀ-ÿ]*)')
_CF_BOOK_DIGIT_RE = re.compile(r'\b([Cc]f)\.\s([A-ZÀ-Ý][A-Za-zÀ-ÿ]*)(\d)')


def _fix_cf_no_space(text: str) -> str:
    if not isinstance(text, str) or 'f.' not in text:
        return text
    out = _CF_NO_SPACE_RE.sub(lambda m: f"{m.group(1)}. {m.group(2)}", text)
    out = _CF_BOOK_DIGIT_RE.sub(lambda m: f"{m.group(1)}. {m.group(2)} {m.group(3)}", out)
    return out


# German `HL.` (allcaps) -> `Hl.` saint abbreviation.
_DE_HL_ALLCAPS_RE = re.compile(r'\bHL\.')


def _fix_de_hl_allcaps(text: str) -> str:
    if not isinstance(text, str) or 'HL.' not in text:
        return text
    return _DE_HL_ALLCAPS_RE.sub('Hl.', text)


# `Allelúia` (Latin acute) inside English text -> plain `Alleluia` /
# `alleluia`. Strictly removes the acute on the `u`.
def _fix_en_accented_alleluia(text: str) -> str:
    if not isinstance(text, str):
        return text
    if 'Allelúia' not in text and 'allelúia' not in text:
        return text
    return text.replace('Allelúia', 'Alleluia').replace('allelúia', 'alleluia')


def _apply_lang_specific_text_fixes(text: str, lang: str) -> str:
    """Bundle of language-aware text fixes applied via _walk_lang_strings."""
    if not isinstance(text, str):
        return text
    out = text
    out = _fix_dollar_s_scanno(out)
    out = _fix_cf_no_space(out)
    if lang == 'it':
        out = _fix_italian_e_backtick(out)
        out = _fix_italian_doubled_tt(out)
    if lang == 'es':
        out = _fix_spanish_engeridro(out)
    if lang == 'la':
        out = _fix_in_illo_it_scanno(out)
    if lang == 'de':
        out = _fix_de_hl_allcaps(out)
    if lang == 'en':
        out = _fix_en_accented_alleluia(out)
    return out


# Dedupe a section's first content block when it duplicates the section's
# heading text. Common in Triduum (holy-week.json, 29 cases).
def _heading_block_match_count(heading: dict, block_body_plain: dict) -> int:
    if not isinstance(heading, dict) or not isinstance(block_body_plain, dict):
        return 0
    matches = 0
    for lang, head_val in heading.items():
        if not isinstance(head_val, str):
            continue
        body_val = block_body_plain.get(lang)
        if isinstance(body_val, str) and head_val.strip() == body_val.strip():
            matches += 1
    return matches


def _dedupe_heading_as_first_rubric(node: dict) -> None:
    """If `node` has a heading and content[0] is a block whose body matches
    the heading text in 3+ langs, drop content[0]. Mutates in place."""
    if not isinstance(node, dict):
        return
    heading = node.get('heading')
    content = node.get('content')
    if not isinstance(heading, dict) or not isinstance(content, list) or not content:
        return
    first = content[0]
    if not isinstance(first, dict):
        return
    body = first.get('body')
    if not isinstance(body, dict):
        return
    plain = body.get('plain')
    if not isinstance(plain, dict):
        return
    if _heading_block_match_count(heading, plain) >= 3:
        node['content'] = content[1:]


def _dedupe_heading_as_first_rubric_in_mass(mass: dict) -> None:
    parts = mass.get('parts')
    if not isinstance(parts, dict):
        return

    def walk(n):
        if isinstance(n, dict):
            _dedupe_heading_as_first_rubric(n)
            content = n.get('content')
            if isinstance(content, list):
                for sub in content:
                    walk(sub)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    for p in parts.values():
        walk(p)


# Cycle 22 — universal text-quality fixes (apostrophes, quotes, FR spacing).

# Replace ASCII straight `'` with curly U+2019 between two letters in
# French and Italian text, where the apostrophe represents an elision.
_LETTER_APOS_LETTER_RE = re.compile(r"([A-Za-zÀ-ÿ])'([A-Za-zÀ-ÿ])")


def _curly_apostrophe(text: str, lang: str) -> str:
    if not isinstance(text, str) or "'" not in text:
        return text
    if lang not in ('fr', 'it'):
        return text
    return _LETTER_APOS_LETTER_RE.sub(lambda m: f"{m.group(1)}’{m.group(2)}", text)


# Convert paired straight `"…"` to `«…»` for French/Italian, but only when
# no guillemets are already present in the string and the count of `"` is
# even (balanced pairs).
def _straight_to_guillemets(text: str, lang: str) -> str:
    if not isinstance(text, str) or '"' not in text:
        return text
    if lang not in ('fr', 'it'):
        return text
    if '«' in text or '»' in text:
        return text
    if text.count('"') % 2 != 0:
        return text
    out = []
    open_quote = True
    for ch in text:
        if ch == '"':
            out.append('«' if open_quote else '»')
            open_quote = not open_quote
        else:
            out.append(ch)
    return ''.join(out)


# Insert ASCII space before French `: ; ! ?` when missing. Only fires when
# the preceding char is a letter (not digit, not `:` from URL, not `/`).
_FR_PUNCT_NO_SPACE_RE = re.compile(r"([A-Za-zÀ-ÿ])([:;!?])(?![/])")


def _french_space_before_punct(text: str, lang: str) -> str:
    if lang != 'fr' or not isinstance(text, str):
        return text
    return _FR_PUNCT_NO_SPACE_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", text)


# Collapse `<space><,;.:>` -> `<,;.:>` for non-French langs and FR `,.`.
# French `:;!?` keep their preceding space (handled separately above).
def _collapse_space_before_punct(text: str, lang: Optional[str]) -> str:
    if not isinstance(text, str) or ' ' not in text:
        return text
    if lang == 'fr':
        # FR: only collapse space before `,` and `.` (not `:;!?`).
        out = re.sub(r' +([,.])(?!\.)', r'\1', text)
        return out
    # Other langs: collapse space before `, . ; :` (don't touch `... ` ellipsis).
    return re.sub(r' +([,.;:])(?!\.)', r'\1', text)


# Cycle 24 — padded parentheses from OCR/source. `( foo )` → `(foo)`.
# Idempotent: regex doesn't re-match its own output.
_PADDED_PAREN_OPEN_RE = re.compile(r'\(\s+')
_PADDED_PAREN_CLOSE_RE = re.compile(r'\s+\)')


# Cycle 27 — `holyXspirit` OCR scanno where a sign-of-cross glyph (✠ or +)
# was misread as a capital X and glued into the adjacent words. Fix only the
# specific phrase to avoid touching legitimate uses of "X".
def _fix_holy_x_spirit(text):
    if not isinstance(text, str) or 'holyXspirit' not in text:
        return text
    return text.replace('holyXspirit', 'Holy Spirit')


# Cycle 28 — internal path leak in sacerdotale/it.json. The Italian
# benediction text reads "si fa d'./misal_todo in domenica" — `./misal_todo`
# is a source-side relative path that leaked into prose. Drop it.
_MISAL_TODO_LEAK_RE = re.compile(r"d['’]\./misal_todo\s+", re.I)


def _fix_misal_todo_path_leak(text):
    if not isinstance(text, str) or 'misal_todo' not in text:
        return text
    # Replace `d'./misal_todo ` with empty (the surrounding text already
    # reads naturally without it).
    return _MISAL_TODO_LEAK_RE.sub('', text)


# Cycle 37 — pt-BR OCR junk `é\S ` between two words ("águia estendeu é\S
# suas asas"). The `é\S ` is stray formatting; the surrounding sentence
# reads cleanly without it.
_PTBR_E_BACKSLASH_S_RE = re.compile(r' é\\S ')


def _fix_ptbr_backslash_s_leak(text):
    if not isinstance(text, str) or '\\S' not in text:
        return text
    return _PTBR_E_BACKSLASH_S_RE.sub(' ', text)


# Cycle 28 — French ordinal scannos. Standard French uses `2e`, `17e`, `1re`,
# NOT `2ème`, `17ème`, `1ère`. The `ème`/`ère` forms are colloquial and not
# typographically correct. Convert in fr only.
_FR_ORDINAL_EME_RE = re.compile(r'(\d+)\s*ème\b')
_FR_ORDINAL_ERE_RE = re.compile(r'(\d+)\s*ère\b')


def _fix_french_ordinals(text, lang):
    if lang != 'fr' or not isinstance(text, str):
        return text
    out = _FR_ORDINAL_EME_RE.sub(r'\1e', text)
    out = _FR_ORDINAL_ERE_RE.sub(r'\1re', out)
    return out


# Cycle 28/29 — French œ ligatures: Oeuvre/oeuvre/Coeur/coeur/Soeur/soeur
# all canonically use œ. Limited to fr (la also gets Oeuvre).
_OE_LIGATURE_PAIRS = [
    (re.compile(r'\bOeuvre\b'), 'Œuvre'),
    (re.compile(r'\boeuvre\b'), 'œuvre'),
    (re.compile(r'\bCoeur\b'), 'Cœur'),
    (re.compile(r'\bcoeur\b'), 'cœur'),
    (re.compile(r'\bSoeur\b'), 'Sœur'),
    (re.compile(r'\bsoeur\b'), 'sœur'),
]


def _fix_oeuvre_ligature(text, lang):
    if lang not in ('fr', 'la') or not isinstance(text, str):
        return text
    if 'euvre' not in text.lower() and 'oeur' not in text.lower():
        return text
    out = text
    for pat, rep in _OE_LIGATURE_PAIRS:
        out = pat.sub(rep, out)
    return out


# Cycle 29 — Italian `E'` (capital E + straight apostrophe) is a typographic
# surrogate for `È` (E with grave accent). 110 hits in EP preface dialogues.
_IT_E_APOS_RE = re.compile(r"\bE['’](?=\s)")


def _fix_italian_e_apostrophe(text, lang):
    if lang != 'it' or not isinstance(text, str) or 'E' not in text:
        return text
    return _IT_E_APOS_RE.sub('È', text)


# Cycle 29 — missing space after period before capital letter, e.g.
# `Per Dóminum.Per Christum.` → `Per Dóminum. Per Christum.`. Lang-agnostic.
# Guard against common abbreviations and URLs by requiring an alpha-then-alpha
# context AND a lowercase-letter-or-accented-letter on the left.
_PERIOD_NO_SPACE_RE = re.compile(
    r'([a-záéíóúàèìòùçñãõâêîôûäëïöü])\.([A-ZÁÉÍÓÚÀÈÌÒÙÑÃÕÂÊÎÔÛÄËÏÖÜ][a-záéíóúàèìòùçñãõâêîôûäëïöü])'
)


def _fix_period_no_space(text):
    if not isinstance(text, str) or '.' not in text:
        return text
    # Apply iteratively to handle chained cases
    prev = None
    out = text
    while prev != out:
        prev = out
        out = _PERIOD_NO_SPACE_RE.sub(r'\1. \2', out)
    return out


# Cycle 29 — missing space after comma. Lang-agnostic.
_COMMA_NO_SPACE_RE = re.compile(
    r'([a-záéíóúàèìòùçñãõâêîôûäëïöü]),([a-záéíóúàèìòùçñãõâêîôûäëïöü])'
)


def _fix_comma_no_space(text):
    if not isinstance(text, str) or ',' not in text:
        return text
    prev = None
    out = text
    while prev != out:
        prev = out
        out = _COMMA_NO_SPACE_RE.sub(r'\1, \2', out)
    return out


# Cycle 29 — IGMR PUA character mapping. The source HTML used Private Use Area
# code points for `—` (em-dash) and `§` (section mark). Map them.
_PUA_CHAR_MAP = {
    '': '—',
    '': '§',
}


def _fix_pua_chars(text):
    if not isinstance(text, str):
        return text
    if '' not in text and '' not in text:
        return text
    out = text
    for ch, rep in _PUA_CHAR_MAP.items():
        out = out.replace(ch, rep)
    return out


def _collapse_padded_parens(text):
    if not isinstance(text, str):
        return text
    out = _PADDED_PAREN_OPEN_RE.sub('(', text)
    out = _PADDED_PAREN_CLOSE_RE.sub(')', out)
    return out


# Cycle 30 — literal `\n` and `\n\n` artifacts that leaked through from the
# upstream HTML→JSON conversion. They appear inside body.plain.<lang>.
# Collapse runs of newlines to a single space so the surrounding sentence
# reads naturally. 3266 occurrences corpus-wide.
_NEWLINE_RUN_RE = re.compile(r'\n+')


def _fix_newline_artifacts(text):
    if not isinstance(text, str) or '\n' not in text:
        return text
    out = _NEWLINE_RUN_RE.sub(' ', text)
    out = re.sub(r'  +', ' ', out)
    return out


# Cycle 30 — Latin `coel*` (variant ligature direction) → `cæl*`. The existing
# `caelum` family handled `cae*` variants; this catches the alternate `coe*`
# spelling (e.g. `Dóminus in coelo` should be `Dóminus in cælo`).
_COE_TO_CAE_RE = re.compile(r'\bcoe(li|lo|lis|lum|los|léstis|lestis)\b')


def _fix_coel_to_cael(text, lang):
    if lang != 'la' or not isinstance(text, str) or 'coe' not in text:
        return text
    return _COE_TO_CAE_RE.sub(lambda m: 'cæ' + m.group(1), text)


# Cycle 30 — vernacular accent scannos. Per-language word-list of common
# OCR holdouts where the canonical form has a diacritic. Cross-checked
# against authoritative dictionaries / wiktionary for each entry.
_VERNACULAR_DIACRITICS = {
    'it': {
        'pero': 'però',  # adverb "however" (vs. pera "pear" without accent)
        "PIU'": 'PIÙ',   # all-caps with apostrophe surrogate for grave
        "PIU’": 'PIÙ',
    },
    'es': {
        'oracion': 'oración',
        'salvacion': 'salvación',
        'ultimo': 'último',
        'comunion': 'comunión',
        # Cycle 33: ES `Amen` (mostly in Easter Vigil) and `Jesus`.
        'Amen': 'Amén',
        'Jesus': 'Jesús',
    },
    'pt-BR': {
        'espirito': 'espírito',
        'Espirito': 'Espírito',
        'tambem': 'também',
        'Tambem': 'Também',
        # Cycle 33: corpus-confirmed accented siblings dominate.
        'misericordia': 'misericórdia',
        'Misericordia': 'Misericórdia',
        'Moises': 'Moisés',
        'porem': 'porém',
        'Porem': 'Porém',
        'prodigios': 'prodígios',
    },
    'fr': {
        'voila': 'voilà',
        'Voila': 'Voilà',
        # Cycle 33: section-heading `APOTRE` (12 hits) → `APÔTRE`.
        'APOTRE': 'APÔTRE',
    },
    'la': {
        # Cycle 33: 4 stragglers in ordinario.json next to 88+ accented siblings.
        'Kyrie': 'Kýrie',
    },
}

def _fix_vernacular_diacritics(text, lang):
    if lang not in _VERNACULAR_DIACRITICS or not isinstance(text, str):
        return text
    table = _VERNACULAR_DIACRITICS[lang]
    out = text
    for k, v in table.items():
        if k in out:
            # For all-letter keys use word-boundary; for keys with non-letters
            # (e.g. "PIU'"), use literal replace.
            if k.isalpha():
                out = re.sub(r'\b' + re.escape(k) + r'\b', v, out)
            else:
                out = out.replace(k, v)
    return out


# Cycle 30 — Italian doubled ASCII apostrophe `''` (typographic surrogate
# for `”` close curly quote) in a few gospel readings. Pair with `''` open
# variants if any. Conservative: only collapse when not adjacent to other
# quote characters.
_IT_DOUBLED_APOS_RE = re.compile(r"''")


def _fix_italian_doubled_apostrophe(text, lang):
    if lang != 'it' or not isinstance(text, str) or "''" not in text:
        return text
    # Replace `''` with `”` (close curly quote). Idempotent.
    return _IT_DOUBLED_APOS_RE.sub('”', text)


# Cycle 32 — French straight ASCII quotes `"…"` → guillemets `«…»`. Quote
# pairs often span multiple segments within a body, so a single-string fix
# can't see the boundary. This pass walks each body (the dict containing
# `plain.fr` and `lines.fr`) as a unit:
#   - For `plain.fr`: alternating replacement based on a fresh local toggle.
#   - For `lines.fr`: walk all segments in source order, propagating the
#     open/close state across line and segment boundaries.
# Reset state between bodies so a malformed quote count in one body doesn't
# leak into the next.

def _convert_quotes_in_body_fr(body: dict) -> None:
    """In-place conversion of straight `"` to `«`/`»` in a single body's
    `plain.fr` and `lines.fr`. Skips bodies that already mix `«` with `"`
    (let those be hand-cleaned to avoid clobbering existing structure)."""
    if not isinstance(body, dict):
        return
    plain = body.get('plain') or {}
    lines = body.get('lines') or {}
    p_fr = plain.get('fr') if isinstance(plain, dict) else None
    l_fr = lines.get('fr') if isinstance(lines, dict) else None

    def has_mixed(text: str) -> bool:
        return ('«' in text or '»' in text) and '"' in text

    # Skip if any plain or any segment text mixes guillemets and ascii quotes.
    if isinstance(p_fr, str) and has_mixed(p_fr):
        return
    if isinstance(l_fr, list):
        for line in l_fr:
            if not isinstance(line, list):
                continue
            for seg in line:
                if isinstance(seg, dict) and isinstance(seg.get('text'), str) and has_mixed(seg['text']):
                    return

    def toggle_replace(text: str, state: list[bool]) -> str:
        # state is [open?] mutable — single-element list as sentinel.
        out = []
        for ch in text:
            if ch == '"':
                if not state[0]:
                    out.append('«')
                    state[0] = True
                else:
                    out.append('»')
                    state[0] = False
            else:
                out.append(ch)
        return ''.join(out)

    # plain.fr: independent toggle (doesn't share state with lines).
    if isinstance(p_fr, str) and '"' in p_fr:
        # Only convert if even count (balanced) — otherwise we'd leave an
        # orphan glyph that's hard to repair.
        if p_fr.count('"') % 2 == 0:
            plain['fr'] = toggle_replace(p_fr, [False])

    # lines.fr: walk in source order; only convert if total `"` count across
    # all segments is even.
    if isinstance(l_fr, list):
        total = 0
        for line in l_fr:
            if not isinstance(line, list):
                continue
            for seg in line:
                if isinstance(seg, dict) and isinstance(seg.get('text'), str):
                    total += seg['text'].count('"')
        if total > 0 and total % 2 == 0:
            state = [False]
            for line in l_fr:
                if not isinstance(line, list):
                    continue
                for seg in line:
                    if isinstance(seg, dict) and isinstance(seg.get('text'), str) and '"' in seg['text']:
                        seg['text'] = toggle_replace(seg['text'], state)


def _fr_quote_state_machine_in_payload(payload: Any) -> None:
    """Walk a payload tree and apply the FR quote-pair state-machine to
    every body block (anything with a `lines` AND/OR `plain` field of
    `lang->X` shape)."""
    if isinstance(payload, dict):
        # A "body" is a dict that has at least one of plain/lines as a Localized.
        is_body = (
            isinstance(payload.get('plain'), dict)
            or isinstance(payload.get('lines'), dict)
        )
        if is_body:
            _convert_quotes_in_body_fr(payload)
        for v in payload.values():
            _fr_quote_state_machine_in_payload(v)
    elif isinstance(payload, list):
        for v in payload:
            _fr_quote_state_machine_in_payload(v)


# Cycle 24 — doubled-period collapse. `..` → `.` but preserve `...` (ellipsis)
# and `....` (ellipsis + sentence period). Negative lookbehind/lookahead.
_DOUBLED_PERIOD_RE = re.compile(r'(?<!\.)\.\.(?!\.)')


def _collapse_doubled_period(text):
    if not isinstance(text, str):
        return text
    return _DOUBLED_PERIOD_RE.sub('.', text)


# Cycle 24 — doubled (or more) comma collapse. `,,` → `,`. Idempotent.
_DOUBLED_COMMA_RE = re.compile(r',{2,}')


def _collapse_doubled_comma(text):
    if not isinstance(text, str):
        return text
    return _DOUBLED_COMMA_RE.sub(',', text)


# Cycle 24 — broken verse-range like `Sir 17, 20- 28` → `Sir 17, 20-28`.
# Scoped to citation fields only (call via `_fix_citation_strings_in_mass`)
# because date ranges in body prose (`Roma, 1384- 9 de março de 1440`) need
# different treatment (em-dash + spaces, not hyphen no-space).
_VERSE_RANGE_BREAK_RE = re.compile(r'(\d)-\s+(\d)')


def _fix_numeric_range_break_in_citation(text):
    if not isinstance(text, str):
        return text
    # Iterate to handle chained occurrences like `1- 3- 5` if any.
    prev = None
    out = text
    while prev != out:
        prev = out
        out = _VERSE_RANGE_BREAK_RE.sub(r'\1-\2', out)
    return out


# Italian-specific scannos found in eucharistic-prayers.json.
def _fix_italian_specific_scannos(text: str) -> str:
    if not isinstance(text, str):
        return text
    out = text
    out = re.sub(r'\bEucarisTica\b', 'Eucaristica', out)
    out = re.sub(r'\bnecessittá\b', 'necessità', out)
    return out


def _strip_preface_title_star_prefix(title: dict) -> None:
    """Strip leading `* ` from preface title strings (per-lang).
    Mutates the dict in place. 5 hits across pf003/pf004/pf084 it/de."""
    if not isinstance(title, dict):
        return
    for lang, value in list(title.items()):
        if isinstance(value, str) and value.startswith('* '):
            title[lang] = value[2:]


# Liturgical typography: replace ASCII shorthand with the proper Unicode
# characters that appear in printed missals.
#   R/.  →  ℟.   (U+211F  RESPONSE)
#   V/.  →  ℣.   (U+2123  VERSICLE)
# We match the literal three-character form, optionally with whitespace
# between the letter and the slash, and only when the next char isn't
# alphanumeric (avoids hits inside URLs, code, etc.). Idempotent: the
# regex won't re-match its own output.
_RESP_MARKER_RE = re.compile(r'\bR\s*/\s*\.')
_VERS_MARKER_RE = re.compile(r'\bV\s*/\s*\.')


def _liturgical_markers(text: str, lang: str = "") -> str:
    if not isinstance(text, str) or not text:
        return text
    text = _RESP_MARKER_RE.sub('℟.', text)
    text = _VERS_MARKER_RE.sub('℣.', text)
    return text


def _apply_liturgical_markers_to_doc(doc: Any) -> None:
    """Walk a document and replace `R/.`/`V/.` shorthand with the proper
    liturgical characters (℟. / ℣.) in every string. Used for IGMR and
    sacerdotale passthrough documents which are not language-keyed (their
    language is set at the document root, not on each branch)."""
    if isinstance(doc, dict):
        for k, v in list(doc.items()):
            if isinstance(v, str):
                doc[k] = _liturgical_markers(v)
            else:
                _apply_liturgical_markers_to_doc(v)
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            if isinstance(v, str):
                doc[i] = _liturgical_markers(v)
            else:
                _apply_liturgical_markers_to_doc(v)


# Cycle 35 — collapse tab/whitespace runs in `html` field strings of
# IGMR/sacerdotale (source HTML preserved them as a 4-8 tab indent).
def _collapse_whitespace_runs(text):
    if not isinstance(text, str):
        return text
    if '\t' not in text and '  ' not in text:
        return text
    # Collapse runs of any whitespace (tabs, multi-spaces) to a single space.
    return re.sub(r'\s{2,}', ' ', text)


def _apply_universal_text_fixes_to_doc(doc: Any, lang: Optional[str]) -> None:
    """Walk a single-language document (igmr, sacerdotale) and apply
    the same text-quality fixes used on language-keyed payloads. The
    document's language lives at the root, not on every branch — so
    we pass `lang` through explicitly."""

    def fn(text: str) -> str:
        if not isinstance(text, str):
            return text
        out = text
        out = _collapse_whitespace_runs(out)
        out = _curly_apostrophe(out, lang or "")
        out = _straight_to_guillemets(out, lang or "")
        out = _french_space_before_punct(out, lang or "")
        out = _collapse_space_before_punct(out, lang)
        out = _collapse_padded_parens(out)
        out = _collapse_doubled_period(out)
        out = _collapse_doubled_comma(out)
        out = _fix_holy_x_spirit(out)
        out = _fix_misal_todo_path_leak(out)
        out = _fix_ptbr_backslash_s_leak(out)
        out = _fix_pua_chars(out)
        out = _fix_newline_artifacts(out)
        out = _fix_period_no_space(out)
        out = _fix_comma_no_space(out)
        out = _fix_french_ordinals(out, lang or "")
        out = _fix_oeuvre_ligature(out, lang or "")
        out = _fix_italian_e_apostrophe(out, lang or "")
        out = _fix_italian_doubled_apostrophe(out, lang or "")
        out = _fix_vernacular_diacritics(out, lang or "")
        out = _fix_coel_to_cael(out, lang or "")
        out = _liturgical_markers(out, lang or "")
        if lang == 'la':
            for pat, rep in _LA_OCR_FIXES:
                out = pat.sub(rep, out)
            out = _fix_la_diacritics(out, 'la')
        if lang == 'it':
            out = _fix_italian_specific_scannos(out)
        return out

    if isinstance(doc, dict):
        for k, v in list(doc.items()):
            if isinstance(v, str):
                doc[k] = fn(v)
            else:
                _apply_universal_text_fixes_to_doc(v, lang)
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            if isinstance(v, str):
                doc[i] = fn(v)
            else:
                _apply_universal_text_fixes_to_doc(v, lang)


def _apply_universal_text_fixes(payload: Any) -> None:
    """Walk a payload tree and apply universal text-quality fixes to
    every string under a language-keyed branch (or under a `text` field
    inside a language-keyed segment list)."""

    def fn(text: str, lang: str) -> str:
        if not isinstance(text, str):
            return text
        out = text
        out = _curly_apostrophe(out, lang)
        out = _straight_to_guillemets(out, lang)
        out = _french_space_before_punct(out, lang)
        out = _collapse_space_before_punct(out, lang)
        out = _collapse_padded_parens(out)
        out = _collapse_doubled_period(out)
        out = _collapse_doubled_comma(out)
        out = _fix_holy_x_spirit(out)
        out = _fix_misal_todo_path_leak(out)
        out = _fix_ptbr_backslash_s_leak(out)
        out = _fix_pua_chars(out)
        out = _fix_newline_artifacts(out)
        out = _fix_period_no_space(out)
        out = _fix_comma_no_space(out)
        out = _fix_french_ordinals(out, lang)
        out = _fix_oeuvre_ligature(out, lang)
        out = _fix_italian_e_apostrophe(out, lang)
        out = _fix_italian_doubled_apostrophe(out, lang)
        out = _fix_vernacular_diacritics(out, lang)
        out = _fix_coel_to_cael(out, lang)
        out = _liturgical_markers(out, lang)
        if lang == 'la':
            for pat, rep in _LA_OCR_FIXES:
                out = pat.sub(rep, out)
            out = _fix_la_diacritics(out, 'la')
        if lang == 'it':
            out = _fix_italian_specific_scannos(out)
        return out

    if isinstance(payload, dict):
        _walk_lang_strings(payload, fn)
        _fix_citation_strings_in_payload(payload)


# Cycle 36 — responsorial-psalm and reading citation backfill. When one
# language's citation is just `<book> <chapter>` while sister langs have a
# detailed verse spec, copy the verse part from the most-detailed sister
# (preserving the destination language's book abbreviation).

# Per-language book abbreviation patterns (allow common variants).
_BOOK_ABBREV_RE = {
    'la': re.compile(r'^([A-Z][a-zA-Zé]{1,5})\s'),
    'es': re.compile(r'^([A-Z][a-zA-Zé]{1,6})\s'),
    'en': re.compile(r'^(\d?\s*[A-Z][a-zA-Z]{1,5})\s'),
    'pt-BR': re.compile(r'^([A-Z][a-zA-Zé]{1,5})\s'),
    'it': re.compile(r'^([A-Z][a-zA-Zà-ÿ]{1,6})\s'),
    'fr': re.compile(r'^([A-Z][a-zA-Zà-ÿ]{1,5})\s'),
    'de': re.compile(r'^([A-Z][a-zA-Zä-üß]{1,6})\s'),
}

# A citation is "truncated" if it's just `<book> <number>` (chapter only).
_TRUNCATED_CITATION_RE = re.compile(r'^[A-Z][a-zA-ZÀ-ÿ]{1,6}\s+\d+\s*$')


def _backfill_truncated_citation(citation: dict) -> None:
    """In-place: for each truncated lang, copy verse spec from a fuller sister.

    Skips DE (different psalm-numbering scheme — Hebrew vs Vulgate, off by 1)
    and EN (uses `:` instead of `,` for verse separator). For other langs
    (la/es/pt-BR/it/fr) which all use comma-separated verse spec, take any
    sister with a richer citation and graft its verse spec onto the
    destination's book abbreviation."""
    if not isinstance(citation, dict):
        return

    # Identify truncated langs and richer sisters (only la/es/pt-BR/it/fr).
    safe_langs = ('la', 'es', 'pt-BR', 'it', 'fr')
    truncated = []
    rich = []
    for L in safe_langs:
        v = citation.get(L)
        if not isinstance(v, str) or not v.strip():
            continue
        if _TRUNCATED_CITATION_RE.match(v.strip()):
            truncated.append(L)
        elif ',' in v:
            rich.append(L)

    if not truncated or not rich:
        return

    # Use the longest rich citation as the donor.
    donor_lang = max(rich, key=lambda L: len(citation[L]))
    donor = citation[donor_lang].strip()

    # Find donor's verse spec — everything after the first chapter number.
    m = re.match(r'^([A-Z][a-zA-Zà-ÿ]{1,6})\s+(\d+)(.*)$', donor)
    if not m:
        return
    donor_book, donor_chap, donor_rest = m.group(1), m.group(2), m.group(3)
    if not donor_rest.strip():
        return  # donor itself is just chapter — nothing to backfill

    for L in truncated:
        target = citation[L].strip()
        m2 = re.match(r'^([A-Z][a-zA-Zà-ÿ]{1,6})\s+(\d+)\s*$', target)
        if not m2:
            continue
        tgt_book, tgt_chap = m2.group(1), m2.group(2)
        # Only backfill when the chapter numbers match — avoids grafting
        # Vulgate verses onto a Hebrew-numbered citation.
        if tgt_chap != donor_chap:
            continue
        citation[L] = f"{tgt_book} {tgt_chap}{donor_rest}"


def _backfill_truncated_citations_in_payload(payload: Any) -> None:
    """Walk the payload and apply citation-backfill to every `citation`
    dict directly under reading-shaped slots."""
    if isinstance(payload, dict):
        for k, v in list(payload.items()):
            if k == 'citation' and isinstance(v, dict):
                _backfill_truncated_citation(v)
            else:
                _backfill_truncated_citations_in_payload(v)
    elif isinstance(payload, list):
        for item in payload:
            _backfill_truncated_citations_in_payload(item)


def _fix_citation_strings_in_payload(payload: Any) -> None:
    """Walk the payload and apply citation-scoped fixes (e.g. numeric range
    break collapse) to every `citation` field. Citation fields are dicts
    `{lang: str}` directly under reading/antiphon/responsorialPsalm slots."""
    if isinstance(payload, dict):
        for k, v in list(payload.items()):
            if k == 'citation' and isinstance(v, dict):
                for lang, val in list(v.items()):
                    if isinstance(val, str):
                        v[lang] = _fix_numeric_range_break_in_citation(val)
            else:
                _fix_citation_strings_in_payload(v)
    elif isinstance(payload, list):
        for item in payload:
            _fix_citation_strings_in_payload(item)


# ---------------------------------------------------------------------------
# Sanctorale alternatives merge
# ---------------------------------------------------------------------------

# Fields that identify the *parent* mass and don't belong inside an alternative.
_PARENT_ONLY_FIELDS = (
    "id", "group", "date", "dateSuffix", "scope", "subgroup",
    "season", "weekIndex", "weekday",
    "movable", "movableCode", "movableMonthAnchor", "undated", "ordinal",
)

# Slot keys an alternative may carry. Anything not listed here is dropped
# from alternatives (e.g. parent-only fields, intermediate scratch fields).
_ALTERNATIVE_FIELDS = (
    "key", "title", "description", "rank", "rankLocalized", "liturgicalColor",
    "entranceAntiphon", "penitentialAct", "gloriaInstruction",
    "collect", "creedInstruction", "readings",
    "prayerOverOfferings", "preface", "communionAntiphon",
    "postcommunion", "prayerOverPeople",
)

_BODY_SLOTS_FOR_EMPTY_CHECK = (
    "entranceAntiphon", "collect", "prayerOverOfferings", "preface",
    "communionAntiphon", "postcommunion", "prayerOverPeople", "readings",
    "description",
)

_SAINT_NAME_TRIM_RE = re.compile(
    r"^(?:Saints?|Ss?\.?|S\.|Bl\.?|Blessed|The|Commemoration\s+of\s+(?:all|the))\s+",
    re.IGNORECASE,
)
_SAINT_NAME_DROP_TAIL_RE = re.compile(
    r"\s*[,;].*$"  # drop ", martyr" / "; bishop, doctor of the Church" tails
)
# Placeholder titles like "11 30z" or "01 01y" — bare day-ids with no real content.
_PLACEHOLDER_TITLE_RE = re.compile(r"^\s*\d{1,2}[\s-]?\d{1,2}\s*[a-z]?\s*$", re.IGNORECASE)


def _is_placeholder_title(text: str) -> bool:
    return bool(text and _PLACEHOLDER_TITLE_RE.match(text.strip()))


def _slugify_saint_name(text: str, *, max_words: int = 2) -> str:
    """Compact kebab slug from a title. Takes the first `max_words` words after
    stripping common leading particles, lowercased and ASCII-folded."""
    if not text:
        return ""
    s = text.strip()
    # Prefer a parenthetical nickname when present (e.g. "(All Souls' Day)").
    paren = re.search(r"\(([^)]{2,40})\)", s)
    if paren:
        candidate = paren.group(1).strip()
        # Skip parentheticals that are clearly metadata, not nicknames.
        if not re.match(r"^\d|optional|memorial|feast|vigil|day|the\s|of\s",
                        candidate, re.IGNORECASE):
            s = candidate
    # Iteratively strip leading particles ("The", "Commemoration of all", etc.).
    for _ in range(4):
        new = _SAINT_NAME_TRIM_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    s = _SAINT_NAME_DROP_TAIL_RE.sub("", s)
    # Take only the first few words so slugs like "all-souls" stay short rather
    # than "commemoration-of-all-the-faithful-departed".
    words = re.split(r"\s+", s, maxsplit=max_words)
    s = " ".join(words[:max_words])
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    # Drop trailing stop-words so "assumption-of" becomes "assumption".
    s = re.sub(r"-(?:of|the|and|de|du|des|della|del)$", "", s).strip("-")
    # Slugs must start with a letter (schema pattern).
    if s and not s[0].isalpha():
        s = "form-" + s
    return s


def _alternative_slug_from_title(title: dict) -> str:
    """Best-effort kebab slug from a Mass title. Prefers English; falls back
    to a normalized Latin (genitive root)."""
    if not isinstance(title, dict):
        return ""
    en = title.get("en") or ""
    slug = _slugify_saint_name(en)
    if slug:
        return slug
    la = title.get("la") or ""
    slug = _slugify_saint_name(la)
    # Latin genitive endings
    slug = re.sub(r"(?:i|is|orum|arum|ae|i-de|martyris|episcopi|presbyteri)$", "", slug).strip("-")
    if slug and not slug[0].isalpha():
        slug = "form-" + slug
    return slug


def _alternative_has_body(mass: dict) -> bool:
    """A celebration has substantive content if it has at least one prayer slot
    or a non-placeholder title. Pure-readings entries with garbage titles
    (the `11-30z` ghost) don't count — readings alone don't make a celebration."""
    title = mass.get("title") or {}
    title_la = title.get("la") or ""
    if _is_placeholder_title(title_la):
        return False
    prayer_slots = ("entranceAntiphon", "collect", "prayerOverOfferings",
                    "preface", "communionAntiphon", "postcommunion",
                    "prayerOverPeople", "description")
    return any(mass.get(k) for k in prayer_slots)


def _to_alternative(mass: dict, key: str, *, drop_readings: bool = False) -> dict:
    """Project a Mass dict into a MassAlternative shape. Drops parent-only
    fields; keeps only slot fields plus the assigned `key`.

    `drop_readings=True` is set for same-celebration alternatives (e.g.
    All Souls' three formularies share readings — they live on the parent;
    duplicating them on each alternative would also duplicate per-formulary
    extraction artifacts).
    """
    alt: dict[str, Any] = {"key": key}
    for k in _ALTERNATIVE_FIELDS:
        if k == "key":
            continue
        if drop_readings and k == "readings":
            continue
        if k in mass:
            alt[k] = mass[k]
    return alt


def _suffix_sort_key(m: dict) -> tuple:
    """Order base (no dateSuffix) first, then alphabetical y/z."""
    suf = m.get("dateSuffix") or ""
    return (1 if suf else 0, suf)


def _collapse_sanctorale_alternatives(masses: list[dict], provenance: Optional[dict] = None) -> list[dict]:
    """Merge sanctorale masses sharing a (date, scope) into one mass with
    `alternatives[]`. Anomalies handled here:
      - Suffix-only buckets (no base): the suffixed mass is promoted to the
        primary, with id/dateSuffix rewritten.
      - Empty placeholder masses (no body) inside a multi-celebration bucket
        are dropped (catches the `11 30z` ghost).

    If `provenance` is provided, source-id keys (including y/z variants) are
    remapped to the new merged ids so consumers don't see orphan keys."""
    bucket_key = lambda m: (
        (m.get("date") or {}).get("month"),
        (m.get("date") or {}).get("day"),
        m.get("scope") or "_universal",
    )

    by_bucket: dict[tuple, list[dict]] = {}
    untouched: list[dict] = []
    for m in masses:
        d = m.get("date") or {}
        if "month" in d and "day" in d:
            by_bucket.setdefault(bucket_key(m), []).append(m)
        else:
            # Movable / undated saints aren't bucketed by date; pass through.
            untouched.append(m)

    out: list[dict] = list(untouched)
    for key, ms in by_bucket.items():
        # Remember every source-mass id that fed into this bucket (for
        # provenance remapping), including those dropped as empty.
        bucket_source_ids = [m["id"] for m in ms]
        ms = [m for m in ms if _alternative_has_body(m) or not m.get("dateSuffix")]
        if not ms:
            continue
        ms.sort(key=_suffix_sort_key)
        primary = ms[0]
        primary_original_id = primary["id"]
        # If the surviving primary still has a dateSuffix (no base existed in
        # the source), strip it and rewrite the id to drop the suffix.
        if primary.get("dateSuffix"):
            d = primary.get("date") or {}
            scope_seg = primary.get("scope")
            base_id = f"sanctorale.{d['month']:02d}-{d['day']:02d}"
            if scope_seg:
                scope_slug = scope_seg.lower().replace(" ", "-")
                base_id = f"{base_id}.{scope_slug}"
            primary["id"] = base_id
            primary.pop("dateSuffix", None)
        else:
            primary.pop("dateSuffix", None)
        # Cycle 31: remap provenance keys for every source id in this bucket
        # to the new merged primary id (including the primary's own original
        # id if it was rewritten above).
        if isinstance(provenance, dict):
            new_id = primary["id"]
            primary_prov = provenance.get(new_id) or provenance.get(primary_original_id)
            for sid in bucket_source_ids:
                if sid != new_id and sid in provenance:
                    primary_prov = primary_prov or provenance[sid]
                    provenance.pop(sid, None)
            if primary_prov and new_id not in provenance:
                provenance[new_id] = primary_prov

        if len(ms) > 1:
            alts: list[dict] = []
            primary_title = primary.get("title") or {}
            primary_slug = _alternative_slug_from_title(primary_title) or "form"
            seen_slugs = {primary_slug}
            for i, alt_mass in enumerate(ms[1:], start=2):
                alt_title = alt_mass.get("title") or {}
                same_celebration = bool(alt_title) and (
                    alt_title.get("la") == primary_title.get("la")
                )
                if same_celebration:
                    # Cycle 31: when primary_slug is the default 'form'
                    # placeholder, don't double it up — just `form-2` etc.
                    if primary_slug == 'form':
                        slug = f"form-{i}"
                    else:
                        slug = f"{primary_slug}-form-{i}"
                else:
                    slug = _alternative_slug_from_title(alt_title) or f"form-{i}"
                # Disambiguate slug collisions
                base_slug = slug
                n = 2
                while slug in seen_slugs:
                    slug = f"{base_slug}-{n}"
                    n += 1
                seen_slugs.add(slug)
                alts.append(_to_alternative(alt_mass, slug, drop_readings=same_celebration))
            primary["alternatives"] = alts
        out.append(primary)
    return out


def _post_process_mass(mass: dict) -> Optional[dict]:
    if _drop_empty_mass(mass):
        return None
    _drop_placeholder_titles(mass)
    _normalize_titles(mass)
    _backfill_missing_title(mass)
    _mark_known_vigil_masses(mass)
    _set_weekday_from_id(mass)
    _reclassify_late_advent_season(mass)
    _clear_late_advent_weekday(mass)
    _enrich_mass_reading_citations(mass)
    _strip_bare_number_segments(mass)
    _strip_rubric_markers_from_text(mass)
    _retype_alleluia_in_mass(mass)
    _drop_stranded_lectio_labels(mass)
    _drop_french_rubric_latin_leak(mass)
    _collapse_duplicate_cycles(mass)
    _normalize_weekday_reading_cycle(mass)
    _fix_empty_lines(mass)
    _fix_prayer_terminations(mass)
    _strip_leading_roman_leak_in_mass(mass)
    _fix_doubled_alleluia_in_mass(mass)
    _fix_double_period_before_marker_in_mass(mass)
    _fix_text_scannos_in_mass(mass)
    _walk_lang_strings(mass, lambda t, _l: _collapse_doubled_quotes(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_tilde_nbsp(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_colon_no_space(t))
    _walk_lang_strings(mass, _fix_midword_cap_scannos)
    _walk_lang_strings(mass, _apply_lang_specific_text_fixes)
    _walk_lang_strings(mass, lambda t, _l: _fix_doubled_preface_label(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_n_bracket_spacing(t))
    # Cycle 24 — universal text-quality fixes the libraries already get via
    # `_apply_universal_text_fixes`. Masses are written with `post_process=False`
    # so they need the same pass here (otherwise padded parens, doubled punct,
    # space-before-punct, etc. survive in mass JSON).
    _walk_lang_strings(mass, _curly_apostrophe)
    _walk_lang_strings(mass, _straight_to_guillemets)
    _walk_lang_strings(mass, _french_space_before_punct)
    _walk_lang_strings(mass, _collapse_space_before_punct)
    _walk_lang_strings(mass, lambda t, _l: _collapse_padded_parens(t))
    _walk_lang_strings(mass, lambda t, _l: _collapse_doubled_period(t))
    _walk_lang_strings(mass, lambda t, _l: _collapse_doubled_comma(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_holy_x_spirit(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_misal_todo_path_leak(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_ptbr_backslash_s_leak(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_pua_chars(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_newline_artifacts(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_period_no_space(t))
    _walk_lang_strings(mass, lambda t, _l: _fix_comma_no_space(t))
    _walk_lang_strings(mass, _fix_french_ordinals)
    _walk_lang_strings(mass, _fix_oeuvre_ligature)
    _walk_lang_strings(mass, _fix_italian_e_apostrophe)
    _walk_lang_strings(mass, _fix_italian_doubled_apostrophe)
    _walk_lang_strings(mass, _fix_vernacular_diacritics)
    _walk_lang_strings(mass, _fix_coel_to_cael)
    _fix_citation_strings_in_payload(mass)
    _backfill_truncated_citations_in_payload(mass)
    _walk_lang_strings(mass, _liturgical_markers)
    _normalize_citation_styles_in_mass(mass)
    _append_period_to_alleluia_end_in_mass(mass)
    _reorder_triduum_parts_preamble_first(mass)
    _dedupe_heading_as_first_rubric_in_mass(mass)
    _backfill_preamble_heading(mass)
    _backfill_subsection_headings_in_mass(mass)
    _titlecase_sanctorale_titles(mass)
    _normalize_en_st_abbrev_in_mass(mass)
    _promote_known_solemnities(mass)
    _backfill_sanctorale_rank(mass)
    _drop_vernacular_la_leak(mass, 'title')
    _drop_vernacular_la_leak(mass, 'description')
    _assign_liturgical_color(mass)
    out = _scrub_tree(mass, None)
    # Cycle 27: scrub_tree → _balance_parens drops orphan `(` and `)` from
    # rubric segments, leaving empty rubric strings behind. Run the empty-
    # rubric cleanup AND the adjacent-segment merge AFTER the scrub pass so
    # those new empties are caught. Also covers any other transformer that
    # might empty out a segment.
    _clean_empty_rubric_segments_in_mass(out)
    _merge_adjacent_segments_in_mass(out)
    # Cycle 32: French straight-quote → guillemet pairs (state machine
    # over each body's lines.fr in source order; plain.fr toggled
    # independently).
    _fr_quote_state_machine_in_payload(out)
    return out


def _post_process_masses_list(masses: list) -> list:
    out = []
    for m in masses:
        if not isinstance(m, dict):
            out.append(m)
            continue
        cleaned = _post_process_mass(m)
        if cleaned is not None:
            out.append(cleaned)
    return out


_SNAKE_TO_CAMEL = {
    'source_file': 'sourceFile',
    'block_count': 'blockCount',
}


def _normalize_snake_case_top_level(payload: dict) -> None:
    """Rename known snake_case top-level fields to camelCase to match the
    rest of the corpus (igmr/sacerdotale used snake_case)."""
    for snake, camel in _SNAKE_TO_CAMEL.items():
        if snake in payload and camel not in payload:
            payload[camel] = payload.pop(snake)


# Match IGMR section markers anchored at the start of a sentence/paragraph.
# Conservative pattern: digits + period + space + capital letter (the start
# of the next section text).
_IGMR_SECTION_SPLIT_RE = re.compile(r'(?=\b(\d{1,3})\.\s+[A-ZÀ-ÝÆŒ])')


def _split_igmr_section_block(block: dict):
    """Split a paragraph block whose text contains multiple `\\d+. X` section
    markers into one block per section. Returns the original block unchanged
    if there's at most one section marker."""
    if not isinstance(block, dict):
        return block
    text = block.get('text', '')
    if not isinstance(text, str) or not text.strip():
        return block
    # Must start with a section number (otherwise this might be a heading
    # paragraph or non-section content with stray digits).
    if not re.match(r'^\s*\d{1,3}\.\s+[A-ZÀ-ÝÆŒ]', text):
        return block
    # Split at each section boundary.
    parts = _IGMR_SECTION_SPLIT_RE.split(text)
    # re.split with a capture group gives ['', '1', 'rest1', '2', 'rest2', ...]
    chunks: list[str] = []
    i = 0
    # Reconstruct: the very first element is text BEFORE the first marker
    # (often empty if text starts with a section).
    if parts and parts[0]:
        chunks.append(parts[0].rstrip())
    # Pairs of (number, rest)
    while i < len(parts) - 1:
        i += 1
        if i + 1 < len(parts):
            num = parts[i]
            rest = parts[i + 1]
            chunks.append((num + '. ' + rest.lstrip().split(' ', 1)[1] if rest.startswith(num + '. ')
                           else f"{num}. {rest.lstrip()}").rstrip())
            i += 1
    # Filter out fragments that aren't proper sections
    chunks = [c.strip() for c in chunks if c and c.strip()]
    if len(chunks) <= 1:
        return block
    # Build new sibling blocks preserving block metadata except text/html
    new_blocks = []
    for c in chunks:
        new = {k: v for k, v in block.items() if k not in ('text', 'html')}
        new['text'] = c
        new_blocks.append(new)
    return new_blocks


def _expand_igmr_blocks(blocks: list) -> list:
    """Walk a list of blocks; replace any block that splits into multiple
    section-blocks with its split components. Recurses into nested `blocks`."""
    out: list = []
    for b in blocks:
        if isinstance(b, dict):
            # Recurse into nested children first
            for child_key in ('blocks', 'content'):
                children = b.get(child_key)
                if isinstance(children, list):
                    b[child_key] = _expand_igmr_blocks(children)
            split = _split_igmr_section_block(b)
            if isinstance(split, list):
                out.extend(split)
            else:
                out.append(split)
        else:
            out.append(b)
    return out


# Cycle 28 — IGMR widget cruft. Source HTML carried a `<span class="float-right
# wrapper">` containing a "▼︎" arrow + the Spanish UI string "Aquí se coloca el
# símbolo "+"" (a select-widget label). Drop that wrapper group entirely from
# every IGMR doc.
_IGMR_WIDGET_TEXT_TOKENS = ('▼︎', 'Aquí se coloca el símbolo')


def _is_igmr_widget_block(b: dict) -> bool:
    if not isinstance(b, dict):
        return False
    classes = b.get('classes') or []
    if isinstance(classes, list) and 'wrapper' in classes and 'float-right' in classes:
        return True
    text = b.get('text') or ''
    if any(tok in text for tok in _IGMR_WIDGET_TEXT_TOKENS):
        return True
    return False


def _strip_igmr_widget_blocks(blocks: list) -> list:
    """Drop the source-side select-widget wrapper plus its two children
    (arrow + Spanish UI string) at every level of the IGMR block tree."""
    out: list = []
    for b in blocks:
        if isinstance(b, dict):
            if _is_igmr_widget_block(b):
                continue  # drop wrapper
            for child_key in ('blocks', 'content'):
                children = b.get(child_key)
                if isinstance(children, list):
                    b[child_key] = _strip_igmr_widget_blocks(children)
            out.append(b)
        else:
            out.append(b)
    return out


# Cycle 28 — empty paragraphs from `<p>\xa0</p>` HTML noise. Drops 510+ empty
# paragraph blocks from IGMR and sacerdotale documents.
def _is_empty_paragraph(b: dict) -> bool:
    if not isinstance(b, dict):
        return False
    if b.get('type') != 'paragraph':
        return False
    text = b.get('text')
    return not isinstance(text, str) or not text.strip()


# Cycle 31 — IGMR paragraph numbers occasionally lost their period:
# `1 Nella preparazione…` or `1) Nella preparazione…` should be
# `1. Nella preparazione…`. Fix at the start of the paragraph AND at
# internal section boundaries (where a merged paragraph contains multiple
# sections). The internal fix only fires after `. ` to avoid touching
# narrative numbers like "the 7 Sacraments". The `\)?\s+` allows for
# `1)` style numbering (which `_balance_parens` would otherwise strip
# to `1 ` and drop the period anchor).
_IGMR_LOST_DOT_START_RE = re.compile(r'^(\d{1,3})\)?\s+([A-ZÀ-ÝŒÆ])')
_IGMR_LOST_DOT_INTERNAL_RE = re.compile(r'(\.\s)(\d{1,3})\)?\s+([A-ZÀ-ÝŒÆ])')


def _fix_igmr_paragraph_number_dot(blocks: list) -> list:
    """Insert the missing `.` after paragraph-leading digit runs that lost
    it during HTML→JSON conversion. Handles both the leading section number
    and any internal section boundaries inside a merged paragraph."""
    out: list = []
    for b in blocks:
        if isinstance(b, dict):
            if b.get('type') == 'paragraph':
                text = b.get('text')
                if isinstance(text, str):
                    new = _IGMR_LOST_DOT_START_RE.sub(r'\1. \2', text)
                    new = _IGMR_LOST_DOT_INTERNAL_RE.sub(r'\1\2. \3', new)
                    if new != text:
                        b['text'] = new
            for child_key in ('blocks', 'content'):
                children = b.get(child_key)
                if isinstance(children, list):
                    b[child_key] = _fix_igmr_paragraph_number_dot(children)
            out.append(b)
        else:
            out.append(b)
    return out


def _strip_empty_paragraph_blocks(blocks: list) -> list:
    """Recursively drop paragraph blocks whose text is empty/whitespace-only.
    Operates on IGMR/sacerdotale doc blocks (not on Mass body lines, which
    have their own emptiness rules)."""
    out: list = []
    for b in blocks:
        if isinstance(b, dict):
            if _is_empty_paragraph(b):
                continue
            for child_key in ('blocks', 'content'):
                children = b.get(child_key)
                if isinstance(children, list):
                    b[child_key] = _strip_empty_paragraph_blocks(children)
            out.append(b)
        else:
            out.append(b)
    return out


def _post_process_igmr_payload(payload: dict) -> dict:
    """Split merged-section blocks in IGMR docs (esp. pt-BR) so each
    numbered section is its own addressable block."""
    if not isinstance(payload, dict):
        return payload
    blocks = payload.get('blocks')
    if isinstance(blocks, list):
        # Cycle 28: drop the source-side select-widget cruft (▼ arrow + Spanish
        # UI string) before re-shaping the rest.
        blocks = _strip_igmr_widget_blocks(blocks)
        # Cycle 28: drop empty paragraph blocks (`<p>\xa0</p>` noise).
        blocks = _strip_empty_paragraph_blocks(blocks)
        # Cycle 31: insert missing `.` in paragraph numbers (10 hits across
        # it/fr/es/pt-BR).
        blocks = _fix_igmr_paragraph_number_dot(blocks)
        payload['blocks'] = _expand_igmr_blocks(blocks)
        payload['blockCount'] = len(payload['blocks'])
    return payload


def _post_process_payload(payload: Any) -> Any:
    """Universal scrub pass on whatever's about to be written. Per-mass
    structural fixes happen earlier in main() so that index counts match."""
    if isinstance(payload, dict):
        _normalize_snake_case_top_level(payload)
        if payload.get('document') == 'igmr':
            _post_process_igmr_payload(payload)
        if 'count' in payload and isinstance(payload.get('masses'), list):
            payload['count'] = len(payload['masses'])
        # Strip leading `* ` from preface titles (orphan source marker).
        if isinstance(payload.get('prefaces'), list):
            for p in payload['prefaces']:
                if isinstance(p, dict) and isinstance(p.get('title'), dict):
                    _strip_preface_title_star_prefix(p['title'])
        # Apply universal text-quality fixes to every lang-keyed string in
        # the payload (covers masses, prefaces, eucharistic-prayers, ordinary,
        # saints, calendar — anything with `la|en|...` keyed dicts or
        # lang-keyed lines arrays).
        _apply_universal_text_fixes(payload)
        # Final segment cleanup: drop trailing empty rubric segments and
        # entirely-empty rubric lines anywhere in the payload tree.
        _clean_empty_rubric_segments_in_mass(payload)
        # saints.json: drop pt-BR/fr/etc. fields that exact-match the Latin
        # field (parser fallback leak) and ensure terminal periods on
        # description bodies. Also backfill rankLocalized for ranks that
        # had only `rank` set (mostly optional-memorial saints).
        if isinstance(payload.get('saints'), list):
            for s in payload['saints']:
                if isinstance(s, dict):
                    _drop_vernacular_la_leak(s, 'title')
                    _drop_vernacular_la_leak(s, 'description')
                    _backfill_rank_localized(s)
                    desc = s.get('description')
                    if isinstance(desc, dict):
                        for L, v in list(desc.items()):
                            if isinstance(v, str):
                                desc[L] = _ensure_terminal_period(v.strip()) if v.strip() else v
    return _scrub_tree(payload, None)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any, *, post_process: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if post_process:
        payload = _post_process_payload(payload)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


_WEEKDAY_ORDER = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
                  "thursday": 4, "friday": 5, "saturday": 6}


def id_to_path(item_id: str, root: Path, *, suffix: str = ".json") -> Path:
    """Map a dotted id to a per-item file path under `root`.

    `tempore.ordinary-time.week-1.sunday` → root/tempore/ordinary-time/week-1/sunday.json
    `sanctorale.04-02`                    → root/sanctorale/04-02.json
    """
    parts = item_id.split(".")
    return root.joinpath(*parts[:-1], parts[-1] + suffix)


def write_index(bucket_dir: Path, *, count: int, ids: list[str], **discriminators) -> None:
    """Emit `<bucket_dir>/_index.json` with sorted ids + bucket metadata."""
    payload: dict[str, Any] = {"count": count, **discriminators, "ids": sorted(ids)}
    write_json(bucket_dir / "_index.json", payload)


def reset_dir(path: Path) -> None:
    """Wipe a directory tree so a fresh build doesn't leave stale per-item files."""
    if path.exists():
        shutil.rmtree(path)


# (devocionario.html and oracoes.html are intentionally not converted — see README.)


def main():
    print(f"V1 input : {V1_OUT}")
    print(f"V2 output: {V2_OUT}")
    V2_OUT.mkdir(parents=True, exist_ok=True)

    # Build prefaces library FIRST so masses can resolve label-only references
    # against it during assembly.
    print("Building prefaces library (early, for label resolution)…")
    prefs = build_prefaces()
    pref_root = V2_OUT / "library" / "preface"
    reset_dir(pref_root)
    # Apply universal text fixes once across the list, then skip the per-write
    # post-process pass — that's where the slowdown is on large per-item runs.
    for p in prefs:
        _apply_universal_text_fixes(p)
        _strip_preface_title_star_prefix(p.get("title") or {})
        write_json(id_to_path(p["id"], V2_OUT / "library"), p, post_process=False)
    write_index(pref_root, count=len(prefs), ids=[p["id"] for p in prefs])

    print("Indexing lecturas…")
    lec_idx = index_lecturas()
    print(f"  {len(lec_idx)} lecturas day-ids")

    masses_by_group: dict[str, list[dict]] = {}
    provenance: dict[str, str] = {}

    for category in ("tiempos", "santos", "comunes_votivas"):
        for base in list_basenames(category):
            for d_id in list_day_ids(category, base):
                day = load_v1(category, base, d_id)
                if not day:
                    continue
                mass = assemble_mass(category, base, day, lec_idx)
                if not mass:
                    continue
                if not is_real_mass(mass):
                    continue
                masses_by_group.setdefault(mass["group"], []).append(mass)
                provenance[mass["id"]] = f"misal_v2/m_<lang>/{category}/m_<lang>_{base}.html#{d_id}"

    # Ordinary Time ferials (Mon-Sat × 34 weeks): no proper Mass formulary in
    # source, but lecturas have Year I/II readings. Synthesize 204 mass shells
    # (title + readings + season metadata) from the lecturas index.
    print("Synthesizing OT ferial masses from lecturas…")
    ot_ferials = synthesize_ot_ferial_masses(lec_idx)
    for m in ot_ferials:
        masses_by_group.setdefault("tempore", []).append(m)
        provenance[m["id"]] = (
            f"misal_v2/m_<lang>/lecturas/m_<lang>_lecturas_to_{m['weekIndex']:02d}.html"
        )
    print(f"  added {len(ot_ferials)} OT ferial mass shells")

    # Regional saints with content embedded directly in m_estructura/santos/*.html
    # (Brazil, USA, Germany, Spain, Argentina, Chile, Uruguay, Africa, Nigeria, etc.)
    print("Extracting embedded regional saints from estructura…")
    embedded = extract_embedded_regional_saints()
    seen_ids = {m["id"] for ms in masses_by_group.values() for m in ms}
    added = 0
    skipped_existing = 0
    skipped_dup = 0
    for m in embedded:
        if m["id"] in seen_ids:
            # Could be a collision with a universal mass OR a duplicate in the
            # embedded list (same `dia` id appears in multiple estructura files).
            if any(mm["id"] == m["id"] for mm in masses_by_group.get("sanctorale", [])):
                skipped_dup += 1
            else:
                skipped_existing += 1
            continue
        masses_by_group.setdefault(m["group"], []).append(m)
        seen_ids.add(m["id"])
        provenance[m["id"]] = f"misal_v2/m_estructura/santos/m_estructura_santos_*.html#{m.get('id','').split('.')[-1]}"
        added += 1
    print(f"  added {added} embedded regional saints (skipped {skipped_existing} colliding with universal, {skipped_dup} duplicates)")

    # Post-process every mass before bundling (drops empty shells, normalizes
    # titles, strips bare-number segments, fixes Latin OCR scannos, ensures
    # terminal periods, promotes known solemnities, etc.). Done here rather
    # than per-bundle so index totals reflect the final count.
    print("Post-processing masses…")
    dropped_count = 0
    for group, masses in list(masses_by_group.items()):
        cleaned = []
        for m in masses:
            out = _post_process_mass(m)
            if out is None:
                provenance.pop(m.get("id", ""), None)
                dropped_count += 1
            else:
                cleaned.append(out)
        masses_by_group[group] = cleaned
    if dropped_count:
        print(f"  dropped {dropped_count} empty mass shells")

    # Per-mass file layout: id IS the path, with one _index.json per bucket.
    masses_root = V2_OUT / "masses"
    reset_dir(masses_root)
    for group, masses in masses_by_group.items():
        if group == "tempore":
            # Bucket by the id's structural season segment so the _index.json
            # matches the actual filesystem layout. (m["season"] can differ
            # from the id prefix — e.g. late-Advent days Dec 17-24 have
            # id `tempore.christmas.day-1XX` but season metadata "advent".)
            by_bucket: dict[str, list[dict]] = {}
            for m in masses:
                parts = m["id"].split(".")
                season_seg = parts[1] if len(parts) >= 2 else (m.get("season") or "unspecified")
                by_bucket.setdefault(season_seg, []).append(m)
            for season, ms in by_bucket.items():
                ms.sort(key=lambda x: (x.get("weekIndex", 99),
                                       _WEEKDAY_ORDER.get(x.get("weekday"), 7)))
                for m in ms:
                    write_json(id_to_path(m["id"], masses_root), m, post_process=False)
                write_index(masses_root / "tempore" / season,
                            count=len(ms), ids=[m["id"] for m in ms],
                            season=season)
                print(f"  tempore/{season}: {len(ms)} masses")
        elif group == "sanctorale":
            # Collapse base/y/z masses sharing a date+scope into one parent
            # mass with `alternatives[]`. Regional saints (with `scope`)
            # bucket separately from universal saints.
            masses = _collapse_sanctorale_alternatives(masses, provenance)
            masses_by_group[group] = masses
            for m in masses:
                write_json(id_to_path(m["id"], masses_root), m, post_process=False)
            write_index(masses_root / "sanctorale",
                        count=len(masses), ids=[m["id"] for m in masses])
            print(f"  sanctorale: {len(masses)} masses (after alternatives merge)")
        elif group in ("common", "votive", "ritual"):
            by_sub: dict[str, list[dict]] = {}
            for m in masses:
                by_sub.setdefault(m.get("subgroup", "misc"), []).append(m)
            for sub, ms in by_sub.items():
                for m in ms:
                    write_json(id_to_path(m["id"], masses_root), m, post_process=False)
                write_index(masses_root / group / sub,
                            count=len(ms), ids=[m["id"] for m in ms],
                            group=group, subgroup=sub)
                print(f"  {group}/{sub}: {len(ms)} masses")

    # Libraries (prefaces already written early; just write the rest now).
    print("Libraries…")
    eps = build_eucharistic_prayers()
    ep_root = V2_OUT / "library" / "eucharistic-prayer"
    reset_dir(ep_root)
    for ep in eps:
        _apply_universal_text_fixes(ep)
        write_json(id_to_path(ep["id"], V2_OUT / "library"), ep, post_process=False)
    write_index(ep_root, count=len(eps), ids=[ep["id"] for ep in eps])

    ord_parts = build_ordinary()
    ord_root = V2_OUT / "library" / "ordinary"
    reset_dir(ord_root)
    for op in ord_parts:
        _apply_universal_text_fixes(op)
        write_json(id_to_path(op["id"], V2_OUT / "library"), op, post_process=False)
    write_index(ord_root, count=len(ord_parts), ids=[op["id"] for op in ord_parts])

    # Calendar — per-entry files mirroring mass id paths
    print("Calendar…")
    cal = build_calendar(masses_by_group)
    cal_root = V2_OUT / "calendar"
    reset_dir(cal_root)
    # Calendar entries are derived projections (id/title/season/...) and
    # carry already-fixed strings from the masses they were copied from.
    for entry in cal["tempore"]:
        write_json(id_to_path(entry["id"], cal_root), entry, post_process=False)
    write_index(cal_root / "tempore",
                count=len(cal["tempore"]),
                ids=[e["id"] for e in cal["tempore"]])
    for entry in cal["sanctorale"]:
        write_json(id_to_path(entry["id"], cal_root), entry, post_process=False)
    write_index(cal_root / "sanctorale",
                count=len(cal["sanctorale"]),
                ids=[e["id"] for e in cal["sanctorale"]])

    # Saints catalog — per-saint projection under data/saints/<MM-DD>[/<scope>].json
    print("Saints catalog…")
    catalog = build_saints_catalog(masses_by_group.get("sanctorale", []))
    saints_root = V2_OUT / "saints"
    reset_dir(saints_root)
    for s in catalog:
        _apply_universal_text_fixes(s)
        _drop_vernacular_la_leak(s, "title")
        _drop_vernacular_la_leak(s, "description")
        _backfill_rank_localized(s)
        # Saint ids start with `sanctorale.`; drop that prefix so the file lives
        # under data/saints/, not data/saints/sanctorale/.
        tail = s["id"].split(".", 1)[1] if s["id"].startswith("sanctorale.") else s["id"]
        write_json(id_to_path(tail, saints_root), s, post_process=False)
    write_index(saints_root, count=len(catalog), ids=[s["id"] for s in catalog])

    # Triduum — pure reference list. The actual mass payloads live under
    # data/masses/...; consumers resolve via id.
    print("Triduum bundle…")
    triduum_ids = set(SPECIAL_DAY_ID_OVERRIDES.values())
    triduum_masses = [m for m in masses_by_group.get("tempore", []) if m["id"] in triduum_ids]
    triduum_root = V2_OUT / "triduum"
    reset_dir(triduum_root)
    write_index(triduum_root,
                count=len(triduum_masses),
                ids=[m["id"] for m in triduum_masses])

    # Provenance
    # Cycle 31: prune provenance to only contain entries whose id resolves
    # to a written mass. Catches stale entries from regional saints whose id
    # changed after assembly without provenance being remapped.
    valid_ids = {m["id"] for ms in masses_by_group.values() for m in ms}
    stale = [k for k in provenance if k not in valid_ids]
    for k in stale:
        provenance.pop(k, None)
    if stale:
        print(f"  pruned {len(stale)} stale provenance entries")
    write_json(V2_OUT / "provenance.json", provenance)

    # IGMR passthrough — already document-shaped
    igmr_dir = V1_OUT / "standalone" / "igmr"
    if igmr_dir.exists():
        for f in igmr_dir.glob("*.json"):
            with f.open() as inp:
                d = json.load(inp)
            src = d.get("language")
            if src and src in LANG_MAP:
                d["language"] = LANG_MAP[src]
            doc_lang = d.get("language")
            _apply_universal_text_fixes_to_doc(d, doc_lang)
            _apply_liturgical_markers_to_doc(d)
            write_json(V2_OUT / "igmr" / f"{LANG_MAP.get(src, src)}.json", d)

    # Sacerdotale passthrough — Priest's manual / appendices
    sac_dir = V1_OUT / "standalone" / "sacerdotale"
    if sac_dir.exists():
        for f in sac_dir.glob("*.json"):
            with f.open() as inp:
                d = json.load(inp)
            src = d.get("language")
            if src and src in LANG_MAP:
                d["language"] = LANG_MAP[src]
            doc_lang = d.get("language")
            _apply_universal_text_fixes_to_doc(d, doc_lang)
            _apply_liturgical_markers_to_doc(d)
            # Cycle 28: drop empty paragraphs in sacerdotale docs (same as IGMR)
            if isinstance(d.get('blocks'), list):
                d['blocks'] = _strip_empty_paragraph_blocks(d['blocks'])
                d['blockCount'] = len(d['blocks'])
            write_json(V2_OUT / "sacerdotale" / f"{LANG_MAP.get(src, src)}.json", d)

    # Note: devocionario.html (multilingual Rosary + devotions) and oracoes.html
    # (Portuguese-only devotional prayers) are deliberately NOT included.
    # See README's "Sources & scope" section.

    # Index
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "languages": ISO_LANGS,
        "languageNames": {
            "la": "Latin",
            "es": "Castilian Spanish",
            "en": "English",
            "pt-BR": "Brazilian Portuguese",
            "it": "Italian",
            "fr": "French",
            "de": "German",
        },
        "groups": {g: len(ms) for g, ms in masses_by_group.items()},
        "rites": ["mass", "mass-with-procession", "chrism-mass", "lords-supper",
                  "celebration-of-the-passion", "easter-vigil"],
        "totals": {
            "masses": sum(len(ms) for ms in masses_by_group.values()),
            "saintsCatalog": len(catalog),
            "prefaces": 75,
            "eucharisticPrayers": 10,
            "ordinaryParts": 4,
        },
        "files": {
            "masses": "masses/<group>/<id-as-path>.json (per-mass; _index.json per bucket)",
            "library": "library/{preface,eucharistic-prayer,ordinary}/<id-tail>.json",
            "saints": "saints/<MM-DD>[/<scope>].json",
            "calendar": "calendar/<group>/<id-as-path>.json",
            "triduum": "triduum/_index.json (reference list of mass ids)",
            "igmr": "igmr/<lang>.json",
            "sacerdotale": "sacerdotale/<lang>.json",
            "provenance": "provenance.json",
        },
    }
    write_json(V2_OUT / "index.json", index)
    print("Done.")


if __name__ == "__main__":
    main()
