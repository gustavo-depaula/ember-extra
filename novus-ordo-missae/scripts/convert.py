#!/usr/bin/env python3
"""
Convert the Missale_romanum HTML corpus into structured JSON.

Outputs (under OUT_DIR):
  by-language/<lang>/<category>/<basename>.json   per-file flat blocks
  structure/<category>/<basename>.json            parsed estructura (slots)
  days/<category>/<basename>/<day-id>.json        fully merged per-day JSON
  standalone/<doc>/<lang>.json                    igmr, sacerdotale (not block-aligned)
  index.json                                      catalog

See JOURNAL.md for the full design.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

ROOT = Path(__file__).resolve().parent.parent / "source" / "Missale_romanum" / "misal_v2"
OUT_DIR = Path(__file__).resolve().parent.parent / "out"

LANGUAGES = ["latin", "cast", "engl", "port", "ital", "fran", "germ"]
LANG_DIRS = {lg: ROOT / f"m_{lg}" for lg in LANGUAGES}

# Categories that follow the padre/hijo pattern.
ALIGNED_CATEGORIES = [
    "ordinario",
    "tiempos",
    "santos",
    "comunes_votivas",
    "lecturas",
    "prefacios",
    "plegarias_euc",
]

# Slot classes we recognize in estructura files.
SLOT_TYPES = {
    "x_titulo",
    "x_ant_ent",
    "x_acto_penit",
    "x_gloria",
    "x_colecta",
    "x_prim_lect",
    "x_salmo",
    "x_seg_lect",
    "x_aleluya",
    "x_evangelio",
    "x_credo",
    "x_or_ofrend",
    "x_prefacio",
    "x_ant_com",
    "x_post_com",
    "x_or_pueblo",
}


# ---------------------------------------------------------------------------
# Block parsing (per-language hijo files)
# ---------------------------------------------------------------------------

INLINE_CLASS_TYPE = {
    "red": "rubric",
    "cap": "capital",
    "cruzroja": "cross",
    "alindcha": "reference",
    "pueblo": "people",
    "ReadingGospelTitle": "reading_title",
    "Summary": "reading_summary",
    "Areadingfrom": "reading_from",
    "Incipit-oneline": "reading_incipit",
    "TheWordoftheLord": "reading_acclamation",
    "Verse": "verse",
    "PsalmAlleluiaVerse": "psalm_verse",
}


def classify_inline(tag: Tag) -> str | None:
    classes = tag.get("class") or []
    for cls in classes:
        if cls in INLINE_CLASS_TYPE:
            return INLINE_CLASS_TYPE[cls]
    return None


def parse_segments(node: Tag) -> list[dict[str, Any]]:
    """Recursively walk a hijo block and produce a list of typed segments."""
    out: list[dict[str, Any]] = []

    def walk(el):
        if isinstance(el, NavigableString):
            text = str(el)
            if text.strip() or text == " ":
                out.append({"type": "text", "value": text})
            return
        if not isinstance(el, Tag):
            return

        name = el.name.lower()

        if name == "br":
            out.append({"type": "break"})
            return

        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            out.append(
                {
                    "type": "heading",
                    "level": int(name[1]),
                    "text": clean_text(el.get_text(" ", strip=True)),
                    "html": str(el),
                }
            )
            return

        if name in ("i", "em"):
            out.append(
                {
                    "type": "italic",
                    "text": clean_text(el.get_text(" ", strip=True)),
                    "html": str(el),
                }
            )
            return

        if name in ("b", "strong"):
            out.append(
                {
                    "type": "bold",
                    "text": clean_text(el.get_text(" ", strip=True)),
                    "html": str(el),
                }
            )
            return

        cls = classify_inline(el)
        if cls is not None:
            out.append(
                {
                    "type": cls,
                    "text": clean_text(el.get_text(" ", strip=True)),
                    "html": str(el),
                }
            )
            return

        # Unrecognized container (div, p, span, etc) — descend.
        if name == "p":
            # Treat as a paragraph break: descend, then add a paragraph marker.
            out.append({"type": "paragraph_start"})
            for child in el.children:
                walk(child)
            out.append({"type": "paragraph_end"})
            return

        for child in el.children:
            walk(child)

    for child in node.children:
        walk(child)
    return out


_WS_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace(" ", " ")
    text = _WS_RE.sub(" ", text).strip()
    return text


def parse_hijo_blocks(html: str, lang: str) -> list[dict[str, Any]]:
    """Parse hijo_N blocks out of a per-language HTML file."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []

    # Try anchored first: <div class="<lang> hijo hijo_N">
    selector = f"div.{lang}.hijo"
    nodes = soup.select(selector)
    if not nodes:
        # Plegarias_euc estructura embeds <div class="red hijo hijo_N"> with no language tag.
        # And some files might just use class="hijo hijo_N".
        nodes = soup.select("div.hijo")

    for node in nodes:
        n = extract_hijo_index(node)
        if n is None:
            continue
        text = clean_text(node.get_text(" ", strip=True))
        # Inner HTML — drop the outer div for cleaner storage.
        inner_html = node.decode_contents().strip()
        segments = parse_segments(node)
        out.append(
            {
                "n": n,
                "text": text,
                "html": inner_html,
                "segments": segments,
            }
        )

    out.sort(key=lambda b: b["n"])
    return out


