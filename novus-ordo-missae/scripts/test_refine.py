"""Unit tests for refine.py text-cleaning + structural transforms.

Run from this directory:  pytest test_refine.py -q
or:  /tmp/missal_venv2/bin/python -m pytest test_refine.py -q
"""

import pathlib
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import refine as R


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_passthrough_normal(self):
        assert R.clean_text("Hello world.") == "Hello world."

    def test_collapses_whitespace(self):
        assert R.clean_text("Hello   world\n\nfoo") == "Hello world foo"

    def test_strips_nbsp(self):
        assert R.clean_text("a\xa0b c") == "a b c"

    def test_empty_input(self):
        assert R.clean_text("") == ""
        assert R.clean_text(None) == ""

    def test_strips_html_tags(self):
        assert R.clean_text("<p>Hello</p>") == "Hello"

    def test_strips_stray_lt(self):
        # Source authoring typo: <h2>Title<</h2> leaves a bare `<`
        assert R.clean_text("Bienheureuse Vierge<") == "Bienheureuse Vierge"

    def test_doubled_punct_collapsed(self):
        # `,,` and `::` get collapsed; `..` is preserved (could be ellipsis)
        assert R.clean_text("Hello,, world.") == "Hello, world."
        assert R.clean_text("Wait::: now") == "Wait: now"
        assert R.clean_text("a!! b") == "a! b"
        assert R.clean_text("a?? b") == "a? b"

    def test_strips_single_leading_period_with_alpha_following(self):
        # ".Heute" → "Heute" (single dot before alpha)
        assert R.clean_text(".Heute") == "Heute"
        assert R.clean_text(",Mass") == "Mass"

    def test_does_not_strip_when_double_dot_at_start(self):
        # ".." starts with dot then dot, second char isn't alpha → no strip
        assert R.clean_text("..Heute") == "..Heute"

    def test_placeholder_only_returns_empty(self):
        assert R.clean_text("...") == ""
        assert R.clean_text("…") == ""
        assert R.clean_text("• ⋯") == ""

    def test_lt_gt_artifact_stripped(self):
        # `<` and `>` artifacts get stripped via _HTML_TAG_RE replace.
        # `p>` (no leading `<`) is harder — only stripped when it appears
        # after whitespace surrounded by < or > chars; here it stays mid-text.
        assert R.clean_text("Text. < More") == "Text. More"
        assert R.clean_text("foo > bar") == "foo bar"

    def test_strips_leading_prayer_label(self):
        assert R.clean_text("Prayer over the Offerings Lord, we offer...") == "Lord, we offer..."
        assert R.clean_text("Prière sur les offrandes En cette fête...") == "En cette fête..."
        assert R.clean_text("Oración colecta Oh, Dios...") == "Oh, Dios..."
        assert R.clean_text("Dopo la comunione Nutriti...") == "Nutriti..."

    def test_doesnt_strip_label_when_only_label(self):
        # Don't drop a body that's just the label (no body text after)
        assert R.clean_text("Prayer over the Offerings") == "Prayer over the Offerings"

    def test_placeholder_after_label_strip(self):
        # "Prière sur les offrandes ..." → strip label, leaves "...", drop as placeholder
        assert R.clean_text("Prière sur les offrandes ...") == ""


# ---------------------------------------------------------------------------
# strip_trailing_rubric
# ---------------------------------------------------------------------------

