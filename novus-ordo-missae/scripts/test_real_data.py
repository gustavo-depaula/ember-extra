"""End-to-end tests with REAL strings from the Missale Romanum source data.

These tests pin down the actual behavior of the converter on real inputs that
have caused bugs in the past — each test references a specific defect that
was fixed during the content-quality pass.
"""

import pathlib
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import refine as R
import convert as C


# =============================================================================
# refine.clean_text — real prayer-body strings
# =============================================================================

class TestRealPrayerBodies:
    """Real prayer texts from the Missale that triggered specific bugs."""

    def test_portuguese_senhor_not_truncated(self):
        """Bug: 'Or:' rubric matched inside 'Senhor:' — truncated 'Senhor' to 'Senh.'"""
        text = ("Ao apresentar-vos os dons da vossa generosidade, nós vos pedimos, "
                "Senhor: assim como destes ao Cristo, obediente até a morte, um nome "
                "que traz a salvação, concedei-nos a proteção de sua força. "
                "Por Cristo, nosso Senhor.")
        result = R.clean_text(text)
        assert "Senhor:" in result, "Expected Senhor: preserved"
        assert result.endswith("nosso Senhor.")

    def test_italian_solennita_not_truncated(self):
        """Bug: 'Solennità' rubric matched inside 'nella solennità' — truncated."""
        text = ("Accetta con benevolenza, o Signore, il sacrificio di salvezza che ti "
                "offriamo nella solennità dell'Immacolata Concezione della beata "
                "Vergine Maria, e come noi la riconosciamo preservata per tua grazia "
                "da ogni macchia di peccato, così, per sua intercessione, fa' che "
                "siamo liberati da ogni colpa. Per Cristo nostro Signore.")
        result = R.clean_text(text)
        assert "solennità dell'Immacolata" in result
        assert result.endswith("Per Cristo nostro Signore.")

    def test_legitimate_or_stripping_after_period(self):
        """Should strip alternate-script 'Oppure:' marker when it follows a period."""
        text = ("Salta de gozo, hija de Sion; canta, hija de Jerusalén; mira que viene "
                "tu rey, santo y salvador del mundo. Oppure: Lc 2, 20")
        result = R.clean_text(text)
        # We expect "Oppure:" stripped when preceded by sentence-end + space
        assert "Oppure:" not in result

    def test_palm_sunday_communion_antiphon_preserved(self):
        """Real Palm Sunday antiphon — must not be cut by trailing-rubric scanner."""
        text = ("Pater, si non potest hic calix transíre nisi bibam illum, "
                "fiat volúntas tua.")
        result = R.clean_text(text)
        assert result == text


class TestRegionalProperLeaks:
    """Bug: region-specific prayers had section-label prefixes leaking into body."""

    def test_english_prayer_over_offerings_label_stripped(self):
        text = "Prayer over the Offerings Lord, we offer you this sacrifice of praise as we rejoice in this memorial of the mother of your Son, grant that through the help of so great a mother we may find you coming to our rescue in every trial. Through Christ our Lord."
        result = R.clean_text(text)
        assert result.startswith("Lord, we offer")

    def test_french_priere_sur_offrandes_stripped(self):
        text = "Prière sur les offrandes En cette fête de saint Rémi, Seigneur, nous t'en prions, regarde avec bonté les offrandes que nous présentons. Par le Christ, notre Seigneur."
        result = R.clean_text(text)
        assert result.startswith("En cette fête")

    def test_spanish_oracion_colecta_stripped(self):
        text = "Oración colecta Oh, Dios, que suscitaste en tu Iglesia a san Hermenegildo, mártir, como valiente defensor de la fe, concédenos vivir intrépidos en la confesión de tu Nombre."
        result = R.clean_text(text)
        assert result.startswith("Oh, Dios")

    def test_german_schlussgebet_stripped(self):
        text = "Schlussgebet Wir danken dir, allmächtiger Gott, für das heilige Sakrament."
        result = R.clean_text(text)
        assert result.startswith("Wir danken dir")

    def test_italian_dopo_la_comunione_stripped(self):
        text = "Dopo la comunione Nutriti dai santi doni, o Signore, umilmente ti preghiamo."
        result = R.clean_text(text)
        assert result.startswith("Nutriti dai santi")

    def test_label_only_no_strip(self):
        # Bare label with no body — must not produce an empty string
        assert R.clean_text("Prayer over the Offerings") == "Prayer over the Offerings"


