"""Table cells laid out in pixels (officer lists and every other column table).

The generic table code builds a row as one string: pad_cell 0x80037780 (a1 = output,
a2 = cell width in px, a3 = flags: & 3 = 0 left, 1 right, 2 centre, 3 right with k = (flags >> 4)
& 3 trailing spaces; the string at caller sp+0x10) writes the string padded with spaces to
width / 8 bytes plus one separating space, and returns the new end (NUL written). At 8 px
a byte this keeps the Japanese columns, but an English label longer than the Japanese bytes
("Empire" in a 4-byte cell) pushes every later cell out of its column, and proportional
spaces shrink the padding. Rows live in fixed-size slots (the Japanese row length + a little),
so a row must never get longer than the Japanese one.

pad_cell is replaced by cell (free RAM): it writes CELL 0x1E + the string (no padding) and
records the column (px, width, flags) in the LAYOUT block (tools/namedict.py); a call whose
output is not where the last one ended starts a new row. A constant string (in the program
image, >= 4 bytes) is written as CELL_REF + its address (4 bytes whatever its length: rows
are tight). A row never passes the end the
Japanese padding would have reached, or more: up to the 38-byte slot's end minus a reserve for the
later columns (min(their Japanese bytes, RESERVE) each; English city names from the string pool
are longer than their 4-byte Japanese cells) (a string is cut at a character boundary). At draw time
namedict's expand turns each CELL into a TAB to the column, aligned by the cell's English
width. All tables share one LAYOUT: the last table built wins (tables are drawn right after
they are built). The row loop's cut of the last byte (0x800360bc) is turned off.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, namedict
from asm import hi, lo

EXE_BASE = 0x80010000 - 0x800
PAD_CELL = 0x80037780
ORIG = 0x27bdffd0                        # addiu sp, sp, -0x30
TRIM = 0x800360bc                        # addiu v0, s3, -1 (+ sb zero, 0(v0)): the row loop cuts the
TRIM_ORIG = 0x2662ffff                   # last byte (the Japanese separating space) -> no cut
CELL = namedict.CELL
STATIC_LO, STATIC_HI = 0x80010000, 0x80119800   # program image (text, data, free RAM): constant
FIRST_RET = 0x80036014                   # return address of the row's first pad_cell call (else: the row loop's)
SLOT = 38                                # every row lives in a 38-byte slot (table + 0x73a + 38 * row)
RESERVE = 7                              # bytes kept for each later column (at most its Japanese bytes):
                                         # CELL + 5 digits, or CELL + a 3-kanji name


def routine(state, layout):
    """state: 5 words (last end, column in bytes, row start, cell index, 0 = empty string)."""
    return [
        ('lw', 't0', 0x10, 'sp'),             # string
        ('lui', 't9', hi(state)),
        ('addiu', 't9', 't9', lo(state)),
        ('lw', 't1', 0, 't9'),
        ('lw', 't2', 4, 't9'),
        ('lw', 't3', 12, 't9'),
        ('bnez', 't0', 'str'),
        ('nop',),
        ('addiu', 't0', 't9', 16),
        'str:',
        ('bne', 'a1', 't1', 'newrow'),
        ('sll', 'a2', 'a2', 16),
        ('b', 'row'),
        ('nop',),
        'newrow:',
        ('addu', 't2', 'zero', 'zero'),
        ('addu', 't3', 'zero', 'zero'),
        ('sw', 'a1', 8, 't9'),
        'row:',
        ('sra', 'a2', 'a2', 16),              # width px (s16)
        ('lui', 't8', hi(layout)),
        ('addiu', 't8', 't8', lo(layout)),
        ('sltiu', 'at', 't3', namedict.MAX_COLS),
        ('beqz', 'at', 'nolay'),
        ('sll', 't4', 't3', 2),
        ('addu', 't4', 't8', 't4'),
        ('sll', 't5', 't2', 3),
        ('sh', 't5', namedict.L_COLS, 't4'),
        ('sb', 'a2', namedict.L_COLS + 2, 't4'),
        ('sb', 'a3', namedict.L_COLS + 3, 't4'),
        ('addiu', 't5', 't3', 1),
        ('sb', 't5', 0, 't8'),                # column count
        'nolay:',
        ('addiu', 't3', 't3', 1),
        ('sw', 't3', 12, 't9'),
        ('sra', 't4', 'a2', 3),
        ('addu', 't2', 't2', 't4'),
        ('addiu', 't2', 't2', 1),
        ('sw', 't2', 4, 't9'),
        ('lw', 't5', 8, 't9'),
        ('addu', 'v0', 'a1', 'zero'),
        ('addu', 't5', 't5', 't2'),           # the Japanese row end so far (its NUL)
        # or more: the slot's end minus a reserve for the later columns (Σ min(bytes, RESERVE))
        ('lui', 'at', hi(FIRST_RET)),
        ('addiu', 'at', 'at', lo(FIRST_RET)),
        ('bne', 'ra', 'at', 'inloop'),
        ('nop',),
        ('lw', 't6', 0x1dc, 's0'),            # first column: the others = table+0x1dc[0x732 ..], 0x731 of them
        ('lbu', 't7', 0x732, 's0'),
        ('lbu', 'a3', 0x731, 's0'),
        ('sll', 't7', 't7', 4),
        ('b', 'sum'),
        ('addu', 't6', 't6', 't7'),
        'inloop:',
        ('addiu', 't6', 's1', 16),            # row loop: s1 = this column, s2 = columns left with it
        ('addiu', 'a3', 's2', -1),
        'sum:',
        ('addu', 'a2', 'zero', 'zero'),
        'sloop:',
        ('blez', 'a3', 'sdone'),
        ('nop',),
        ('lh', 't7', 2, 't6'),
        ('addiu', 'a3', 'a3', -1),
        ('sra', 't7', 't7', 3),
        ('addiu', 't7', 't7', 1),
        ('sltiu', 'at', 't7', RESERVE + 1),
        ('bnez', 'at', 'small'),
        ('addiu', 't6', 't6', 16),
        ('addiu', 't7', 'zero', RESERVE),
        'small:',
        ('b', 'sloop'),
        ('addu', 'a2', 'a2', 't7'),
        'sdone:',
        ('lw', 't7', 8, 't9'),
        ('nop',),
        ('addiu', 't7', 't7', SLOT - 1),
        ('subu', 't7', 't7', 'a2'),
        ('sltu', 'at', 't5', 't7'),
        ('bnez', 'at', 'more'),
        ('nop',),
        ('b', 'lim'),
        ('nop',),
        'more:',
        ('addu', 't5', 't7', 'zero'),
        'lim:',
        # a string of >= 4 bytes in the program image (constant): CELL_REF + its address
        ('lui', 'at', hi(STATIC_LO)),
        ('sltu', 'at', 't0', 'at'),
        ('bnez', 'at', 'text'),
        ('lui', 'at', hi(STATIC_HI)),
        ('addiu', 'at', 'at', lo(STATIC_HI)),
        ('sltu', 'at', 't0', 'at'),
        ('beqz', 'at', 'text'),
        ('addiu', 'a0', 'v0', 4),
        ('sltu', 'at', 't5', 'a0'),
        ('bnez', 'at', 'text'),
        ('lbu', 'v1', 0, 't0'),
        ('lbu', 'a1', 1, 't0'),
        ('beqz', 'v1', 'text'),
        ('lbu', 'v1', 2, 't0'),
        ('beqz', 'a1', 'text'),
        ('lbu', 'a1', 3, 't0'),
        ('beqz', 'v1', 'text'),
        ('nop',),
        ('beqz', 'a1', 'text'),
        ('addiu', 'at', 'zero', namedict.CELL_REF),
        ('sb', 'at', 0, 'v0'),
        ('srl', 'at', 't0', 14),
        ('andi', 'at', 'at', 0x7F),
        ('ori', 'at', 'at', 0x80),
        ('sb', 'at', 1, 'v0'),
        ('srl', 'at', 't0', 7),
        ('andi', 'at', 'at', 0x7F),
        ('ori', 'at', 'at', 0x80),
        ('sb', 'at', 2, 'v0'),
        ('andi', 'at', 't0', 0x7F),
        ('ori', 'at', 'at', 0x80),
        ('sb', 'at', 3, 'v0'),
        ('b', 'end'),
        ('addu', 'v0', 'a0', 'zero'),
        'text:',
        ('addiu', 'at', 'zero', CELL),
        ('sb', 'at', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        'copy:',
        ('lbu', 'v1', 0, 't0'),
        ('addiu', 'a0', 'v0', 1),
        ('beqz', 'v1', 'end'),
        ('addiu', 'at', 'v1', -0x81),
        ('sltiu', 'at', 'at', 0x1F),
        ('bnez', 'at', 'two'),
        ('addiu', 'at', 'v1', -0xE0),
        ('sltiu', 'at', 'at', 0x1D),
        ('beqz', 'at', 'one'),
        ('nop',),
        'two:',
        ('addiu', 'a0', 'v0', 2),
        'one:',
        ('sltu', 'at', 't5', 'a0'),
        ('bnez', 'at', 'end'),
        ('nop',),
        'cp:',
        ('lbu', 'v1', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'v1', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('bne', 'v0', 'a0', 'cp'),
        ('nop',),
        ('b', 'copy'),
        ('nop',),
        'end:',
        ('sb', 'zero', 0, 'v0'),
        ('jr', 'ra'),
        ('sw', 'v0', 0, 't9'),
    ]


def model(rows):
    """Python model: rows [[(string bytes, width px, flags)]] (each a new row; string = (bytes,
    address) when it lies in the program image) ->
    ([row bytes], layout of the last row as namedict.Dict.layout)."""
    out = []
    for row in rows:
        b, col, layout = bytearray(), 0, []
        for k, (t, w, flags) in enumerate(row):
            if len(layout) < namedict.MAX_COLS:
                layout.append((col * 8, w & 0xFF, flags & 0xFF))
            col += (w >> 3) + 1
            lim = max(col, SLOT - 1 - sum(min((x[1] >> 3) + 1, RESERVE) for x in row[k + 1:]))
            if isinstance(t, tuple):              # (string, address in the program image)
                t, a = t
                if len(t) >= 4 and len(b) + 4 <= lim:
                    a &= 0x1FFFFF
                    b += bytes([namedict.CELL_REF, 0x80 | a >> 14 & 0x7F, 0x80 | a >> 7 & 0x7F, 0x80 | a & 0x7F])
                    continue
            b.append(CELL)
            i = 0
            while i < len(t):
                n = 2 if namedict.is_lead(t[i]) else 1
                if len(b) + n > lim:
                    break
                b += t[i:i + n]
                i += n
        out.append(bytes(b))
    return out, layout


def apply(exe, heap, layout):
    """Returns bytes of RAM used."""
    assert struct.unpack_from('<I', exe, PAD_CELL - EXE_BASE)[0] == ORIG
    assert struct.unpack_from('<I', exe, TRIM - EXE_BASE)[0] == TRIM_ORIG
    exe[TRIM - EXE_BASE:TRIM - EXE_BASE + 4] = asm.assemble(TRIM, [('addiu', 'v0', 's3', 0)])
    state = heap.put(bytes(20))
    L = routine(state, layout)
    size = len(asm.assemble(0x80000000, L))
    at = heap.alloc(size)
    exe[at - EXE_BASE:at - EXE_BASE + size] = asm.assemble(at, L)
    exe[PAD_CELL - EXE_BASE:PAD_CELL - EXE_BASE + 8] = asm.assemble(PAD_CELL, [('j', at), ('nop',)])
    return size + 20
