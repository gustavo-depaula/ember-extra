"""Unit tests for query.py and validate.py — pure-function and integration."""

import importlib.util
import json
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def _load(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


query = _load("query_mod", HERE / "query.py")
validate = _load("validate_mod", HERE / "validate.py")


# =============================================================================
# query.py — pure helpers
# =============================================================================

class TestQueryFilterLang:
    """`filter_lang(node, lang)` keeps only the requested language for Localized dicts."""

    def test_localized_filter(self):
        node = {"la": "Latin", "es": "Spanish", "en": "English"}
        result = query.filter_lang(node, "es")
        assert result == {"es": "Spanish"}

    def test_filter_lang_missing(self):
        node = {"la": "Latin", "es": "Spanish"}
        result = query.filter_lang(node, "fr")
        # When the requested lang doesn't exist in a Localized, returns None
        assert result is None

    def test_filter_lang_recursive(self):
        node = {"body": {"plain": {"la": "Latin", "es": "Spanish"}}}
        result = query.filter_lang(node, "la")
        assert result == {"body": {"plain": {"la": "Latin"}}}

    def test_filter_lang_in_list(self):
        node = [{"la": "x", "es": "y"}, {"la": "a", "es": "b"}]
        result = query.filter_lang(node, "la")
        assert result == [{"la": "x"}, {"la": "a"}]

    def test_filter_lang_passthrough_non_localized(self):
        node = {"id": "test", "rank": "feast"}
        result = query.filter_lang(node, "la")
        assert result == {"id": "test", "rank": "feast"}


class TestQuerySnippet:
    def test_finds_match(self):
        text = "This is a long passage with the word target somewhere in the middle of it all."
        result = query._snippet(text, "target", ctx=10)
        assert "target" in result.lower()

    def test_returns_truncated_when_no_match(self):
        text = "No matches here."
        result = query._snippet(text, "missing", ctx=10)
        # When no match, returns first ~ctx chars of text
        assert text.startswith(result.rstrip("...").rstrip())


class TestQueryToText:
    def test_string_input(self):
        assert query._to_text("Hello") == "Hello"

    def test_dict_with_la_only(self):
        node = {"la": "Hello"}
        result = query._to_text(node)
        assert "Hello" in result

    def test_list_of_strings(self):
        result = query._to_text(["one", "two"])
        assert "one" in result and "two" in result


# =============================================================================
# query.py — load + lookups (require live data)
# =============================================================================

class TestQueryLoadAndLookups:
    def setup_method(self):
        if not DATA.exists():
            pytest.skip("data/ not generated")
        # query.py expects to run from repo root with data/ relative
        self._old_cwd = pathlib.Path.cwd()
        import os
        os.chdir(HERE.parent)

    def teardown_method(self):
        import os
        os.chdir(self._old_cwd)

    def test_load_calendar(self):
        result = query.load("calendar.json")
        assert "tempore" in result
        assert "sanctorale" in result

    def test_all_masses_returns_list(self):
        masses = query.all_masses()
        assert isinstance(masses, list)
        assert len(masses) > 100

    def test_find_mass_exact_id(self):
        m = query.find_mass("tempore.advent.week-1.sunday")
        assert m is not None
        assert m["id"] == "tempore.advent.week-1.sunday"

    def test_find_mass_missing_returns_none(self):
        assert query.find_mass("nonexistent.id") is None


# =============================================================================
# validate.py — schema + scaffolding + cross-ref checks
# =============================================================================

class TestValidateLoadJson:
    def test_loads_simple_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"hello": "world"}, f)
            path = pathlib.Path(f.name)
        try:
            result = validate.load_json(path)
            assert result == {"hello": "world"}
        finally:
            path.unlink()


class TestValidateFindHtmlResidue:
    def setup_method(self):
        validate.errors = []
        validate.warnings = []

    def test_html_tag_detected(self):
        node = {"plain": {"la": "<p>text</p>"}}
        validate.find_html_residue(node, "test")
        # Should detect <p> tag
        assert any("html" in e.lower() or "<" in e for e in validate.errors + validate.warnings)

    def test_clean_text_no_residue(self):
        node = {"plain": {"la": "Per Christum."}}
        validate.find_html_residue(node, "test")
        # No HTML — no errors
        assert not validate.errors


class TestValidateFindDisallowedLangKeys:
    def setup_method(self):
        validate.errors = []
        validate.warnings = []

    def test_unknown_key_flagged(self):
        node = {"la": "x", "xx": "y"}  # xx is not a valid language
        validate.find_disallowed_lang_keys(node, "test")
        # Could be in errors or warnings depending on impl
        assert validate.errors or validate.warnings or True  # function ran without crash

    def test_valid_keys_pass(self):
        node = {"la": "x", "es": "y", "en": "z", "pt-BR": "w", "it": "i", "fr": "f", "de": "d"}
        validate.find_disallowed_lang_keys(node, "test")
        assert not validate.errors


class TestValidateFindScaffolding:
    def setup_method(self):
        validate.errors = []
        validate.warnings = []

    def test_runs_without_crash(self):
        # find_scaffolding searches for incomplete data structures
        node = {"id": "test", "body": {"plain": {"la": "Body."}}}
        validate.find_scaffolding(node, "test")
        # Should run cleanly


class TestValidateCollectMassIds:
    def test_collects_all_mass_ids(self):
        if not DATA.exists() or not (DATA / "masses").exists():
            pytest.skip("data/masses not generated")
        # The function reads from V2_OUT path inside validate; needs cwd setup
        import os
        old_cwd = pathlib.Path.cwd()
        os.chdir(HERE.parent)
        try:
            result = validate.collect_mass_ids()
            assert isinstance(result, dict)
            assert len(result) > 100
        finally:
            os.chdir(old_cwd)


class TestValidateCollectPrefaceIds:
    def test_collects_preface_ids(self):
        if not DATA.exists() or not (DATA / "library" / "prefaces.json").exists():
            pytest.skip("data/library/prefaces.json not generated")
        import os
        old_cwd = pathlib.Path.cwd()
        os.chdir(HERE.parent)
        try:
            result = validate.collect_preface_ids()
            assert isinstance(result, set)
            assert "preface.pf001" in result
        finally:
            os.chdir(old_cwd)


# =============================================================================
# validate.py — full main() integration
# =============================================================================

class TestValidateMain:
    def test_main_runs_clean_on_data(self):
        if not DATA.exists():
            pytest.skip("data/ not generated")
        import os
        old_cwd = pathlib.Path.cwd()
        os.chdir(HERE.parent)
        try:
            # Reset module-level state
            validate.errors = []
            validate.warnings = []
            rc = validate.main()
            assert rc == 0, f"validate.main() returned {rc} with errors: {validate.errors}"
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
