"""More room for the game's string class (C++ strings: every expanded message in the
event overlays REVENT / REVCOL / ... goes through it, notes/overflow_audit.md).

Its representation (12-byte header + text + NUL) comes from three fixed pools in .bss:
32 x 48 B (0x8011e61c), 16 x 96 B (0x8011ec20), 4 x 256 B (0x8011f224); each = bitmap
(1 bit per slot, MSB first, rounded up to words) followed by the slots. Nothing larger,
no fallback: a full pool or a string > 243 bytes is silently not allocated and the
string keeps a stale pointer (Historical Events list crashed: English titles filled the
16 slots of 96 B). The pool choice in the allocator (0x80021d90) and the free function
(0x80021c58) are replaced by a table walk: the original pools plus larger ones in free
RAM, a full pool falls through to the next larger one. 0x80021b9c (bitmap, count, size)
still allocates in a pool.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm

EXE_BASE = 0x80010000 - 0x800
ALLOC_SEL = 0x80021db4          # in the allocator: pool choice ... 0x80021e2c (v0 = slot or 0)
ALLOC_SEL_END = 0x80021e2c
FREE = 0x80021c58               # free(rep)
SLOT_ALLOC = 0x80021b9c         # (bitmap, count, size) -> slot or 0
# (slot size, count, bitmap address or None = new in free RAM), smallest first
POOLS = [(48, 32, 0x8011e61c), (96, 16, 0x8011ec20), (96, 32, None),
         (256, 4, 0x8011f224), (256, 16, None), (528, 6, None)]
# clean code at the patch sites (pool choice and the free function's first words)
SEL_HEAD = [0x21820001, 0x2443000c, 0x28610031, 0x10200008]
FREE_HEAD = [0x1080004b, 0x00000000, 0x3c028012, 0x2442e620]


def word(exe, va):
    return struct.unpack_from('<I', exe, va - EXE_BASE)[0]


def put(exe, va, data):
    exe[va - EXE_BASE:va - EXE_BASE + len(data)] = data


def bitmap_bytes(count):
    return (count + 31) // 32 * 4


def code(table):
    return [
        'pick:',                              # t8 = bytes needed; keeps t4/t5 (allocator's)
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('lui', 't6', asm.hi(table)),
        ('addiu', 't6', 't6', asm.lo(table)),
        'pk:',
        ('lw', 't7', 0, 't6'),                # slot size, 0 = end
        ('nop',),
        ('beqz', 't7', 'pkfail'),
        ('slt', 'at', 't7', 't8'),
        ('bnez', 'at', 'pknext'),
        ('lw', 'a1', 4, 't6'),
        ('lw', 'a0', 8, 't6'),
        ('jal', SLOT_ALLOC),
        ('addu', 'a2', 't7', 'zero'),
        ('bnez', 'v0', 'pkout'),
        ('nop',),
        'pknext:',
        ('b', 'pk'),
        ('addiu', 't6', 't6', 12),
        'pkfail:',
        ('addu', 'v0', 'zero', 'zero'),
        'pkout:',
        ('lw', 'ra', 16, 'sp'),
        ('nop',),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24),

        'free:',                              # a0 = slot (header address)
        ('beqz', 'a0', 'frout'),
        ('lui', 't6', asm.hi(table)),
        ('addiu', 't6', 't6', asm.lo(table)),
        'fr:',
        ('lw', 't7', 0, 't6'),
        ('lw', 't1', 4, 't6'),                # count
        ('beqz', 't7', 'frout'),
        ('lw', 't2', 8, 't6'),                # bitmap
        ('addiu', 't3', 't1', 31),
        ('srl', 't3', 't3', 5),
        ('sll', 't3', 't3', 2),
        ('addu', 't3', 't2', 't3'),           # first slot
        ('sltu', 'at', 'a0', 't3'),
        ('bnez', 'at', 'frnext'),
        ('subu', 't0', 'a0', 't3'),
        ('divu', 't0', 't7'),
        ('mflo', 't0'),
        ('nop',),
        ('sltu', 'at', 't0', 't1'),
        ('beqz', 'at', 'frnext'),
        ('andi', 't3', 't0', 7),
        ('addiu', 'v1', 'zero', 0x80),
        ('srlv', 'v1', 'v1', 't3'),
        ('nor', 'v1', 'v1', 'zero'),
        ('srl', 't0', 't0', 3),
        ('addu', 't0', 't2', 't0'),
        ('lbu', 'v0', 0, 't0'),
        ('nop',),
        ('and', 'v0', 'v0', 'v1'),
        ('jr', 'ra'),
        ('sb', 'v0', 0, 't0'),
        'frnext:',
        ('b', 'fr'),
        ('addiu', 't6', 't6', 12),
        'frout:',
        ('jr', 'ra'),
        ('nop',),
    ]


def apply(exe, heap):
    """Patch the program (bytearray); pools + code into heap. Returns bytes of RAM used."""
    assert [word(exe, ALLOC_SEL + 4 * i) for i in range(4)] == SEL_HEAD, 'string allocator'
    assert [word(exe, FREE + 4 * i) for i in range(4)] == FREE_HEAD, 'string free'
    used, rows = 0, []
    for size, count, bitmap in POOLS:
        if bitmap is None:
            n = bitmap_bytes(count) + size * count
            bitmap = heap.put(bytes(n))       # all slots free
            used += n
        rows.append(struct.pack('<3I', size, count, bitmap))
    table = heap.put(b''.join(rows) + bytes(12))
    lst = code(0)
    n = len(asm.assemble(0x80000000, lst))
    at = heap.alloc(n)
    lst = code(table)
    put(exe, at, asm.assemble(at, lst))
    lab = asm.labels(at, lst)
    # allocator: v1 = len + 13 (header + NUL) -> t8, then the table walk; v0 as before
    sel = [('addiu', 't8', 't4', 13), ('jal', lab['pick']), ('nop',), ('b', ALLOC_SEL_END), ('nop',)]
    put(exe, ALLOC_SEL, asm.assemble(ALLOC_SEL, sel))
    put(exe, FREE, asm.assemble(FREE, [('j', lab['free']), ('nop',)]))
    return used + len(rows) * 12 + 12 + n
