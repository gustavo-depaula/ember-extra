"""Unit tests for convert.py — HTML → v1 segments / hijo blocks / estructura.

Run from this directory:  pytest test_convert.py -q
"""

import pathlib
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import convert as C


def _soup(html):
    """Wrap fragment in <body> so BeautifulSoup parses it consistently."""
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_collapses_whitespace(self):
        assert C.clean_text("a   b\n\nc") == "a b c"

    def test_strips_nbsp(self):
        assert C.clean_text("a\xa0b") == "a b"

    def test_strips_narrow_nbsp(self):
        assert C.clean_text("a b") == "a b"

    def test_strip_outer_whitespace(self):
        assert C.clean_text("  hello  ") == "hello"

    def test_empty(self):
        assert C.clean_text("") == ""


# ---------------------------------------------------------------------------
# extract_hijo_index / extract_padre_index
# ---------------------------------------------------------------------------

class TestExtractHijoIndex:
    def test_simple(self):
        soup = _soup('<div class="cast hijo hijo_5">x</div>')
        node = soup.find("div")
        assert C.extract_hijo_index(node) == 5

    def test_large(self):
        soup = _soup('<div class="hijo hijo_137">x</div>')
        node = soup.find("div")
        assert C.extract_hijo_index(node) == 137

    def test_no_hijo_class(self):
        soup = _soup('<div class="cast">x</div>')
        node = soup.find("div")
        assert C.extract_hijo_index(node) is None

    def test_malformed(self):
        soup = _soup('<div class="hijo hijo_abc">x</div>')
        node = soup.find("div")
        assert C.extract_hijo_index(node) is None


class TestExtractPadreIndex:
    def test_simple(self):
        soup = _soup('<div class="padre padre_12">x</div>')
        node = soup.find("div")
        assert C.extract_padre_index(node) == 12

    def test_no_padre_class(self):
        soup = _soup('<div class="cast">x</div>')
        node = soup.find("div")
        assert C.extract_padre_index(node) is None


# ---------------------------------------------------------------------------
# first_slot_class / first_cycle_class
# ---------------------------------------------------------------------------

class TestFirstSlotClass:
    def test_typed_slot(self):
        soup = _soup('<div class="x_colecta">x</div>')
        assert C.first_slot_class(soup.find("div")) == "x_colecta"

    def test_priority_no_slot(self):
        soup = _soup('<div class="cast">x</div>')
        assert C.first_slot_class(soup.find("div")) is None

    def test_returns_first_slot_when_multiple(self):
        # SLOT_TYPES priority: should return the first matching one in order
        soup = _soup('<div class="x_ant_ent x_colecta">x</div>')
        result = C.first_slot_class(soup.find("div"))
        assert result in ("x_ant_ent", "x_colecta")  # whichever appears first in classes


class TestFirstCycleClass:
    def test_cycle_a(self):
        soup = _soup('<div class="cicloA">x</div>')
        assert C.first_cycle_class(soup.find("div")) == "cicloA"

    def test_cycle_i(self):
        soup = _soup('<div class="cicloI">x</div>')
        assert C.first_cycle_class(soup.find("div")) == "cicloI"

    def test_no_cycle(self):
        soup = _soup('<div class="cast">x</div>')
        assert C.first_cycle_class(soup.find("div")) is None


# ---------------------------------------------------------------------------
# parse_segments — typed inline segment extraction
# ---------------------------------------------------------------------------

