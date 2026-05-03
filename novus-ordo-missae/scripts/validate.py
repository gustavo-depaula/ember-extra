#!/usr/bin/env python3
"""
Validate the Missale Romanum JSON corpus.

Checks:
  - Every JSON file conforms to schema/missal.schema.json
  - Every Mass id is unique
  - Every prefaceRef resolves to an entry in library/prefaces.json
  - Every language tag is one of the supported BCP-47 codes
  - No leftover HTML markers, no leftover scaffolding fields
  - Counts match index.json totals

Exits 0 on success, non-zero on any failure.

Requires: jsonschema
    /tmp/missal_venv2/bin/pip install jsonschema
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHEMA = ROOT / "schema" / "missal.schema.json"

SUPPORTED_LANGS = {"la", "es", "en", "pt-BR", "it", "fr", "de"}

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def load_json(path: Path) -> Any:
    with path.open() as f:
        return json.load(f)


def load_schema():
    with SCHEMA.open() as f:
        return json.load(f)


def validate_against_schema(schema, defs_key: str, instance, label: str):
    """Validate `instance` against the named definition in schema/$defs."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        warn("jsonschema not installed — schema validation skipped. `pip install jsonschema` to enable.")
        return
    sub = {"$schema": "https://json-schema.org/draft/2020-12/schema",
           "$id": schema["$id"], "$ref": f"#/$defs/{defs_key}", "$defs": schema["$defs"]}
    v = Draft202012Validator(sub)
    for e in v.iter_errors(instance):
        err(f"{label}: schema violation at {list(e.absolute_path)}: {e.message}")


# ---------------------------------------------------------------------------
# Walkers
# ---------------------------------------------------------------------------


HTML_TAG_RE = re.compile(r"<\w+[^>]*>")


