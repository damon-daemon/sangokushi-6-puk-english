"""Scan binary files for Shift-JIS text runs (2-byte SJIS + half-width kana), survey helper.
usage: sjscan.py [-v] file...   -> per file: runs, chars;  -v prints runs (offset, text)
"""
import re, sys
RUN = re.compile(rb'(?:[\x81-\x84\x88-\x9f\xe0-\xea][\x40-\x7e\x80-\xfc]|[\xa6-\xdf]|[\x20-\x7e](?=[\x81-\x9f\xe0-\xea\xa6-\xdf]))+')


def runs(d, minc=2):
    for m in RUN.finditer(d):
        b = m.group()
        try:
            s = b.decode('cp932')
        except UnicodeDecodeError:
            continue
        nj = sum(1 for c in s if ord(c) > 0x7f)
        two = sum(1 for c in s if ord(c) > 0x3000)
        if nj >= minc and (two >= 1 or nj >= 4):
            yield m.start(), s


if __name__ == '__main__':
    v = sys.argv[1] == '-v'
    for f in sys.argv[1 + v:]:
        d = open(f, 'rb').read()
        r = list(runs(d))
        print('%-28s runs %6d chars %7d' % (f.split('extract/')[-1], len(r), sum(len(s) for _, s in r)))
        if v:
            for o, s in r:
                print('  %06x %s' % (o, s))
