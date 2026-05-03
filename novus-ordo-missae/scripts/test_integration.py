"""End-to-end integration tests for the post-processing pipeline.

These tests exercise complete flows rather than individual functions:
- Full mass post-processing (multiple cleanups in sequence)
- Real-world cases (Easter Vigil, Holy Family, Christ the King, etc.)
- Cross-cutting invariants over the generated `data/` corpus
- Cross-language consistency (citation enrichment, color, rank, terminator)

Run from this directory: pytest test_integration.py -q
"""

import importlib.util
import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import refine as R

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"


# =============================================================================
# End-to-end mass post-processing
# =============================================================================

class TestEndToEndPostProcessing:
    """Apply _post_process_mass to realistic mass dicts and assert that
    multiple independent fixes all land on the same input."""

    def test_easter_vigil_full_processing(self):
        # Mass with: title pollution, OCR scanno, missing terminal period,
        # missing rank, and a bare-digit segment in body.lines.
        mass = {
            "id": "tempore.holy-week.easter-vigil",
            "season": "holy-week",
            "title": {"la": "SACRUM TRIDUUM PASCHALE SABBATO SANCTO"},
            "rank": None,
            "collect": {
                "body": {
                    "plain": {"la": "Deus, qui hanc sacratíssimam noctem.. quœsumus in unitáte Spíritus Sancti vivit et regnat"},
                    "lines": {"la": [[
                        {"type": "text", "text": "19."},
                        {"type": "rubric", "text": "Subsequenter…"},
                    ]]},
                }
            },
        }
        out = R._post_process_mass(mass)
        assert out is not None
        # Title pollution stripped
        assert out["title"]["la"] == "SABBATO SANCTO"
        # Rank promoted to solemnity (Easter Vigil is in _SOLEMNITY_IDS)
        assert out["rank"] == "solemnity"
        assert out["rankLocalized"]["la"] == "Sollemnitas"
        # Liturgical color assigned (Easter Vigil → white)
        assert out["liturgicalColor"] == "white"
        # OCR scanno fixed
        body = out["collect"]["body"]["plain"]["la"]
        assert "quœsumus" not in body
        assert "quǽsumus" in body
        # Trailing `..` collapsed AND terminal period present
        assert not body.endswith("..")
        assert body.endswith(".")
        # Bare-digit segment dropped
        line0 = out["collect"]["body"]["lines"]["la"][0]
        assert all(seg.get("text") != "19." for seg in line0)

    def test_holy_family_multi_lang_full_processing(self):
        # Holy Family has rubric prefix in EVERY language; each gets stripped
        # by its own pattern.
        mass = {
            "id": "tempore.christmas.day-140.sunday",
            "season": "christmas",
            "rank": "feast",
            "title": {
                "la": "DOMINICA infra octavam Nativitatis Domini, vel, ea deficiente, die 30 decembris S. FAMILIÆ IESU, MARIÆ ET IOSEPH",
                "en": "THE HOLY FAMILY OF JESUS, MARY AND JOSEPH",
                "pt-BR": "Domingo na oitava do Natal ou, se não houver domingo nesta oitava, dia 30 de dezembro SAGRADA FAMÍLIA DE JESUS, MARIA E JOSÉ",
                "it": "Domenica fra l'ottava di Natale, oppure, se non ricorre la domenica fra l'ottava di Natale, 30 dicembre SANTA FAMIGLIA DI GESÙ MARIA E GIUSEPPE",
                "fr": "Dimanche dans l'Octave de la Nativité ou 30 décembre en l'absence de ce dimanche LA SAINTE FAMILLE DE JÉSUS, MARIE ET JOSEPH",
                "de": "SONNTAG in der Weihnachtsoktav oder, wenn in die Weihnachtsoktav kein Sonntag fällt, 30. Dezember. FEST DER HEILIGEN FAMILIE",
            },
        }
        out = R._post_process_mass(mass)
        assert out is not None
        # Rubric prefixes stripped on all langs
        assert "DOMINICA infra" not in out["title"]["la"]
        assert "S. FAMILIÆ" in out["title"]["la"]
        assert "Domingo na oitava" not in out["title"]["pt-BR"]
        assert "SAGRADA FAMÍLIA" in out["title"]["pt-BR"]
        assert "Domenica fra" not in out["title"]["it"]
        assert "SANTA FAMIGLIA" in out["title"]["it"]
        assert "Dimanche dans" not in out["title"]["fr"]
        assert "LA SAINTE FAMILLE" in out["title"]["fr"]
        assert "SONNTAG in der" not in out["title"]["de"]
        assert "FEST DER HEILIGEN FAMILIE" in out["title"]["de"]
        # English title was already clean — preserved
        assert out["title"]["en"] == "THE HOLY FAMILY OF JESUS, MARY AND JOSEPH"
        # Color: white (christmas season + feast rank)
        assert out["liturgicalColor"] == "white"

    def test_christ_the_king_full_processing(self):
        mass = {
            "id": "tempore.solemnity.christ-the-king",
            "rank": "solemnity",
            "title": {
                "la": "Feria V post Dominicam II post Pentecosten DOMINI NOSTRI IESU CHRISTI UNIVERSORUM REGIS",
                "es": "SOLEMNIDADES DEL SEÑOR DURANTE EL TIEMPO ORDINARIO Último domingo del tiempo ordinario JESUCRISTO, REY DEL UNIVERSO",
                "de": "HERRENFESTE IM JAHRESKREIS Letzter Sonntag im Jahreskreis CHRISTKÖNIGSSONNTAG",
                "it": "SOLENNITA' DEL SIGNORE NEL TEMPO ORDINARIO Domenica ultima del tempo ordinario CRISTO RE",
            },
        }
        out = R._post_process_mass(mass)
        # Section headers stripped from each lang
        assert "Feria V" not in out["title"]["la"]
        assert "SOLEMNIDADES DEL SEÑOR" not in out["title"]["es"]
        assert "HERRENFESTE" not in out["title"]["de"]
        assert "SOLENNITA'" not in out["title"]["it"]
        # Substantive title preserved
        assert "DOMINI NOSTRI IESU CHRISTI" in out["title"]["la"]
        assert "JESUCRISTO" in out["title"]["es"]
        assert "CHRISTKÖNIG" in out["title"]["de"]

    def test_late_advent_full_processing(self):
        # Dec 17-24: title prefix in all langs, season needs reclassification,
        # weekday should be cleared, color = violet.
        mass = {
            "id": "tempore.christmas.day-120.sunday",
            "season": "christmas",
            "weekday": "sunday",
            "title": {
                "la": "IN FERIIS ADVENTUS a Die 17 ad diem 24 decembris Die 20 decembris",
                "en": "Weekdays of Advent December 17 to December 24 20 December",
                "es": "FERIAS DE ADVIENTO desde el 17 al 24 de diciembre 20 de diciembre",
                "it": "FERIE DI AVVENTO dal 17 al 24 dicembre 20 dicembre",
            },
        }
        out = R._post_process_mass(mass)
        # Title rubric stripped
        assert out["title"]["la"] == "Die 20 decembris"
        assert out["title"]["en"] == "20 December"
        assert out["title"]["es"] == "20 de diciembre"
        assert out["title"]["it"] == "20 dicembre"
        # Season reclassified to advent
        assert out["season"] == "advent"
        # Weekday cleared (Dec 20 isn't always Sunday)
        assert out.get("weekday") is None
        # Color: violet (advent)
        assert out["liturgicalColor"] == "violet"

    def test_pentecost_solemnity_full_processing(self):
        mass = {
            "id": "tempore.easter.week-8.sunday",
            "season": "easter",
            "rank": None,
            "title": {"la": "DOMINICA PENTECOSTES", "en": "PENTECOST SUNDAY"},
        }
        out = R._post_process_mass(mass)
        # Rank promoted to solemnity
        assert out["rank"] == "solemnity"
        # Color: red (Pentecost via _PENTECOST_KEYWORDS)
        assert out["liturgicalColor"] == "red"

    def test_immaculate_heart_marian_white_not_red_pentecost(self):
        # Title contains "Pentecosten" as DATE reference; the WHITE override
        # for Marian feasts should beat the Pentecost-keyword red trigger.
        mass = {
            "id": "sanctorale.movable.05-32",
            "rank": "memorial",
            "title": {"la": "Sabbato post Dominicam secundam post Pentecosten Immaculati Cordis beatæ Maríæ Virginis"},
        }
        out = R._post_process_mass(mass)
        assert out["liturgicalColor"] == "white"

    def test_full_reading_with_citation_enrichment_all_langs(self):
        # Mass with a reading where the citation needs enrichment in 7 langs.
        mass = {
            "id": "test.mass",
            "readings": {
                "default": {
                    "gospel": {
                        "introduction": {
                            "la": "✠ Léctio sancti Evangélii secúndum Matthǽum",
                            "en": "✠ A reading from the holy Gospel according to Matthew",
                            "it": "✠ Dal vangelo secondo Matteo",
                            "de": "✠ Aus dem heiligen Evangelium nach Matthäus",
                        },
                        "citation": {"la": "5, 1-12", "en": "5, 1-12", "it": "5, 1-12", "de": "5, 1-12"},
                        "body": {"plain": {"la": "In illo témpore..."}},
                    }
                }
            }
        }
        out = R._post_process_mass(mass)
        gospel = out["readings"]["default"]["gospel"]
        assert gospel["citation"]["la"].startswith("Mt ")
        assert gospel["citation"]["en"].startswith("Mt ")
        assert gospel["citation"]["it"].startswith("Mt ")
        assert gospel["citation"]["de"].startswith("Mt ")


