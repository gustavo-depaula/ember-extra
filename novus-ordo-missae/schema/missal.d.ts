/**
 * TypeScript types for the Missale Romanum corpus.
 *
 * Companion to `missal.schema.json`.  Languages: la, es, en, pt-BR, it, fr, de.
 */

export type Language = "la" | "es" | "en" | "pt-BR" | "it" | "fr" | "de";

export type Localized = Partial<Record<Language, string>>;

export type SegmentType =
  | "text"
  | "rubric"
  | "reference"
  | "italic"
  | "response"
  | "signOfCross"
  | "dropCap";

export interface Segment {
  type: SegmentType;
  text?: string;
}

export type Line = Segment[];

export interface RichText {
  /** Joined plain text per language. */
  plain?: Localized;
  /** Per-language list of lines, preserving the source's prayer-line formatting. */
  lines?: Partial<Record<Language, Line[]>>;
}

export interface Antiphon {
  citation?: Localized;
  body: RichText;
}

export interface Prayer {
  body: RichText;
  /** Source's localized label (e.g. "Collect"/"Coleta"). */
  label?: Localized;
  /** Scripture citation when the Prayer carries one (e.g. gospelAcclamation
   *  verse-before-the-Gospel reference, "Mt 11, 29ab"). */
  citation?: Localized;
}

/** A reference to a preface in `library/prefaces.json`. */
export interface PrefaceRef {
  /** Ordered preface IDs: proper first, then alternatives. The Roman Missal
   * lets the priest pick from any of these on a given day (e.g. all 5
   * paschal prefaces are usable on Easter weekdays). */
  prefaceRefs: string[];     // e.g. ["preface.pf016", "preface.pf017"]
  label?: Localized;
  excerpt?: Localized;
}

export interface Reading {
  /** "First Reading" / "Gospel" / etc. */
  label?: Localized;
  /** "A reading from the Book of the Prophet Isaiah" */
  introduction?: Localized;
  /** Scripture citation, e.g. "2:1-5" */
  citation?: Localized;
  summary?: Localized;
  body: RichText;
  /** "The Word of the Lord" */
  conclusion?: Localized;
  /** "Thanks be to God" */
  response?: Prayer;
}

export interface ResponsorialPsalm {
  body: RichText;
  label?: Localized;
  /** Psalm citation, e.g. "Ps 50, 3-4. 5-6a. 12-13. 14 et 17 (: cf. 3a)" */
  citation?: Localized;
}

export interface ReadingSet {
  firstReading?: Reading;
  responsorialPsalm?: ResponsorialPsalm;
  secondReading?: Reading;
  /** Easter Sunday and Pentecost only: Victimae Paschali laudes /
   * Veni Sancte Spiritus. Falls between the second reading and the
   * gospel acclamation in the rite. */
  sequentia?: Reading;
  gospelAcclamation?: Prayer;
  gospel?: Reading;
}

export type LectionaryCycle = "A" | "B" | "C" | "I" | "II" | "default";

export type Readings = Partial<Record<LectionaryCycle, ReadingSet>>;

export type Section =
  | {
      type: "section";
      level?: number;
      heading: Localized;
      content?: Section[];
    }
  | {
      type: "block";
      body: RichText;
    };

export interface SolemnIntercession {
  type: "solemn-intercession";
  ordinal: string; // I, II, III, ..., X
  forWhom: Localized;
  invitation?: Localized;
  silenceRubric?: Localized;
  collect?: Localized;
  response?: Localized;
}

export interface OTReadingUnit {
  type: "ot-reading-unit";
  ordinal: number; // 1..7
  rubric?: RichText;
  collect?: RichText;
  alternativeCollect?: RichText;
}

export interface RenewalOfBaptismalPromises {
  /** Per-language ordered list of {role, text} exchanges. */
  questions: Partial<Record<Language, { role: string; text: string }[]>>;
}

export interface Part {
  heading: Localized;
  content: Section[];
  /** Good Friday only. */
  solemnIntercessions?: SolemnIntercession[];
  /** Easter Vigil only. */
  oldTestamentReadings?: OTReadingUnit[];
  /** Easter Vigil only. */
  renewalOfBaptismalPromises?: RenewalOfBaptismalPromises;
}

