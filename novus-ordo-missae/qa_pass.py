#!/usr/bin/env python3
"""Final QA pass on the Roman Missal JSON corpus."""
import json
import os
import re
import random
from collections import defaultdict, Counter
from glob import glob

DATA = '/Users/gustavo/Documents/prayer/.claude/worktrees/iridescent-plotting-bumblebee/missal_to_json/data'
LANGS = ['la', 'es', 'en', 'pt-BR', 'it', 'fr', 'de']

# Index every mass by id across the entire corpus.
INDEX = {}  # id -> mass dict
SOURCES = {}  # id -> file


def walk_files():
    for root, _, files in os.walk(os.path.join(DATA, 'masses')):
        for f in files:
            if f.endswith('.json'):
                yield os.path.join(root, f)


def load_all():
    for fp in walk_files():
        with open(fp) as f:
            d = json.load(f)
        ms = d.get('masses', [])
        if isinstance(ms, list):
            for m in ms:
                mid = m.get('id')
                if mid:
                    INDEX[mid] = m
                    SOURCES[mid] = fp
        elif isinstance(ms, dict):
            for k, m in ms.items():
                mid = m.get('id') or k
                INDEX[mid] = m
                SOURCES[mid] = fp


def collect_langs(field):
    """Return set of populated langs for a prayer field."""
    if not isinstance(field, dict):
        return set()
    body = field.get('body')
    if not isinstance(body, dict):
        # might be lines-only or different shape
        if 'plain' in field:
            body = field
        else:
            return set()
    plain = body.get('plain') or {}
    out = set()
    for k, v in plain.items():
        if k in LANGS and isinstance(v, str) and v.strip():
            out.add(k)
    return out


def get_text(field, lang):
    if not isinstance(field, dict):
        return None
    body = field.get('body', field)
    plain = body.get('plain') if isinstance(body, dict) else None
    if not isinstance(plain, dict):
        return None
    return plain.get(lang)