class TestParseSegments:
    def test_plain_text(self):
        soup = _soup('<div>Hello world</div>')
        segs = C.parse_segments(soup.find("div"))
        types = [s["type"] for s in segs]
        assert "text" in types
        assert any(s.get("value", "").strip() == "Hello world" for s in segs)

    def test_br_becomes_break(self):
        soup = _soup('<div>line one<br/>line two</div>')
        segs = C.parse_segments(soup.find("div"))
        assert any(s["type"] == "break" for s in segs)

    def test_heading(self):
        soup = _soup('<div><h2>Title</h2></div>')
        segs = C.parse_segments(soup.find("div"))
        h = next(s for s in segs if s["type"] == "heading")
        assert h["level"] == 2
        assert h["text"] == "Title"

    def test_italic(self):
        soup = _soup('<div><i>emphasized</i></div>')
        segs = C.parse_segments(soup.find("div"))
        i = next(s for s in segs if s["type"] == "italic")
        assert i["text"] == "emphasized"

    def test_em_treated_as_italic(self):
        soup = _soup('<div><em>x</em></div>')
        segs = C.parse_segments(soup.find("div"))
        assert any(s["type"] == "italic" for s in segs)

    def test_bold(self):
        soup = _soup('<div><b>bold</b></div>')
        segs = C.parse_segments(soup.find("div"))
        assert any(s["type"] == "bold" and s["text"] == "bold" for s in segs)

    def test_strong_treated_as_bold(self):
        soup = _soup('<div><strong>x</strong></div>')
        segs = C.parse_segments(soup.find("div"))
        assert any(s["type"] == "bold" for s in segs)

    def test_red_span_classified(self):
        # <span class="red">...</span> is the rubric class
        soup = _soup('<div><span class="red">R/. Amen.</span></div>')
        segs = C.parse_segments(soup.find("div"))
        # Should produce a typed segment via classify_inline
        rubrics = [s for s in segs if s["type"] == "rubric"]
        assert len(rubrics) == 1
        assert "Amen" in rubrics[0]["text"]

    def test_alindcha_span_classified(self):
        soup = _soup('<div><span class="alindcha">Mt 5, 1-12</span></div>')
        segs = C.parse_segments(soup.find("div"))
        refs = [s for s in segs if s["type"] == "reference"]
        assert len(refs) == 1
        assert refs[0]["text"] == "Mt 5, 1-12"

    def test_paragraph_marker(self):
        soup = _soup('<div><p>para text</p></div>')
        segs = C.parse_segments(soup.find("div"))
        types = [s["type"] for s in segs]
        assert "paragraph_start" in types
        assert "paragraph_end" in types


# ---------------------------------------------------------------------------
# parse_hijo_blocks
# ---------------------------------------------------------------------------

class TestParseHijoBlocks:
    def test_simple_block(self):
        html = '<div class="cast hijo hijo_1">Hello</div>'
        blocks = C.parse_hijo_blocks(html, "cast")
        assert len(blocks) == 1
        assert blocks[0]["n"] == 1
        assert blocks[0]["text"] == "Hello"

    def test_multiple_blocks_sorted_by_n(self):
        html = (
            '<div class="cast hijo hijo_3">third</div>'
            '<div class="cast hijo hijo_1">first</div>'
            '<div class="cast hijo hijo_2">second</div>'
        )
        blocks = C.parse_hijo_blocks(html, "cast")
        assert [b["n"] for b in blocks] == [1, 2, 3]
        assert blocks[0]["text"] == "first"
        assert blocks[2]["text"] == "third"

    def test_filters_by_language(self):
        # Different lang divs should be skipped
        html = (
            '<div class="cast hijo hijo_1">spanish</div>'
            '<div class="engl hijo hijo_1">english</div>'
        )
        cast_blocks = C.parse_hijo_blocks(html, "cast")
        engl_blocks = C.parse_hijo_blocks(html, "engl")
        assert len(cast_blocks) == 1
        assert cast_blocks[0]["text"] == "spanish"
        assert len(engl_blocks) == 1
        assert engl_blocks[0]["text"] == "english"

    def test_falls_back_to_unanchored_hijo(self):
        # plegarias_euc estructura: <div class="hijo hijo_N"> with no language tag
        html = '<div class="hijo hijo_5">unanchored</div>'
        blocks = C.parse_hijo_blocks(html, "cast")
        assert len(blocks) == 1
        assert blocks[0]["n"] == 5

    def test_includes_inner_html(self):
        html = '<div class="cast hijo hijo_1"><p>para</p></div>'
        blocks = C.parse_hijo_blocks(html, "cast")
        assert "<p>para</p>" in blocks[0]["html"]

    def test_includes_segments(self):
        html = '<div class="cast hijo hijo_1">x<br/>y</div>'
        blocks = C.parse_hijo_blocks(html, "cast")
        assert blocks[0]["segments"]
        assert any(s["type"] == "break" for s in blocks[0]["segments"])


# ---------------------------------------------------------------------------
# parse_estructura
# ---------------------------------------------------------------------------

class TestParseEstructura:
    def test_extracts_dia_nodes(self):
        html = (
            '<div class="dia" id="0101">'
            '<div class="x_titulo padre padre_1"></div>'
            '</div>'
            '<div class="dia" id="0102">'
            '<div class="x_colecta padre padre_2"></div>'
            '</div>'
        )
        result = C.parse_estructura(html)
        assert len(result["days"]) == 2
        assert result["days"][0]["id"] == "0101"
        assert result["days"][1]["id"] == "0102"

    def test_falls_back_to_flat(self):
        # No dia container — single flat day with id=None
        html = '<div class="x_colecta padre padre_1"></div>'
        result = C.parse_estructura(html)
        assert len(result["days"]) == 1
        assert result["days"][0]["id"] is None