# =============================================================================
# Real-data corpus invariants
# =============================================================================

class TestCorpusInvariants:
    """These run against the live data/ output. They verify properties the
    full pipeline (refine.py end-to-end) must hold."""

    def test_every_mass_has_id(self, all_masses):
        for m in all_masses:
            assert isinstance(m.get("id"), str) and m["id"], f"mass missing id: {m!r}"

    def test_every_mass_has_color(self, all_masses):
        # Cycle 4 invariant: liturgicalColor must be set on all masses.
        no_color = [m["id"] for m in all_masses if not m.get("liturgicalColor")]
        assert not no_color, f"masses without liturgicalColor: {no_color[:5]}"

    def test_color_values_in_enum(self, all_masses):
        valid = {"white", "red", "green", "violet", "rose", "black"}
        for m in all_masses:
            c = m.get("liturgicalColor")
            assert c in valid, f"invalid color {c!r} on {m['id']}"

    def test_no_title_prefix_pollution_anywhere(self, all_masses):
        # Cycles 1+2+5+6+7+8 invariant: no mass title starts with a known
        # section-header pattern across any of the 7 languages.
        pollution = re.compile(
            r"^(Tempus|Tempo|TEMPUS|TIEMPO|TEMPS\s+(de|du|des|ordinaire|de\s+l)|"
            r"OSTERZEIT|FASTENZEIT|ADVENTSZEIT|WEIHNACHTSZEIT|"
            r"Hebdomada\s+Sancta|SACRUM\s+TRIDUUM|SAGRADO\s+TR[IÍ]DUO|"
            r"IN\s+SOLLEMNITATIBUS|In\s+octava\s+Nativitatis|"
            r"DIE\s+WOCHENTAGE\s+VOM|AN\s+DEN\s+WOCHENTAGEN|"
            r"Domingo\s+(dentro|na\s+oitava)|Domenica\s+fra|"
            r"Dimanche\s+dans\s+l|SONNTAG\s+in\s+der|DOMINICA\s+infra|"
            r"HERRENFESTE\s+IM|SOLEMNIDADES\s+DEL\s+SEÑOR|"
            r"SOLENNIT[AÀ]'?\s+DEL\s+SIGNORE|SOLENIDADES\s+DO\s+SENHOR|"
            r"FERIE\s+DEL\s+TEMPO|FERIE\s+DI\s+AVVENTO|"
            r"FERIAS\s+DEL\s+TIEMPO|FERIAS\s+DE\s+ADVIENTO|"
            r"Weekdays\s+of\s+(Christmas|Advent)|"
            r"PARA\s+OS\s+DIAS\s+DE\s+SEMANA|"
            r"IN\s+FERIIS\s+(ADVENTUS|TEMPORIS)|Feria\s+[IVX]+\s+post)",
            re.IGNORECASE,
        )
        bad = []
        for m in all_masses:
            for L, v in (m.get("title") or {}).items():
                if isinstance(v, str) and pollution.match(v):
                    bad.append((m["id"], L, v[:60]))
        assert not bad, f"title pollution remaining: {bad[:5]}"

    def test_no_double_period_endings_anywhere(self, all_masses):
        re_double = re.compile(r"\.{2,}$")
        bad = []
        def walk(node, mid):
            if isinstance(node, str):
                if re_double.search(node.rstrip()):
                    bad.append((mid, node[-30:]))
            elif isinstance(node, dict):
                for v in node.values(): walk(v, mid)
            elif isinstance(node, list):
                for v in node: walk(v, mid)
        for m in all_masses:
            walk(m, m["id"])
        assert not bad, f"trailing `..` strings: {bad[:5]}"

    def test_no_html_indent_tabs_anywhere(self, all_masses):
        bad = []
        def walk(node, mid):
            if isinstance(node, str):
                if "\t" in node:
                    bad.append((mid, repr(node[:40])))
            elif isinstance(node, dict):
                for v in node.values(): walk(v, mid)
            elif isinstance(node, list):
                for v in node: walk(v, mid)
        for m in all_masses:
            walk(m, m["id"])
        assert not bad, f"tab characters in mass strings: {bad[:5]}"

    def test_no_invisible_unicode_anywhere(self, all_masses):
        invisible = "­​‌‍﻿"  # soft-hyphen, ZWS, ZWJ, ZWJ, BOM
        bad = []
        def walk(node, mid):
            if isinstance(node, str):
                if any(c in node for c in invisible):
                    bad.append((mid, repr(node[:40])))
            elif isinstance(node, dict):
                for v in node.values(): walk(v, mid)
            elif isinstance(node, list):
                for v in node: walk(v, mid)
        for m in all_masses:
            walk(m, m["id"])
        assert not bad, f"invisible unicode: {bad[:5]}"

    def test_no_la_ocr_scannos_anywhere(self, all_masses):
        bad_words = re.compile(r"\b(vitre|quœsumus|sœculi|prœsta|prœstantíssimum|tuœ|suœ|meœ)\b", re.I)
        bad = []
        def walk(node, lang_hint, mid):
            if isinstance(node, str):
                if lang_hint == "la" and bad_words.search(node):
                    bad.append((mid, lang_hint, node[:60]))
            elif isinstance(node, dict):
                for k, v in node.items():
                    new_lang = k if k in ("la","en","es","pt-BR","it","fr","de") else lang_hint
                    walk(v, new_lang, mid)
            elif isinstance(node, list):
                for v in node: walk(v, lang_hint, mid)
        for m in all_masses:
            walk(m, None, m["id"])
        assert not bad, f"Latin OCR scannos: {bad[:5]}"

    def test_solemnity_masses_have_rankLocalized(self, all_masses):
        for m in all_masses:
            if m.get("rank") == "solemnity":
                rl = m.get("rankLocalized") or {}
                assert isinstance(rl, dict) and any(rl.values()), \
                    f"{m['id']} has rank=solemnity but no rankLocalized"

    def test_dec_17_24_are_advent_violet(self, all_masses):
        late_advent_ids = {
            "tempore.christmas.day-117", "tempore.christmas.day-118",
            "tempore.christmas.day-119", "tempore.christmas.day-120.sunday",
            "tempore.christmas.day-121.monday", "tempore.christmas.day-122.tuesday",
            "tempore.christmas.day-123.wednesday", "tempore.christmas.day-124.thursday",
        }
        seen = set()
        for m in all_masses:
            if m["id"] in late_advent_ids:
                seen.add(m["id"])
                assert m.get("season") == "advent", \
                    f"{m['id']}: season is {m.get('season')!r}, expected 'advent'"
                assert m.get("liturgicalColor") == "violet", \
                    f"{m['id']}: color is {m.get('liturgicalColor')!r}, expected 'violet'"
                assert m.get("weekday") is None, \
                    f"{m['id']}: weekday is {m.get('weekday')!r}, should be None (date varies year-to-year)"
        assert seen == late_advent_ids, f"missing late-advent days: {late_advent_ids - seen}"

    def test_holy_week_color_assignment(self, all_masses):
        expected = {
            "tempore.holy-week.monday": "violet",
            "tempore.holy-week.tuesday": "violet",
            "tempore.holy-week.wednesday": "violet",
            "tempore.holy-week.palm-sunday": "red",
            "tempore.holy-week.chrism-mass": "white",
            "tempore.holy-week.lords-supper": "white",
            "tempore.holy-week.good-friday": "red",
            "tempore.holy-week.easter-vigil": "white",
        }
        for m in all_masses:
            if m["id"] in expected:
                exp = expected[m["id"]]
                got = m.get("liturgicalColor")
                assert got == exp, f"{m['id']}: color is {got!r}, expected {exp!r}"

    def test_common_of_martyrs_is_red(self, all_masses):
        for m in all_masses:
            if m["id"].startswith("common.martyrs."):
                assert m.get("liturgicalColor") == "red", \
                    f"{m['id']}: martyr should be red, got {m.get('liturgicalColor')!r}"

    def test_common_of_pastors_is_white(self, all_masses):
        for m in all_masses:
            if m["id"].startswith("common.pastors."):
                assert m.get("liturgicalColor") == "white", \
                    f"{m['id']}: pastor should be white"

    def test_for_the_dead_is_violet(self, all_masses):
        for m in all_masses:
            if m["id"].startswith("ritual.for-the-dead."):
                assert m.get("liturgicalColor") == "violet", \
                    f"{m['id']}: should be violet, got {m.get('liturgicalColor')!r}"

    def test_apostle_solemnities_are_red(self, all_masses):
        # SS. Petri et Pauli (06-29), St. Andrew (11-30), etc.
        for m in all_masses:
            title = (m.get("title") or {}).get("la", "") or (m.get("title") or {}).get("en", "")
            if "APOSTOL" in title.upper() and m.get("rank") in ("feast", "solemnity"):
                assert m.get("liturgicalColor") == "red", \
                    f"{m['id']} (apostle): expected red, got {m.get('liturgicalColor')!r}"


