"""Unit tests for validate.py — schema + cross-reference checks.

Most of validate.py runs against the live data/ directory, so the tests here
are mostly integration-style end-to-end checks: regenerate is not required;
we just exercise the validator against the current data/ snapshot.
"""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
PYTHON = "/tmp/missal_venv2/bin/python"


def test_validate_runs_clean():
    """validate.py should report 0 errors / 0 warnings on the current data/."""
    if not DATA.exists():
        pytest.skip("data/ not generated")
    result = subprocess.run(
        [PYTHON, str(SCRIPTS / "validate.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, f"validate.py failed:\n{result.stdout}\n{result.stderr}"
    assert "errors: 0" in result.stdout
    assert "warnings: 0" in result.stdout


def test_audit_library_runs_clean():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    result = subprocess.run(
        [PYTHON, str(SCRIPTS / "audit_library.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert "0 issues" in result.stdout, f"audit_library reported issues:\n{result.stdout}"


def test_audit_sample_runs_clean_for_seed_1():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    result = subprocess.run(
        [PYTHON, str(SCRIPTS / "audit_sample.py"), "1"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert "No bugs found" in result.stdout, f"audit_sample seed=1 found bugs:\n{result.stdout}"


# -----------------------------------------------------------------------------
# Direct invariants over the data/ tree (don't shell out)
# -----------------------------------------------------------------------------

def _all_mass_files():
    if not (DATA / "masses").exists():
        return []
    return [f for f in (DATA / "masses").rglob("*.json") if f.name != "_index.json"]


def _iter_masses():
    """Yield each per-file mass dict."""
    for f in _all_mass_files():
        yield json.load(f.open())


def test_no_duplicate_mass_ids():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    seen = set()
    for f in _all_mass_files():
        m = json.load(f.open())
        mid = m.get("id")
        assert mid, f"Mass with no id in {f.name}"
        assert mid not in seen, f"Duplicate mass id: {mid}"
        seen.add(mid)


def test_all_scopes_lowercase_kebab():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    for m in _iter_masses():
        scope = m.get("scope")
        if scope:
            assert scope == scope.lower(), f"Scope not lowercase: {m['id']} scope={scope!r}"
            assert " " not in scope, f"Scope contains space: {m['id']} scope={scope!r}"


def test_all_dateSuffix_lowercase():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    for m in _iter_masses():
        ds = m.get("dateSuffix")
        if ds:
            assert ds == ds.lower(), f"dateSuffix not lowercase: {m['id']} dateSuffix={ds!r}"


def test_reading_slot_canonical_order():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    canonical = ["firstReading", "responsorialPsalm", "secondReading", "gospelAcclamation", "gospel"]
    for m in _iter_masses():
        for cy, slots in (m.get("readings") or {}).items():
            if not isinstance(slots, dict):
                continue
            relevant = [k for k in slots.keys() if k in canonical]
            expected = [k for k in canonical if k in slots]
            assert relevant == expected, (
                f"Out-of-order readings in {m['id']} cycle={cy}: {relevant}"
            )


def test_rank_and_localized_consistent():
    if not DATA.exists():
        pytest.skip("data/ not generated")
    for m in _iter_masses():
        rank = m.get("rank")
        rl = m.get("rankLocalized") or {}
        # If one is present, the other must be present too.
        if rank or rl:
            assert rank, f"{m['id']} has rankLocalized but no rank"
            assert rl, f"{m['id']} has rank={rank!r} but no rankLocalized"


def test_no_html_torn_fragments_in_bodies():
    """No `p>`, `<`, `</p>`, etc. as standalone segment text in mass bodies."""
    if not DATA.exists():
        pytest.skip("data/ not generated")
    import re
    TORN = re.compile(r"^[<>/]*\s*(?:p|br|span|div|font)?\s*[<>/]*$", re.IGNORECASE)
    for m in _iter_masses():
        for field in ("entranceAntiphon", "collect", "prayerOverOfferings",
                      "communionAntiphon", "postcommunion"):
            v = m.get(field) or {}
            lines_dict = (v.get("body") or {}).get("lines") or {}
            for lang, lines in lines_dict.items():
                if not isinstance(lines, list):
                    continue
                for line in lines:
                    for seg in line:
                        t = (seg.get("text") or "").strip()
                        if t and len(t) <= 8 and any(c in t for c in "<>/") and TORN.match(t):
                            pytest.fail(f"HTML torn fragment in {m['id']} {field} {lang}: {t!r}")


def test_no_untranslated_latin_leak_in_vernacular():
    """Vernacular body should never be byte-identical to the Latin body."""
    if not DATA.exists():
        pytest.skip("data/ not generated")
    import re
    def norm(s):
        return re.sub(r"\s+", " ", (s or "").strip()).lower()
    for m in _iter_masses():
        for field in ("entranceAntiphon", "collect", "prayerOverOfferings",
                      "communionAntiphon", "postcommunion"):
            v = m.get(field) or {}
            plain = (v.get("body") or {}).get("plain") or {}
            la = norm(plain.get("la", ""))
            if not la or len(la) < 30:
                continue
            for lang in ("es", "en", "pt-BR", "it", "fr", "de"):
                other = norm(plain.get(lang, ""))
                if other and other == la:
                    pytest.fail(f"Latin leak: {m['id']}.{field}.{lang} == .la")


def test_all_prefaceRef_resolve():
    pref_dir = DATA / "library" / "preface"
    if not DATA.exists() or not pref_dir.exists():
        pytest.skip("data/library/preface/ not generated")
    preface_ids = set()
    for f in pref_dir.glob("*.json"):
        if f.name == "_index.json":
            continue
        preface_ids.add(json.load(f.open())["id"])
    for m in _iter_masses():
        p = m.get("preface")
        if isinstance(p, dict) and "prefaceRef" in p:
            ref = p["prefaceRef"]
            assert ref in preface_ids, f"Broken prefaceRef in {m['id']}: {ref}"


def test_calendar_entries_resolve_to_masses():
    cal_root = DATA / "calendar"
    if not DATA.exists() or not cal_root.exists():
        pytest.skip("data/calendar/ not generated")
    mass_ids = {m["id"] for m in _iter_masses()}
    # Triduum is a reference list — entries must already be in mass_ids.
    for f in cal_root.rglob("*.json"):
        if f.name == "_index.json":
            continue
        entry = json.load(f.open())
        mid = entry.get("id")
        assert mid in mass_ids, f"Calendar entry has no mass: {mid}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
