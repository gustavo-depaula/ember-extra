# novus-ordo-missae

Roman Missal (Ordinary Form) extracted from [pedropasinn/Missale_romanum](https://github.com/pedropasinn/Missale_romanum) HTML and structured as JSON. 7 languages: `la`, `en`, `es`, `pt-BR`, `it`, `fr`, `de`. 777 mass formularies + 75 prefaces + 10 eucharistic prayers + Order of Mass + calendar + saints catalog + Triduum bundle.

**This is pure data extraction. No app code, no UI, no shipping concerns.** The output `data/` is the artifact.

## Copyright posture

Project's own contributions (schema, scripts, docs) are public domain (see top-level `LICENSE`). The underlying liturgical texts in `data/` are derivative work; the texts themselves may not be in the public domain. Do not assert ownership over text content.

## Layout

```
.
├── build.sh                        # orchestrator: clone → venv → convert → refine → validate → test
├── source/Missale_romanum/         # gitignored; clone of upstream HTML
├── out/                            # gitignored; intermediate raw JSON from convert.py
├── data/                           # committed; final structured corpus
├── schema/                         # JSON Schema (Draft 2020-12) for the corpus
├── scripts/
│   ├── convert.py                  # source HTML → out/
│   ├── refine.py                   # out/ → data/  (THE post-processor — most work happens here)
│   ├── validate.py                 # validates data/ against schema + cross-refs
│   ├── query.py                    # CLI to inspect data/
│   └── test_*.py                   # pytest suite (776 tests, all must stay green)
└── docs/
    ├── architecture.md             # pipeline architecture
    ├── audits.md                   # the audit/TDD/fix loop methodology — READ THIS BEFORE STARTING WORK
    ├── examples.md                 # consumer snippets
    └── special-rites.md            # Triduum schema explanation
```

## Pipeline

```
source/ ──convert.py──▶ out/ ──refine.py──▶ data/ ──validate.py──▶ ✓
```

Run end-to-end:

```bash
./build.sh                  # full pipeline (creates venv if missing)
./build.sh --no-tests       # skip pytest
./build.sh --no-validate    # skip schema validation
```

Or manually (after `./build.sh` set up `.venv`):

```bash
.venv/bin/python scripts/refine.py     # rebuild data/
.venv/bin/python scripts/validate.py   # must end "0 errors, 0 warnings"
.venv/bin/python -m pytest scripts/ -q # 776 tests pass
```

## How to work in this project

**Everything goes through `scripts/refine.py`.** That's the post-processor that turns the raw conversion into the clean corpus. Cycle 18+ of audit/TDD/fix work is captured there as named functions; `_post_process_mass(mass)` is the orchestrator that calls them in sequence.

**Run audits via the loop in `docs/audits.md`.** That doc is the methodology for finding new defect classes, writing failing tests, fixing them, and verifying production data hits zero. Don't invent your own ad-hoc workflow — the loop is battle-tested across 23+ cycles.

**Fixes follow two patterns:**

1. **Per-string fix** — `(text: str, lang: str) -> str`, wired via `_walk_lang_strings(mass, fn)` in `_post_process_mass` (mass-scope) or via `_apply_universal_text_fixes` in `_post_process_payload` (payload-wide, hits libraries too).
2. **Per-tree fix** — `(mass: dict) -> None` that mutates structurally, registered in the `_post_process_mass` orchestrator at the right point in the sequence.

Naming: `_fix_<defect>` for the unit, `_<thing>_in_mass` for the orchestrator wrapper.

**Latin OCR fixes** extend `_LA_OCR_FIXES` (regex table) or `_LA_DIACRITIC_WORDS` (word-list). Both gated to `lang == 'la'` in `_scrub_string`.

**Tests live in `scripts/test_postprocess.py`.** One class per defect family. TDD red-then-green is the norm.

## Hard rules

- **`validate.py` must always hit 0 errors, 0 warnings.** Non-negotiable.
- **All 776 tests must stay green** before committing.
- **Fixes must be idempotent** — running `refine.py` twice produces byte-identical output. Use `dict.fromkeys(...)` not `set(...)` for ordering. Don't write regexes that re-match their own output.
- **Spot-check audit-agent claims before acting.** Agents hallucinate counts. Run a one-liner against `data/` to verify the count is real.
- **Don't fight source data.** If a German body is empty in the upstream HTML, the post-processor can't conjure one. Note as a source-data limit; move on.
- **Walk the tree, not just the slot.** Defects often live in deeply-nested `parts[X].content[i].content[j].body.lines.<lang>[k]`. Don't write fixes that only touch top-level mass slots.

## Data shape (key facts)

- Mass IDs are dotted: `tempore.advent.week-1.sunday`, `sanctorale.01-02`, `tempore.holy-week.easter-vigil`.
- Localized fields are dicts: `{la, en, es, pt-BR, it, fr, de}`. Not all langs are filled for every field — partial localization is normal.
- Rich text comes in pairs: `body.plain.<lang>` (string) and `body.lines.<lang>` (segment array of `{type, text}`). Segment types: `text`, `rubric`, `reference`, `italic`, `response`, `signOfCross`, `dropCap`.
- Reading cycles: `A`/`B`/`C` for Sundays + solemnities; `default` for proper-season weekdays and fixed feasts. (`I`/`II` ferial cycles are not yet extracted — gap, not a feature.)
- Mass formularies are bundled per file: `data/masses/<group>/<bucket>.json`, each holding 30–60 masses inside `{season, count, masses[]}`. (Refactor to per-mass files is planned but not yet executed.)
- Library docs (`prefaces.json`, `eucharistic-prayers.json`, `ordinary.json`) live under `data/library/`. Already keyed by canonical ID.
- `data/calendar.json`, `data/saints.json`, `data/triduum.json`, `data/index.json`, `data/provenance.json` are derived/index files at the corpus root.

## Conventions

- **Python 3.11.** No exotic versions.
- **Style:** functional, top-level `def`, snake_case, no classes for the post-processor (only for tests).
- **Comments:** rare. Explain *why* if non-obvious; never *what*. Most fixes are self-explanatory by name.
- **Don't write planning/journal/changelog docs.** That clutter was deleted; don't recreate it. Use git log + commit messages for history.
- **Prefer editing existing files over creating new ones.** New helpers go in `refine.py`, new tests in `test_postprocess.py`.
- **No co-author trailers in commit messages.**

## What NOT to do

- Don't add app integration — that lives in `gustavo-depaula/ember`, not here.
- Don't add new MD docs unless explicitly asked. We just deleted 7 stale ones.
- Don't claim copyright over the underlying texts.
- Don't bypass `_post_process_mass` — every mass write must go through it.
- Don't break idempotency.
- Don't introduce new dependencies without need. The current set is `beautifulsoup4`, `lxml`, `jsonschema`, `pytest`. That's it.

## Where to look first

Doing audit/TDD/fix work? → `docs/audits.md`
Understanding the pipeline? → `docs/architecture.md`
Writing consumer code? → `docs/examples.md`
Touching the Triduum (Palm Sunday, Easter Vigil, etc.)? → `docs/special-rites.md`
Looking at the schema? → `schema/missal.schema.json`
