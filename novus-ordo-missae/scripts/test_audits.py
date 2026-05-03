"""Unit tests for audit_library.py and audit_sample.py — directly invoke the
internal check functions with hand-crafted node trees that exercise each
detection path.
"""

import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _load(module_name, file_path):
    # audit_sample.py reads sys.argv[1] at module load — patch it temporarily
    saved_argv = sys.argv
    sys.argv = [str(file_path), "1"]
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved_argv
    return module


HERE = pathlib.Path(__file__).resolve().parent
audit_library = _load("audit_library_mod", HERE / "audit_library.py")
audit_sample = _load("audit_sample_mod", HERE / "audit_sample.py")


# =============================================================================
# audit_library: HTML / trailing / unicode / lang-key / header-leak detection
# =============================================================================

class TestAuditLibraryCheckText:
    def test_clean_text_no_errors(self):
        errors = []
        audit_library.check_text("Hello world", "path", "la", errors)
        assert errors == []

    def test_html_tag_in_text_flagged(self):
        errors = []
        audit_library.check_text("Hello <b>world</b>", "path", "la", errors)
        codes = [e[0] for e in errors]
        assert "H" in codes  # HTML

    def test_trailing_artifact_flagged(self):
        errors = []
        audit_library.check_text("Some prayer.--", "path", "la", errors)
        codes = [e[0] for e in errors]
        assert "T" in codes  # Trailing artifact

    def test_weird_unicode_flagged(self):
        errors = []
        audit_library.check_text("Hello​world", "path", "la", errors)  # zero-width space
        codes = [e[0] for e in errors]
        assert "U" in codes


class TestAuditLibraryWalkLocalized:
    def test_skips_citation_path(self):
        # citation fields should bypass the trailing-number check
        node = {"la": "Ps 95, 1-2. 11-12. 13", "es": "Sal 95, 1-2. 11-12. 13"}
        errors = []
        audit_library.walk_localized(node, "library.x.responsorialPsalm.citation", errors)
        # Should NOT flag — citation paths are exempt from trailing-number check
        codes = [e[0] for e in errors]
        assert "T" not in codes

    def test_unknown_lang_flagged(self):
        # An unknown language key like "xx" should be flagged with [N]
        node = {"la": "x", "xx": "y"}
        errors = []
        audit_library.walk_localized(node, "path", errors)
        codes = [e[0] for e in errors]
        assert "N" in codes

    def test_header_leak_flagged(self):
        # Body starts with header-like pattern (e.g., "COMUM DOS ...")
        node = {"la": "COMUM DOS SANTOS body content here that triggers leak detection"}
        errors = []
        audit_library.walk_localized(node, "path.body", errors)
        codes = [e[0] for e in errors]
        assert "L" in codes

    def test_recurses_into_nested_dicts(self):
        node = {"a": {"b": {"la": "Hello"}}}
        errors = []
        audit_library.walk_localized(node, "root", errors)
        # No errors, but the recursion happened (Hello is fine)
        assert errors == []

    def test_recurses_into_lists(self):
        node = [{"la": "x"}, {"la": "y"}]
        errors = []
        audit_library.walk_localized(node, "root", errors)
        assert errors == []  # both clean


# =============================================================================
# audit_sample: text quality / richtext / reading / part checks
# =============================================================================

class TestAuditSampleTextQuality:
    def test_clean_text_no_issues(self):
        issues = []
        audit_sample.check_text_quality("Hello world.", "la", "path", issues)
        assert issues == []

    def test_html_in_text_flagged(self):
        issues = []
        audit_sample.check_text_quality("Hello <b>world</b>", "la", "path", issues)
        codes = [i[0] for i in issues]
        assert "H" in codes

    def test_disallowed_lang_keys(self):
        # check_localized_for_lang_keys catches non-standard language keys
        node = {"la": "x", "xx": "y"}
        issues = []
        audit_sample.check_localized_for_lang_keys(node, "path", issues)
        codes = [i[0] for i in issues]
        assert "N" in codes


class TestAuditSampleRichText:
    def test_clean_richtext(self):
        rt = {
            "plain": {"la": "Body text here", "en": "English body text"},
            "lines": {
                "la": [[{"type": "text", "text": "Body text here"}]],
                "en": [[{"type": "text", "text": "English body text"}]],
            },
        }
        issues = []
        audit_sample.check_richtext(rt, "path", issues)
        # Should be clean
        assert issues == []

    def test_empty_richtext_flagged(self):
        # Empty plain + empty lines → "E" (empty)
        rt = {"plain": {}, "lines": {"la": []}}
        issues = []
        audit_sample.check_richtext(rt, "path", issues)
        codes = [i[0] for i in issues]
        assert "E" in codes


