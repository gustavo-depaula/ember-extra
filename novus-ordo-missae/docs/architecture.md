# Architecture

## What this corpus represents

The Roman Missal (Ordinary Form). It is *not* a calendar of who-celebrates-which-Mass-on-which-day; it is a **library** of formularies, fixed elements, and special liturgies. The calendar — the function `(date, year-cycle, weekday-cycle) → MassFormulary` — is left to consumers, since it requires applying the General Roman Calendar's rules of precedence.

## Three classes of content

1. **Mass formularies** — `data/masses/**/*.json`. One *Mass* object per formulary, identified by a canonical kebab-dotted ID. Standard parts (entrance antiphon, collect, readings, …) are top-level fields. The 657 formularies cover:
   - the proper of time (advent / christmas / lent / holy-week / easter / ordinary-time / solemnity)
   - the proper of saints (12 universal monthly buckets + regional propers for Africa, Argentina, Brazil, France, Religious Orders)
   - the commons (BVM, martyrs, doctors, pastors, virgins, saints, dedication of a church)
   - rituals (for the dead, for various needs)
   - votive Masses

2. **Special-rite liturgies** — five Triduum + Palm Sunday liturgies need additional structure that doesn't fit the standard schema (the Easter Vigil is four parts, Good Friday isn't even a Mass). These get a `rite` discriminator and a `parts` dict whose keys are canonical liturgical sections. Each part has a `Section[]` content tree; some carry typed sub-fields (`solemnIntercessions`, `oldTestamentReadings`, `renewalOfBaptismalPromises`).

3. **Library** — `data/library/`. Independent reference texts: 75 Prefaces, 10 Eucharistic Prayers, the Order of Mass, Universal Prayer formulas, Solemn Blessings. Mass formularies that don't embed their preface use `prefaceRef: "preface.pfNNN"` as a pointer.

Plus three derived indexes:

- **`calendar.json`** — flat list of every Mass with minimal metadata (date, season, week, weekday, scope, rank). Use to drive a calendar UI.
- **`saints.json`** — 275-entry sanctoral catalog with biographical descriptions; same data as `masses/sanctorale/*.json` but *without* prayer bodies, so it's small (~700 KB) and easy to load.
- **`triduum.json`** — the 8 Holy Week / Triduum liturgies bundled together for convenience.
- **`provenance.json`** — every Mass id → the source HTML file it came from. Useful for debugging or for going back to the upstream when the data here is incomplete.

## Pipeline

```
source HTML               →  out/  (raw)             →  data/  (domain)
[source/Missale_romanum]     [convert.py]               [refine.py]
                             one JSON per language      one JSON bundle per group/category
                             + one per estructura       with cross-language merging,
                                                        typed extraction, special-rite
                                                        recognition, schema-conformant output
```

`convert.py` is a faithful HTML-to-JSON pass that parses the source's `hijo_N` content blocks per language and the parallel `padre_N` "structure" files, joining them by index. Output mirrors the source.

`refine.py` is the domain pass. It takes the v1 output and produces a clean domain model:

- Slot types like `x_colecta` become field names like `collect`.
- Source codes like `cast` / `engl` / `port` become BCP-47 tags `es` / `en` / `pt-BR`.
- Ordinals like `A010` (Advent, week 1, Sunday) become canonical IDs like `tempore.advent.week-1.sunday`.
- Saints' titles, ranks, and biographical descriptions are extracted from the title block by re-parsing the source HTML with structural awareness.
- Reading sections (label / introduction / citation / body / conclusion / response) are extracted from per-paragraph CSS classes (`ReadingGospelTitle`, `Areadingfrom`, etc.) and the inline `<span class="alindcha">` citation.
- Triduum days are recognized by title pattern, their generic content is parsed into a heading tree, and where the source provides typed structures (Solemn Intercessions ordinals, OT-reading rubric/collect pairs, baptismal Q&A), they're promoted into typed arrays on the relevant Part.

## Naming choices and why

- **Kebab-case dotted IDs.** Every ID uses lowercase letters, digits, hyphens, and dots only. No underscores. This keeps IDs URL-safe and consistent.
- **BCP-47 language tags.** `pt-BR` rather than `pt` because the source is specifically Brazilian Portuguese; differentiating from Continental Portuguese is liturgically relevant. Latin uses `la` (the Vatican-recommended ISO 639-1 code).
- **Camel-case field names.** `prayerOverOfferings`, `entranceAntiphon`, `solemnIntercessions`. Easy to consume in JSON / TS / Python.
- **Semantic-only segments.** `text`, `rubric`, `reference`, `italic`, `response`, `signOfCross`, `dropCap`. No `bold`, no `paragraph_*`, no raw `<br>`. Line breaks are first-class via the `lines: Line[]` structure on every `RichText`.
- **Cross-references are by canonical ID, not by file path.** `prefaceRef: "preface.pf056"` is stable; `library/prefaces.json` is the canonical resolver location, but a consumer could reorganize files freely as long as IDs stay unique.
- **`title` and `description` separated for sanctoral entries.** `title` is the saint's name and date label, ready for display ("Saints Basil the Great and Gregory Nazianzen, bishops and doctors of the Church"). `description` is the biographical italic prose, a separate field consumers can show on demand.
- **Provenance as a separate file.** Every Mass id maps to a source path, but those paths shouldn't pollute the mass objects. They live in `provenance.json`.

## What's deliberately NOT modeled

- **The General Roman Calendar.** Movable feasts, precedence rules, lectionary year cycle (A/B/C) — those are computational. This corpus provides the *data* a calendar engine needs, not the engine itself.
- **The lectionary itself.** Reading bodies for some Sundays' gospels are absent because the source HTML structures gospels differently from OT readings. Where present, they're typed; where absent, the field simply isn't there.
- **The Gradual / Antiphonal.** Chant melodies and tonus settings — out of scope.
- **Adaptations beyond what's in the source.** The corpus reflects what `pedropasinn/Missale_romanum` contains, not the full breadth of every conference's Roman Missal.

## Iteration history

This corpus went through three major iterations:

1. **v0 → v1**: HTML extraction. Faithful to source, mirrors the `hijo`/`padre` structure with `x_*` slot types. ~390 MB across 3000 files.

2. **v1 → v2**: Domain shape. ISO language codes, kebab-dotted IDs, semantic-only rich text, structured antiphons / readings / prefaces / saints / calendar. ~50 MB across ~150 files.

3. **v2 → v3 (current)**: Special rites. Discriminator + typed parts for the Triduum. Solemn Intercessions, OT Vigil readings, Renewal of Baptismal Promises, and Palm Sunday procession forms typed. JSON Schema and TypeScript types published. Validation script. Query CLI.

See `docs/audits.md` for the post-processing methodology used to refine the corpus.
