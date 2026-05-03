"""Session-scoped fixtures shared across the test suite.

Loading the corpus and running validate.py are the two things that dominate
test wall-clock time. Both are pure with respect to the data on disk, so we
do them once per session and let every test reuse the result.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
from dataclasses import dataclass

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
SCHEMA_PATH = ROOT / "schema" / "missal.schema.json"

sys.path.insert(0, str(HERE))


def _require_data() -> None:
    if not DATA.exists():
        pytest.skip("data/ not generated")


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def all_mass_files() -> list[pathlib.Path]:
    _require_data()
    masses = DATA / "masses"
    if not masses.exists():
        pytest.skip("data/masses not generated")
    return sorted(f for f in masses.rglob("*.json") if f.name != "_index.json")


@pytest.fixture(scope="session")
def masses_by_file(all_mass_files) -> dict[pathlib.Path, dict]:
    return {f: json.loads(f.read_text()) for f in all_mass_files}


@pytest.fixture(scope="session")
def all_masses(masses_by_file) -> list[dict]:
    return list(masses_by_file.values())


@pytest.fixture(scope="session")
def sanctorale_masses(masses_by_file) -> dict[pathlib.Path, dict]:
    sanct_root = DATA / "masses" / "sanctorale"
    return {f: m for f, m in masses_by_file.items() if sanct_root in f.parents}


@pytest.fixture(scope="session")
def all_readings(all_masses) -> list[tuple[str, str, str, str]]:
    """Flattened (mass_id, slot, lang, citation) tuples for every reading citation."""
    out: list[tuple[str, str, str, str]] = []
    for m in all_masses:
        if not isinstance(m, dict):
            continue
        for cyc, slots in (m.get("readings") or {}).items():
            if not isinstance(slots, dict):
                continue
            for slot in ("firstReading", "secondReading", "gospel"):
                r = slots.get(slot)
                if not isinstance(r, dict):
                    continue
                cit = r.get("citation") or {}
                for L, v in cit.items():
                    if isinstance(v, str) and v.strip():
                        out.append((m["id"], slot, L, v))
    return out


@pytest.fixture(scope="session")
def schema() -> dict:
    if not SCHEMA_PATH.exists():
        pytest.skip("schema/missal.schema.json not generated")
    return json.loads(SCHEMA_PATH.read_text())


@dataclass
class ValidateResult:
    returncode: int
    errors: list[str]
    warnings: list[str]
    stdout: str


@pytest.fixture(scope="session")
def validate_result() -> ValidateResult:
    """Run validate.main() once per session and capture its result.

    The script uses module-level `errors`/`warnings` lists and ROOT-relative
    paths, so we reset state and chdir to ROOT before invoking it (mirrors the
    pattern in test_query_validate.py).
    """
    _require_data()
    validate = _load_module("validate_session", HERE / "validate.py")

    old_cwd = pathlib.Path.cwd()
    os.chdir(ROOT)
    try:
        validate.errors = []
        validate.warnings = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = validate.main()
        return ValidateResult(
            returncode=rc,
            errors=list(validate.errors),
            warnings=list(validate.warnings),
            stdout=buf.getvalue(),
        )
    finally:
        os.chdir(old_cwd)
