# Missale Romanum — JSON corpus

A complete, queryable, multilingual JSON representation of the **Roman Missal (Ordinary Form)** — every Mass formulary, the Sacred Triduum's special liturgies, the General Instruction, the Prefaces and Eucharistic Prayers, and the sanctoral calendar.

- **796 Masses** spanning the proper of time (advent, christmas, lent, holy-week, easter, ordinary-time, solemnities), the proper of saints (12 monthly buckets + 12 regional propers — Brazil, Argentina, Chile, Uruguay, Spain, France, USA, Africa, Nigeria, Argentina-Chile, Spanish-speaking, German-speaking, Religious Orders), the commons (BVM, martyrs, pastors, doctors, virgins, saints, dedication), the rituals (for the dead, for various needs), and the votive Masses.
- **75 Prefaces** + **10 Eucharistic Prayers** + Order of Mass parts in the library.
- **Five special-rite liturgies fully structured**: Palm Sunday with its three Entrance Forms, the Holy Thursday Chrism Mass with the Renewal of Priestly Promises, the Lord's Supper with the Washing of Feet and the Transfer of the Blessed Sacrament, the Good Friday Celebration of the Lord's Passion with the **ten Solemn Intercessions** typed (`I`–`X`), and the Easter Vigil with its **four typed parts** — Service of Light, Liturgy of the Word (with the **seven Old Testament readings** as typed units, each with its proper Collect and optional alternative), Baptismal Liturgy (with the Renewal of Baptismal Promises as a typed Q&A sequence), and Liturgy of the Eucharist.
- **Seven languages** at every level: Latin (la), Castilian Spanish (es), English (en), Brazilian Portuguese (pt-BR), Italian (it), French (fr), German (de).
- **Validated** against `schema/missal.schema.json` (JSON Schema 2020-12). Cross-references resolve, IDs are unique, every language tag is supported, no HTML residue.

This corpus is the primary input artifact for the Ember app and is intended to be reused by other Catholic / liturgical projects.

## Quick start

```bash
# Browse the data with the query CLI
python scripts/query.py mass tempore.advent.week-1.sunday
python scripts/query.py preface preface.pf056
python scripts/query.py saints --month 3 --text
python scripts/query.py search "Hosanna"
python scripts/query.py triduum --lang la

# Validate the corpus
python scripts/validate.py
```

Or load from any language with the schema:

```python
import json
from pathlib import Path

DATA = Path("data")
mass = next(
    m for m in json.loads((DATA / "masses/tempore/advent.json").read_text())["masses"]
    if m["id"] == "tempore.advent.week-1.sunday"
)
print(mass["collect"]["body"]["plain"]["en"])
```

```typescript
import type { Mass, MassesFile } from "./schema/missal";

const file: MassesFile = require("./data/masses/tempore/advent.json");
const sundayI = file.masses.find(m => m.id === "tempore.advent.week-1.sunday")!;
console.log(sundayI.collect?.body.plain?.en);
```

## Sources & scope