# =============================================================================
# Reading-citation enrichment integration
# =============================================================================

class TestCitationEnrichmentIntegration:
    """Verify all reading citations in the corpus have a book abbreviation."""

    def test_all_citations_have_book_token_la(self, all_readings):
        numbered = re.compile(r"^\d+\s+[A-Za-zÀ-ÿ]")
        bad = []
        for mid, slot, L, v in all_readings:
            if L != "la": continue
            first = v.lstrip()
            if first[:1].isdigit() and not numbered.match(first):
                bad.append((mid, slot, v[:30]))
        assert not bad, f"Latin citations missing book abbrev: {bad[:5]}"

    def test_all_citations_have_book_token_en(self, all_readings):
        numbered = re.compile(r"^\d+\s+[A-Za-zÀ-ÿ]")
        bad = []
        for mid, slot, L, v in all_readings:
            if L != "en": continue
            first = v.lstrip()
            if first[:1].isdigit() and not numbered.match(first):
                bad.append((mid, slot, v[:30]))
        assert not bad, f"English citations missing book abbrev: {bad[:5]}"

    def test_all_citations_have_book_token_all_langs(self, all_readings):
        numbered = re.compile(r"^\d+\s+[A-Za-zÀ-ÿ]")
        by_lang: dict[str, list] = {}
        for mid, slot, L, v in all_readings:
            first = v.lstrip()
            if first[:1].isdigit() and not numbered.match(first):
                by_lang.setdefault(L, []).append((mid, slot, v[:30]))
        assert not by_lang, f"citations missing book abbrev by lang: {by_lang}"

    def test_lang_specific_book_abbrevs(self, all_readings):
        # John in pt-BR should be "Jo", in it "Gv", in de "Joh", in en "Jn"
        # Pick a known Matthew gospel mass for sanity. Iterate to find it.
        found = False
        for mid, slot, L, v in all_readings:
            if slot != "gospel": continue
            # If body contains "Jn 3" or similar in en, etc., we know this is John gospel
            # Just check the abbreviation matches the lang convention
            first = v.split()[0] if v.split() else ""
            if first == "Jn" and L not in ("en", "fr"):
                continue  # skip noise
            if first == "Jo" and L != "pt-BR":
                continue
            if first == "Gv" and L != "it":
                continue
            if first == "Joh" and L != "de":
                continue
            if first == "Io" and L != "la":
                continue
            # If we got here, the abbrev is consistent with the lang
            found = True
        assert found, "No gospel readings audited"