class TestStripTrailingRubric:
    def test_no_op_for_short(self):
        assert R.strip_trailing_rubric("Short") == "Short"

    def test_or_does_not_match_inside_word(self):
        # The "Or:" rubric phrase must not match inside "Senhor:" — that's
        # what was eating Portuguese text.
        text = ("Ao apresentar-vos os dons da vossa generosidade, nós vos pedimos, "
                "Senhor: assim como destes ao Cristo, obediente até a morte, um nome "
                "que traz a salvação, concedei-nos a proteção de sua força. Por Cristo, nosso Senhor.")
        result = R.strip_trailing_rubric(text)
        assert "Senhor: assim" in result
        assert result.endswith("nosso Senhor.")

    def test_solennita_does_not_match_inside_phrase(self):
        # "solennità" inside "nella solennità" is legitimate content.
        text = ("Accetta con benevolenza, o Signore, il sacrificio di salvezza che ti "
                "offriamo nella solennità dell'Immacolata Concezione della beata Vergine Maria, "
                "e come noi la riconosciamo preservata per tua grazia da ogni macchia di peccato, "
                "così, per sua intercessione, fa' che siamo liberati da ogni colpa. Per Cristo nostro Signore.")
        result = R.strip_trailing_rubric(text)
        assert "solennità dell'Immacolata" in result
        assert result.endswith("Per Cristo nostro Signore.")

    def test_strips_section_marker_after_period(self):
        # Italian "Tempo Pasquale" leaking after a sentence end SHOULD strip.
        # Body must be >=60 chars total for strip_trailing_rubric to engage.
        text = ("Concedi a tutti noi la gioia eterna senza fine, e ricolma il nostro spirito "
                "di un'eccezionale serenità che permanga ogni giorno. Per Cristo. Tempo Pasquale")
        result = R.strip_trailing_rubric(text)
        assert "Tempo Pasquale" not in result

    def test_no_op_below_60_chars(self):
        # Short bodies don't get rubric-stripped (most short ones are antiphons).
        text = "Some prayer body. Tempo Pasquale"  # < 60 chars
        result = R.strip_trailing_rubric(text)
        assert result == text  # unchanged


# ---------------------------------------------------------------------------
# _is_html_junk
# ---------------------------------------------------------------------------

class TestIsHtmlJunk:
    def test_real_text_is_not_junk(self):
        assert not R._is_html_junk("Hello world")
        assert not R._is_html_junk("Per Christum")

    def test_torn_p_tag_is_junk(self):
        assert R._is_html_junk("p>")
        assert R._is_html_junk("<p>")
        assert R._is_html_junk("</p>")
        assert R._is_html_junk("<p")

    def test_torn_span_div_is_junk(self):
        assert R._is_html_junk("span>")
        assert R._is_html_junk("</div>")

    def test_lt_gt_alone_is_junk(self):
        assert R._is_html_junk("<")
        assert R._is_html_junk(">")
        assert R._is_html_junk("</>")

    def test_long_text_with_brackets_is_not_junk(self):
        assert not R._is_html_junk("This is a long text with > a sign in it")


# ---------------------------------------------------------------------------
# _drop_latin_leak
# ---------------------------------------------------------------------------

class TestDropLatinLeak:
    def test_drops_vernacular_equal_to_latin(self):
        plain = {"la": "Apériens autem Petrus os dixit verba longa et significantia",
                 "fr": "Apériens autem Petrus os dixit verba longa et significantia"}
        lines = {"la": [["X"]], "fr": [["X"]]}
        R._drop_latin_leak(plain, lines)
        assert "fr" not in plain
        assert "fr" not in lines

    def test_keeps_real_translation(self):
        plain = {"la": "Apériens autem Petrus os dixit",
                 "fr": "Pierre prit la parole et dit ces choses-là"}
        lines = {"la": [], "fr": []}
        R._drop_latin_leak(plain, lines)
        assert "fr" in plain
        assert plain["fr"] == "Pierre prit la parole et dit ces choses-là"

    def test_skips_short_la(self):
        # If Latin is < 30 chars, do not check (short antiphons may legitimately match).
        plain = {"la": "Amen.", "fr": "Amen."}
        lines = {"la": [], "fr": []}
        R._drop_latin_leak(plain, lines)
        assert "fr" in plain  # NOT dropped because la is short

    def test_case_insensitive_match(self):
        plain = {"la": "Apériens autem Petrus os dixit verba longa et significantia",
                 "fr": "APÉRIENS AUTEM PETRUS OS DIXIT VERBA LONGA ET SIGNIFICANTIA"}
        lines = {}
        R._drop_latin_leak(plain, lines)
        assert "fr" not in plain


# ---------------------------------------------------------------------------
# _merge_mid_sentence_lines
# ---------------------------------------------------------------------------

