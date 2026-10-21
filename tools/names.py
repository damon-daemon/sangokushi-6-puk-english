"""Names: the records keep their Japanese, the text renderer shows English.

The officer table of the scenario files (DATA/SNDATAnn.S6, SSLG/SSLGSDnn.S6: 596
records of 0x5C at 0x5A; slots 0-579 = officers, the same in every file) holds
surname [6], given name [6], style name [6] and a half-width kana reading [12];
the program copies them into 160-byte RAM records (full name [9], surname + style
[9], surname/given/style [5]). English does not fit, so nothing is written there:
tools/namedict.py packs a Japanese -> English dictionary into free RAM and the print
loop replaces names while drawing (longest match). The Japanese stays in the records,
saves and comparisons.

tl/names/officers.json: one row per line, {"id", "en": [surname, given], "style"?,
"full"?} (full = the whole name when it is not "surname given", e.g. Ahuinan).
tl/names/people.json: people who are not officers (event characters the events copy into
a temporary officer's name fields from program strings): {"keys": [surname key, given
key?], "en": English of the whole, "parts"?: [English of the surname alone or null, ...]};
keys = progstr keys (tools/progstr.py), the Japanese is read from the program.

  names.py export        script/names/officers.json (Japanese, reading, English)
  names.py check         validate tl/names against the disc
  names.py pairs         print the Japanese -> English pairs
  names.py find TEXT...  pairs whose Japanese contains TEXT (or whose English contains it)
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, setup, text

SCEN = ['DATA/SNDATA%02d.S6' % i for i in range(1, 24)] + ['SSLG/SSLGSD%02d.S6' % i for i in range(20)]
OFFICER_TABLE, OFFICER_SIZE, OFFICERS = 0x5A, 0x5C, 580
TL_NAMES = os.path.join(setup.TL, 'names')


def field(b):
    return text.decode(b.split(b'\0')[0])


def officers(disc, recs=None):
    """[(surname, given, style, reading)] of slots 0-579 (checked equal in every scenario file)."""
    recs = recs or disc.records()
    out = None
    for name in SCEN:
        d = disc.file(name, recs)
        rows = []
        for i in range(OFFICERS):
            r = d[OFFICER_TABLE + i * OFFICER_SIZE:][:OFFICER_SIZE]
            rows.append(tuple(field(r[a:b]) for a, b in ((2, 8), (8, 14), (14, 20), (20, 32))))
        if out is None:
            out = rows
        else:                       # SSLG files: slot 550 differs in one file (placeholder)
            diff = [i for i in range(OFFICERS) if rows[i] != out[i] and i != 550]
            if diff:
                raise ValueError('%s: officer names differ in slots %s' % (name, diff[:10]))
    return out


def load(name):
    path = os.path.join(TL_NAMES, name + '.json')
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding='utf-8'))


def dump_rows(path, rows):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[\n' + ',\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n]\n')


def officer_pairs(jp, rows):
    """[(japanese, english, tier)] for every officer: full name, surname, surname + style,
    style (tier 0: must agree) and the given name alone (tier 1: dropped on a conflict).
    A one-word 'full' (Ahuinan) also stands for the surname alone (tier 2)."""
    out = []
    for r in rows:
        sur, given, style, _ = jp[r['id']]
        esur, egiven = r['en']
        full = r.get('full') or ('%s %s' % (esur, egiven)).strip()
        out.append((sur + given, full, 0))
        out.append((sur, esur, 0) if ' ' in full else (sur, full, 2))
        if 'full' not in r:
            out.append((given, egiven, 1))
        if style and r.get('style'):
            out += [(style, r['style'], 0), (sur + style, '%s %s' % (esur, r['style']), 0)]
    return [(j, e, t) for j, e, t in out if j and e]


def people_pairs(disc, recs=None):
    """[(japanese, english, tier)] of tl/names/people.json (whole name tier 0, parts tier 2)."""
    rows = load('people')
    if not rows:
        return []
    import progstr
    imgs = {i.tag: i for i in progstr.images({t: bytearray(b) for t, b in progstr.disc_files(disc, recs).items()})}
    out = []
    for r in rows:
        jp = []
        for k in r['keys']:
            tag, va = progstr.parse_key(k)
            jp.append(text.decode(progstr.slot(imgs[tag], va)[0]))
        out.append((''.join(jp), r['en'], 0))
        out += [(j, e, 2) for j, e in zip(jp, r.get('parts', [])) if e]
    return out


def pairs(disc, recs=None, quiet=True):
    """{japanese text: english}; raises ValueError on conflicts and missing rows."""
    jp = officers(disc, recs)
    rows = load('officers')
    errs, soft = [], []
    have = {r['id'] for r in rows}
    for i, (sur, given, _, _) in enumerate(jp):
        if sur and i not in have and not sur.startswith('武将'):
            errs.append('officers: no English for %d %s%s' % (i, sur, given))
    out, tier = {}, {}
    for j, e, t in sorted(officer_pairs(jp, rows) + people_pairs(disc, recs), key=lambda x: x[2]):
        if not e.isascii() or not e.strip():
            errs.append('officers: bad English for %s: %r' % (j, e))
        elif j in out and out[j] != e:
            if t == 0 and tier[j] == 0:  # tiers 1-2: the first (lower) one wins
                errs.append('conflict: %s = %r / %r' % (j, out[j], e))
            else:
                soft.append('%s: %r kept, %r dropped' % (j, out[j], e))
        else:
            out[j], tier[j] = e, t
    if errs:
        raise ValueError('\n'.join(errs))
    if not quiet and soft:
        print('given names left out (conflict):\n  ' + '\n  '.join(soft))
    return out


def compose_sets(disc, recs=None):
    """(surnames, given/style names) that tools/namedict.py may join as "Surname Given"."""
    jp = officers(disc, recs)
    sur, giv = set(), set()
    for r in load('officers'):
        s, g, st, _ = jp[r['id']]
        if 'full' not in r:
            sur.add(s)
            giv.add(g)
        if st and r.get('style'):
            giv.add(st)
    return sur, giv


def export(disc, recs=None):
    jp = officers(disc, recs)
    en = {r['id']: r for r in load('officers')}
    os.makedirs(os.path.join(setup.SCRIPT, 'names'), exist_ok=True)
    rows = []
    for i, (sur, given, style, reading) in enumerate(jp):
        row = {'id': i, 'jp': [sur, given, style], 'kana': reading}
        if i in en:
            row['en'] = en[i]['en'] + [en[i].get('style', '')]
        rows.append(row)
    dump_rows(os.path.join(setup.SCRIPT, 'names', 'officers.json'), rows)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    disc = iso.Disc(setup.CLEAN)
    if cmd == 'export':
        export(disc)
    elif cmd == 'check':
        try:
            p = pairs(disc, quiet=False)
        except ValueError as e:
            sys.exit(str(e))
        print('%d name pairs OK' % len(p))
    elif cmd == 'pairs':
        for j, e in sorted(pairs(disc).items()):
            print('%s\t%s' % (text.gaiji_tags(j), e))
    elif cmd == 'find' and len(sys.argv) > 2:
        p = pairs(disc)
        for q in sys.argv[2:]:
            hits = sorted((text.gaiji_tags(j), e) for j, e in p.items()
                          if q in text.gaiji_tags(j) or q.lower() in e.lower())
            print('\n'.join('%s\t%s' % h for h in hits) or '%s: not found' % q)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