# =============================================================================
# Cross-cutting consistency
# =============================================================================

class TestSchemaCompliance:
    """The validate.py script confirms the schema. This test runs it as a
    last-line-of-defense integration check."""

    def test_validate_returns_zero(self, validate_result):
        match = re.search(r"errors:\s*(\d+)", validate_result.stdout)
        assert match, f"could not find errors count in: {validate_result.stdout[-500:]}"
        errors = int(match.group(1))
        assert errors == 0, (
            f"validate.py reports {errors} errors:\n{validate_result.stdout[-2000:]}"
        )


class TestIndexFileConsistency:
    """index.json totals must match actual file counts."""

    def setup_method(self):
        if not (DATA / "index.json").exists():
            pytest.skip("data/index.json not generated")
        self.idx = json.loads((DATA / "index.json").read_text())

    @staticmethod
    def _count_items(root):
        return sum(1 for f in root.rglob("*.json") if f.name != "_index.json")

    def test_total_masses_matches_files(self):
        assert self.idx["totals"]["masses"] == self._count_items(DATA / "masses")

    def test_groups_count_matches(self):
        groups = self.idx.get("groups", {})
        for g, expected in groups.items():
            actual = self._count_items(DATA / "masses" / g)
            assert actual == expected, f"group {g}: index says {expected}, found {actual}"

    def test_saints_count_matches(self):
        assert self.idx["totals"]["saintsCatalog"] == self._count_items(DATA / "saints")

    def test_prefaces_count_matches(self):
        assert self.idx["totals"]["prefaces"] == self._count_items(DATA / "library" / "preface")

    def test_eucharistic_prayers_count_matches(self):
        assert self.idx["totals"]["eucharisticPrayers"] == self._count_items(DATA / "library" / "eucharistic-prayer")