class TestPlaceholderContent:
    """Bug: 'Prière sur les offrandes ...' (placeholder) became '...' after strip."""

    def test_placeholder_after_strip_returns_empty(self):
        # `_PLACEHOLDER_TEXT_RE` is checked AFTER label-strip
        assert R.clean_text("Prière sur les offrandes ...") == ""
        assert R.clean_text("Schlussgebet …") == ""
        assert R.clean_text("Collecte ...") == ""

    def test_dots_only_returns_empty(self):
        assert R.clean_text("...") == ""
        assert R.clean_text("…") == ""
        assert R.clean_text("   ...   ") == ""
        assert R.clean_text("•⋯") == ""


class TestHtmlArtifacts:
    """Bug: source HTML had `&gt;` / `&lt;` outside tags producing stray `p>` `<` text."""

    def test_stray_lt_before_h2_close(self):
        # Source had `<h2>Title<</h2>` — `<` survived because it's not a real tag
        assert R.clean_text("Bienheureuse Vierge Marie Reine<") == "Bienheureuse Vierge Marie Reine"
        assert R.clean_text("19. TOUS LES SAINTS<") == "19. TOUS LES SAINTS"

    def test_stray_gt_in_text(self):
        assert R.clean_text("foo > bar") == "foo bar"

    def test_html_tags_still_stripped(self):
        assert R.clean_text("<p>text</p>") == "text"
        assert R.clean_text('<span class="red">rubric</span>') == "rubric"


# =============================================================================
# refine._merge_mid_sentence_lines — real liturgical poetry
# =============================================================================