def extract_hijo_index(node: Tag) -> int | None:
    classes = node.get("class") or []
    for cls in classes:
        if cls.startswith("hijo_"):
            try:
                return int(cls[len("hijo_") :])
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Structure parsing (estructura files)
# ---------------------------------------------------------------------------


def parse_estructura(html: str) -> dict[str, Any]:
    """Parse an estructura file into a list of days with typed slots."""
    soup = BeautifulSoup(html, "lxml")
    days: list[dict[str, Any]] = []

    dia_nodes = soup.select("div.dia")
    if dia_nodes:
        for dia in dia_nodes:
            days.append(parse_dia(dia))
    else:
        # Some files (plegarias_euc, prefacios intro section) don't use dia containers.
        # Fall back to flat slot collection.
        days.append({"id": None, "languages": [], "slots": collect_slots(soup)})

    return {"days": days}


def parse_dia(dia: Tag) -> dict[str, Any]:
    classes = dia.get("class") or []
    languages = [c[1:] for c in classes if c.startswith("x") and c[1:] in LANGUAGES]
    return {
        "id": dia.get("id"),
        "languages": languages,
        "slots": collect_slots(dia),
    }


def collect_slots(scope: Tag) -> list[dict[str, Any]]:
    """Walk the scope and produce slots in document order.

    A slot is a div whose first matching class is one of SLOT_TYPES, or a generic
    "padre"-only div (no semantic class) — the latter treated as type "generic".
    Inside a slot we collect padre indices grouped by agrupado_*.
    """
    slots: list[dict[str, Any]] = []
    seen_padres: set[int] = set()

    # Iterate top-level children in source order, but slots can be nested
    # (e.g. inside cicloA). Use a recursive walker that emits slots when it
    # finds them, and also emits standalone "padre" divs that aren't inside
    # a recognized slot.
    def walk(el: Tag, slot_context: dict[str, Any] | None):
        if not isinstance(el, Tag):
            return

        slot_class = first_slot_class(el)
        if slot_class:
            slot = {
                "type": slot_class,
                "id": el.get("id"),
                "groups": [],
                "padres": [],
            }
            # Slot itself may carry a padre (e.g. <div class="x_titulo padre padre_1">)
            padre_idx = extract_padre_index(el)
            if padre_idx is not None and padre_idx not in seen_padres:
                slot["padres"].append(padre_idx)
                seen_padres.add(padre_idx)

            # Descend collecting agrupado groups and direct padre children.
            for child in el.children:
                collect_inside_slot(child, slot)

            slots.append(slot)
            return

        # cicloA/cicloB/cicloC/cicloI/cicloII wrappers → descend, marking the cycle.
        cycle = first_cycle_class(el)
        if cycle:
            # Emit a marker, then descend.
            cycle_marker = {"type": "cycle_start", "cycle": cycle}
            slots.append(cycle_marker)
            for child in el.children:
                walk(child, slot_context)
            slots.append({"type": "cycle_end", "cycle": cycle})
            return

        # Plain padre at this scope (no enclosing slot type) → emit as generic.
        padre_idx = extract_padre_index(el)
        if padre_idx is not None and padre_idx not in seen_padres:
            classes = el.get("class") or []
            extra = [c for c in classes if c != "padre" and not c.startswith("padre_")]
            slots.append(
                {
                    "type": "generic",
                    "padres": [padre_idx],
                    "groups": [],
                    "classes": extra,
                }
            )
            seen_padres.add(padre_idx)
            return

        # Otherwise descend.
        for child in el.children:
            walk(child, slot_context)

    def collect_inside_slot(child, slot: dict[str, Any]):
        if not isinstance(child, Tag):
            return
        classes = child.get("class") or []
        # agrupado_ant / agrupado_post / agrupado_ante → group.
        for cls in classes:
            if cls.startswith("agrupado_"):
                group_name = cls[len("agrupado_") :]
                padre_idx = extract_padre_index(child)
                if padre_idx is not None and padre_idx not in seen_padres:
                    slot["groups"].append({"group": group_name, "padre": padre_idx})
                    seen_padres.add(padre_idx)
                return
        # Direct padre.
        padre_idx = extract_padre_index(child)
        if padre_idx is not None and padre_idx not in seen_padres:
            extras = [c for c in classes if c != "padre" and not c.startswith("padre_")]
            slot["padres"].append(padre_idx)
            seen_padres.add(padre_idx)
            if extras:
                slot.setdefault("padre_classes", {})[str(padre_idx)] = extras
            return
        # Nested? descend looking for more padres/groups.
        for sub in child.children:
            collect_inside_slot(sub, slot)

    for child in scope.children:
        walk(child, None)

    return slots