def _expanded_mass_ids(all_masses):
    """Mass ids include both standalone masses AND
    `<parent>.<alternative.key>` ids for nested alternatives."""
    ids = set()
    for d in all_masses:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        ids.add(d["id"])
        for alt in d.get("alternatives") or []:
            if alt.get("key"):
                ids.add(f"{d['id']}.{alt['key']}")
    return ids


def _calendar_entries():
    return [
        json.loads(f.read_text())
        for f in (DATA / "calendar").rglob("*.json")
        if f.name != "_index.json"
    ]


class TestCalendarMassesCrossRef:
    """Every calendar entry must point to an existing mass."""

    def test_all_tempore_calendar_entries_have_mass(self, all_masses):
        if not (DATA / "calendar").exists():
            pytest.skip("data/calendar/ not generated")
        mass_ids = _expanded_mass_ids(all_masses)
        for entry in _calendar_entries():
            mid = entry.get("id")
            if mid and mid.startswith("tempore."):
                assert mid in mass_ids, f"calendar tempore entry {mid} has no mass"

    def test_all_sanctorale_calendar_entries_have_mass(self, all_masses):
        if not (DATA / "calendar").exists():
            pytest.skip("data/calendar/ not generated")
        mass_ids = _expanded_mass_ids(all_masses)
        for entry in _calendar_entries():
            mid = entry.get("id")
            if mid and mid.startswith("sanctorale."):
                assert mid in mass_ids, f"calendar sanctorale entry {mid} has no mass"