This corpus is a structural rewrite of the [pedropasinn/Missale_romanum](https://github.com/pedropasinn/Missale_romanum) HTML repository. **Everything from the missal proper is included.** Specifically:

| Source path                                | Status                                    |
|--------------------------------------------|-------------------------------------------|
| `misal_v2/m_<lang>/{ordinario,tiempos,santos,comunes_votivas,lecturas,prefacios,plegarias_euc}/*.html` | ✓ converted into `data/masses/`, `data/library/` |
| `misal_v2/m_estructura/**`                 | ✓ used to derive structure & cross-language alignment |
| `misal_v2/igmr/igmr_<lang>.html`           | ✓ converted into `data/igmr/<lang>.json` |
| `misal_v2/sacerdotale/sacerdotale_<lang>*.html` | ✓ converted into `data/sacerdotale/<lang>.json` |

**Deliberately excluded from this corpus:**

| Source path                       | Why excluded                                              |
|-----------------------------------|-----------------------------------------------------------|
| `devocionario.html` (608 KB)      | Mixed-language devotional collection (Rosary in 9+ languages including Russian/Arabic/etc., Sacerdotale duplicate). Not strictly missal content. |
| `oracoes.html` (237 KB)           | Portuguese-only devotional prayer collection. Not strictly missal content. |
| `feria_actual.html`, `feria_liturgica.js` | Calendar UI + computational logic. The data this corpus provides is consumed by such an engine, not provided by it. |
| `home_clean.html`, `index.html`, `preferencias.html`, `politica_misal.html`, `ayuda.html`, `ayudaweb.html` | Application UI scaffolding (settings, help, navigation). |
| `mis_funciones_*.js`, `missal_kindle*.js`, `home_clean.js`, `autocolumn.js` | Application JavaScript code. |
| `*.css`, `cordova*.js`, `jquery*.js`, `iscroll*.js`, `plugins/`, `images/` | Styling, frameworks, icons. |

## Repository layout

```
missal_to_json/
├── README.md                               # this file
├── data/                                   # the JSON corpus (consume this)
│   ├── index.json                          # schema version, totals, file map
│   ├── calendar.json                       # tempore + sanctorale flat index
│   ├── saints.json                         # focused saints catalog (date, rank, description)
│   ├── triduum.json                        # the 8 Holy Week / Triduum liturgies
│   ├── provenance.json                     # mass id → source HTML traceback
│   ├── masses/
│   │   ├── tempore/{advent,christmas,lent,holy-week,easter,ordinary-time,solemnity}.json
│   │   ├── sanctorale/{01..12, africa, argentina, argentina-chile, brazil, chile,
│   │   │              france, german-speaking, nigeria, religious-orders, spain,
│   │   │              united-states, uruguay}.json
│   │   ├── common/{blessed-virgin-mary,martyrs,doctors-of-the-church,pastors,saints,virgins,dedication-of-church}.json
│   │   ├── ritual/{for-the-dead,various-needs}.json
│   │   └── votive/votive-masses.json
│   ├── library/
│   │   ├── prefaces.json                   # 75 prefaces
│   │   ├── eucharistic-prayers.json        # 10 EPs (Roman Canon, II–IV, VN I–IV, Rec I–II)
│   │   └── ordinary.json                   # Order of Mass + Universal Prayer + Solemn Blessings
│   ├── igmr/<lang>.json                    # General Instruction of the Roman Missal (6 languages)
│   └── sacerdotale/<lang>.json             # Priest's manual / appendices (6 languages)
│
├── schema/
│   ├── missal.schema.json                  # JSON Schema (Draft 2020-12)
│   └── missal.d.ts                         # TypeScript types
│
├── scripts/
│   ├── convert.py                          # source HTML → out/ (intermediate)
│   ├── refine.py                           # out/ → data/ (domain-shaped)
│   ├── validate.py                         # validate data/ against schema
│   └── query.py                            # CLI to inspect data/
│
└── docs/
    ├── architecture.md                     # pipeline architecture + design rationale
    ├── audits.md                           # how to run the audit/TDD/fix loop
    ├── examples.md                         # copy-paste examples by use case
    └── special-rites.md                    # Triduum schema explanation
```

## The data model

Everything is a **Mass** (or a Mass-shaped liturgy).  A Mass has standard parts when present:

```jsonc
{
  "id": "sanctorale.01-02",
  "group": "sanctorale",
  "rite": "mass",                       // or one of 5 special-rite values
  "date": { "month": 1, "day": 2 },
  "rank": "memorial",
  "title": Localized,
  "description": Localized,             // for saints: biographical
  "rankLocalized": Localized,
  "entranceAntiphon": Antiphon,
  "collect": Prayer,
  "readings": Readings,                 // tree by lectionary cycle
  "prayerOverOfferings": Prayer,
  "preface": Prayer | PrefaceRef,       // ref into library/prefaces.json
  "communionAntiphon": Antiphon,
  "postcommunion": Prayer,
  "prayerOverPeople": Prayer,
  "parts": { … }                        // present only for special rites
}
```

Where:

- `Localized` = `{ la, es, en, pt-BR, it, fr, de }` — every key optional.
- `Prayer.body` is a `RichText`: `{ plain: Localized, lines: { lang: Line[] } }` with `Line = Segment[]`. Segments are purely semantic: `text`, `rubric`, `reference`, `italic`, `response`, `signOfCross`, `dropCap`. No presentational types.
- `Antiphon` = `{ citation?, body }`.
- `Reading` = `{ label, introduction, citation, summary, body, conclusion, response }`.
- `Readings` = `{ A, B, C, I, II, default }` — Sundays use A/B/C; weekdays use I/II; default when no cycle distinction.
- `PrefaceRef` = `{ prefaceRef: "preface.pf056", label, excerpt }` — resolvable into `library/prefaces.json`.

See **`schema/missal.schema.json`** and **`schema/missal.d.ts`** for the full type surface.

## Special rites (Triduum)

Five Holy Week liturgies have additional structure. They're identified by their `rite` field, populated with `parts.<key>`, and carry typed sub-fields where applicable.

| Mass id                                       | `rite`                          | Typed parts                                                                                                                                           |
|-----------------------------------------------|---------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tempore.holy-week.palm-sunday`               | `mass-with-procession`          | `commemorationOfTheLordsEntrance` (with three Entrance Forms), `mass`                                                                                  |
| `tempore.holy-week.chrism-mass`               | `chrism-mass`                   | `renewalOfPriestlyPromises`                                                                                                                            |
| `tempore.holy-week.lords-supper`              | `lords-supper`                  | `washingOfFeet`, `transferOfTheBlessedSacrament`                                                                                                       |
| `tempore.holy-week.good-friday`               | `celebration-of-the-passion`    | `liturgyOfTheWord` (with `solemnIntercessions: SolemnIntercession[]`), `adorationOfTheCross`, `holyCommunion`                                          |
| `tempore.holy-week.easter-vigil`              | `easter-vigil`                  | `serviceOfLight`, `liturgyOfTheWord` (with `oldTestamentReadings: OTReadingUnit[]`), `baptismalLiturgy` (with `renewalOfBaptismalPromises`), `liturgyOfTheEucharist` |

Each `Part` has a `heading: Localized` and an ordered `content: Section[]` tree. Sections nest by source heading level; leaves are localized rich-text blocks. Where the content has a known typed shape (the 10 Solemn Intercessions, the 7 OT Vigil readings, the 19 Baptismal Q&A), the typed structure hangs off the Part itself — you don't have to navigate the tree.

```jsonc
// Good Friday
{
  "id": "tempore.holy-week.good-friday",
  "rite": "celebration-of-the-passion",
  "parts": {
    "liturgyOfTheWord": {
      "heading": Localized,
      "content": Section[],
      "solemnIntercessions": [
        { "type": "solemn-intercession", "ordinal": "I", "forWhom": Localized,
          "invitation": Localized, "silenceRubric": Localized,
          "collect": Localized, "response": Localized },
        // ... II, III, ..., X
      ]
    },
    "adorationOfTheCross": { … },
    "holyCommunion": { … }
  }
}
```

## ID conventions

Canonical IDs are kebab-case dotted, lowercase:

| Form                                                     | Example                                       |
|----------------------------------------------------------|------------------------------------------------|
| `tempore.<season>.week-<n>.<weekday>`                    | `tempore.advent.week-1.sunday`                |
| `tempore.holy-week.<liturgy>`                            | `tempore.holy-week.easter-vigil`              |
| `tempore.solemnity.<name>`                               | `tempore.solemnity.christ-the-king`           |
| `sanctorale.<MM>-<DD>[.<scope>]`                         | `sanctorale.01-02`, `sanctorale.05-13.brazil` |
| `common.<subgroup>.<source-id>`                          | `common.martyrs.mart002`                      |
| `ritual.<subgroup>.<source-id>` / `votive.…`             | `ritual.for-the-dead.dif001`                  |
| `eucharistic-prayer.<n>` / `preface.<id>` / `ordinary.…` | `eucharistic-prayer.3`, `preface.pf056`        |

## Languages

BCP-47 codes throughout: `la`, `es`, `en`, `pt-BR`, `it`, `fr`, `de`. Coverage varies by language — Spanish and Latin are most complete; German is partially translated in the upstream source (expect gaps on minor saints' days); English is fully covered for universal-calendar saint titles + prayers (200/201) but the upstream HTML lacks English biographical sketches for almost all saints (only 2 of 378 are present in source) and does not include English versions of regional/scope-specific saints (Argentine, Brazilian, Spanish, etc.). For sanctorale: ~99% of universal saints have English title + collect; ~58% have English preface; antiphons (entrance/communion) are present for ~24% of universal saints. These gaps reflect the upstream `pedropasinn/Missale_romanum` HTML — they are not extractor bugs. Importing the missing English content would require a separate authoritative source (e.g., USCCB English Roman Missal).

## Build pipeline

```
source HTML                       →  out/  (raw, HTML-shaped)  →  data/  (domain-shaped, the artifact)
[source/Missale_romanum]             [convert.py]                  [refine.py]
                                     ~390 MB · 3000 files          ~75 MB · ~60 files
```

To regenerate from scratch, run the orchestrator:

```bash
./build.sh
```

`build.sh` clones `pedropasinn/Missale_romanum` into `source/` (if missing), creates a `.venv`, installs deps, then runs `convert.py → refine.py → validate.py → pytest`. Pass `--no-tests` or `--no-validate` to skip those steps.

`source/` and `out/` are gitignored. `data/` is the committed artifact.

## What's preserved, what's deferred

Preserved fully:
- Every Mass formulary in 7 languages with structured antiphons, readings (per cycle), prayers, prefaces (or refs).
- Every special-rite Triduum liturgy with typed parts and (where applicable) typed sub-fields.
- The 10 Solemn Intercessions of Good Friday as typed structures: `{ ordinal, forWhom, invitation, silenceRubric, collect, response }`.
- The 7 OT readings of the Easter Vigil as typed `{ rubric, collect, alternativeCollect? }` units.
- The 19 Q&A exchanges of the Renewal of Baptismal Promises in order, per language.
- The 10 Eucharistic Prayers and 75 Prefaces with bodies, titles, and back-references from each Mass that uses them.
- The General Instruction of the Roman Missal in 6 languages.
- The biographical descriptions of 229 saints in up to 7 languages.

Deferred to a future revision (data is preserved as rich text — consumers can extract themselves):
- Splitting the responsorial psalm into refrain + verses.
- Voicing the Passion narratives (Narrator / Christ / Synagogue) — not marked in source.
- Splitting alternative prayers connected by `Or:` markers into typed `alternatives[]` arrays inside each `Prayer`.
- Decomposing the Reproaches (Improperia) into typed alternating-choir structures.
- Modeling the Easter Proclamation (Exsultet) as a separate preface-like structure.

See `docs/special-rites.md` for the full rationale.