def first_slot_class(el: Tag) -> str | None:
    classes = el.get("class") or []
    for c in classes:
        if c in SLOT_TYPES:
            return c
    return None


def first_cycle_class(el: Tag) -> str | None:
    classes = el.get("class") or []
    for c in classes:
        if c in ("cicloA", "cicloB", "cicloC", "cicloI", "cicloII"):
            return c
    return None


def extract_padre_index(el: Tag) -> int | None:
    classes = el.get("class") or []
    for c in classes:
        if c.startswith("padre_"):
            try:
                return int(c[len("padre_") :])
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Standalone documents (igmr, sacerdotale)
# ---------------------------------------------------------------------------


def parse_standalone(html: str) -> dict[str, Any]:
    """Parse a standalone document (no padre/hijo alignment) into structured paragraphs."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    # IGMR has a #scroller container with the actual content.
    scroller = body.select_one("#scroller") or body
    out_blocks: list[dict[str, Any]] = []
    for child in scroller.children:
        block = standalone_block(child)
        if block:
            out_blocks.append(block)
    title = ""
    h1 = scroller.find(["h1", "h2"])
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))
    return {"title": title, "blocks": out_blocks}


def standalone_block(el) -> dict[str, Any] | None:
    if isinstance(el, NavigableString):
        text = clean_text(str(el))
        if text:
            return {"type": "text", "text": text}
        return None
    if not isinstance(el, Tag):
        return None
    name = el.name.lower()
    if name in ("script", "style", "select", "input", "form", "nav"):
        return None
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return {
            "type": "heading",
            "level": int(name[1]),
            "text": clean_text(el.get_text(" ", strip=True)),
            "html": str(el),
            "id": el.get("id"),
        }
    if name == "p":
        return {
            "type": "paragraph",
            "text": clean_text(el.get_text(" ", strip=True)),
            "html": str(el),
            "id": el.get("id"),
        }
    if name in ("ul", "ol"):
        items = []
        for li in el.find_all("li", recursive=False):
            items.append(
                {
                    "text": clean_text(li.get_text(" ", strip=True)),
                    "html": str(li),
                }
            )
        return {"type": "list", "ordered": name == "ol", "items": items}
    if name in ("div", "section", "article"):
        # Recurse into the div, returning its sub-blocks as a group.
        sub: list[dict[str, Any]] = []
        for c in el.children:
            b = standalone_block(c)
            if b:
                sub.append(b)
        if not sub:
            text = clean_text(el.get_text(" ", strip=True))
            if not text:
                return None
            return {"type": "text", "text": text, "html": str(el)}
        return {
            "type": "group",
            "id": el.get("id"),
            "classes": el.get("class") or [],
            "blocks": sub,
        }
    if name == "table":
        return {"type": "table", "html": str(el)}
    text = clean_text(el.get_text(" ", strip=True))
    if text:
        return {"type": "text", "text": text, "html": str(el)}
    return None


# ---------------------------------------------------------------------------
# File walking + writing
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def basename_for(category: str, lang: str, filename: str) -> str:
    """Strip language and m_<lang>_ / m_estructura_ prefix from filename, keep canonical basename."""
    stem = Path(filename).stem
    prefix_lang = f"m_{lang}_"
    prefix_estr = "m_estructura_"
    if stem.startswith(prefix_lang):
        return stem[len(prefix_lang) :]
    if stem.startswith(prefix_estr):
        return stem[len(prefix_estr) :]
    return stem


def discover_aligned() -> dict[str, dict[str, dict[str, Path]]]:
    """Build {category: {basename: {lang: Path}}} for aligned categories."""
    out: dict[str, dict[str, dict[str, Path]]] = {}
    for cat in ALIGNED_CATEGORIES:
        cat_map: dict[str, dict[str, Path]] = {}
        for lang in LANGUAGES:
            lang_dir = LANG_DIRS[lang] / cat
            if not lang_dir.is_dir():
                continue
            for f in sorted(lang_dir.iterdir()):
                if f.suffix.lower() != ".html":
                    continue
                base = basename_for(cat, lang, f.name)
                cat_map.setdefault(base, {})[lang] = f
        out[cat] = cat_map
    return out


def discover_estructura() -> dict[str, dict[str, Path]]:
    """Build {category: {basename: Path}} for estructura files."""
    out: dict[str, dict[str, Path]] = {}
    estr_root = ROOT / "m_estructura"
    for cat_dir in sorted(estr_root.iterdir()):
        if not cat_dir.is_dir():
            continue
        cat = cat_dir.name
        cat_map: dict[str, Path] = {}
        for f in sorted(cat_dir.iterdir()):
            if f.suffix.lower() != ".html":
                continue
            base = basename_for(cat, "", f.name)
            cat_map[base] = f
        out[cat] = cat_map
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def convert_aligned(
    aligned: dict[str, dict[str, dict[str, Path]]],
    estructura: dict[str, dict[str, Path]],
) -> dict[str, Any]:
    """Convert the aligned categories. Returns the summary for index.json."""
    summary: dict[str, Any] = {}

    for category, basenames in aligned.items():
        cat_summary: dict[str, Any] = {}
        for basename, lang_paths in basenames.items():
            print(f"  {category}/{basename} → {sorted(lang_paths)}", flush=True)

            # 1) Per-language flat blocks.
            lang_blocks: dict[str, list[dict[str, Any]]] = {}
            for lang, path in lang_paths.items():
                html = path.read_text(encoding="utf-8")
                blocks = parse_hijo_blocks(html, lang)
                payload = {
                    "language": lang,
                    "category": category,
                    "basename": basename,
                    "source_file": str(path.relative_to(ROOT)),
                    "block_count": len(blocks),
                    "blocks": blocks,
                }
                out_path = OUT_DIR / "by-language" / lang / category / f"{basename}.json"
                write_json(out_path, payload)
                lang_blocks[lang] = blocks

            # 2) Estructura.
            estr_path = estructura.get(category, {}).get(basename)
            estr_data = None
            if estr_path is not None:
                html = estr_path.read_text(encoding="utf-8")
                estr_data = parse_estructura(html)
                estr_payload = {
                    "category": category,
                    "basename": basename,
                    "source_file": str(estr_path.relative_to(ROOT)),
                    "days": estr_data["days"],
                }
                write_json(OUT_DIR / "structure" / category / f"{basename}.json", estr_payload)

            # 3) Days merged.
            day_ids: list[str] = []
            if estr_data and estr_data["days"]:
                for day in estr_data["days"]:
                    day_id = day.get("id") or "_root"
                    day_ids.append(day_id)
                    merged = build_day_payload(category, basename, day, lang_blocks)
                    out_path = OUT_DIR / "days" / category / basename / f"{day_id}.json"
                    write_json(out_path, merged)

            cat_summary[basename] = {
                "languages": sorted(lang_paths.keys()),
                "block_counts": {lg: len(b) for lg, b in lang_blocks.items()},
                "has_structure": estr_data is not None,
                "day_ids": day_ids,
            }
        summary[category] = cat_summary
    return summary


def build_day_payload(
    category: str,
    basename: str,
    day: dict[str, Any],
    lang_blocks: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    # Build a quick lookup: lang → {n: block}
    by_n: dict[str, dict[int, dict[str, Any]]] = {
        lg: {b["n"]: b for b in blocks} for lg, blocks in lang_blocks.items()
    }

    languages_present: set[str] = set()

    def fill(n: int) -> dict[str, Any]:
        out: dict[str, Any] = {"n": n, "content": {}}
        for lg in LANGUAGES:
            if lg not in by_n:
                continue
            block = by_n[lg].get(n)
            if not block:
                continue
            out["content"][lg] = {
                "text": block["text"],
                "html": block["html"],
                "segments": block["segments"],
            }
            if block["text"]:
                languages_present.add(lg)
        return out

    out_slots: list[dict[str, Any]] = []
    for slot in day["slots"]:
        st = slot.get("type")
        if st in ("cycle_start", "cycle_end"):
            out_slots.append(slot)
            continue

        items: list[dict[str, Any]] = []
        for n in slot.get("padres", []):
            items.append({"role": "main", "padre": n, **fill(n)})
        for grp in slot.get("groups", []):
            items.append(
                {"role": grp["group"], "padre": grp["padre"], **fill(grp["padre"])}
            )
        out_slots.append(
            {
                "type": st,
                "id": slot.get("id"),
                "classes": slot.get("classes"),
                "padre_classes": slot.get("padre_classes"),
                "items": items,
            }
        )

    return {
        "id": day.get("id"),
        "category": category,
        "basename": basename,
        "estructura_languages": day.get("languages", []),
        "languages_with_content": sorted(languages_present),
        "slots": out_slots,
    }


def convert_standalone() -> dict[str, Any]:
    """Convert IGMR and sacerdotale documents."""
    summary: dict[str, Any] = {}
    igmr_dir = ROOT / "igmr"
    if igmr_dir.is_dir():
        igmr_summary: dict[str, Any] = {}
        for f in sorted(igmr_dir.iterdir()):
            if not f.suffix == ".html":
                continue
            m = re.match(r"igmr_(\w+)\.html", f.name)
            if not m:
                continue
            lang = m.group(1)
            print(f"  igmr/{f.name}", flush=True)
            html = f.read_text(encoding="utf-8")
            data = parse_standalone(html)
            payload = {
                "document": "igmr",
                "language": lang,
                "source_file": str(f.relative_to(ROOT)),
                "title": data["title"],
                "block_count": len(data["blocks"]),
                "blocks": data["blocks"],
            }
            write_json(OUT_DIR / "standalone" / "igmr" / f"{lang}.json", payload)
            igmr_summary[lang] = {"block_count": len(data["blocks"])}
        summary["igmr"] = igmr_summary

    sac_dir = ROOT / "sacerdotale"
    if sac_dir.is_dir():
        sac_summary: dict[str, Any] = {}
        for f in sorted(sac_dir.iterdir()):
            if not f.suffix == ".html":
                continue
            m = re.match(r"sacerdotale_(\w+?)(_new)?\.html", f.name)
            if not m:
                continue
            lang = m.group(1)
            print(f"  sacerdotale/{f.name}", flush=True)
            html = f.read_text(encoding="utf-8")
            data = parse_standalone(html)
            payload = {
                "document": "sacerdotale",
                "language": lang,
                "source_file": str(f.relative_to(ROOT)),
                "title": data["title"],
                "block_count": len(data["blocks"]),
                "blocks": data["blocks"],
            }
            write_json(OUT_DIR / "standalone" / "sacerdotale" / f"{lang}.json", payload)
            sac_summary[lang] = {"block_count": len(data["blocks"])}
        summary["sacerdotale"] = sac_summary

    return summary


def main():
    print(f"Source root: {ROOT}", flush=True)
    print(f"Output dir : {OUT_DIR}", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Discovering files…", flush=True)
    aligned = discover_aligned()
    estructura = discover_estructura()

    print("Converting aligned categories…", flush=True)
    aligned_summary = convert_aligned(aligned, estructura)

    print("Converting standalone documents…", flush=True)
    standalone_summary = convert_standalone()

    index = {
        "languages": LANGUAGES,
        "aligned_categories": aligned_summary,
        "standalone_documents": standalone_summary,
    }
    write_json(OUT_DIR / "index.json", index)
    print(f"Done. Index written to {OUT_DIR/'index.json'}", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