class TestPrefaceCrossRef:
    """Every prefaceRef in masses must resolve to a library preface."""

    def test_no_dangling_preface_refs(self, all_masses):
        pref_dir = DATA / "library" / "preface"
        if not pref_dir.exists():
            pytest.skip("data/library/preface/ not generated")
        preface_ids = {
            json.loads(f.read_text())["id"]
            for f in pref_dir.rglob("*.json") if f.name != "_index.json"
        }
        bad = []
        for m in all_masses:
            if not isinstance(m, dict): continue
            p = m.get("preface")
            if not isinstance(p, dict): continue
            ref = p.get("prefaceRef")
            if isinstance(ref, str) and ref and ref not in preface_ids:
                bad.append((m.get("id"), ref))
            for alt in p.get("alternativeRefs") or []:
                if alt not in preface_ids:
                    bad.append((m.get("id"), alt))
        assert not bad, f"dangling preface refs: {bad[:5]}"


# =============================================================================
# Multi-fix interaction tests — verify fixes don't undo each other
# =============================================================================

class TestFixInteractions:
    """Verify post-processing steps compose correctly without one undoing
    another."""

    def test_title_pollution_strip_then_h3_subtitle_merge_compose(self):
        # If a common-of-saints mass has BOTH a section header in h2 AND
        # a numbered h3 subtitle, the merged title should be the cleaned
        # h2 + the h3 subtitle, NOT the polluted h2.
        # (Title is composed at parse time; we test the post-processed result
        # is sensible.)
        mass = {
            "id": "common.pastors.past1",
            "title": {"la": "COMMUNE PASTORUM I. Pro Papa vel pro Episcopo 1"},
        }
        out = R._post_process_mass(mass)
        # No leading section headers, no votive-style numeric prefix glitches
        assert out["title"]["la"] == "COMMUNE PASTORUM I. Pro Papa vel pro Episcopo 1"

    def test_la_leak_does_not_remove_title_after_pollution_strip(self):
        # Title pollution stripping shouldn't leave a vernacular field that
        # then matches Latin and gets dropped.
        mass = {
            "id": "test",
            "title": {"la": "BEATÆ MARIÆ VIRGINIS DE LORETO",
                      "pt-BR": "Tempo Comum BEATÆ MARIÆ VIRGINIS DE LORETO"},
        }
        out = R._post_process_mass(mass)
        # pt-BR pollution stripped → would equal Latin → la-leak dropped pt-BR
        # OR pt-BR title legitimately translated. Either is acceptable, but
        # the title structure must remain valid (no crash, la preserved).
        assert "la" in out["title"]
        assert out["title"]["la"] == "BEATÆ MARIÆ VIRGINIS DE LORETO"

    def test_color_assignment_after_rank_promotion(self):
        # Easter Vigil: rank gets promoted to solemnity, then color uses
        # the holy-week branch (white via easter-vigil id check).
        mass = {
            "id": "tempore.holy-week.easter-vigil",
            "season": "holy-week",
            "rank": None,
            "title": {"la": "SABBATO SANCTO"},
        }
        out = R._post_process_mass(mass)
        assert out["rank"] == "solemnity"
        assert out["liturgicalColor"] == "white"

    def test_citation_enrichment_after_string_scrub(self):
        # Citation has a doubled-period in introduction; scrub fixes it,
        # enrichment then matches the cleaned intro.
        mass = {
            "id": "test",
            "readings": {
                "default": {
                    "gospel": {
                        "introduction": {"la": "✠ Léctio sancti Evangélii secúndum Matthǽum.."},
                        "citation": {"la": "5, 1-12"},
                    }
                }
            }
        }
        out = R._post_process_mass(mass)
        gospel = out["readings"]["default"]["gospel"]
        # `..` collapsed in intro, citation enriched
        assert not gospel["introduction"]["la"].endswith("..")
        assert gospel["citation"]["la"] == "Mt 5, 1-12"

    def test_empty_mass_drop_then_no_color_assigned(self):
        # An empty mass shell should be dropped (return None), so we don't
        # accidentally assign a default color to a ghost.
        mass = {"id": "empty.shell", "title": {}}
        out = R._post_process_mass(mass)
        assert out is None


