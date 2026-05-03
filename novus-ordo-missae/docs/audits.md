# Audit / TDD / fix loop — methodology

This doc captures the workflow used to drive the post-processor (`scripts/refine.py`) toward perfection. ~23 cycles ran end-to-end before this was written down; the corpus went from messy first-pass output to "0 validate.py errors, 0 warnings, 776 passing tests" along the way.

The loop is meant to be run repeatedly. Each pass finds concrete defects, fixes them, locks the fix in with a test, and verifies the defect is gone in production data. Repeat until the audits stop returning anything mechanical.

## The cycle

1. **Audit** — spawn 2–3 parallel agents to find concrete defects across different surfaces of the data.
2. **Triage** — separate post-processable defects from source-data limits.
3. **TDD red** — write failing unit tests for the chosen fixes.
4. **Implement** — add the helper(s) to `scripts/refine.py` and wire them into the pipeline.
5. **TDD green** — confirm new tests pass and full suite still passes.
6. **Regenerate** — `python3.11 scripts/refine.py`.
7. **Validate** — `python3.11 scripts/validate.py` (must end with `0 errors, 0 warnings`).
8. **Verify in production** — count the defect class in the actual `data/` files; expect N → 0.
9. **Commit** the cycle.
10. **Loop** — schedule the next pass and start over.

## Step 1 — Audit prompts that work

Don't ask "find all bugs." Ask one agent per surface, with the prior-fix list excluded.

Surfaces we've used effectively (rotate among them per cycle):

- **Text quality across all masses** — spacing, punctuation, OCR scannos, doubled words, mismatched quotes.
- **Latin OCR specifically** — diacritics, ligatures (`æ`/`œ`), tripled letters, mid-word capitalization in `lang == 'la'`.
- **Cross-language consistency** — same-field length disparity, leaked Latin in vernacular slots, citation-style drift across langs.
- **Structural defects** — heading/body misclassification, duplicate sub-sections, empty arrays, orphan brackets, segment-vs-plain drift.
- **Specific bundles** (commons, ritual, votive, sanctorale by month) — these often have unique defect classes the seasonal files don't.
- **Library/companion files** — `prefaces.json`, `eucharistic-prayers.json`, `saints.json`, `calendar.json`. Less-traveled surfaces.
- **Segment-level (`lines.<lang>` arrays)** — empty trailing rubrics, role-text mismatches, role-enum violations.

For each prompt, give the agent:

- Working dir: `/path/to/novus-ordo-missae/`.
- Where to look: a glob like `data/masses/**/*.json`, or `data/library/*.json`.
- What's already fixed: a bulleted list of prior cycles' defect classes so it doesn't waste time re-flagging them.
- What to report: file path, JSON path inside the file, concrete example, count, distribution by file. Quantify everything.
- Length cap: "report under 600 words" — keeps the response actionable.

Concrete example of an audit prompt:

> Audit `data/masses/**/*.json` for **structural defects**. Already-fixed: triduum part ordering, preamble heading translations, sub-section heading lang-gap backfill, trailing empty rubric segments, dedupe-heading-as-first-rubric. Find: misclassified rubric blocks, duplicate sub-sections, missing/null body fields, common-of-saints prayer fragments, inconsistent sub-section IDs, prefaceRefs that don't resolve. For each: file path + JSON path + count. Quantify per file. Report under 600 words.

Three agents in parallel (single message, multiple tool uses) gets you a broad cycle's-worth of findings in one shot.

## Step 2 — Triage

Walk each finding into one of three buckets:

- **Mechanical & post-processable** — a regex, a word-list, or a tree walker can fix it. These are the cycle's targets.
- **Source-data limits** — the upstream HTML genuinely doesn't carry that content (e.g., empty `de` body, missing translations, wrong sacerdotale doc). Note in the cycle summary; do not fix.
- **False positives** — the audit agent flagged something that's actually correct. Push back via a quick spot-check before acting.

Spot-check audit claims with a one-liner before writing tests. Agents hallucinate counts and miss context. Examples of recoverable spot-checks:

```bash
grep -rn '<bad-pattern>' data/masses/ | wc -l       # quick prevalence check
python3.11 -c "import json; d=json.load(open('data/masses/X.json')); print(d['masses'][N])"
```

## Step 3 — TDD red

Add tests *before* the implementation, in `scripts/test_postprocess.py`. Class per defect family, methods covering:

- The basic positive case ("this fixes the bug").
- Idempotency ("running twice doesn't double-mutate").
- Lang-scoping when relevant ("don't apply to non-target langs").
- Negative case ("don't touch the legitimate-looking thing nearby").
- Edge cases that nearly match the pattern.

Run the new tests and confirm they fail:

```bash
python3.11 -m pytest scripts/test_postprocess.py -k "TestNewClass" --tb=line -q
```

