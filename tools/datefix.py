"""Dates on the main map as "Apr 192" (glossary) instead of year and month numbers.

Two screens draw the date as two text objects: the year (sprintf "%3d年" into one buffer,
drawn at x+8) and the month (sprintf "%2d月" into the next one, at x+48): the map header
(program: created at 0x800df7a4, updated at 0x800dfab0) and the force panel (USER.BIN). The year's sprintf call goes to dt_year in free
RAM, which reads the month from the current date itself and prints "Apr 192" into the year
buffer; the month format becomes " " (tl/progstr), so the month object stays blank.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm
from asm import hi, lo

OVL_BASE = 0x80199000
EXE_BASE = 0x80010000 - 0x800
SPRINTF = 0x8003fc28
CUR_DATE = 0x800d55d4           # a0 = 0 -> v0 = date record (+8 year-100, +9 month)
JAL_SPRINTF = 0x0c00ff0a
SITES = [('PROG', 0x800df7a4), ('PROG', 0x800dfab0), ('USER', 0x801ce06c)]   # jal sprintf of the year
MONTHS = b''.join(m.encode() + b'\0' for m in
                  'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())


def _off(tag, va):
    return va - EXE_BASE if tag == 'PROG' else va - OVL_BASE


def apply(progs, heap):
    """progs: {tag: bytearray}; code + month names into heap. Returns bytes of RAM used."""
    for tag, va in SITES:
        assert struct.unpack_from('<I', progs[tag], _off(tag, va))[0] == JAL_SPRINTF, (tag, hex(va))
    months = heap.put(MONTHS)
    fmt = heap.put(b'%s %d\0')
    L = [                                     # a0 = buffer, a2 = year
        ('addiu', 'sp', 'sp', -32),
        ('sw', 'ra', 28, 'sp'),
        ('sw', 'a0', 16, 'sp'),
        ('sw', 'a2', 20, 'sp'),
        ('jal', CUR_DATE),
        ('addu', 'a0', 'zero', 'zero'),
        ('lbu', 't0', 9, 'v0'),
        ('lui', 't1', hi(months)),
        ('addiu', 't0', 't0', -1),
        ('sltiu', 'at', 't0', 12),
        ('bnez', 'at', 'ok'),
        ('sll', 't0', 't0', 2),
        ('addu', 't0', 'zero', 'zero'),
        'ok:',
        ('addiu', 't1', 't1', lo(months)),
        ('addu', 'a2', 't1', 't0'),
        ('lw', 'a0', 16, 'sp'),
        ('lw', 'a3', 20, 'sp'),
        ('lui', 'a1', hi(fmt)),
        ('jal', SPRINTF),
        ('addiu', 'a1', 'a1', lo(fmt)),
        ('lw', 'ra', 28, 'sp'),
        ('nop',),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 32),
    ]
    return len(MONTHS) + 8 + _place(progs, heap, L)


def _place(progs, heap, L):
    size = len(asm.assemble(0x80000000, L))
    at = heap.alloc(size)
    code = asm.assemble(at, L)
    progs['PROG'][at - EXE_BASE:at - EXE_BASE + size] = code
    for tag, va in SITES:
        progs[tag][_off(tag, va):_off(tag, va) + 4] = asm.assemble(va, [('jal', at)])
    return size
