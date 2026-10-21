"""Shrink the program's 16x16 kanji font and free its RAM (notes/engine.md "Font").

The program carries 2664 full-width glyphs (1bpp 16x16, 32 B each, at GLYPHS) with a
sorted u16 Shift-JIS code list (CODES), ~90 KB in all. The English build keeps only
  - the glyphs the BIOS kanji ROM does not have (it holds only 0x8140-0x84BE and the
    level-1 kanji 0x889F-0x9872): NEC specials 0x87xx, level-2 kanji and the gaiji
    0xF040- (rare name kanji), copied from the original set (untranslated text);
  - full-width digits and Latin letters (drawn from tools/pixfont.py, centred in the
    16-px cell) and the full-width space: the officer name entry (USER/INIT overlays,
    get_font) lists the whole set as its character grid;
and draw_char looks a 2-byte code up in that small set first, then falls back to the
BIOS ROM font (Krom2RawAdd, the path the game uses when its BIOS-font flag is set) for
every other kanji/kana (untranslated Japanese). The rest of the area is returned as
free RAM for the English code and data (Heap).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont

EXE_BASE = 0x80010000 - 0x800
GLYPHS, CODES, OLD_N = 0x800f6668, 0x8010b368, 0xA68
AREA_END = CODES + 2 * OLD_N                 # 0x8010c838
INIT_CODES, INIT_GLYPHS, INIT_COUNT = 0x8004f718, 0x8004f71c, 0x8004f6f0
INIT_CODES_HI = 0x8004f704                   # lui a0, 0x8011
LOOKUP, LOOKUP_END = 0x80040700, 0x80040788  # draw_char: BIOS-flag test .. single-byte path
DRAW_END = 0x800408b4                        # draw_char common exit
SEARCH, KANJI_BLIT, KROM2RAWADD, BLIT = 0x800403a4, 0x800402ac, 0x80014380, 0x8003fd30
GP_COUNT, GP_CELL_W = -0x4990, -0x7f80

LATIN = ([0x8140] + list(range(0x824F, 0x8259)) + list(range(0x8260, 0x827A))
         + list(range(0x8281, 0x829B)))


def keep_original(code):
    return not (0x8140 <= code <= 0x84BE or 0x889F <= code <= 0x9872)


def latin_glyph(code):
    ch = chr(code - 0x8140 + 0x20) if code != 0x8140 else ' '
    if 0x824F <= code <= 0x829A:
        ch = chr(code - 0x824F + ord('0')) if code < 0x8260 else chr(code - 0x8260 + ord('A')) if code < 0x8281 else chr(code - 0x8281 + ord('a'))
    rows, adv = pixfont.glyphs()[ch]
    sh = max(0, (16 - (adv - pixfont.SPACING)) // 2)
    out = bytearray()
    for r in rows:
        v = r << (8 - sh)
        out += bytes([v >> 8 & 0xFF, v & 0xFF])
    return bytes(out)


def lookup_code():
    """draw_char 2-byte path from LOOKUP: v1 = code, s0/s1 = x/y, t1 = cell height."""
    return asm.assemble(LOOKUP, [
        ('lhu', 'v0', GP_COUNT, 'gp'),
        ('addiu', 'a0', 'zero', 1),
        ('beqz', 'v0', 'bios'),
        ('addu', 'a1', 'v1', 'zero'),
        ('jal', SEARCH),                      # keeps a1 (code) and t1
        ('nop',),
        ('bltz', 'v0', 'bios'),
        ('sll', 'a0', 'v0', 16),
        ('sra', 'a0', 'a0', 16),
        ('sw', 't1', 0x10, 'sp'),
        ('lh', 'a3', GP_CELL_W, 'gp'),
        ('addu', 'a1', 's0', 'zero'),
        ('jal', KANJI_BLIT),
        ('addu', 'a2', 's1', 'zero'),
        ('b', DRAW_END),
        ('addiu', 'v0', 'zero', 1),
        'bios:',
        ('jal', KROM2RAWADD),
        ('addu', 'a0', 'a1', 'zero'),
        ('addiu', 'v1', 'zero', 15),
        ('sw', 'v1', 0x10, 'sp'),
        ('addiu', 'v1', 'zero', 1),
        ('sw', 'v1', 0x14, 'sp'),
        ('addu', 'a0', 'v0', 'zero'),
        ('addu', 'a1', 's0', 'zero'),
        ('addu', 'a2', 's1', 'zero'),
        ('jal', BLIT),
        ('addiu', 'a3', 'zero', 16),
        ('b', DRAW_END),
        ('addiu', 'v0', 'zero', 1),
    ])


class Heap:
    """Free RAM in the program image: alloc() hands out addresses, put() writes."""
    def __init__(self, exe, lo, hi):
        self.exe, self.lo, self.hi, self.top, self.end = exe, lo, hi, lo, hi

    def alloc_top(self, size, align=4):
        """Space at the end of the free RAM: its address does not move when the code grows."""
        a = (self.hi - size) & -align
        if a < self.top:
            raise ValueError('free RAM exhausted (%d bytes over)' % (self.top - a))
        self.hi = a
        return a

    def alloc(self, size, align=4):
        self.top += -self.top % align
        a = self.top
        self.top += size
        if self.top > self.hi:
            raise ValueError('free RAM exhausted (%d bytes over)' % (self.top - self.hi))
        return a

    def put(self, data, align=4):
        a = self.alloc(len(data), align)
        self.exe[a - EXE_BASE:a - EXE_BASE + len(data)] = data
        return a

    def used(self):
        return self.top - self.lo + self.end - self.hi, self.end - self.lo


def word(exe, va):
    return struct.unpack_from('<I', exe, va - EXE_BASE)[0]


def apply(exe):
    """Patch the program (bytearray); returns a Heap over the freed area."""
    assert word(exe, INIT_COUNT) == 0x24020000 | OLD_N and word(exe, INIT_CODES) == 0x2484b368
    assert word(exe, INIT_GLYPHS) == 0x24a56668 and word(exe, INIT_CODES_HI) == 0x3c048011
    assert word(exe, LOOKUP) == 0x0c010248, 'draw_char BIOS-flag call'
    codes = struct.unpack_from('<%dH' % OLD_N, exe, CODES - EXE_BASE)
    old = bytes(exe[GLYPHS - EXE_BASE:CODES - EXE_BASE])
    new = {c: old[32 * i:32 * i + 32] for i, c in enumerate(codes) if keep_original(c)}
    new.update({c: latin_glyph(c) for c in LATIN})
    order = sorted(new)
    n = len(order)
    exe[GLYPHS - EXE_BASE:AREA_END - EXE_BASE] = bytes(AREA_END - GLYPHS)
    heap = Heap(exe, GLYPHS, AREA_END)
    heap.put(b''.join(new[c] for c in order))
    # the search reads codes[count] too (inclusive bound): 0xFFFF sentinel
    tab = heap.put(struct.pack('<%dH' % (n + 1), *(order + [0xFFFF])))
    exe[INIT_CODES_HI - EXE_BASE:INIT_CODES_HI - EXE_BASE + 4] = struct.pack('<I', 0x3c040000 | asm.hi(tab))
    exe[INIT_CODES - EXE_BASE:INIT_CODES - EXE_BASE + 4] = struct.pack('<I', 0x24840000 | asm.lo(tab) & 0xFFFF)
    exe[INIT_COUNT - EXE_BASE:INIT_COUNT - EXE_BASE + 4] = struct.pack('<I', 0x24020000 | n)
    code = lookup_code()
    assert LOOKUP + len(code) <= LOOKUP_END
    exe[LOOKUP - EXE_BASE:LOOKUP_END - EXE_BASE] = code + bytes(LOOKUP_END - LOOKUP - len(code))
    return heap