Exit code should be non-zero.

## Step 4 — Implement

Two patterns dominate in `scripts/refine.py`:

- **Per-string fix** — a function `(text: str, lang: str) -> str`. Wire via `_walk_lang_strings(mass, fn)` inside `_post_process_mass(...)` (mass-scope) or via `_apply_universal_text_fixes` inside `_post_process_payload(...)` (payload-wide, hits libraries too).
- **Per-tree fix** — a function `(mass: dict) -> None` that mutates structurally. Add to the `_post_process_mass` orchestrator at the right point in the sequence.

Naming convention is consistent: `_fix_<defect>` or `_<verb>_<defect>` for the unit, `_<thing>_in_mass` for the orchestrator-level wrapper that walks the whole mass.

For Latin OCR fixes specifically: extend `_LA_OCR_FIXES` (regex table) or `_LA_DIACRITIC_WORDS` (word-list). Both are gated to `lang == 'la'` in `_scrub_string`.

## Step 5 — TDD green + full suite

```bash
python3.11 -m pytest scripts/test_postprocess.py -k "TestNewClass" --tb=short -q   # new tests pass
python3.11 -m pytest scripts/ --tb=short -q                                        # nothing else broke
```

Full suite must stay green. If a pre-existing test broke, that's a sign your fix changed behavior somewhere unexpected — investigate before continuing.

## Step 6–7 — Regenerate + validate

```bash
python3.11 scripts/refine.py        # ends with "Done."
python3.11 scripts/validate.py      # must end with "✓ All checks passed." + 0 errors, 0 warnings
```

If validate flips from green to red, your fix introduced something. Don't just commit — debug it.

## Step 8 — Verify in production

The defect count must reach the expected target (usually 0). Run a one-liner specific to the defect:

```python
# Example: count remaining `Per Dominum.` (unaccented) in la context.
python3.11 -c "
import json, glob, re
n = 0
for fp in glob.glob('data/masses/**/*.json', recursive=True):
    with open(fp) as f: d = json.load(f)
    def walk(node, lang=None):
        global n
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ('la','en','es','pt-BR','it','fr','de'):
                    if isinstance(v, str):
                        if k == 'la' and 'Per Dominum.' in v: n += 1
                    else: walk(v, k)
                else: walk(v, lang)
        elif isinstance(node, list):
            for x in node: walk(x, lang)
    walk(d)
print(n)
"
```

If the count is non-zero, the fix didn't catch every case. Iterate.

## Step 9 — Commit

One cycle = one commit. Subject line: `Cycle N: <summary of fix categories>`. Body lists each fix with before/after counts. Lets you review history mass-by-mass later.

## Step 10 — Loop

Either schedule another pass right away or pause. The audit signal degrades over cycles — early cycles find dozens of defect classes per pass; later cycles find one or two.

## Hard-won principles

- **Idempotency.** Every fix must produce the same output on a second run. Use `dict.fromkeys(...)` not `set(...)` for ordering. Use word-list patterns gated by language. Use regex patterns that don't re-match their own output.
- **Don't fight source data.** If a German body is empty in the upstream HTML, the post-processor can't conjure one. Note these as source-data limits and move on.
- **Quantify before fixing.** "Found 5 cases" is actionable; "found some cases" is not. Always count first.
- **One defect class per test class.** Makes regressions specific. The class name is the defect's name.
- **Walk the tree, not just the slot.** Defects often live in deeply-nested `parts[X].content[i].content[j].body.lines.<lang>[k]`. Don't write a fix that only touches top-level mass slots — walk it.
- **Run validate.py and `git diff data/`.** Validate gates correctness. The diff tells you what changed and lets you eyeball whether the fix was scoped right.
- **Don't trust audit-agent counts blindly.** Spot-check before writing tests. Agents will confidently report "found in 50 files" for things that don't exist.

## Files to know

| File | Role |
|---|---|
| `scripts/refine.py` | The post-processor. Reads `out/`, writes `data/`. All fixes land here. |
| `scripts/test_postprocess.py` | Where unit tests live. One class per defect family. |
| `scripts/validate.py` | Schema validation + cross-ref checks. Must stay 0 errors, 0 warnings. |
| `scripts/test_integration.py` | End-to-end pipeline assertions on real masses. |
| `data/` | The committed output corpus. Re-generate after every cycle. |
| `out/` | Intermediate raw HTML→JSON output. Gitignored. Regenerable from `source/`. |
| `source/Missale_romanum/` | Upstream HTML clone. Gitignored. |

## When to stop

When three consecutive cycles return only source-data limits and false positives. At that point, the post-processor has caught everything it can. Further improvements need either:

- Schema changes (structural reshaping the post-processor can't do).
- Source-data swaps (different upstream, or hand-editing).
- New surfaces nobody's audited yet (a fresh angle the prior agents didn't cover).
