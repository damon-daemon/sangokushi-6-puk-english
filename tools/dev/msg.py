"""MESSAGE.S6 survey decoder (heuristic, phase 1).

File = 2 KB-aligned blocks; the program holds a u32 table of block byte offsets
(JP 0x80110058, 65 blocks; US 0x800EE954, 87 blocks). Block = u16 n, n u16 offsets
(relative to the block), entries. Message id = block * 1000 + index (01 4A refs).
Entry = bytecode; text runs are `01 22 <bytes>` up to the next byte 01-06.

usage: msg.py dump jp|us > out.txt
"""
import os, re, struct, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = {'jp': ('extract/jp/DATA/MESSAGE.S6', 'extract/jp/SLPM_863.12', 0x100858),
       'us': ('extract/us/DATA/MESSAGE.S6', 'extract/us/SLUS_009.18', 0xDF154)}


def load(ver):
    m, x, t = SRC[ver]
    d = open(os.path.join(ROOT, m), 'rb').read()
    exe = open(os.path.join(ROOT, x), 'rb').read()
    offs = []
    p = t
    while True:
        v = struct.unpack_from('<I', exe, p)[0]
        if offs and (v <= offs[-1] or v > len(d)):
            break
        offs.append(v)
        p += 4
    blocks = []
    for b in offs[:-1]:
        n = struct.unpack_from('<H', d, b)[0]
        o = struct.unpack_from('<%dH' % n, d, b + 2)
        ents = []
        for i in range(n):
            a = b + o[i]
            if i + 1 < n:
                e = b + o[i + 1]
            else:
                e = a
                while e < len(d) and d[e]:
                    e += 1
            ents.append(d[a:e])
        blocks.append(ents)
    return blocks


TEXT = re.compile(rb'\x01\x22([^\x01-\x06]*)')


def texts(entry):
    return [m.group(1) for m in TEXT.finditer(entry)]


def skeleton(entry):
    """Entry bytecode with text replaced by a marker and message refs blanked."""
    s = TEXT.sub(b'\x01T', entry)
    return re.sub(rb'\x01\x4a..', b'\x01J', s, flags=re.S)


def hira(s):
    """Half-width katakana stand for hiragana in this game's text."""
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c for c in s)


def show(t, ver):
    if ver == 'jp':
        s = re.sub('[\uff66-\uff9f]+', lambda m: hira(unicodedata.normalize('NFKC', m.group())),
                   t.decode('cp932', 'replace'))
    else:
        s = t.decode('latin-1')
    return s.replace('\n', '\\n').replace('\x1b', '\\e')


if __name__ == '__main__':
    ver = sys.argv[2]
    for bi, ents in enumerate(load(ver)):
        for i, e in enumerate(ents):
            ts = texts(e)
            print('%d %s' % (bi * 1000 + i, ' | '.join(show(t, ver) for t in ts)))