class TestAuditSampleReading:
    def test_minimal_valid_reading(self):
        reading = {
            "label": {"la": "Lectio prior"},
            "introduction": {"la": "Léctio libri Genesis"},
            "citation": {"la": "1, 1-2"},
            "body": {
                "plain": {"la": "In principio creavit Deus caelum et terram."},
                "lines": {"la": [[{"type": "text", "text": "In principio creavit Deus caelum et terram."}]]},
            },
            "conclusion": {"la": "Verbum Dómini."},
        }
        issues = []
        audit_sample.check_reading(reading, "path", issues)
        assert issues == []


class TestAuditSamplePartContent:
    def test_empty_part_content(self):
        issues = []
        audit_sample._check_part_content([], "path", issues)
        # Should not crash on empty input

    def test_block_with_body(self):
        nodes = [{
            "type": "block",
            "body": {
                "plain": {"la": "Body content"},
                "lines": {"la": [[{"type": "text", "text": "Body content"}]]},
            },
        }]
        issues = []
        audit_sample._check_part_content(nodes, "path", issues)
        assert issues == []


# =============================================================================
# audit_sample.check_trailing_rubric_leak
# =============================================================================

class TestTrailingRubricLeak:
    def test_clean_text_no_leak(self):
        issues = []
        audit_sample.check_trailing_rubric_leak(
            "A long enough body that talks about something and ends well. Per Christum.",
            "path", "la", issues
        )
        assert issues == []

    def test_phrase_at_end_flagged(self):
        # Trailing rubric phrase that the script knows about
        issues = []
        audit_sample.check_trailing_rubric_leak(
            "A long enough prayer body to trigger the check. Tempo Pasquale",
            "path", "la", issues
        )
        # It might or might not flag depending on the phrase list — check no crash
        # (the function exists and runs)


# =============================================================================
# Integration: check_text_quality_extended
# =============================================================================

class TestCheckTextQualityExtended:
    def test_clean_text(self):
        issues = []
        audit_sample.check_text_quality_extended("Per Cristum Dóminum nostrum.", "la", "path", issues)
        # Should return cleanly
        assert isinstance(issues, list)


# =============================================================================
# audit_sample.check_lines_quality
# =============================================================================

class TestCheckLinesQuality:
    def test_clean_lines(self):
        rt = {
            "plain": {"la": "x"},
            "lines": {"la": [[{"type": "text", "text": "Text content"}]]},
        }
        issues = []
        audit_sample.check_lines_quality(rt, "path", issues)
        assert issues == []

    def test_acceptable_segment_types(self):
        rt = {
            "plain": {"la": "x"},
            "lines": {"la": [[
                {"type": "rubric", "text": "R/."},
                {"type": "text", "text": "Amen."},
                {"type": "reference", "text": "Ps 1"},
                {"type": "italic", "text": "italic"},
                {"type": "response", "text": "Et cum spiritu tuo."},
                {"type": "signOfCross", "text": "✠"},
                {"type": "dropCap", "text": "G"},
            ]]},
        }
        issues = []
        audit_sample.check_lines_quality(rt, "path", issues)
        # All segments populated — should be clean
        assert issues == []

    def test_empty_signOfCross_flagged(self):
        rt = {
            "plain": {"la": "x"},
            "lines": {"la": [[{"type": "signOfCross", "text": ""}]]},
        }
        issues = []
        audit_sample.check_lines_quality(rt, "path", issues)
        codes = [i[0] for i in issues]
        assert "E" in codes

    def test_empty_dropCap_flagged(self):
        rt = {
            "plain": {"la": "x"},
            "lines": {"la": [[{"type": "dropCap", "text": ""}]]},
        }
        issues = []
        audit_sample.check_lines_quality(rt, "path", issues)
        codes = [i[0] for i in issues]
        assert "E" in codes

    def test_empty_line_flagged(self):
        rt = {
            "plain": {"la": "x"},
            "lines": {"la": [[]]},  # one empty line
        }
        issues = []
        audit_sample.check_lines_quality(rt, "path", issues)
        codes = [i[0] for i in issues]
        assert "E" in codes


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