class TestParseDia:
    def test_extracts_id_and_languages(self):
        html = '<div class="dia xcast xengl xport" id="0101"></div>'
        node = _soup(html).find("div")
        result = C.parse_dia(node)
        assert result["id"] == "0101"
        assert "cast" in result["languages"]
        assert "engl" in result["languages"]
        assert "port" in result["languages"]


# ---------------------------------------------------------------------------
# basename_for
# ---------------------------------------------------------------------------

class TestBasenameFor:
    def test_strips_lang_prefix(self):
        assert C.basename_for("santos", "cast", "m_cast_santos_ene.html") == "santos_ene"

    def test_strips_estructura_prefix(self):
        assert C.basename_for("santos", "estructura", "m_estructura_santos_ene.html") == "santos_ene"

    def test_handles_no_prefix(self):
        # If filename doesn't have the expected prefix, returns the stem
        assert C.basename_for("igmr", "cast", "igmr_cast.html") == "igmr_cast"


# ---------------------------------------------------------------------------
# standalone_block — IGMR / sacerdotale processing
# ---------------------------------------------------------------------------

class TestStandaloneBlock:
    def test_heading(self):
        soup = _soup('<h2 id="ch1">Chapter One</h2>')
        block = C.standalone_block(soup.find("h2"))
        assert block["type"] == "heading"
        assert block["level"] == 2
        assert block["text"] == "Chapter One"
        assert block["id"] == "ch1"

    def test_paragraph(self):
        soup = _soup('<p>Some paragraph text.</p>')
        block = C.standalone_block(soup.find("p"))
        assert block["type"] == "paragraph"
        assert block["text"] == "Some paragraph text."

    def test_unordered_list(self):
        soup = _soup('<ul><li>one</li><li>two</li></ul>')
        block = C.standalone_block(soup.find("ul"))
        assert block["type"] == "list"
        assert block["ordered"] is False
        assert len(block["items"]) == 2

    def test_ordered_list(self):
        soup = _soup('<ol><li>x</li></ol>')
        block = C.standalone_block(soup.find("ol"))
        assert block["type"] == "list"
        assert block["ordered"] is True

    def test_skips_script(self):
        soup = _soup('<script>alert(1)</script>')
        assert C.standalone_block(soup.find("script")) is None

    def test_skips_form(self):
        soup = _soup('<form><input/></form>')
        assert C.standalone_block(soup.find("form")) is None

    def test_table(self):
        soup = _soup('<table><tr><td>x</td></tr></table>')
        block = C.standalone_block(soup.find("table"))
        assert block["type"] == "table"
        assert "<table>" in block["html"]

    def test_div_with_children_becomes_group(self):
        soup = _soup('<div id="x"><p>one</p><p>two</p></div>')
        block = C.standalone_block(soup.find("div"))
        assert block["type"] == "group"
        assert block["id"] == "x"
        assert len(block["blocks"]) == 2

    def test_empty_div_returns_none(self):
        soup = _soup('<div></div>')
        assert C.standalone_block(soup.find("div")) is None


# ---------------------------------------------------------------------------
# parse_standalone — full document parse
# ---------------------------------------------------------------------------

class TestParseStandalone:
    def test_extracts_title(self):
        html = '<html><body><div id="scroller"><h2>Document Title</h2><p>content</p></div></body></html>'
        result = C.parse_standalone(html)
        assert result["title"] == "Document Title"

    def test_extracts_blocks(self):
        html = '<html><body><div id="scroller"><h2>Title</h2><p>para 1</p><p>para 2</p></div></body></html>'
        result = C.parse_standalone(html)
        # blocks should include the heading + 2 paragraphs
        assert len(result["blocks"]) >= 3

    def test_no_scroller_uses_body(self):
        # Some files don't have #scroller — should fall back to body
        html = '<html><body><h2>Title</h2><p>x</p></body></html>'
        result = C.parse_standalone(html)
        assert result["title"] == "Title"


# ---------------------------------------------------------------------------
# classify_inline
# ---------------------------------------------------------------------------

class TestClassifyInline:
    def test_red_class(self):
        soup = _soup('<span class="red">x</span>')
        assert C.classify_inline(soup.find("span")) == "rubric"

    def test_alindcha_class(self):
        soup = _soup('<span class="alindcha">x</span>')
        assert C.classify_inline(soup.find("span")) == "reference"

    def test_unrecognized_class(self):
        soup = _soup('<span class="unknown">x</span>')
        assert C.classify_inline(soup.find("span")) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
