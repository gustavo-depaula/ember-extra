# Examples

Copy-paste-ready snippets for common consumer tasks. Python and TypeScript shown side by side.

## 1. Get the Collect of the First Sunday of Advent in Portuguese

**Python**
```python
import json
m = next(
    x for x in json.load(open("data/masses/tempore/advent.json"))["masses"]
    if x["id"] == "tempore.advent.week-1.sunday"
)
print(m["collect"]["body"]["plain"]["pt-BR"])
```

**TypeScript**
```typescript
import advent from "./data/masses/tempore/advent.json";
import type { MassesFile } from "./schema/missal";

const sundayI = (advent as MassesFile).masses.find(
  m => m.id === "tempore.advent.week-1.sunday"
)!;
console.log(sundayI.collect?.body.plain?.["pt-BR"]);
```

## 2. Resolve a preface reference

```python
import json
mass = next(
    x for x in json.load(open("data/masses/sanctorale/01.json"))["masses"]
    if x["id"] == "sanctorale.01-02"
)
ref = mass["preface"]["prefaceRef"]   # "preface.pf056"

prefaces = json.load(open("data/library/prefaces.json"))["prefaces"]
preface = next(p for p in prefaces if p["id"] == ref)
print(preface["title"]["en"])         # "PREFACE OF HOLY PASTORS …"
print(preface["body"]["plain"]["en"])
```

## 3. List every saint in March with their ranks

```python
import json
saints = json.load(open("data/saints.json"))["saints"]
march = sorted(
    [s for s in saints if (s.get("date") or {}).get("month") == 3],
    key=lambda s: s["date"]["day"],
)
for s in march:
    rank = s.get("rank", "(no rank)")
    name = s.get("title", {}).get("en") or list(s.get("title", {}).values())[0]
    print(f"{s['date']['day']:2d}  {rank:18s}  {name[:60]}")
```

## 4. Get the 10 Solemn Intercessions of Good Friday in Latin and English

```python
import json
gf = next(
    m for m in json.load(open("data/triduum.json"))["masses"]
    if m["id"] == "tempore.holy-week.good-friday"
)
for si in gf["parts"]["liturgyOfTheWord"]["solemnIntercessions"]:
    print(f"\n{si['ordinal']}. {si['forWhom']['en']}  ⸻  {si['forWhom']['la']}")
    print(f"   collect.en: {si['collect']['en'][:120]}")
    print(f"   collect.la: {si['collect']['la'][:120]}")
```

## 5. Walk the seven Old Testament readings of the Easter Vigil with their proper Collects

```python
import json
ev = next(
    m for m in json.load(open("data/triduum.json"))["masses"]
    if m["id"] == "tempore.holy-week.easter-vigil"
)
for unit in ev["parts"]["liturgyOfTheWord"]["oldTestamentReadings"]:
    print(f"\nReading {unit['ordinal']}")
    print(f"  rubric.en : {unit['rubric']['plain']['en']}")
    print(f"  collect.en: {unit['collect']['plain']['en'][:120]}")
    if "alternativeCollect" in unit:
        print(f"  alternative collect: yes")
```

## 6. Walk the Renewal of Baptismal Promises (Q&A)

```python
import json
ev = next(
    m for m in json.load(open("data/triduum.json"))["masses"]
    if m["id"] == "tempore.holy-week.easter-vigil"
)
qa = ev["parts"]["baptismalLiturgy"]["renewalOfBaptismalPromises"]["questions"]["en"]
for ex in qa:
    role_pad = ex["role"].ljust(8)
    print(f"  {role_pad}: {ex['text']}")
```

## 7. Get the Eucharistic Prayer III in Latin and side-by-side English

```python
import json
ep3 = next(
    e for e in json.load(open("data/library/eucharistic-prayers.json"))["eucharisticPrayers"]
    if e["id"] == "eucharistic-prayer.3"
)
la = ep3["body"]["lines"]["la"]
en = ep3["body"]["lines"]["en"]
# Lines may not align 1:1 across languages; here, show in parallel up to the shorter list.
for la_line, en_line in zip(la[:20], en[:20]):
    print("  LA:", " ".join(s["text"] for s in la_line if s.get("text")))
    print("  EN:", " ".join(s["text"] for s in en_line if s.get("text")))
    print()
```

## 8. Search for a phrase across all Masses

```python
import json
from pathlib import Path

q = "Hosanna"
hits = []
for f in Path("data/masses").rglob("*.json"):
    for m in json.loads(f.read_text())["masses"]:
        for field in ("collect", "entranceAntiphon", "communionAntiphon", "postcommunion"):
            v = m.get(field)
            if not isinstance(v, dict):
                continue
            plain = (v.get("body") or {}).get("plain") or {}
            for lang, text in plain.items():
                if q.lower() in text.lower():
                    hits.append((m["id"], field, lang))
                    break

for mid, field, lang in hits[:10]:
    print(f"  {mid:50s} {field:25s} {lang}")
```

Or use the bundled CLI:

```bash
python scripts/query.py search Hosanna
```

## 9. Find every Mass that uses a specific preface

```python
import json
from pathlib import Path

target_ref = "preface.pf056"   # Holy Pastors
hits = []
for f in Path("data/masses").rglob("*.json"):
    for m in json.loads(f.read_text())["masses"]:
        preface = m.get("preface") or {}
        if isinstance(preface, dict) and preface.get("prefaceRef") == target_ref:
            hits.append(m["id"])

print(f"{len(hits)} masses use {target_ref}:")
for mid in hits[:20]:
    print(f"  {mid}")
```

## 10. Render a complete Mass formulary as text

```python
import json

m = next(
    x for x in json.load(open("data/masses/sanctorale/01.json"))["masses"]
    if x["id"] == "sanctorale.01-02"
)

LANG = "en"

def localized(field, lang=LANG):
    if field is None:
        return ""
    if isinstance(field, dict):
        return field.get(lang) or next(iter(field.values()), "")
    return ""

print(f"\n{m['title'][LANG]}")
print(f"{m.get('rankLocalized', {}).get(LANG, '')}\n")
if m.get("description"):
    print(m["description"][LANG])
    print()

for slot, label in (
    ("entranceAntiphon", "Entrance Antiphon"),
    ("collect", "Collect"),
    ("prayerOverOfferings", "Prayer over the Offerings"),
    ("communionAntiphon", "Communion Antiphon"),
    ("postcommunion", "Prayer after Communion"),
):
    field = m.get(slot)
    if not field:
        continue
    print(f"\n## {label}")
    if "citation" in field:
        print(f"  ({localized(field['citation'])})")
    body = field.get("body") or {}
    text = (body.get("plain") or {}).get(LANG, "")
    print(text)

# Preface
preface = m.get("preface")
if isinstance(preface, dict) and "prefaceRef" in preface:
    print(f"\n## Preface")
    print(f"  → see {preface['prefaceRef']}: {localized(preface.get('label', {}))}")
```
