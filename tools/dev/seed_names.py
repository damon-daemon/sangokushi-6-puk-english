"""One-off: seed tl/names/officers.json (DS 3 spelling when the Japanese matches, else pypinyin).
Prints rows to review (US name differs, unknown kanji)."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import iso, names, setup
from pypinyin import lazy_pinyin

DS3 = os.path.expanduser('~/Projects/sangokushi_ds_3_translation/script/names_officers.json')
ds3 = {r['jp']: r['en'] for r in json.load(open(DS3, encoding='utf-8'))}
disc = iso.Disc(setup.CLEAN)
jp = names.officers(disc)
us = open(os.path.join(setup.ROOT, 'extract/us/DATA/SNDATA01.S6'), 'rb').read()

def py(s):
    p = lazy_pinyin(s)
    bad = [c for c, x in zip(s, p) if c == x]
    return ''.join(p).capitalize(), bad

rows, review = [], []
for i, (sur, given, style, kana) in enumerate(jp):
    if not sur or sur.startswith('武将'):
        continue
    (ps, b1), (pg, b2), (pst, b3) = py(sur), py(given), py(style)
    row = {'id': i, 'en': [ps, pg]}
    d = ds3.get(sur + given)
    u = us[0x5C + i * 0x5C:][:30].split(b'\0')[0].decode('ascii', 'replace').replace('@', '')
    if d and '.' in d:
        d = None
    if not d and (b1 or b2) and ' ' in u:
        d = u
    if d:
        parts = d.split(' ')
        if len(parts) == 2:
            row['en'] = parts
        elif d.replace(' ', '').lower() != (ps + pg).lower() or ' ' not in d:
            row['full'] = d
    if style:
        row['style'] = pst
    mine = row.get('full') or ' '.join(row['en'])
    note = []
    if b1 or b2 or b3:
        note.append('unknown kanji %s' % (b1 + b2 + b3))
    if u.replace(' ', '').lower() != mine.replace(' ', '').lower():
        note.append('US %r' % u)
    if not d:
        note.append('not in DS3')
    if note:
        review.append('%d %s|%s|%s %s -> %s  %s' % (i, sur, given, style, kana, mine, '; '.join(note)))
    rows.append(row)
names.dump_rows(os.path.join(names.TL_NAMES, 'officers.json'), rows)
print('\n'.join(review))
print(len(rows), 'rows', len(review), 'to review', file=sys.stderr)
