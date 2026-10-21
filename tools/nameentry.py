"""Latin letters in the name entry screens (notes/engine.md "Name entry").

Two screens use the same code: INIT.BIN (new officer: surname, given name, style name
[4 bytes each], kana reading) and USER.BIN (force name, 4 bytes). The fields are the
record fields of the game ([5] with the NUL), so a name stays within 4 bytes: 2 kanji
or 4 Latin letters.

  - the katakana grid (mode 3, button カナ) becomes A-Z ' - . (full-width Shift-JIS
    cells, drawn by the kanji path);
  - append (the cell handler's copy loop) goes through nm_append in free RAM: a Latin
    cell is stored as one ASCII byte, upper case at the start of the field and lower
    case after it ("Liu"); other cells keep their 2 bytes but only when both fit (the
    game checked len < max, enough for 2 bytes only while lengths were even); Latin is
    refused in the reading field (INIT field 4: 0x800c5b44 converts the reading to
    half-width kana and loops forever on a byte pair it does not know); blank cells
    (grid padding, the empty slots of a kanji list) are refused (they appended NULs);
  - backspace removes the last character (1 or 2 bytes) instead of always 2 bytes, and
    its callers act from 1 byte instead of 2.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm
from asm import hi, lo

OVL_BASE = 0x80199000
EXE_BASE = 0x80010000 - 0x800
GRID = ['ＡＢＣＤＥＦＧＨＩＪ', 'ＫＬＭＮＯＰＱＲＳＴ', 'ＵＶＷＸＹＺ’－．']
LATIN = {0x8166: 0x27, 0x817C: 0x2D, 0x8144: 0x2E}      # ’ － ． (letters: 0x8260-0x8279)


class Screen:
    def __init__(self, tag, length, maxlen, grid, cursor, page, field, grid_va, count_va,
                 append_at, append_back, bs_at, bs_back, bs_checks, append_stub):
        self.__dict__.update(locals())
        del self.__dict__['self']


# append_at / bs_at: where the copy loop starts (a1 = field pointer there for INIT);
# *_back: where the original code continues; bs_checks: 'sltiu at, v0, 2' before the
# backspace calls; append_stub: USER's append is a whole function (a0 = struct).
SCREENS = [
    Screen('INIT', 0x298, 0x29c, 0x16d8, 0x16dc, 0x16e4, 0x2c0, 0x801d3fd8, 0x8011763a,
           0x801aa470, 0x801aa4f4, 0x801aa5a0, 0x801aa5f0, [0x801ac2fc], False),
    Screen('USER', 0x278, 0x27c, 0x12e4, 0x12e8, 0x12f0, None, 0x801ddcf4, 0x80117c9e,
           0x801a6624, 0x801a65b8, 0x801a66cc, 0x801a6720, [0x801a7b38], True),
]
ORIG = {   # first words of each patched place (clean disc)
    0x801aa470: 0x10000015, 0x801aa5a0: 0x10000008, 0x801ac2fc: 0x2c410002,
    0x801a6624: 0x24850272, 0x801a66cc: 0x24830272, 0x801a7b38: 0x2c410002,
}


def grid_bytes():
    cells = ''.join(r + '　' * (10 - len(r)) for r in GRID)
    return cells.encode('cp932').replace('　'.encode('cp932'), b'  ') + b'\0', len(cells)


def routines(sc):
    """nm_append(a0 = entry struct, a1 = field) and nm_bs(a0, a1); a0, a1 kept."""
    L = [
        'append:',
        ('lw', 't0', sc.page, 'a0'),
        ('lw', 't1', sc.cursor, 'a0'),
        ('sll', 't2', 't0', 5),               # page * 50
        ('sll', 't3', 't0', 4),
        ('addu', 't2', 't2', 't3'),
        ('sll', 't3', 't0', 1),
        ('addu', 't2', 't2', 't3'),
        ('addu', 't1', 't1', 't2'),
        ('lw', 't0', sc.grid, 'a0'),
        ('sll', 't1', 't1', 1),
        ('addu', 't0', 't0', 't1'),
        ('lbu', 't2', 0, 't0'),
        ('lbu', 't3', 1, 't0'),
        ('sltiu', 'at', 't2', 0x81),          # blank cell (padding, empty kanji list): nothing
        ('bnez', 'at', 'done'),
        ('lw', 't4', sc.length, 'a0'),
        ('lw', 't5', sc.maxlen, 'a0'),
        ('sll', 't6', 't2', 8),
        ('or', 't6', 't6', 't3'),
        ('ori', 'at', 'zero', 0x8260),
        ('subu', 't7', 't6', 'at'),
        ('sltiu', 'at', 't7', 26),
        ('bnez', 'at', 'letter'),
        ('addiu', 't7', 't7', 0x41),
    ]
    for code, ch in LATIN.items():
        L += [('ori', 'at', 'zero', code), ('beq', 't6', 'at', 'one'), ('addiu', 't7', 'zero', ch)]
    L += [
        ('addiu', 't6', 't4', 2),             # 2-byte character: both bytes must fit
        ('slt', 'at', 't5', 't6'),
        ('bnez', 'at', 'done'),
        ('addu', 't6', 'a1', 't4'),
        ('sb', 't2', 0, 't6'),
        ('sb', 't3', 1, 't6'),
        ('b', 'term'),
        ('addiu', 't4', 't4', 2),
        'letter:',
        ('beqz', 't4', 'one'),                # upper case first, then lower case
        ('nop',),
        ('ori', 't7', 't7', 0x20),
        'one:',
    ]
    if sc.field is not None:                  # never ASCII in the kana reading
        L += [('lbu', 't6', sc.field, 'a0'), ('addiu', 'at', 'zero', 4), ('beq', 't6', 'at', 'done'), ('nop',)]
    L += [
        ('slt', 'at', 't4', 't5'),
        ('beqz', 'at', 'done'),
        ('addu', 't6', 'a1', 't4'),
        ('sb', 't7', 0, 't6'),
        ('addiu', 't4', 't4', 1),
        'term:',
        ('addu', 't6', 'a1', 't4'),
        ('sb', 'zero', 0, 't6'),
        ('sw', 't4', sc.length, 'a0'),
        'done:',
        ('jr', 'ra'),
        ('nop',),

        'bs:',                                # cut the last character (1 or 2 bytes)
        ('lw', 't4', sc.length, 'a0'),
        ('addu', 't0', 'zero', 'zero'),
        ('addu', 't1', 'zero', 'zero'),
        'bl:',
        ('slt', 'at', 't0', 't4'),
        ('beqz', 'at', 'bdone'),
        ('addu', 't2', 'a1', 't0'),
        ('lbu', 't2', 0, 't2'),
        ('addu', 't1', 't0', 'zero'),
        ('addiu', 't3', 't2', -0x81),
        ('sltiu', 'at', 't3', 0x1F),
        ('bnez', 'at', 'two'),
        ('addiu', 't3', 't2', -0xE0),
        ('sltiu', 'at', 't3', 0x1D),
        ('bnez', 'at', 'two'),
        ('nop',),
        ('b', 'bl'),
        ('addiu', 't0', 't0', 1),
        'two:',
        ('b', 'bl'),
        ('addiu', 't0', 't0', 2),
        'bdone:',
        ('addu', 't2', 'a1', 't1'),
        ('sb', 'zero', 0, 't2'),
        ('jr', 'ra'),
        ('sw', 't1', sc.length, 'a0'),
    ]
    return L


def counts(progs):
    """Cell counts of the Latin grids, again after progstr: each count byte is byte 2 of a
    "2__K..." button-label string (PROG:80117638 / 80117c9c), which progstr rewrites with the
    Japanese '_' (0x5f = 95 katakana cells: the ABC grid showed leftover katakana and a page 2)."""
    n = grid_bytes()[1]
    for sc in SCREENS:
        off = sc.count_va - EXE_BASE
        assert progs['PROG'][off] in (0x5f, n), hex(sc.count_va)
        progs['PROG'][off] = n
    return len(SCREENS)


def _put(img, tag, va, data):
    off = va - EXE_BASE if tag == 'PROG' else va - OVL_BASE
    img[off:off + len(data)] = data


def _word(img, tag, va):
    off = va - EXE_BASE if tag == 'PROG' else va - OVL_BASE
    return struct.unpack_from('<I', img, off)[0]


def apply(progs, heap):
    """progs: {tag: bytearray}; code into heap (resident). Returns bytes of RAM used."""
    grid, n = grid_bytes()
    used = 0
    for sc in SCREENS:
        img = progs[sc.tag]
        for va in (sc.append_at, sc.bs_at, *sc.bs_checks):
            assert _word(img, sc.tag, va) == ORIG[va], '%s %08x' % (sc.tag, va)
        L = routines(sc)
        size = len(asm.assemble(0x80000000, L))
        at = heap.alloc(size)
        _put(progs['PROG'], 'PROG', at, asm.assemble(at, L))
        used += size
        lab = asm.labels(at, L)
        _put(img, sc.tag, sc.grid_va, grid)
        _put(progs['PROG'], 'PROG', sc.count_va, bytes([n]))
        if sc.append_stub:                    # whole function: a0 = struct; tail-calls the redraw
            stub = [('addiu', 'sp', 'sp', -24), ('sw', 'ra', 16, 'sp'), ('jal', lab['append']),
                    ('addiu', 'a1', 'a0', 0x272), ('lw', 'ra', 16, 'sp'), ('nop',),
                    ('j', sc.append_back), ('addiu', 'sp', 'sp', 24)]
            bs = [('addiu', 'a1', 'a0', 0x272), ('jal', lab['bs']), ('nop',), ('b', sc.bs_back), ('nop',)]
        else:                                 # s0 = struct, a1 = field
            stub = [('jal', lab['append']), ('addu', 'a0', 's0', 'zero'), ('b', sc.append_back), ('nop',)]
            bs = [('jal', lab['bs']), ('addu', 'a0', 's0', 'zero'), ('b', sc.bs_back), ('nop',)]
        _put(img, sc.tag, sc.append_at, asm.assemble(sc.append_at, stub))
        _put(img, sc.tag, sc.bs_at, asm.assemble(sc.bs_at, bs))
        for va in sc.bs_checks:
            _put(img, sc.tag, va, asm.assemble(va, [('sltiu', 'at', 'v0', 1)]))
    return used