class TestMergeMidSentenceLines:
    def test_merges_when_prev_no_terminator(self):
        # The user-flagged Sapientiam case: "nómina" has no terminator,
        # next line should merge.
        lines = [
            [{"type": "text", "text": "Sapiéntiam Sanctórum narrent pópuli,"}],
            [{"type": "text", "text": "et laudes eórum núntiet Ecclésia;"}],
            [{"type": "text", "text": "nómina"}],
            [{"type": "text", "text": "autem eórum vivent in sǽculum sǽculi."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 3
        last = result[-1][0]["text"]
        assert "nómina autem" in last
        assert last.endswith("sǽculi.")

    def test_keeps_comma_break(self):
        # Comma at line end is a legitimate poetic-chant break; do not merge.
        lines = [
            [{"type": "text", "text": "Hello world,"}],
            [{"type": "text", "text": "another line."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 2

    def test_keeps_semicolon_break(self):
        lines = [
            [{"type": "text", "text": "Hello world;"}],
            [{"type": "text", "text": "another line."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 2

    def test_keeps_period_break(self):
        lines = [
            [{"type": "text", "text": "Hello world."}],
            [{"type": "text", "text": "Another sentence."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 2

    def test_does_not_merge_into_rubric_line(self):
        # If the next line starts with a rubric segment, don't merge — the
        # break is intentional structure.
        lines = [
            [{"type": "text", "text": "no terminator"}],
            [{"type": "rubric", "text": "R/."}, {"type": "text", "text": "Amen."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 2

    def test_preserves_single_line(self):
        lines = [[{"type": "text", "text": "Just one line"}]]
        assert R._merge_mid_sentence_lines(lines) == lines

    def test_handles_empty(self):
        assert R._merge_mid_sentence_lines([]) == []


# ---------------------------------------------------------------------------
# normalize_rank
# ---------------------------------------------------------------------------

class TestNormalizeRank:
    def test_solemnity_keywords(self):
        assert R.normalize_rank({"la": "Sollemnitas"}) == "solemnity"
        assert R.normalize_rank({"es": "Solemnidad"}) == "solemnity"
        assert R.normalize_rank({"en": "Solemnity"}) == "solemnity"
        assert R.normalize_rank({"de": "Hochfest"}) == "solemnity"

    def test_feast_keywords(self):
        assert R.normalize_rank({"la": "Festum"}) == "feast"
        assert R.normalize_rank({"es": "Fiesta"}) == "feast"
        assert R.normalize_rank({"en": "Feast"}) == "feast"
        assert R.normalize_rank({"de": "Fest"}) == "feast"

    def test_memorial_keywords(self):
        assert R.normalize_rank({"la": "Memoria"}) == "memorial"
        assert R.normalize_rank({"de": "Gedenktag"}) == "memorial"

    def test_optional_memorial_priority(self):
        # "optional-memorial" is more specific than "memorial" — must match first
        assert R.normalize_rank({"en": "Optional Memorial"}) == "optional-memorial"

    def test_unknown_returns_none(self):
        assert R.normalize_rank({"en": "At Mass during the Day"}) is None
        assert R.normalize_rank({"en": "Vigil Mass"}) is None


# ---------------------------------------------------------------------------
# _strip_leading_prayer_label (already covered via clean_text)
# ---------------------------------------------------------------------------

class TestStripLeadingPrayerLabel:
    def test_strips_english(self):
        assert R._strip_leading_prayer_label("Prayer over the Offerings Lord, we...") == "Lord, we..."
        assert R._strip_leading_prayer_label("Prayer after Communion God of...") == "God of..."

    def test_strips_french(self):
        assert R._strip_leading_prayer_label("Prière sur les offrandes Seigneur,...") == "Seigneur,..."

    def test_strips_german(self):
        assert R._strip_leading_prayer_label("Schlussgebet Wir haben...") == "Wir haben..."

    def test_no_strip_when_no_label(self):
        assert R._strip_leading_prayer_label("Lord, hear our prayer.") == "Lord, hear our prayer."

    def test_no_strip_when_label_only(self):
        # No content after label → leave intact (would over-strip)
        assert R._strip_leading_prayer_label("Prayer over the Offerings") == "Prayer over the Offerings"


# ---------------------------------------------------------------------------
# _build_lines_from_div
# ---------------------------------------------------------------------------

class TestBuildLinesFromDiv:
    def _div(self, html):
        return BeautifulSoup(html, "lxml").find()

    def test_simple_text(self):
        div = self._div("<div>Hello world.</div>")
        lines = R._build_lines_from_div(div)
        assert lines == [[{"type": "text", "text": "Hello world."}]]

    def test_br_splits_lines(self):
        div = self._div("<div>line one<br/>line two</div>")
        lines = R._build_lines_from_div(div)
        assert len(lines) == 2
        assert lines[0][0]["text"] == "line one"
        assert lines[1][0]["text"] == "line two"

    def test_p_creates_line_boundary(self):
        div = self._div("<div><p>para one.</p><p>para two.</p></div>")
        lines = R._build_lines_from_div(div)
        assert len(lines) == 2

    def test_red_span_becomes_rubric(self):
        div = self._div('<div>Body text. <span class="red">R/. Amen.</span></div>')
        lines = R._build_lines_from_div(div)
        # Expect text + rubric on same line
        flat = [s for line in lines for s in line]
        assert any(s["type"] == "rubric" and "Amen" in s["text"] for s in flat)

    def test_alindcha_span_becomes_reference(self):
        div = self._div('<div>Some text <span class="alindcha">Mt 5, 1-12</span></div>')
        lines = R._build_lines_from_div(div)
        flat = [s for line in lines for s in line]
        assert any(s["type"] == "reference" and "Mt 5, 1-12" in s["text"] for s in flat)

    def test_drops_html_junk(self):
        div = self._div("<div>Real text <p>p&gt;</p> more text</div>")
        lines = R._build_lines_from_div(div)
        # The `p>` from `&gt;` should be filtered
        flat = [s for line in lines for s in line]
        assert not any(s["text"].strip() == "p>" for s in flat)

    def test_skips_headings(self):
        div = self._div("<div><h2>Heading</h2><p>Body content here.</p></div>")
        lines = R._build_lines_from_div(div)
        flat = [s for line in lines for s in line]
        assert not any("Heading" in s["text"] for s in flat)


# ---------------------------------------------------------------------------
# _extract_citation_and_strip_from_text (psalm/alleluia title parsing)
# ---------------------------------------------------------------------------

class TestExtractCitationFromTitle:
    def test_psalm_la(self):
        cit, rest = R._extract_citation_and_strip_from_text(
            "Psalmus Responsorius Ps 50, 3-4. 5-6a. 12-13. 14 et 17 (: cf. 3a)", "la"
        )
        assert cit and "Ps 50" in cit

    def test_psalm_es(self):
        cit, rest = R._extract_citation_and_strip_from_text(
            "Salmo Responsorial Sal 95, 1-2. 4-5", "es"
        )
        assert cit and "Sal 95" in cit

    def test_psalm_en(self):
        cit, rest = R._extract_citation_and_strip_from_text(
            "Responsorial Psalm Ps 23", "en"
        )
        assert cit and "Ps 23" in cit

    def test_alleluia(self):
        cit, rest = R._extract_citation_and_strip_from_text(
            "Alleluia, Versus ad Evangelium Mt 4, 4b", "la"
        )
        assert cit and "Mt 4" in cit

    def test_no_title_prefix_returns_none(self):
        cit, rest = R._extract_citation_and_strip_from_text(
            "Random text without title prefix", "en"
        )
        assert cit is None


# ---------------------------------------------------------------------------
# _LEADING_LABEL_RUBRIC_RE (used by _plain_from_lines for antiphon preamble)
# ---------------------------------------------------------------------------

class TestLeadingLabelRubric:
    def test_matches_antiphona_n(self):
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antiphona 1")
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antífona 2")
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antiphon 3")
        assert R._LEADING_LABEL_RUBRIC_RE.match("ANTIFONA 4")
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antienne 5")

    def test_matches_canto_n(self):
        assert R._LEADING_LABEL_RUBRIC_RE.match("1º Canto")
        assert R._LEADING_LABEL_RUBRIC_RE.match("2° canto")

    def test_does_not_match_full_sentence(self):
        assert not R._LEADING_LABEL_RUBRIC_RE.match("Antiphona prima est de pace")
        assert not R._LEADING_LABEL_RUBRIC_RE.match("Hello world")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
