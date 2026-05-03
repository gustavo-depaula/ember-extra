# Special rites in the Missale Romanum

The standard `Mass` schema (entranceAntiphon → collect → readings → prayerOverOfferings → preface → communionAntiphon → postcommunion) covers ~99% of the Roman Missal. The remaining ~1% are structurally distinct liturgies that need their own modeling. This document maps them and proposes schemas.

## Catalog of special liturgies in this corpus

Found in `tiempos_semanasta*` (Holy Week / Sacred Triduum):

| Source file | Day-id | Liturgy | Standard Mass? |
|---|---|---|---|
| tiempos_semanasta | SS00 | Palm Sunday of the Lord's Passion | Mass + Procession |
| tiempos_semanasta | SS01..SS03 | Holy Mon / Tue / Wed | Standard Masses |
| tiempos_semanasta2 | SS04A | Holy Thursday Chrism Mass | Mass + Renewal of Priestly Promises + Blessing of Oils |
| tiempos_semanasta3 | SS04 | Holy Thursday Mass of the Lord's Supper | Mass + Washing of Feet + Transfer of Blessed Sacrament |
| tiempos_semanasta4 | SS05 | Good Friday Celebration of the Lord's Passion | **NOT a Mass** — distinct three-part liturgy |
| tiempos_semanasta5 | SS06 | Easter Vigil in the Holy Night | **Four-part liturgy** (Light, Word with 9 readings, Baptism, Eucharist) |

The non-Triduum specials in this corpus (`comunes_votivas/`) — Funeral Masses, Votive Masses, Masses for Various Needs — are structurally normal Masses with different propers; they fit the standard schema. They don't need separate modeling.

## Design — common envelope

Every Mass/Liturgy gets a `rite` discriminator. Default is `"mass"`. Special values:

- `"mass-with-procession"` — Palm Sunday
- `"chrism-mass"` — Holy Thursday Chrism Mass
- `"lords-supper"` — Holy Thursday Evening
- `"celebration-of-the-passion"` — Good Friday (the only entry that is NOT a Mass)
- `"easter-vigil"` — Holy Saturday night

Special rites add a `parts` dict whose keys identify the canonical liturgical sections. Each part has `{heading: Localized, content: Section[]}` where `Section` is a tree of `{type: "section" | "block", ...}` already used elsewhere.

The parts dict makes consumers select a section by name (`vigil.parts.serviceOfLight`) without parsing free text. The `content` tree below each part preserves source ordering and rubrics — when v3 wants to type the `Reproaches`, the `Litany of Saints`, the 10 `SolemnIntercession`s, etc., that work happens *inside* a part without reshaping consumers.

### Schema outline

```typescript
type Liturgy = {
  id: string
  group: "tempore" | "sanctorale" | "common" | …
  season?: "advent" | … | "holy_week"
  title: Localized
  rite: "mass" | "mass-with-procession" | "chrism-mass" | "lords-supper"
       | "celebration-of-the-passion" | "easter-vigil"

  // Standard Mass fields (when present, regardless of rite)
  entranceAntiphon?: Antiphon
  collect?: Prayer
  readings?: Readings
  prayerOverOfferings?: Prayer
  preface?: Prayer | PrefaceRef
  communionAntiphon?: Antiphon
  postcommunion?: Prayer
  prayerOverPeople?: Prayer

  // Special parts (only present when rite ≠ "mass")
  parts?: { [partKey]: Part }
}

type Part = {
  heading: Localized
  content: Section[]    // tree of {type: "section", level, heading, content[]} | {type: "block", body: RichText}
}
```

### Per-rite part keys

**Palm Sunday (`mass-with-procession`)**
- `commemorationOfTheLordsEntrance` — the procession with palms (one of three forms)
- `mass` — the Mass that follows (the standard fields like `collect` still appear at the top level)

**Chrism Mass (`chrism-mass`)**
- `renewalOfPriestlyPromises`
- `blessingOfTheOils` — three oils: of the Sick, of Catechumens, Sacred Chrism

**Lord's Supper (`lords-supper`)**
- `washingOfFeet` (Mandatum)
- `transferOfTheBlessedSacrament`

**Good Friday (`celebration-of-the-passion`)** — three explicit parts in the source, headed `FIRST PART:`, `SECOND PART:`, `THIRD PART:`
- `liturgyOfTheWord` — readings + Passion (St. John) + 10 Solemn Intercessions
- `adorationOfTheCross` — Showing of the Cross + Adoration with Reproaches and Hymn
- `holyCommunion` — communion from reserved Sacrament (no consecration)

**Easter Vigil (`easter-vigil`)** — four explicit parts headed `FIRST PART: …` etc.
- `serviceOfLight` — Lucernarium: blessing of fire, Paschal Candle preparation, procession, Exsultet
- `liturgyOfTheWord` — up to 9 readings (7 OT + Epistle + Gospel) each with Psalm and Collect
- `baptismalLiturgy` — Litany of Saints, Blessing of Water, Baptisms, Renewal of Promises, Sprinkling
- `liturgyOfTheEucharist` — standard Mass continuation

## Mapping strategy

1. Identify the rite by **title pattern** across all 7 languages (don't rely on day-ids; titles are the source's truth).
2. Run `extract_special_rites` to walk every `generic` slot and produce a `Section[]` tree headed by the source's H5 markers.
3. Walk the tree, top-level by top-level: when a section's heading matches a known part marker (e.g. `FIRST PART: …` in any language), route its subtree into the corresponding part key.
4. Sections that don't match a known part go under `parts.preamble` (introductory rubrics) or `parts.appendix` (concluding rubrics) so nothing is lost.

## Identifier overrides

Special days deserve canonical, readable IDs (numeric SS-codes are opaque):

| Day-id | Old v2 id | New canonical id |
|---|---|---|
| SS00 | `tempore.holy_week.00` | `tempore.holy_week.palm-sunday` |
| SS04A | `tempore.holy_week.04a` | `tempore.holy_week.chrism-mass` |
| SS04 | `tempore.holy_week.04` | `tempore.holy_week.lords-supper` |
| SS05 | `tempore.holy_week.05` | `tempore.holy_week.good-friday` |
| SS06 | `tempore.holy_week.06` | `tempore.holy_week.easter-vigil` |
| SS01..SS03 | `tempore.holy_week.01..03` | `tempore.holy_week.monday`, `.tuesday`, `.wednesday` |

## What this iteration delivers

- Top-level `rite` discriminator on every Mass.
- `parts` dict on Triduum days, with content preserved as nested sections + blocks.
- Renamed canonical IDs for the 5 unique liturgies + 3 weekdays of Holy Week.
- A new `out2/masses/triduum.json` bundle so the special liturgies are easy to find.

## What's deferred to v3

- Decomposing the **10 Solemn Intercessions** of Good Friday into a typed `SolemnIntercession[]` array (each with `forWhom`, `invitation`, `collect`).
- Splitting the **Easter Vigil's 7 OT readings** into a typed array `[{ordinal, reading, psalm, collect, alternativeCollect?}]`.
- Modeling the **Reproaches (Improperia)** as alternating choirs.
- Splitting the **Easter Proclamation (Exsultet)** into preface + body.
- Splitting **Renewal of Baptismal Promises** into question/response pairs.
- Voicing the **Passion narratives** (Narrator / Christ / Synagogue) — not in source HTML.
