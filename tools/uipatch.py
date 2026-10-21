"""Single-instruction UI patches (checked against the clean program/overlay): one reference
of a shared string pointed at another string, where one use needs a shorter English than
the others; byte pokes into the Japanese layout. build.py applies them before the strings.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm

EXE_BASE = 0x80010000 - 0x800
OVL_BASE = 0x80199000
GP = 0x8011da2c

PATCHES = [
    # (tag, va, clean instruction, new instruction, why)
    # HEX unit info box: the officer label (32 px plate) shares 武将 PROG:80118678 with the
    # box's "Officer" button; the label takes 大将 PROG:80118738 ("Cmdr.") instead.
    ('HEX', 0x801a7f68, ('addiu', 'a3', 'gp', 0x80118678 - GP), ('addiu', 'a3', 'gp', 0x80118738 - GP)),
    # City list page 3 (Farm/MaxF/Comm./MaxC): a row hook wrote '/' over the Japanese separator
    # bytes 9 and 19 ("31/120"); table rows are cells now (tools/cells.py): the byte lands in a
    # digit ("1/0"). Separate columns instead.
    ('PROG', 0x800a2758, ('sb', 'v0', 9, 's0'), ('nop',)),
    ('PROG', 0x800a275c, ('sb', 'v0', 0x13, 's0'), ('nop',)),
    # Ruler panel (Dipl / Info ruler select, built 0x800b5c4c, placed 0x800b5430..): the city plate
    # takes style 2 (48 px) instead of 1 (32 px, two kanji): English city names were clipped
    # ("Puyanc"); the name plate (64 px) moves 8 px right to make room (x 0xde -> 0xe6).
    ('PROG', 0x800b5d84, ('addu', 'a1', 'a2', 'zero'), ('addiu', 'a1', 'zero', 2)),
    ('PROG', 0x800b5514, ('addiu', 'a2', 'zero', 0xde), ('addiu', 'a2', 'zero', 0xe6)),
    # the same pair in the sibling panel (built 0x800bf00c, placed 0x800be6b8 / 0x800be904)
    ('PROG', 0x800bf034, ('addu', 'a1', 'a2', 'zero'), ('addiu', 'a1', 'zero', 2)),
    ('PROG', 0x800be904, ('addiu', 'a2', 'zero', 0xda), ('addiu', 'a2', 'zero', 0xe2)),
    # New-officer summary (INIT window table, 24-byte entries: ?, id, style | ..., x, y, str): the
    # dream value (0x272b) has style 0x9f (two kanji): "Defense" came out "Defen"; style 0xa2 =
    # the name/parent values (room up to the stats panel at x 0xd0). Data words, not code.
    ('INIT', 0x801d5828, 0x8b02009f, 0x8b0200a2),
    # Army Setup (GunjiHenseiWindow, PROG table 0x800f3d90): the "remaining troops" header (x 0xe8)
    # follows the city name with two kanji of room ("PuyangRemaining"): 24 px further right.
    ('PROG', 0x800f3df4, 0xe8, 0x100),
    # Edit > Officer (USER refresh 0x801a4a80): the name's kana reading (record via 0x80050430,
    # half-width -> hiragana by 0x800c5bf4 into sp+0x20) is drawn small above the English name
    # (text object 0x2734). Hidden: the buffer is emptied instead of converted.
    ('USER', 0x801a4cf0, ('jal', 0x800c5bf4), ('sb', 'zero', 0x20, 'sp')),
    # the same reading line in the other officer panels (bio windows: save screen, officer info ...):
    # every 0x80050430 -> 0x800c5bf4 -> setText site (INIT 0x801aae28 stores the reading: kept)
    ('PROG', 0x8008bdac, ('jal', 0x800c5bf4), ('sb', 'zero', 0x30, 'sp')),
    ('USER', 0x801a24b8, ('jal', 0x800c5bf4), ('sb', 'zero', 0x30, 'sp')),
    ('INIT', 0x801afc78, ('jal', 0x800c5bf4), ('sb', 'zero', 0, 's1')),
    ('MC', 0x8019a4ac, ('jal', 0x800c5bf4), ('sb', 'zero', 0x30, 'sp')),
    # Officer card rank line (0x8008b8c8): a general's title got 将軍 (PROG:801160e0) appended
    # ("Horse Gen.Rank"); the English titles carry "Gen." themselves.
    ('PROG', 0x8008b8c8, ('jal', 0x80014d70), ('nop',)),
    # Name entry (INIT officer / USER force name): the Kanji mode (4: kana initials -> kanji with that
    # reading) lists nothing, the kanji font is mostly gone (tools/kanji.py). Name fields open on the
    # ABC grid (mode 3) instead of 4, and the Kanji button (0x2711) keeps the current mode (s2 already
    # holds it). Its label is blanked in tl/progstr (PROG:801175d0 / 80117c38).
    ('INIT', 0x801aa8ac, ('addiu', 'v0', 'zero', 4), ('addiu', 'v0', 'zero', 3)),
    ('INIT', 0x801abf84, ('addiu', 's2', 'zero', 4), ('nop',)),
    ('USER', 0x801a6888, ('addiu', 'v0', 'zero', 4), ('addiu', 'v0', 'zero', 3)),
    ('USER', 0x801a78b4, ('addiu', 's2', 'zero', 4), ('nop',)),
]


# Info > Fields (battlefield list, USER): each page strcpy's its 7 labels (msg 62077 + 7 * page + row,
# 戦場＜都市→都市＞) into 35-byte records (obj + 0x1e4 + 35 * row) and gives the label widget
# (0x8002fee0, keeps the pointer) that record. English names every city: the records move to
# FIELD_ROWS x FIELD_SIZE bytes of free RAM (row = s0).
FIELD_COPY = ('USER', 0x801b4f24, [0x001010c0, 0x00501822, 0x00031080, 0x00621020, 0x02221021, 0x244401e4])
FIELD_ROWS, FIELD_SIZE = 7, 64


def fields(progs, heap):
    """Field label records -> free RAM. Returns the bytes used."""
    tag, va, words = FIELD_COPY
    o = _off(tag, va)
    assert progs[tag][o:o + 4 * len(words)] == b''.join(w.to_bytes(4, 'little') for w in words), 'field label copy'
    buf = heap.alloc(FIELD_ROWS * FIELD_SIZE)
    progs[tag][o:o + 4 * len(words)] = asm.assemble(va, [
        ('sll', 'v0', 's0', FIELD_SIZE.bit_length() - 1), ('lui', 'a0', asm.hi(buf)), ('addu', 'a0', 'a0', 'v0'),
        ('addiu', 'a0', 'a0', asm.lo(buf)), ('nop',), ('nop',)])
    return FIELD_ROWS * FIELD_SIZE


# Load/save list (MC.BIN, "%3d/%2d %5s %4s": date, scenario title, ruler): the save record keeps the
# title in 11 bytes (memcpy(rec + 0xd, title(scenario), 11) at MC 0x8019c88c; title() = 0x800d6714,
# scenarios 1-23 -> table 0x80114a10): English titles came out cut ("Death of Do"). The call goes to
# a copy of title() with short forms instead (the full titles stay everywhere else).
SAVE_TITLE_CALL = ('MC', 0x8019c87c, ('jal', 0x800d6714))
SAVE_TITLES = [
    'Y. Turbans', 'Dong Zhuo', 'Warlords', 'Hebei Feud', 'Dragon/Phnx', 'Hanzhong', 'Wuzhang',
    'Volunteers', 'vs. Lu Bu', 'Hebei Unity', 'Yuan Shu', 'Red Cliffs', 'Ma Chao', 'North Exp.',
    'Duels', 'Zhuo Slain', 'Into Shu', 'Liu Bei End', 'White Horse', 'Sun Ce', 'Yuan Rout',
    'North Camp.', 'Zhuge Dan']


def save_titles(progs, heap):
    """Short scenario titles for the save record. Returns the bytes of RAM used."""
    assert len(SAVE_TITLES) == 23 and all(len(t) <= 11 for t in SAVE_TITLES)
    tag, va, old = SAVE_TITLE_CALL
    o = _off(tag, va)
    assert progs[tag][o:o + 4] == asm.assemble(va, [old]), 'save title call'
    ptrs = [heap.put(t.encode('ascii') + b'\0') for t in SAVE_TITLES]
    tab = heap.put(b''.join(struct.pack('<I', a) for a in ptrs))
    code = heap.alloc(44)
    b = asm.assemble(code, [
        ('addiu', 'v0', 'a0', -1), ('sltiu', 'at', 'v0', 23), ('beq', 'at', 'zero', 'none'), ('sll', 'v0', 'v0', 2),
        ('lui', 'at', asm.hi(tab)), ('addu', 'at', 'at', 'v0'), ('lw', 'v0', asm.lo(tab), 'at'), ('jr', 'ra'),
        ('nop',), 'none:', ('jr', 'ra'), ('addu', 'v0', 'zero', 'zero')])
    assert len(b) == 44
    heap.exe[code - EXE_BASE:code - EXE_BASE + 44] = b
    progs[tag][o:o + 4] = asm.assemble(va, [('jal', code)])
    return sum(len(t) + 1 for t in SAVE_TITLES) + 4 * 23 + 44


def _off(tag, va):
    return va - EXE_BASE if tag == 'PROG' else va - OVL_BASE


def apply(progs):
    """progs: {tag: bytearray}. Returns the number of patched instructions."""
    for tag, va, old, new in PATCHES:
        o = _off(tag, va)
        old, new = [w.to_bytes(4, 'little') if isinstance(w, int) else asm.assemble(va, [w]) for w in (old, new)]
        assert progs[tag][o:o + 4] == old, (tag, hex(va))
        progs[tag][o:o + 4] = new
    return len(PATCHES)