def main():
    load_all()
    print(f"Total masses indexed: {len(INDEX)}")
    print(f"Source files: {len(set(SOURCES.values()))}")

    # ------------------------------------------------------------------
    print("\n=== 1. PREVIOUSLY-BROKEN CASES ===")

    targets = {
        'common.saints.sanct3': ('collect', 'pt-BR should be ABSENT'),
        'common.martyrs.mart4': ('entranceAntiphon', 'all 7 langs'),
        'sanctorale.11-01': ('collect', 'la/en/it present, pt-BR absent if missing in source'),
        'tempore.easter.week-1.monday': ('postcommunion', 'IT no Congedo'),
        'tempore.advent.week-1.sunday': ('firstReading', 'response should be R/. Thanks be to God.'),
    }

    for mid, (field_name, note) in targets.items():
        m = INDEX.get(mid)
        if not m:
            # try fuzzy match
            cand = [k for k in INDEX if mid in k or k.endswith(mid.split('.')[-1])]
            print(f"  [MISS] {mid}: not found; near matches: {cand[:5]}")
            continue
        f = m.get(field_name)
        langs = collect_langs(f) if f else set()
        print(f"  {mid}.{field_name}: langs={sorted(langs)} ({note})")
        if mid == 'tempore.easter.week-1.monday' and f:
            it = get_text(f, 'it') or ''
            tail = it[-120:].replace('\n', ' / ')
            has_congedo = 'Congedo' in it
            print(f"    IT tail: ...{tail}")
            print(f"    contains 'Congedo': {has_congedo}")
        if mid == 'tempore.advent.week-1.sunday' and f:
            # firstReading — check cycles A/B/C response
            print(f"    firstReading shape keys: {list(f.keys()) if isinstance(f,dict) else type(f)}")
            # find response
            def find_response(node, path=''):
                hits = []
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == 'response':
                            hits.append((path + '.response', v))
                        else:
                            hits.extend(find_response(v, path + '.' + k))
                elif isinstance(node, list):
                    for i, v in enumerate(node):
                        hits.extend(find_response(v, f'{path}[{i}]'))
                return hits
            for path, resp in find_response(f)[:6]:
                if isinstance(resp, dict):
                    body = resp.get('body', resp)
                    plain = body.get('plain') if isinstance(body, dict) else None
                    en = plain.get('en') if isinstance(plain, dict) else None
                    print(f"    {path}: en={en!r}")
                else:
                    print(f"    {path}: {str(resp)[:80]}")
        if mid == 'common.martyrs.mart4' and f:
            for L in LANGS:
                t = get_text(f, L)
                ok = isinstance(t, str) and t.strip()
                print(f"    {L}: {'OK' if ok else 'MISSING'} ({len(t) if t else 0} chars)")
        if mid == 'common.saints.sanct3' and f:
            present = sorted(collect_langs(f))
            print(f"    pt-BR present? {'pt-BR' in present}")
        if mid == 'sanctorale.11-01' and f:
            for L in ['la', 'en', 'it', 'pt-BR']:
                t = get_text(f, L)
                print(f"    {L}: {'OK' if t else 'absent'} ({len(t) if t else 0} chars)")

    # ------------------------------------------------------------------
    print("\n=== 2. REGRESSION SAMPLE (20 random masses, prayer-field lang counts) ===")
    random.seed(7)
    ids = sorted(INDEX.keys())
    sample = random.sample(ids, min(20, len(ids)))
    prayer_fields = ['entranceAntiphon', 'collect', 'prayerOverOfferings',
                     'communionAntiphon', 'postcommunion']
    for mid in sample:
        m = INDEX[mid]
        line = [mid]
        for fn in prayer_fields:
            f = m.get(fn)
            n = len(collect_langs(f)) if f else -1
            line.append(f"{fn[:4]}={n}")
        print('  ' + ' | '.join(line))

    # ------------------------------------------------------------------
    print("\n=== 3. EUCHARISTIC PRAYER III (Roman Canon body, first 200 chars per lang) ===")
    # Ordo Missae lives in igmr/ or library/?
    # Search for "Vere Sanctus" / "You are indeed Holy"
    candidates = []
    for fp in glob(os.path.join(DATA, '**/*.json'), recursive=True):
        try:
            with open(fp) as f:
                txt = f.read()
            if 'Vere Sanctus' in txt or 'You are indeed Holy' in txt:
                candidates.append(fp)
        except Exception:
            pass
    print(f"  files containing canon-III phrasing: {len(candidates)}")
    for fp in candidates[:5]:
        print(f"   - {fp}")

    def find_canon(node, path=''):
        hits = []
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and ('Vere Sanctus' in v or 'You are indeed Holy' in v):
                    hits.append((path + '.' + k, node))
                else:
                    hits.extend(find_canon(v, path + '.' + k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                hits.extend(find_canon(v, f'{path}[{i}]'))
        return hits

    for fp in candidates[:3]:
        with open(fp) as f:
            d = json.load(f)
        hits = find_canon(d)
        if not hits:
            continue
        print(f"\n  -- {fp} ({len(hits)} hit(s)) --")
        # take parent of first hit, find sibling lang strings
        path0, parent = hits[0]
        # parent should be the "plain" dict
        if isinstance(parent, dict):
            for L in LANGS:
                v = parent.get(L)
                if isinstance(v, str):
                    print(f"   {L}: {v[:200]!r}")
                else:
                    print(f"   {L}: <missing>")
        break

    # ------------------------------------------------------------------
    print("\n=== 4. TRIDUUM INTEGRITY ===")
    # Triduum data is in triduum.json at data root
    tp = os.path.join(DATA, 'triduum.json')
    with open(tp) as f:
        triduum = json.load(f)
    print('  triduum top keys:', list(triduum.keys())[:15])

    def deep_find_keys(node, want, path='', out=None):
        if out is None:
            out = []
        if isinstance(node, dict):
            for k, v in node.items():
                np = path + '.' + k
                if k == want:
                    out.append((np, v))
                deep_find_keys(v, want, np, out)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                deep_find_keys(v, want, f'{path}[{i}]', out)
        return out

    # Easter Vigil: 7 OT readings
    vigil_hits = deep_find_keys(triduum, 'easterVigil') or deep_find_keys(triduum, 'vigil')
    if not vigil_hits:
        # try title
        for k in triduum:
            if 'vigil' in k.lower() or 'paschal' in k.lower():
                vigil_hits.append((k, triduum[k]))
    print(f"  vigil hits: {len(vigil_hits)} -> sample paths: {[p for p,_ in vigil_hits[:3]]}")
    if vigil_hits:
        path, vig = vigil_hits[0]
        # Look for OT readings
        ot = deep_find_keys(vig, 'oldTestamentReadings') + deep_find_keys(vig, 'readings')
        for p, v in ot[:3]:
            n = len(v) if isinstance(v, list) else '?'
            print(f"    {p}: list-len={n}")

    # Renewal of baptismal promises
    rb = deep_find_keys(triduum, 'renewalOfBaptismalPromises') + deep_find_keys(triduum, 'baptismalPromises')
    print(f"  baptismal-promise hits: {len(rb)}")
    for p, v in rb[:2]:
        # count exchanges per lang
        if isinstance(v, dict):
            ex = v.get('exchanges') or v.get('parts') or v.get('lines')
            if isinstance(ex, list):
                # count populated langs in first exchange
                if ex:
                    first = ex[0]
                    print(f"    {p}: exchange-count={len(ex)}, first keys={list(first.keys()) if isinstance(first,dict) else type(first)}")
            else:
                print(f"    {p}: keys={list(v.keys())[:15]}")

    # Exsultet in serviceOfLight
    sol = deep_find_keys(triduum, 'serviceOfLight')
    print(f"  serviceOfLight hits: {len(sol)}")
    for p, v in sol[:1]:
        ex = deep_find_keys(v, 'exsultet') + deep_find_keys(v, 'easterProclamation')
        print(f"    {p}: exsultet-hits={len(ex)}")

    # Good Friday: 10 Solemn Intercessions
    gf = deep_find_keys(triduum, 'goodFriday') or deep_find_keys(triduum, 'solemnIntercessions')
    print(f"  goodFriday/solemn-int hits: {len(gf)}")
    for p, v in gf[:2]:
        si = deep_find_keys(v, 'solemnIntercessions') + deep_find_keys(v, 'intercessions')
        for sp, sv in si[:2]:
            n = len(sv) if isinstance(sv, list) else (len(sv.get('items', [])) if isinstance(sv, dict) else '?')
            print(f"    {p}{sp}: count={n}")

    # Palm Sunday
    ps = deep_find_keys(triduum, 'palmSunday') + deep_find_keys(triduum, 'passionSunday')
    print(f"  palmSunday hits: {len(ps)}")
    for p, v in ps[:1]:
        pg = deep_find_keys(v, 'passionGospel') + deep_find_keys(v, 'processionGospel') + deep_find_keys(v, 'cycles')
        for sp, sv in pg[:6]:
            kind = type(sv).__name__
            extra = ''
            if isinstance(sv, dict):
                extra = f"keys={list(sv.keys())[:8]}"
            elif isinstance(sv, list):
                extra = f"len={len(sv)}"
            print(f"    {p}{sp}: {kind} {extra}")

    # ------------------------------------------------------------------
    print("\n=== 5. STATISTICS ===")
    all7_collect = 0
    one_or_two_collect = 0
    empty_masses = 0
    field_lang_hist = defaultdict(Counter)
    for mid, m in INDEX.items():
        any_text = False
        for fn in prayer_fields + ['preface']:
            f = m.get(fn)
            if f:
                ls = collect_langs(f)
                if ls:
                    any_text = True
                field_lang_hist[fn][len(ls)] += 1
        if not any_text:
            empty_masses += 1
        col = m.get('collect')
        if col:
            n = len(collect_langs(col))
            if n == 7:
                all7_collect += 1
            elif n in (1, 2):
                one_or_two_collect += 1
    print(f"  Masses with all-7-language collect: {all7_collect}")
    print(f"  Masses with 1-2 lang collect: {one_or_two_collect}")
    print(f"  Empty masses (no prayer text in any field/lang): {empty_masses}")
    print("  Per-field lang-count histogram (n_langs -> mass_count):")
    for fn in prayer_fields:
        hist = dict(sorted(field_lang_hist[fn].items()))
        print(f"    {fn}: {hist}")

    # Identify orphans - masses with 0 langs in collect
    zero_collect = [mid for mid, m in INDEX.items()
                    if m.get('collect') and not collect_langs(m['collect'])]
    print(f"  Masses with collect present but 0 langs: {len(zero_collect)}")
    for mid in zero_collect[:8]:
        print(f"    - {mid}")

    # Trailing artifacts scan
    print("\n=== 6. TRAILING-ARTIFACT REGRESSION SCAN ===")
    artifact_re = re.compile(r'(\bdiv>\s*$|\.v\s*$|^[_\-\s]+$|\bpag\.\s*\d+\s*$)', re.IGNORECASE)
    italian_leak_re = re.compile(r'Congedo come', re.IGNORECASE)
    leak_examples = []
    artifact_examples = []
    for mid, m in INDEX.items():
        for fn in prayer_fields + ['preface']:
            f = m.get(fn)
            if not f:
                continue
            for L in LANGS:
                t = get_text(f, L)
                if not isinstance(t, str):
                    continue
                if italian_leak_re.search(t):
                    leak_examples.append((mid, fn, L, t[-80:]))
                if artifact_re.search(t.strip()):
                    artifact_examples.append((mid, fn, L, t[-60:]))
    print(f"  Italian 'Congedo come' leaks remaining: {len(leak_examples)}")
    for ex in leak_examples[:5]:
        print(f"    {ex[0]}.{ex[1]}.{ex[2]}: ...{ex[3]!r}")
    print(f"  Trailing artifact remnants: {len(artifact_examples)}")
    for ex in artifact_examples[:5]:
        print(f"    {ex[0]}.{ex[1]}.{ex[2]}: ...{ex[3]!r}")


if __name__ == '__main__':
    main()