class TestRealAntiphonLines:
    def test_sapientiam_antiphon_real_case(self):
        """User-flagged bug: 4 lines split mid-sentence on 'nómina'."""
        lines = [
            [{"type": "text", "text": "Sapiéntiam Sanctórum narrent pópuli,"}],
            [{"type": "text", "text": "et laudes eórum núntiet Ecclésia;"}],
            [{"type": "text", "text": "nómina"}],
            [{"type": "text", "text": "autem eórum vivent in sǽculum sǽculi."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 3
        assert result[0][0]["text"].endswith(",")
        assert result[1][0]["text"].endswith(";")
        assert result[2][0]["text"] == "nómina autem eórum vivent in sǽculum sǽculi."

    def test_chant_breaks_with_comma_preserved(self):
        """Comma at line-end is a legitimate poetic chant break."""
        lines = [
            [{"type": "text", "text": "Beátus vir,"}],
            [{"type": "text", "text": "qui non ábiit in consílio impiórum,"}],
            [{"type": "text", "text": "et in via peccatórum non stetit,"}],
            [{"type": "text", "text": "et in cáthedra pestiléntiæ non sedit."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 4  # all preserved

    def test_response_after_text_not_merged(self):
        """Previous line ends without terminator but next starts with rubric — don't merge."""
        lines = [
            [{"type": "text", "text": "Domine"}],
            [{"type": "rubric", "text": "R/."}, {"type": "text", "text": "Audi nos."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 2

    def test_dropcap_after_text_not_disturbed(self):
        """A line with text followed by a line starting with dropCap shouldn't merge weirdly."""
        lines = [
            [{"type": "text", "text": "First sentence."}],
            [{"type": "dropCap", "text": "G"}, {"type": "text", "text": "ratia plena."}],
        ]
        result = R._merge_mid_sentence_lines(lines)
        assert len(result) == 2


# =============================================================================
# refine._is_html_junk — real artifacts from authoring typos
# =============================================================================

class TestHtmlJunkRealCases:
    def test_real_p_artifact_from_corpus_christi(self):
        # Real: source had `&gt;` after Lauda Sion stanza, parsed as `p>`
        assert R._is_html_junk("p>")
        assert R._is_html_junk("<")  # Real: source had `&lt;` between stanzas

    def test_real_text_with_gt_not_junk(self):
        assert not R._is_html_junk("foo > bar")
        assert not R._is_html_junk("Real text content")


# =============================================================================
# refine._extract_citation_and_strip_from_text — real psalm titles
# =============================================================================

class TestRealPsalmCitations:
    """Real psalm-title strings from the Missale source."""

    def test_latin_with_et_connector(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Psalmus Responsorius Ps 89, 3-4. 5-6. 12-13. 14 et 17", "la"
        )
        assert cit == "Ps 89, 3-4. 5-6. 12-13. 14 et 17"

    def test_latin_with_y_connector_spanish(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Salmo Responsorial Sal 79, 9 y 12. 13-14. 15-16. 19-20", "es"
        )
        assert cit and "Sal 79" in cit

    def test_latin_with_paren_refrain_marker(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Psalmus Responsorius Ps 50, 3-4. 5-6a. 12-13. 14 et 17 (: cf. 3a)", "la"
        )
        assert cit and "Ps 50" in cit
        # The refrain indicator `(: cf. 3a)` should NOT be in the citation
        assert "cf." not in cit

    def test_short_psalm_just_chapter(self):
        cit, _ = R._extract_citation_and_strip_from_text("Psalmus Responsorius Ps 22", "la")
        assert cit == "Ps 22"

    def test_alleluia_verse_simple(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Alleluia, Versus ad Evangelium Mt 4, 4b", "la"
        )
        assert cit and "Mt 4" in cit

    def test_german_antwortpsalm(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Antwortpsalm Ps 147, 12-13. 14-15. 19-20", "de"
        )
        assert cit and "Ps 147" in cit

    def test_french_psaume_responsoriel(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Psaume Responsoriel Ps 23", "fr"
        )
        assert cit == "Ps 23"

    def test_no_match_returns_none(self):
        cit, _ = R._extract_citation_and_strip_from_text(
            "Some random text without psalm prefix", "la"
        )
        assert cit is None


# =============================================================================
# refine._build_lines_from_div — real estructura HTML
# =============================================================================

class TestBuildLinesFromRealHtml:
    def _div(self, html):
        return BeautifulSoup(html, "lxml").find("div") or BeautifulSoup(html, "lxml").find()

    def test_collect_with_red_rubric(self):
        # Real shape from Spanish santos source
        div = self._div(
            '<div>Señor y Dios nuestro, que en la difícil situación de la Iglesia mozárabe '
            'suscitaste en san Eulogio de Córdoba un valiente defensor de la fe.<br/>'
            'Por Jesucristo nuestro Señor.</div>'
        )
        lines = R._build_lines_from_div(div)
        assert len(lines) == 2
        assert "Señor y Dios" in lines[0][0]["text"]
        assert lines[1][0]["text"] == "Por Jesucristo nuestro Señor."

    def test_antiphon_with_alindcha(self):
        # Real shape: antiphon with embedded scripture reference
        div = self._div(
            '<div>Dóminus dixit ad me: <span class="alindcha">Ps 2, 7</span> '
            'Fílius meus es tu, ego hódie génui te.</div>'
        )
        lines = R._build_lines_from_div(div)
        flat = [s for line in lines for s in line]
        types = [s["type"] for s in flat]
        assert "reference" in types
        ref = next(s for s in flat if s["type"] == "reference")
        assert ref["text"] == "Ps 2, 7"

    def test_p_creates_separate_lines(self):
        div = self._div('<div><p>Para uno</p><p>Para dos</p></div>')
        lines = R._build_lines_from_div(div)
        assert len(lines) == 2

    def test_skips_h4_heading(self):
        # Real: source has h4 like "Sobre as oferendas" before body content
        div = self._div('<div><h4>Sobre as oferendas</h4>Real prayer body here.</div>')
        lines = R._build_lines_from_div(div)
        flat = [s for line in lines for s in line]
        assert not any("Sobre as oferendas" in s["text"] for s in flat)


# =============================================================================
# refine._drop_latin_leak — real Pentecost-style scenarios
# =============================================================================

class TestDropLatinLeakReal:
    def test_pentecost_french_was_actually_latin(self):
        """Real bug: French source for Pentecost readings had Latin verbatim."""
        plain = {
            "la": "Apériens autem Petrus os dixit: «In veritáte compério quóniam non est personárum accéptor Deus, sed in omni gente, qui timet eum et operátur iustítiam.",
            "fr": "Apériens autem Petrus os dixit: «In veritáte compério quóniam non est personárum accéptor Deus, sed in omni gente, qui timet eum et operátur iustítiam.",
            "es": "Apriendo Pedro la boca dijo: «Verdaderamente comprendo que Dios no hace acepción de personas, sino que en cualquier nación.",
        }
        lines = {"la": [], "fr": [], "es": []}
        R._drop_latin_leak(plain, lines)
        assert "fr" not in plain  # dropped because == la
        assert "es" in plain      # kept because real translation
        assert "la" in plain      # always kept

    def test_short_antiphon_not_dropped(self):
        """Antiphons under 30 chars may legitimately match across languages."""
        plain = {"la": "Amen.", "fr": "Amen."}
        lines = {}
        R._drop_latin_leak(plain, lines)
        assert "fr" in plain  # kept


# =============================================================================
# refine._strip_leading_prayer_label — exhaustive language coverage
# =============================================================================

class TestPrayerLabelStripExhaustive:
    cases = [
        # (label, body, expected_after_strip)
        ("Prayer over the Offerings", "Lord, we offer", "Lord, we offer"),
        ("Prayer after Communion", "God, who", "God, who"),
        ("Prayer after communion", "God, who", "God, who"),  # lowercase variant
        ("Prière sur les offrandes", "En cette", "En cette"),
        ("Prière après la communion", "Que cette", "Que cette"),
        ("Orácao sobre as oferendas", "Senhor", "Senhor"),
        ("Orácao após a comunhão", "Senhor", "Senhor"),
        ("Orácao do dia", "Pai santo", "Pai santo"),
        ("Sulle offerte", "Accetta", "Accetta"),
        ("Dopo la comunione", "Nutriti", "Nutriti"),
        ("Colletta", "O Padre", "O Padre"),
        ("Orazione sulle offerte", "O Padre", "O Padre"),
        ("Orazione dopo la comunione", "O Padre", "O Padre"),
        ("Schlussgebet", "Wir bitten", "Wir bitten"),
        ("Gabengebet", "Herr", "Herr"),
        ("Tagesgebet", "Allmächtiger Gott", "Allmächtiger Gott"),
        ("Eingangsgebet", "Lord", "Lord"),
        ("Oración sobre las ofrendas", "Recibe", "Recibe"),
        ("Oración colecta", "Oh Dios", "Oh Dios"),
        ("Oración después de la comunión", "Oh Dios", "Oh Dios"),
    ]

    def test_all_label_variants(self):
        for label, body, expected in self.cases:
            input_text = f"{label} {body}"
            result = R._strip_leading_prayer_label(input_text)
            assert result == expected, f"Failed for {label!r}: got {result!r}"


# =============================================================================
# refine.normalize_rank — exhaustive language coverage
# =============================================================================

class TestNormalizeRankExhaustive:
    def test_solemnity_all_languages(self):
        cases = ["Sollemnitas", "Solemnitas", "Solemnidad", "Solemnity",
                 "Solenidade", "Solennità", "Solennité", "Hochfest"]
        for v in cases:
            assert R.normalize_rank({"x": v}) == "solemnity", f"Failed: {v!r}"

    def test_feast_all_languages(self):
        cases = ["Festum", "Fiesta", "Feast", "Festa", "Fête", "Fest"]
        for v in cases:
            assert R.normalize_rank({"x": v}) == "feast", f"Failed: {v!r}"

    def test_memorial_all_languages(self):
        cases = ["Memoria", "Memorial", "Memória", "Mémoire", "Gedenktag"]
        for v in cases:
            assert R.normalize_rank({"x": v}) == "memorial", f"Failed: {v!r}"

    def test_optional_memorial_priority_over_memorial(self):
        # Should match "optional-memorial" before falling back to "memorial"
        assert R.normalize_rank({"x": "Memoria ad libitum"}) == "optional-memorial"
        assert R.normalize_rank({"x": "Memoria libera"}) == "optional-memorial"
        assert R.normalize_rank({"x": "Optional Memorial"}) == "optional-memorial"
        assert R.normalize_rank({"x": "Memória facultativa"}) == "optional-memorial"
        assert R.normalize_rank({"x": "Memoria facoltativa"}) == "optional-memorial"
        assert R.normalize_rank({"x": "Mémoire facultative"}) == "optional-memorial"
        assert R.normalize_rank({"x": "Nicht gebotener Gedenktag"}) == "optional-memorial"

    def test_case_insensitive(self):
        assert R.normalize_rank({"x": "FEAST"}) == "feast"
        assert R.normalize_rank({"x": "festum"}) == "feast"
        assert R.normalize_rank({"x": "MEMORIA"}) == "memorial"

    def test_within_phrase(self):
        # The keyword can appear anywhere in the string
        assert R.normalize_rank({"x": "1º Domingo Solenidade"}) == "solemnity"

    def test_unknown_returns_none(self):
        assert R.normalize_rank({"x": "Vigil Mass"}) is None
        assert R.normalize_rank({"x": "At Mass during the Day"}) is None
        assert R.normalize_rank({"x": ""}) is None


# =============================================================================
# refine.strip_trailing_rubric — concrete trailing-phrase strips
# =============================================================================

class TestStripTrailingRubricRealCases:
    def test_strips_tempo_pasquale_after_period(self):
        text = ("Concedi, o Padre, ai tuoi figli che, sostenuti dal sacramento di salvezza "
                "che ti abbiamo offerto, possiamo essere guidati dalla tua provvidenza. "
                "Per Cristo nostro Signore. Tempo Pasquale")
        result = R.strip_trailing_rubric(text)
        assert "Tempo Pasquale" not in result

    def test_strips_oppure_alternative_marker(self):
        text = ("This is a long enough body to trigger trailing-rubric scanning. " * 2 +
                "End sentence. Oppure: Lc 2, 20")
        result = R.strip_trailing_rubric(text)
        assert "Oppure:" not in result

    def test_phrase_appearing_twice_not_stripped(self):
        # If phrase appears multiple times it's likely legitimate content
        text = "Tempo Pasquale ... long body content ... Tempo Pasquale " * 5
        result = R.strip_trailing_rubric(text)
        # Should not have stripped — phrase appears multiple times
        assert "Tempo Pasquale" in result

    def test_short_body_no_strip(self):
        # < 60 chars
        assert R.strip_trailing_rubric("Short. Tempo Pasquale") == "Short. Tempo Pasquale"


# =============================================================================
# convert.parse_segments — real liturgical HTML fragments
# =============================================================================

class TestRealSourceHtml:
    def test_red_rubric_in_prayer(self):
        # Real shape from sacerdotale: rubric in red span
        html = '<div><span class="red">Si dice il</span> Credo.</div>'
        soup = BeautifulSoup(html, "lxml")
        segs = C.parse_segments(soup.find("div"))
        rubrics = [s for s in segs if s["type"] == "rubric"]
        assert len(rubrics) == 1
        assert "Si dice il" in rubrics[0]["text"]

    def test_alindcha_with_citation(self):
        # Real shape from antiphon: <span class="alindcha">Cf. Ps 24</span>
        html = '<div><h4>Antífona da entrada<span class="alindcha">Cf. Sl 24</span></h4></div>'
        soup = BeautifulSoup(html, "lxml")
        segs = C.parse_segments(soup.find("div"))
        types = [s["type"] for s in segs]
        assert "heading" in types

    def test_lauda_sion_stanza_with_br_breaks(self):
        # Real shape from Corpus Christi sequence
        html = ('<div class="port hijo hijo_73">'
                'Terra, exulta de alegria,<br/>louva teu pastor e guia<br/>'
                'com teus hinos,tua voz!</div>')
        blocks = C.parse_hijo_blocks(html, "port")
        assert len(blocks) == 1
        breaks = [s for s in blocks[0]["segments"] if s["type"] == "break"]
        assert len(breaks) == 2  # two <br/> tags


# =============================================================================
# convert.parse_estructura — real day structure
# =============================================================================

class TestRealEstructura:
    def test_dia_with_typed_slots(self):
        # Real structure: a saint day with typical slots
        html = '''
        <div class="dia xcast xengl xport" id="0102">
          <div class="x_titulo padre padre_1"></div>
          <div class="x_ant_ent">
            <div class="agrupado_ant padre padre_2"></div>
            <div class="agrupado_post padre padre_3"></div>
          </div>
          <div class="x_colecta">
            <div class="agrupado_post padre padre_5"></div>
          </div>
        </div>
        '''
        result = C.parse_estructura(html)
        assert len(result["days"]) == 1
        day = result["days"][0]
        assert day["id"] == "0102"
        assert "cast" in day["languages"]
        slot_types = [s["type"] for s in day["slots"]]
        assert "x_titulo" in slot_types
        assert "x_ant_ent" in slot_types
        assert "x_colecta" in slot_types


# =============================================================================
# convert.standalone_block — real IGMR/sacerdotale HTML
# =============================================================================

class TestStandaloneIGMRBlocks:
    def test_chapter_heading_with_id(self):
        soup = BeautifulSoup(
            '<h2 id="CHAPTER_I"><b>Chapter I — The Importance of the Mass</b></h2>',
            "lxml"
        )
        block = C.standalone_block(soup.find("h2"))
        assert block["type"] == "heading"
        assert block["level"] == 2
        assert "Chapter I" in block["text"]
        assert block["id"] == "CHAPTER_I"

    def test_paragraph_with_bold(self):
        soup = BeautifulSoup('<p><b>The Mass</b> is the source and summit.</p>', "lxml")
        block = C.standalone_block(soup.find("p"))
        assert block["type"] == "paragraph"
        assert "The Mass" in block["text"]
        assert "source and summit" in block["text"]

    def test_navigation_form_skipped(self):
        # IGMR pages have nav forms that should be ignored
        soup = BeautifulSoup('<form><select><option>Ch1</option></select></form>', "lxml")
        assert C.standalone_block(soup.find("form")) is None

    def test_table_preserved_as_html(self):
        soup = BeautifulSoup(
            '<table><tr><td>Cell 1</td><td>Cell 2</td></tr></table>', "lxml"
        )
        block = C.standalone_block(soup.find("table"))
        assert block["type"] == "table"
        assert "<table>" in block["html"]
        assert "Cell 1" in block["html"]


# =============================================================================
# refine._LEADING_LABEL_RUBRIC_RE — real antiphon label patterns
# =============================================================================

class TestAntiphonaLabelPatterns:
    def test_latin_antiphona_n(self):
        for n in range(1, 10):
            assert R._LEADING_LABEL_RUBRIC_RE.match(f"Antiphona {n}")

    def test_spanish_antifona_n(self):
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antífona 1")
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antífona 7")

    def test_english_antiphon_n(self):
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antiphon 1")
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antiphon 5")

    def test_french_antienne_n(self):
        # Was missing before — caused Lords-Supper antiphon mis-extraction
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antienne 2")
        assert R._LEADING_LABEL_RUBRIC_RE.match("Antienne 7")

    def test_portuguese_canto_n(self):
        # Was failing before — pattern required digits after "Canto"
        assert R._LEADING_LABEL_RUBRIC_RE.match("1º Canto")
        assert R._LEADING_LABEL_RUBRIC_RE.match("2º Canto")

    def test_does_not_match_real_antiphon_text(self):
        assert not R._LEADING_LABEL_RUBRIC_RE.match("Antiphona prima est de Domino")
        assert not R._LEADING_LABEL_RUBRIC_RE.match("Antienne du Cantique")


# =============================================================================
# Real Cf. citation normalization
# =============================================================================

class TestCfNormalization:
    """Bug: source had 'Cf.', 'cf.', 'Cfr.', 'Cf' — must canonicalize to 'Cf.'"""
    import re as _re
    _NORM = _re.compile(r"^(?:Cf\.?|cf\.?|Cfr\.?)(\s+)")

    def test_already_canonical(self):
        assert self._NORM.sub(r"Cf.\1", "Cf. Mt 5, 1") == "Cf. Mt 5, 1"

    def test_lowercase_cf(self):
        assert self._NORM.sub(r"Cf.\1", "cf. Mt 5, 1") == "Cf. Mt 5, 1"

    def test_cfr_form(self):
        assert self._NORM.sub(r"Cf.\1", "Cfr. Mt 5, 1") == "Cf. Mt 5, 1"

    def test_no_period(self):
        assert self._NORM.sub(r"Cf.\1", "Cf Mt 5, 1") == "Cf. Mt 5, 1"

    def test_doesnt_touch_non_prefix(self):
        # "Cfr" embedded in middle shouldn't be touched
        assert self._NORM.sub(r"Cf.\1", "Mt 5, 1 Cfr") == "Mt 5, 1 Cfr"


# =============================================================================
# Ordinary Time ferial coverage (synthesized from lecturas)
# =============================================================================

class TestOrdinaryTimeFerials:
    """OT ferial weekday Masses are synthesized from lecturas (Mon-Sat × 34 weeks)."""

    @classmethod
    def setup_class(cls):
        import json
        ot_root = pathlib.Path(__file__).resolve().parent.parent / "data" / "masses" / "tempore" / "ordinary-time"
        cls.by_id = {}
        for f in ot_root.rglob("*.json"):
            if f.name == "_index.json":
                continue
            d = json.loads(f.read_text())
            cls.by_id[d["id"]] = d
        cls.index = json.loads((ot_root / "_index.json").read_text())

    def test_total_count_includes_ferials(self):
        # 34 Sundays + 34 weeks × 6 ferials (Mon..Sat) = 238
        assert self.index["count"] == 238
        assert len(self.by_id) == 238

    def test_every_week_has_six_ferials(self):
        for week in range(1, 35):
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday"):
                mid = f"tempore.ordinary-time.week-{week}.{day}"
                assert mid in self.by_id, f"missing {mid}"

    def test_week1_monday_shape(self):
        m = self.by_id["tempore.ordinary-time.week-1.monday"]
        assert m["season"] == "ordinary-time"
        assert m["weekIndex"] == 1
        assert m["weekday"] == "monday"
        assert m["liturgicalColor"] == "green"
        # Title is multilingual
        title = m.get("title") or {}
        assert "la" in title and "en" in title
        # Source-prefix pollution must be stripped
        assert not title["la"].startswith("Tempus")
        # Both year cycles present in readings
        readings = m.get("readings") or {}
        assert "I" in readings, "Year I readings missing"
        assert "II" in readings, "Year II readings missing"
        assert "firstReading" in readings["I"]
        assert "firstReading" in readings["II"]
        # Cycle I week 1 Monday: Heb 1:1-6 ("Multifáriam et multis modis…")
        first_la = readings["I"]["firstReading"].get("body", {}).get("plain", {}).get("la", "")
        assert "Multifáriam" in first_la
        # Cycle II week 1 Monday: 1 Sam 1:1-8 ("Fuit vir unus de Ramáthaim…")
        first_la_ii = readings["II"]["firstReading"].get("body", {}).get("plain", {}).get("la", "")
        assert "Ramáthaim" in first_la_ii or "Ramathaim" in first_la_ii

    def test_ferials_have_no_prayer_slots(self):
        # Source has no proper Mass formulary per OT ferial — prayer fields
        # should be absent (consumers fall back to the Sunday formulary).
        for week in (1, 17, 34):
            for day in ("monday", "saturday"):
                m = self.by_id[f"tempore.ordinary-time.week-{week}.{day}"]
                for slot in ("collect", "entranceAntiphon", "communionAntiphon",
                             "prayerOverOfferings", "postcommunion"):
                    assert slot not in m, f"week-{week}.{day}: unexpected {slot}"


# =============================================================================
# Sanctorale alternatives (y/z merged inside one parent mass)
# =============================================================================

class TestSanctoraleAlternatives:
    """Multi-celebration days are merged: the date's primary celebration sits
    at the root of the mass file, additional optional celebrations live in
    `alternatives[]` keyed by saint-name slug."""

    @classmethod
    def setup_class(cls):
        import json
        cls.SANCT = pathlib.Path(__file__).resolve().parent.parent / "data" / "masses" / "sanctorale"
        cls.CAL_SANCT = pathlib.Path(__file__).resolve().parent.parent / "data" / "calendar" / "sanctorale"

    def test_jan_20_has_sebastian_alternative(self):
        """Jan 20 = Fabian (primary) or Sebastian (alternative)."""
        import json
        m = json.loads((self.SANCT / "01-20.json").read_text())
        assert "Fabiani" in m["title"]["la"], f"primary should be St. Fabian, got {m['title']!r}"
        alts = m.get("alternatives") or []
        assert len(alts) == 1, f"expected 1 alternative, got {len(alts)}"
        assert alts[0]["key"] == "sebastian"
        assert "Sebastiani" in alts[0]["title"]["la"]
        # Alternatives must NOT carry parent-only fields.
        for forbidden in ("id", "group", "date", "dateSuffix", "scope"):
            assert forbidden not in alts[0], f"alternative leaked {forbidden}"
        # No legacy y/z mass file should remain.
        assert not (self.SANCT / "01-20z.json").exists()

    def test_all_souls_three_formularies(self):
        """All Souls (Nov 2) has three prayer formularies of the same celebration.
        Same title across all three → keys are 'all-souls-form-2', 'all-souls-form-3'."""
        import json
        m = json.loads((self.SANCT / "11-02.json").read_text())
        assert "OMNIUM FIDELIUM DEFUNCTORUM" in m["title"]["la"].upper()
        alts = m.get("alternatives") or []
        assert len(alts) == 2, f"expected 2 alternative formularies, got {len(alts)}"
        keys = sorted(a["key"] for a in alts)
        for k in keys:
            assert "form" in k, f"All Souls alternative key should mark formulary, got {k!r}"
        # Each alt has its own collect (the formularies differ in prayers).
        for a in alts:
            assert "collect" in a, "All Souls alternative missing its own collect"

    def test_assumption_vigil_at_root_day_as_alternative(self):
        """Aug 15 has the Vigil at root and the Day Mass as the only alternative.
        The Vigil keeps its 'IN VIGILIA' title marker; the Day Mass is keyed by saint slug."""
        import json
        m = json.loads((self.SANCT / "08-15.json").read_text())
        assert "VIGILIA" in m["title"]["la"].upper(), f"primary should be Vigil, got {m['title']!r}"
        alts = m.get("alternatives") or []
        assert len(alts) == 1, f"expected 1 alternative (Day Mass), got {len(alts)}"
        # Day Mass title doesn't say VIGIL/VIGILIA
        assert "VIGILIA" not in alts[0]["title"]["la"].upper()

    def test_no_dateSuffix_field_anywhere(self):
        """`dateSuffix` was retired when y/z merged into alternatives."""
        import json
        for f in self.SANCT.rglob("*.json"):
            if f.name == "_index.json":
                continue
            m = json.loads(f.read_text())
            assert "dateSuffix" not in m, f"{f.relative_to(self.SANCT)}: dateSuffix should be gone"
            for alt in m.get("alternatives") or []:
                assert "dateSuffix" not in alt, f"{f.relative_to(self.SANCT)}#{alt.get('key')}: dateSuffix in alt"

    def test_calendar_expands_alternatives(self):
        """Calendar still surfaces every option: parent + alternatives are
        emitted as separate calendar entries."""
        assert (self.CAL_SANCT / "01-20.json").exists(), "Fabian calendar entry missing"
        assert (self.CAL_SANCT / "01-20" / "sebastian.json").exists(), "Sebastian calendar entry missing"
        assert (self.CAL_SANCT / "11-02.json").exists()

    def test_05_22_promoted_to_base(self):
        """Anomaly: source had only 05-22z (St. Rita) without a base. The
        suffix-only entry is promoted to the universal base."""
        import json
        rita = self.SANCT / "05-22.json"
        assert rita.exists(), "St. Rita should now be the universal base on 05-22"
        m = json.loads(rita.read_text())
        assert "Ritæ" in m["title"]["la"] or "Rita" in (m["title"].get("en") or "")
        assert not (self.SANCT / "05-22z.json").exists()

    def test_all_souls_gospel_acclamation_alternatives(self):
        """All Souls (Nov 2) lists 11 alternative gospel acclamations in the
        Lectionary. They should be split: the first lives at
        readings.default.gospelAcclamation; the other 10 nested inside it
        as `alternatives` — NOT crammed into a single body."""
        import json
        m = json.loads((self.SANCT / "11-02.json").read_text())
        r = (m.get("readings") or {}).get("default") or {}
        ga = r.get("gospelAcclamation") or {}
        ga_la = (ga.get("body") or {}).get("plain", {}).get("la", "")
        # Each individual acclamation is short (~100-200 chars)
        assert len(ga_la) < 250, f"primary GA still bundled: {len(ga_la)} chars"
        alts = ga.get("alternatives") or []
        assert len(alts) >= 5, f"expected multiple GA alternatives, got {len(alts)}"
        # Each alternative has its own citation
        cits = [(a.get("citation") or {}).get("la") for a in alts]
        assert all(cits), f"some alternatives missing citation: {cits}"
        assert len(set(cits)) == len(cits), f"duplicate citations: {cits}"

    def test_same_celebration_alternatives_drop_readings(self):
        """All Souls' three formularies share their readings via the
        Lectionary's 'Masses for the Dead' pool. Alternatives whose title
        matches the parent must NOT carry their own `readings` field —
        consumers fall back to the parent."""
        import json
        m = json.loads((self.SANCT / "11-02.json").read_text())
        for alt in m.get("alternatives") or []:
            assert "readings" not in alt, (
                f"All Souls alt {alt.get('key')!r} should not duplicate readings"
            )

    def test_11_30_empty_placeholder_dropped(self):
        """The empty 11-30z placeholder ('11 30z' with no body) is filtered out;
        Nov 30 just keeps St. Andrew."""
        import json
        m = json.loads((self.SANCT / "11-30.json").read_text())
        assert "ANDRE" in m["title"]["la"].upper()
        # No alternative should be the junk placeholder.
        for a in m.get("alternatives") or []:
            assert a["title"].get("la") != "11 30z"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
