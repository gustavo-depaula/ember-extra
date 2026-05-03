"""Integration tests for refine.py's larger pipeline functions:
make_rich_text, prayer_from_items, antiphon_from_items, reading_from_items,
psalm_from_items, refine_segments_to_lines, _plain_from_lines.

Each test feeds realistic v1 input shapes and asserts the v2 output structure.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import refine as R


# =============================================================================
# refine_segments_to_lines
# =============================================================================

class TestRefineSegmentsToLines:
    def test_simple_text(self):
        segs = [{"type": "text", "value": "Hello world."}]
        lines = R.refine_segments_to_lines(segs)
        assert len(lines) == 1
        assert lines[0][0]["type"] == "text"
        assert lines[0][0]["text"] == "Hello world."

    def test_break_creates_new_line(self):
        # Lines must end with sentence terminators (`.,;:!?`) to survive the
        # mid-sentence merge step.
        segs = [
            {"type": "text", "value": "First sentence."},
            {"type": "break"},
            {"type": "text", "value": "Second sentence."},
        ]
        lines = R.refine_segments_to_lines(segs)
        assert len(lines) == 2
        assert lines[0][0]["text"] == "First sentence."
        assert lines[1][0]["text"] == "Second sentence."

    def test_paragraph_marks_create_lines(self):
        segs = [
            {"type": "paragraph_start"},
            {"type": "text", "value": "Hello"},
            {"type": "paragraph_end"},
        ]
        lines = R.refine_segments_to_lines(segs)
        assert any(l for l in lines if l and l[0].get("text") == "Hello")

    def test_dropped_types_removed(self):
        # heading / bold are in DROPPED_TYPES
        segs = [
            {"type": "heading", "level": 2, "text": "Title"},
            {"type": "text", "value": "Body."},
        ]
        lines = R.refine_segments_to_lines(segs)
        # heading dropped — only body remains
        flat = [s for l in lines for s in l]
        assert not any("Title" in s.get("text", "") for s in flat)
        assert any("Body" in s.get("text", "") for s in flat)

    def test_text_like_psalm_verse_kept(self):
        segs = [{"type": "psalm_verse", "text": "Verse text"}]
        lines = R.refine_segments_to_lines(segs)
        flat = [s for l in lines for s in l]
        assert any(s.get("text") == "Verse text" for s in flat)

    def test_rubric_mapped_correctly(self):
        segs = [{"type": "rubric", "text": "R/."}]
        lines = R.refine_segments_to_lines(segs)
        assert lines[0][0]["type"] == "rubric"

    def test_reference_mapped_correctly(self):
        segs = [{"type": "reference", "text": "Mt 5, 1-12"}]
        lines = R.refine_segments_to_lines(segs)
        assert lines[0][0]["type"] == "reference"

    def test_cross_segment_default_text(self):
        # cross type maps to signOfCross; if no text, default "✠"
        segs = [{"type": "cross"}]
        lines = R.refine_segments_to_lines(segs)
        assert lines[0][0]["type"] == "signOfCross"
        assert lines[0][0]["text"] == "✠"

    def test_dropcap_preserved(self):
        segs = [
            {"type": "capital", "text": "G"},
            {"type": "text", "value": "rátia plena."},
        ]
        lines = R.refine_segments_to_lines(segs)
        flat = [s for l in lines for s in l]
        assert any(s["type"] == "dropCap" and s["text"] == "G" for s in flat)

    def test_consecutive_text_segments_merged(self):
        # Two text segments with same line should merge
        segs = [
            {"type": "text", "value": "Hello "},
            {"type": "text", "value": "world."},
        ]
        lines = R.refine_segments_to_lines(segs)
        # Single line, single text segment with combined content
        assert len(lines) == 1
        text_segs = [s for s in lines[0] if s["type"] == "text"]
        assert len(text_segs) == 1
        assert "Hello world" in text_segs[0]["text"]

    def test_html_junk_filtered(self):
        # `p>` from authoring typo should be dropped
        segs = [
            {"type": "text", "value": "Real content"},
            {"type": "break"},
            {"type": "text", "value": "p>"},
            {"type": "break"},
            {"type": "text", "value": "More content"},
        ]
        lines = R.refine_segments_to_lines(segs)
        flat = [s for l in lines for s in l]
        assert not any(s.get("text") == "p>" for s in flat)


# =============================================================================
# _plain_from_lines
# =============================================================================

class TestPlainFromLines:
    def test_simple(self):
        lines = [[{"type": "text", "text": "Hello world."}]]
        assert R._plain_from_lines(lines) == "Hello world."

    def test_multi_line_joined_with_space(self):
        lines = [
            [{"type": "text", "text": "line one"}],
            [{"type": "text", "text": "line two"}],
        ]
        assert R._plain_from_lines(lines) == "line one line two"

    def test_dropcap_joined_without_space(self):
        # "G" + "rátia" should become "Grátia"
        lines = [[
            {"type": "dropCap", "text": "G"},
            {"type": "text", "text": "rátia plena"},
        ]]
        assert R._plain_from_lines(lines) == "Grátia plena"

    def test_strips_leading_antiphona_label(self):
        # Real Triduum case: "Antiphona 2" + "Cf. Io 13, 12" should be stripped
        lines = [[
            {"type": "rubric", "text": "Antiphona 2"},
            {"type": "reference", "text": "Cf. Io 13, 12"},
            {"type": "text", "text": "Real antiphon body."},
        ]]
        result = R._plain_from_lines(lines)
        assert "Antiphona 2" not in result
        assert "Real antiphon body" in result

    def test_empty_lines_produces_empty_string(self):
        assert R._plain_from_lines([]) == ""

    def test_skips_whitespace_only_text(self):
        lines = [[{"type": "text", "text": "  "}]]
        assert R._plain_from_lines(lines) == ""


# =============================================================================
# make_rich_text
# =============================================================================

class TestMakeRichText:
    def test_single_language(self):
        content = {"latin": {
            "text": "Hello world",
            "segments": [{"type": "text", "value": "Hello world"}],
        }}
        rt = R.make_rich_text(content)
        assert rt["plain"]["la"] == "Hello world"
        assert "la" in rt["lines"]

    def test_multi_language(self):
        content = {
            "latin": {"text": "Pax", "segments": [{"type": "text", "value": "Pax"}]},
            "engl": {"text": "Peace", "segments": [{"type": "text", "value": "Peace"}]},
            "cast": {"text": "Paz", "segments": [{"type": "text", "value": "Paz"}]},
        }
        rt = R.make_rich_text(content)
        assert rt["plain"]["la"] == "Pax"
        assert rt["plain"]["en"] == "Peace"
        assert rt["plain"]["es"] == "Paz"

    def test_returns_none_for_empty(self):
        assert R.make_rich_text({}) is None
        assert R.make_rich_text({"latin": {"text": "", "segments": []}}) is None

    def test_falls_back_to_text_when_no_segments(self):
        # No segments — should fall back to .text
        content = {"latin": {"text": "Just text", "segments": []}}
        rt = R.make_rich_text(content)
        assert rt["plain"]["la"] == "Just text"

    def test_drops_latin_leak_in_vernacular(self):
        long_la = "Apériens autem Petrus os dixit verba longa et significantia in tempore."
        content = {
            "latin": {"text": long_la, "segments": [{"type": "text", "value": long_la}]},
            "fran": {"text": long_la, "segments": [{"type": "text", "value": long_la}]},  # same as Latin
            "engl": {"text": "Pierre opened his mouth and said long and meaningful words.",
                     "segments": [{"type": "text", "value": "Pierre opened his mouth and said long and meaningful words."}]},
        }
        rt = R.make_rich_text(content)
        assert "la" in rt["plain"]
        assert "en" in rt["plain"]
        assert "fr" not in rt["plain"]  # dropped — fr == la

    def test_no_fallback_when_segments_existed_but_were_stripped(self):
        # If segments were present but produced empty plain (label-only stripped),
        # don't fall back to raw text.
        content = {"latin": {
            "text": "Antiphona 2 Cf. Io 13, 12. 13.",
            "segments": [
                {"type": "paragraph_start"},
                {"type": "rubric", "text": "Antiphona 2"},
                {"type": "reference", "text": "Cf. Io 13, 12. 13."},
                {"type": "paragraph_end"},
            ],
        }}
        rt = R.make_rich_text(content)
        # plain should be empty (the only segments were a label + reference,
        # both stripped from the plain output by _plain_from_lines)
        assert "la" not in rt["plain"]


# =============================================================================
# prayer_from_items
# =============================================================================

class TestPrayerFromItems:
    def test_post_role_chosen(self):
        # Body must be substantial enough to escape `_looks_like_fragment` and
        # `_looks_like_rubric_only` filters (>= 80 chars works).
        body_text = ("Concede, quaesumus, omnipotens Deus, ut famuli tui ad caelestia "
                     "nobis dona valeant pervenire. Per Christum Dóminum nostrum.")
        items = [{
            "role": "post",
            "content": {"latin": {"text": body_text,
                                  "segments": [{"type": "text", "value": body_text}]}},
        }]
        result = R.prayer_from_items(items)
        assert result is not None
        assert "Concede" in result["body"]["plain"]["la"]

    def test_main_role_chosen_when_no_post(self):
        items = [{
            "role": "main",
            "content": {"latin": {"text": "Body text.", "segments": [{"type": "text", "value": "Body text."}]}},
        }]
        result = R.prayer_from_items(items)
        assert result["body"]["plain"]["la"] == "Body text."

    def test_returns_none_for_empty(self):
        assert R.prayer_from_items([]) is None

    def test_strips_label_only_content(self):
        # Item with role=ant containing only a label heading should be excluded
        items = [
            {"role": "ant", "content": {"latin": {"text": "Collecta", "segments": [{"type": "heading", "level": 4, "text": "Collecta"}]}}},
            {"role": "post", "content": {"latin": {"text": "Real prayer text.", "segments": [{"type": "text", "value": "Real prayer text."}]}}},
        ]
        result = R.prayer_from_items(items)
        assert result["body"]["plain"]["la"] == "Real prayer text."


# =============================================================================
# antiphon_from_items
# =============================================================================

class TestAntiphonFromItems:
    def test_basic_antiphon(self):
        items = [{
            "role": "post",
            "content": {"latin": {"text": "Veníte, adoremus.", "segments": [{"type": "text", "value": "Veníte, adoremus."}]}},
        }]
        result = R.antiphon_from_items(items)
        assert result["body"]["plain"]["la"] == "Veníte, adoremus."

    def test_extracts_citation_from_reference(self):
        items = [
            {"role": "ant", "content": {"latin": {
                "segments": [{"type": "reference", "text": "Cf. Ps 24, 1-3"}],
            }}},
            {"role": "post", "content": {"latin": {"text": "Ad te levavi.", "segments": [{"type": "text", "value": "Ad te levavi."}]}}},
        ]
        result = R.antiphon_from_items(items)
        assert result.get("citation", {}).get("la") == "Cf. Ps 24, 1-3"

    def test_filters_prose_in_citation_field(self):
        # Reference segment with prose (no digits) should be filtered
        items = [
            {"role": "ant", "content": {"latin": {
                "segments": [{"type": "reference", "text": "A long prose sentence with no scripture reference"}],
            }}},
            {"role": "post", "content": {"latin": {"text": "Body.", "segments": [{"type": "text", "value": "Body."}]}}},
        ]
        result = R.antiphon_from_items(items)
        # Citation should be omitted (the "reference" was junk)
        assert "citation" not in result or "la" not in result.get("citation", {})

    def test_citation_cf_normalized(self):
        items = [
            {"role": "ant", "content": {"latin": {
                "segments": [{"type": "reference", "text": "cf. Mt 5, 1"}],  # lowercase
            }}},
            {"role": "post", "content": {"latin": {"text": "Body.", "segments": [{"type": "text", "value": "Body."}]}}},
        ]
        result = R.antiphon_from_items(items)
        assert result["citation"]["la"] == "Cf. Mt 5, 1"


# =============================================================================
# psalm_from_items
# =============================================================================

class TestPsalmFromItems:
    def test_extracts_citation_from_title(self):
        items = [{
            "role": "post",
            "content": {"latin": {
                "text": "Psalmus Responsorius Ps 50, 3-4. 5-6a. 12-13. R/. Miserere mei.",
                "segments": [
                    {"type": "reading_title", "text": "Psalmus Responsorius Ps 50, 3-4. 5-6a. 12-13"},
                    {"type": "rubric", "text": "R/."},
                    {"type": "psalm_verse", "text": "Miserere mei."},
                ],
            }},
        }]
        result = R.psalm_from_items(items)
        assert result.get("citation", {}).get("la") and "Ps 50" in result["citation"]["la"]

    def test_no_title_no_citation(self):
        items = [{
            "role": "post",
            "content": {"latin": {"text": "Just body.", "segments": [{"type": "text", "value": "Just body."}]}},
        }]
        result = R.psalm_from_items(items)
        if result:
            assert "citation" not in result or not result.get("citation")


# =============================================================================
# parse_temporal_day_id / parse_sanctorale_day_id
# =============================================================================

class TestParseTemporalDayId:
    def test_advent_sunday(self):
        # A010 = Advent week 1 Sunday
        result = R.parse_temporal_day_id("A010")
        assert result is not None

    def test_lent_thursday(self):
        # Q014 = Lent week 1 Thursday
        result = R.parse_temporal_day_id("Q014")
        assert result is not None

    def test_easter_sunday(self):
        # P010 = Easter Sunday
        result = R.parse_temporal_day_id("P010")
        assert result is not None


class TestParseSanctoraleDayId:
    def test_january_first(self):
        result = R.parse_sanctorale_day_id("0101")
        assert result is not None
        assert result.get("month") == 1
        assert result.get("day") == 1

    def test_december_thirtyfirst(self):
        result = R.parse_sanctorale_day_id("1231")
        assert result.get("month") == 12
        assert result.get("day") == 31

    def test_with_suffix(self):
        result = R.parse_sanctorale_day_id("0125Z")
        assert result.get("suffix") in ("Z", "z")
        assert result.get("month") == 1
        assert result.get("day") == 25

    def test_movable_code(self):
        # MM-NN where NN > 31 is a movable feast
        result = R.parse_sanctorale_day_id("0532")
        # Day > 31 → movable
        assert result.get("movableCode") or result is None


# =============================================================================
# label_only_preface_to_ref + _resolve_preface_label
# =============================================================================

class TestPrefaceLabelResolver:
    def test_normalize_pref_label(self):
        # Strips PRÆFATIO prefix and ligatures
        normalized = R._normalize_pref_label("PRÆFATIO I DE ADVENTU De duobus adventibus Christi")
        assert "advent" in normalized.lower() or "adventu" in normalized

    def test_normalize_lowercase(self):
        n1 = R._normalize_pref_label("PREFACE I OF ADVENT")
        n2 = R._normalize_pref_label("preface i of advent")
        # Should produce comparable normalized forms
        assert n1 == n2 or n1.lower() == n2.lower()


# =============================================================================
# _looks_like_rubric_only / _looks_like_header_leak / _looks_like_fragment
# =============================================================================

class TestLooksLikeRubric:
    def test_empty_text(self):
        assert R._looks_like_rubric_only("")
        assert R._looks_like_rubric_only("   ")

    def test_real_prayer_long_enough_not_rubric(self):
        text = "Concede, quaesumus, omnipotens Deus, famulis tuis hanc gratiam multiplicare et sanctificare."
        assert not R._looks_like_rubric_only(text)

    def test_short_per_starts_treated_as_fragment(self):
        # The "per" function-word-start heuristic catches short Latin doxology fragments
        assert R._looks_like_rubric_only("Per Christum.")


class TestLooksLikeHeaderLeak:
    def test_real_prayer_not_header(self):
        assert not R._looks_like_header_leak("Lord, hear our prayer.")
        assert not R._looks_like_header_leak("Per Christum Dóminum nostrum.")

    def test_empty_text(self):
        assert not R._looks_like_header_leak("")


class TestLooksLikeFragment:
    def test_real_sentence_not_fragment(self):
        # Long sentence, capitalized — not a fragment
        assert not R._looks_like_fragment("This is a complete sentence with many words.")

    def test_capitalized_short_text_not_fragment(self):
        # "Lord, hear our prayer" starts capitalized, not a function word
        assert not R._looks_like_fragment("Lord, hear our prayer.")

    def test_lowercase_function_word_start_is_fragment(self):
        # Match a function word from the regex: "per", "del", "che", "que", "et", "and"
        assert R._looks_like_fragment("et reliqua brevia verba")
        assert R._looks_like_fragment("and so on we go forward")

    def test_long_text_not_fragment_even_if_lowercase_start(self):
        # Length >= 80 disqualifies as fragment
        long_text = "et so on " * 20  # > 80 chars
        assert not R._looks_like_fragment(long_text)


# =============================================================================
# split_text_on_newlines
# =============================================================================

class TestSplitTextOnNewlines:
    def test_no_newlines(self):
        assert R.split_text_on_newlines("hello world") == ["hello world"]

    def test_single_newline(self):
        assert R.split_text_on_newlines("line one\nline two") == ["line one", "line two"]

    def test_multiple_newlines(self):
        result = R.split_text_on_newlines("a\n\n\nb")
        # Multiple \n+ collapse to one split
        assert "a" in result
        assert "b" in result

    def test_empty_string(self):
        assert R.split_text_on_newlines("") == [""]


# =============================================================================
# strip_html_inline
# =============================================================================

class TestStripHtmlInline:
    def test_no_html(self):
        assert R.strip_html_inline("plain text") == "plain text"

    def test_strips_tag(self):
        assert "<b>" not in R.strip_html_inline("<b>bold</b>")
        assert "bold" in R.strip_html_inline("<b>bold</b>")

    def test_handles_none(self):
        assert R.strip_html_inline(None) is None


# =============================================================================
# localized
# =============================================================================

class TestLocalized:
    def test_maps_source_to_iso(self):
        result = R.localized({"latin": "Pax", "cast": "Paz", "engl": "Peace"})
        assert result["la"] == "Pax"
        assert result["es"] == "Paz"
        assert result["en"] == "Peace"

    def test_skips_unknown_source(self):
        result = R.localized({"latin": "Pax", "xx": "ignored"})
        assert "la" in result
        assert "xx" not in result

    def test_skips_empty_text(self):
        result = R.localized({"latin": "", "engl": "Peace"})
        assert "la" not in result
        assert result["en"] == "Peace"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