def find_html_residue(node: Any, path: str = "") -> None:
    """Detect any remaining HTML tags in string fields."""
    if isinstance(node, str):
        if HTML_TAG_RE.search(node):
            warn(f"HTML residue at {path}: {node[:80]!r}")
    elif isinstance(node, dict):
        for k, v in node.items():
            find_html_residue(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find_html_residue(v, f"{path}[{i}]")


def find_disallowed_lang_keys(node: Any, path: str = "") -> None:
    """Detect any localized dict that uses a non-supported language key."""
    if isinstance(node, dict):
        # Heuristic: a Localized dict has all-string values and at least one supported key.
        keys = set(node.keys())
        looks_localized = bool(keys & SUPPORTED_LANGS) and all(isinstance(v, str) for v in node.values()) and bool(node)
        if looks_localized:
            extra = keys - SUPPORTED_LANGS
            if extra:
                err(f"Disallowed language tags at {path}: {sorted(extra)}")
        for k, v in node.items():
            find_disallowed_lang_keys(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find_disallowed_lang_keys(v, f"{path}[{i}]")


def find_scaffolding(node: Any, path: str = "") -> None:
    """Detect leftover internal fields that shouldn't appear in the output."""
    forbidden = {"_sourceHtml", "_src", "legacyDayId", "legacyBasename", "legacyId"}
    if isinstance(node, dict):
        for k, v in node.items():
            if k in forbidden:
                err(f"Scaffolding field at {path}: {k}")
            find_scaffolding(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find_scaffolding(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Cross-reference validation
# ---------------------------------------------------------------------------


def iter_item_files(root: Path):
    """Yield every per-item *.json under root, skipping _index.json sentinels."""
    for jf in root.rglob("*.json"):
        if jf.name == "_index.json":
            continue
        yield jf


def id_to_path(item_id: str, root: Path, *, suffix: str = ".json") -> Path:
    """Mirror of refine.id_to_path — used to verify each file lives where its id says."""
    parts = item_id.split(".")
    return root.joinpath(*parts[:-1], parts[-1] + suffix)


def collect_mass_ids() -> dict[str, str]:
    """Return {mass_id: source_file_path}."""
    out: dict[str, str] = {}
    masses_root = DATA / "masses"
    for jf in iter_item_files(masses_root):
        d = load_json(jf)
        mid = d.get("id")
        if not mid:
            err(f"Mass missing id in {jf.relative_to(DATA)}")
            continue
        if mid in out:
            err(f"Duplicate mass id {mid!r}: in {out[mid]} and {jf.relative_to(DATA)}")
        else:
            out[mid] = str(jf.relative_to(DATA))
        # Path-matches-id check: catches mis-routed writes.
        expected = id_to_path(mid, masses_root)
        if jf != expected:
            err(f"Mass {mid!r} at {jf.relative_to(DATA)} does not match id-derived path {expected.relative_to(DATA)}")
    return out


def collect_preface_ids() -> set[str]:
    ids = set()
    pref_root = DATA / "library" / "preface"
    for jf in iter_item_files(pref_root):
        p = load_json(jf)
        pid = p.get("id")
        if not pid:
            err(f"Preface missing id in {jf.relative_to(DATA)}")
            continue
        if pid in ids:
            err(f"Duplicate preface id {pid!r}")
        ids.add(pid)
    return ids


def validate_preface_refs(preface_ids: set[str]) -> None:
    """Walk every Mass and ensure every prefaceRef resolves."""
    for jf in iter_item_files(DATA / "masses"):
        m = load_json(jf)
        preface = m.get("preface") or {}
        ref = preface.get("prefaceRef") if isinstance(preface, dict) else None
        if ref:
            if ref not in preface_ids:
                err(f"Mass {m.get('id')}: prefaceRef {ref!r} not in library")
        for alt in (preface.get("alternativeRefs") if isinstance(preface, dict) else None) or []:
            if alt not in preface_ids:
                err(f"Mass {m.get('id')}: alternativeRef {alt!r} not in library")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not DATA.exists():
        print(f"data/ not found at {DATA}", file=sys.stderr)
        return 2

    schema = load_schema()

    print("→ Loading index…")
    index = load_json(DATA / "index.json")
    expected_languages = set(index.get("languages") or [])
    if expected_languages != SUPPORTED_LANGS:
        warn(f"index.json languages {sorted(expected_languages)} != supported set {sorted(SUPPORTED_LANGS)}")

    print("→ Validating individual mass files against schema…")
    mass_count = 0
    for jf in sorted(iter_item_files(DATA / "masses")):
        m = load_json(jf)
        mass_count += 1
        validate_against_schema(schema, "Mass", m, f"{jf.relative_to(DATA)}#{m.get('id')}")
        find_html_residue(m, f"{m.get('id')}")
        find_disallowed_lang_keys(m, f"{m.get('id')}")
        find_scaffolding(m, f"{m.get('id')}")

    print(f"  validated {mass_count} mass formularies")

    print("→ Validating libraries…")
    for jf in iter_item_files(DATA / "library" / "preface"):
        p = load_json(jf)
        validate_against_schema(schema, "Preface", p, f"prefaces#{p.get('id')}")
        find_html_residue(p, p.get("id", "?"))
    for jf in iter_item_files(DATA / "library" / "eucharistic-prayer"):
        e = load_json(jf)
        validate_against_schema(schema, "EucharisticPrayer", e, f"eps#{e.get('id')}")
    for jf in iter_item_files(DATA / "library" / "ordinary"):
        op = load_json(jf)
        validate_against_schema(schema, "OrdinaryPart", op, f"ordinary#{op.get('id')}")

    print("→ Validating saints catalog…")
    saint_count = 0
    for jf in iter_item_files(DATA / "saints"):
        s = load_json(jf)
        saint_count += 1
        validate_against_schema(schema, "SaintEntry", s, f"saints#{s.get('id')}")

    print("→ Validating calendar…")
    for jf in iter_item_files(DATA / "calendar"):
        entry = load_json(jf)
        validate_against_schema(schema, "CalendarEntry", entry, f"calendar#{entry.get('id')}")

    print("→ Cross-references (preface refs)…")
    mass_ids = collect_mass_ids()
    preface_ids = collect_preface_ids()
    validate_preface_refs(preface_ids)

    print("→ Counting…")
    expected = index.get("totals", {})
    if expected.get("masses") and expected["masses"] != mass_count:
        err(f"index.json totals.masses = {expected['masses']} but found {mass_count}")
    expected_saints = expected.get("saintsCatalog")
    if expected_saints and expected_saints != saint_count:
        err(f"index.json totals.saintsCatalog = {expected_saints} but found {saint_count}")

    print()
    print(f"masses: {mass_count}")
    print(f"prefaces: {len(preface_ids)}")
    print(f"unique mass ids: {len(mass_ids)}")
    print(f"errors: {len(errors)}")
    print(f"warnings: {len(warnings)}")

    if warnings:
        print()
        print("WARNINGS:")
        for w in warnings[:30]:
            print(f"  · {w}")
        if len(warnings) > 30:
            print(f"  ... and {len(warnings) - 30} more")

    if errors:
        print()
        print("ERRORS:")
        for e in errors[:30]:
            print(f"  ✗ {e}")
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more")
        return 1

    print()
    print("✓ All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