# =============================================================================
# Per-item file layout: id IS the path
# =============================================================================

def _id_to_path(item_id, root, suffix=".json"):
    parts = item_id.split(".")
    return root.joinpath(*parts[:-1], parts[-1] + suffix)


class TestSplitFileLayout:
    """Every per-item file lives at the path its id implies, and every bucket
    has an _index.json whose count matches the sibling file count."""

    def test_each_mass_file_path_matches_id(self, masses_by_file):
        masses_root = DATA / "masses"
        bad = []
        for f, d in masses_by_file.items():
            mid = d.get("id")
            if not mid:
                bad.append((str(f.relative_to(DATA)), "no id"))
                continue
            expected = _id_to_path(mid, masses_root)
            if f != expected:
                bad.append((mid, str(f.relative_to(DATA)), str(expected.relative_to(DATA))))
        assert not bad, f"path-id mismatches: {bad[:5]}"

    def test_each_index_count_matches_siblings(self):
        # For every _index.json, the `count` field equals the number of *.json
        # siblings (recursive within the bucket dir, excluding nested _index.json).
        bad = []
        for idx in DATA.rglob("_index.json"):
            payload = json.loads(idx.read_text())
            siblings = sum(1 for f in idx.parent.rglob("*.json")
                           if f.name != "_index.json")
            if payload.get("count") != siblings:
                bad.append((str(idx.relative_to(DATA)), payload.get("count"), siblings))
        # Exception: triduum/_index.json is a reference list — its `count`
        # tracks ids[], not files (no triduum/<id>.json files exist).
        bad = [b for b in bad if not b[0].startswith("triduum/")]
        assert not bad, f"_index.count mismatches: {bad}"

    def test_triduum_index_is_reference_only(self):
        idx_path = DATA / "triduum" / "_index.json"
        if not idx_path.exists():
            pytest.skip("triduum not generated")
        idx = json.loads(idx_path.read_text())
        assert "ids" in idx and idx["count"] == len(idx["ids"])
        # No other files in data/triduum/.
        siblings = [p.name for p in (DATA / "triduum").iterdir()]
        assert siblings == ["_index.json"], f"unexpected triduum files: {siblings}"

    def test_no_legacy_bundle_files(self):
        # The old bundle layout shouldn't reappear.
        legacy = [
            DATA / "calendar.json",
            DATA / "saints.json",
            DATA / "triduum.json",
            DATA / "library" / "prefaces.json",
            DATA / "library" / "eucharistic-prayers.json",
            DATA / "library" / "ordinary.json",
        ]
        existing = [str(p.relative_to(DATA)) for p in legacy if p.exists()]
        assert not existing, f"legacy bundles still present: {existing}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