export type MassGroup = "tempore" | "sanctorale" | "common" | "ritual" | "votive";

export type Rite =
  | "mass"
  | "mass-with-procession"
  | "chrism-mass"
  | "lords-supper"
  | "celebration-of-the-passion"
  | "easter-vigil";

export type Season =
  | "advent"
  | "christmas"
  | "lent"
  | "holy-week"
  | "easter"
  | "ordinary-time"
  | "solemnity";

export type Weekday =
  | "sunday"
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday";

export type Rank = "solemnity" | "feast" | "memorial" | "optional-memorial";

export interface Mass {
  /** Canonical kebab-dotted ID, e.g. `tempore.advent.week-1.sunday`, `sanctorale.01-02`. */
  id: string;
  group: MassGroup;
  /** When omitted, treat as "mass". */
  rite?: Rite;

  // Tempore-specific
  season?: Season;
  weekIndex?: number;
  weekday?: Weekday;

  // Sanctorale-specific
  date?: { month: number; day: number };
  dateSuffix?: string;
  scope?: string;

  // Commons / votives / ritual
  subgroup?: string;

  title?: Localized;
  description?: Localized;
  rank?: Rank;
  rankLocalized?: Localized;

  // Standard Mass parts
  entranceAntiphon?: Antiphon;
  penitentialAct?: Prayer;
  gloriaInstruction?: Prayer;
  collect?: Prayer;
  creedInstruction?: Prayer;
  readings?: Readings;
  /** Palm Sunday only — the Gospel of the Lord's Entrance into Jerusalem read at the procession, separate from the Passion Gospel of the Mass. */
  processionGospel?: Readings;
  prayerOverOfferings?: Prayer;
  preface?: Prayer | PrefaceRef;
  communionAntiphon?: Antiphon;
  postcommunion?: Prayer;
  prayerOverPeople?: Prayer;

  // Special-rite parts (only when rite !== "mass")
  parts?: Record<string, Part>;
}

export interface Preface {
  id: string;            // e.g. "preface.pf056"
  ordinal?: string;
  title?: Localized;
  body: RichText;
}

export interface EucharisticPrayer {
  id: string;            // e.g. "eucharistic-prayer.3"
  title?: Localized;
  body: RichText;
}

export interface OrdinaryPart {
  id: string;            // e.g. "ordinary.ordinario"
  title?: Localized;
  body: RichText;
}

export interface SaintEntry {
  id: string;            // e.g. "sanctorale.01-02"
  date?: { month: number; day: number };
  scope?: string;
  title?: Localized;
  rank?: Rank;
  rankLocalized?: Localized;
  description?: Localized;
}

export interface CalendarEntry {
  id: string;
  title?: Localized;
  season?: Season;
  weekIndex?: number;
  weekday?: Weekday;
  date?: { month: number; day: number };
  scope?: string;
  rank?: Rank;
}

export interface Calendar {
  tempore: CalendarEntry[];
  sanctorale: CalendarEntry[];
}

export interface Index {
  schemaVersion: string;
  generatedAt: string;
  languages: Language[];
  languageNames: Record<Language, string>;
  groups: Record<MassGroup, number>;
  rites: Rite[];
  totals: {
    masses: number;
    saintsCatalog: number;
    prefaces: number;
    eucharisticPrayers: number;
    ordinaryParts: number;
  };
  files: Record<string, string>;
}

export interface MassesFile {
  /** Bucket key — "advent" / "01" / "martyrs" / etc. */
  season?: Season | string;
  bucket?: string;
  subgroup?: string;
  group?: MassGroup;
  count: number;
  masses: Mass[];
}

export interface PrefacesFile {
  count: number;
  prefaces: Preface[];
}

export interface EucharisticPrayersFile {
  count: number;
  eucharisticPrayers: EucharisticPrayer[];
}

export interface OrdinaryFile {
  count: number;
  parts: OrdinaryPart[];
}

export interface SaintsFile {
  count: number;
  saints: SaintEntry[];
}

export interface TriduumFile {
  count: number;
  masses: Mass[];
}
