"""Japanese -> English name dictionary in free RAM + the print loop that uses it.

print_string 0x8003f068 draws a NUL-terminated string byte by byte (putc). Its loop is
replaced by print_all (in the heap):

  1. expand the string into BUF: at every 2-byte character, look up the longest
     dictionary key; a hit is replaced by its English. When the hit is a surname (S)
     and a given/style name (G) follows directly, that is looked up too and joined
     with a space ("曹操" = 曹 S + 操 G -> "Cao Cao"), so full names are not stored.
     No hit -> the character is copied (drawn in Japanese).
  2. draw BUF through the word-wrap wrapper (tools/vwf.py) and putc, and stop when a
     line after the first would start below the pixel buffer: the buffer is
     wrap width x height (0x800409e4) and the blitter never checks the height, so
     English longer than the Japanese box would overwrite the memory after it.
     (Not covered: a single word wider than the line wraps inside putc.)
  Columns: the Japanese lays tables out in padded strings at 8 px a byte (list rows
  like "  曹純   征服  23   99", sprintf "%-9s" / "%3d"). With proportional English the
  source column (bytes since the line start) is kept where the padding says so:
     - a run of >= 2 spaces before digits: the number is right-aligned on the
       Japanese end column of its digits (digits are tabular, tools/pixfont.py);
     - a run of >= 2 spaces before anything else: left-aligned on the column after
       the run;
     - right after a name hit, one space is enough (before digits or Japanese);
     - spaces at the start of a line before a name (Japanese centring of short
       names in a column) become a one-column (8 px) indent;
     - a single space before digits (not after a name) is digit-wide at draw time.
  Lines with ESC (colour codes) keep plain spaces. The tab in the expanded text:
  TAB 0x1F, 0x20 + (px >> 6), 0x20 + (px & 63) (never a control or lead byte);
  print_all sets x = max(x + space, line start + px); measure() does the same.
  Table rows (tools/cells.py) mark each cell with CELL 0x1E (CELL_REF 0x1D + 3 bytes: the cell's
  string is at that address) instead of padding; the
  columns are in the LAYOUT block. expand puts a TAB at each cell, aligned (left / right /
  centre as the table asks) once the cell's English width is known; spaces in cells are plain.

Dictionary (built from {japanese: english} pairs, tools/names.py):
  IDX:  u32 n, then n x (u16 first character (lead << 8 | trail), u16 offset into BLOB),
        sorted by character
  BLOB: per first character, entries longest key first: u8 header = 0x20 | S 0x80 |
        G 0x40 | length of the rest of the key (< 32), the rest of the key, the English,
        NUL; a 0 byte ends the group.
pack() checks every pair with expand() (a model of the MIPS code) and stores a pair
literally (no S/G flags) when the composition would give another English.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont, text, vwf
from asm import hi, lo

PRINT_CALL = 0x8003f12c                  # b 0x8003f144 / lbu v0, 0(s0): enters print_string's loop
PRINT_EXIT = 0x8003f150                  # addiu v0, zero, 1 after the loop
GP_CELL_H, GP_BUF_H = -0x7f7e, -0x4962
CAP = 1024                               # expanded string (messages <= 510 bytes)
F_ANY, F_G, F_S = 0x20, 0x40, 0x80
TAB, TAB_BASE = 0x1F, 0x20
CELL = 0x1E                              # table cell marker (tools/cells.py)
CELL_REF = 0x1D                          # cell whose string is elsewhere: + 3 bytes 0x80 | 7 bits of
RAM = 0x80000000                         # the address (21 bits, RAM offset)
# LAYOUT block (tools/cells.py writes, expand reads): u8 column count, 3 pad, 16 x (u16 px,
# u8 width px, u8 flags), then expand's cell index (word) and open cell (word: its tab in BUF)
MAX_COLS, L_COLS, L_CI, L_P = 16, 4, 68, 72
LAYOUT_SIZE = 76
WIDTHS = bytes(pixfont.glyphs()[chr(c)][1] for c in range(0x20, 0x7F))
DIGIT_ADV = pixfont.DIGIT_W + pixfont.SPACING
SPACE_W = pixfont.SPACE_ADV
FIG_EXTRA = DIGIT_ADV - SPACE_W


def is_lead(c):
    return 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC


def cell_px(buf, widths=None):
    """Pixel width of an expanded table cell (no tabs, no newlines)."""
    widths = widths or WIDTHS
    x, i = 0, 0
    while i < len(buf):
        c = buf[i]
        i += 1
        if c == 0x20:
            j = i
            while j < len(buf) and buf[j] == 0x20:
                j += 1
            x += SPACE_W + (FIG_EXTRA if j < len(buf) and 0x30 <= buf[j] <= 0x39 else 0)
        elif c < 0x20:
            pass
        elif c < 0x7F:
            x += widths[c - 0x20]
        elif 0xA0 <= c < 0xE0:
            x += vwf.DEFAULT_W
        else:
            x += 16
            i += 1
    return x


class Dict:
    """entries: {key bytes: (english bytes, flags)}; layout: table columns [(px, width, flags)]
    as tools/cells.py records them (model of the LAYOUT block)"""
    def __init__(self):
        self.ent = {}
        self.layout = []
        self.deref = lambda a: b''               # CELL_REF: address -> string (model)

    def lookup(self, s, i, mask):
        best = None
        for n in range(len(s) - i, 1, -1):
            e = self.ent.get(s[i:i + n])
            if e and e[1] & mask:
                return e[0], n, e[1]
        return best

    def expand(self, s):
        out, i, start, esc = bytearray(), 0, 0, False
        ci, cell = 0, [None]

        def cell_end():
            p = cell[0]
            if p is None:
                return
            cell[0] = None
            col, w, flags = self.layout[ci - 1]
            px = cell_px(out[p + 3:])
            free = w - px
            off = [0, free, free >> 1, free - ((flags >> 4) & 3) * 8][flags & 3]
            col += max(off, 0)
            out[p + 1], out[p + 2] = TAB_BASE + (col >> 6), TAB_BASE + (col & 63)

        def tab(px):
            out.extend([TAB, TAB_BASE + (px >> 6), TAB_BASE + (px & 63)])

        def spaces(i, after_name):
            """at a space: (new i, True) when a column tab was emitted"""
            if esc or len(out) > CAP - 4 - 1:
                return i, False
            j = i
            while j < len(s) and s[j] == 0x20:
                j += 1
            if i == start and j + 1 < len(s) and is_lead(s[j]) and self.lookup(s, j, F_ANY):
                tab(8)                                  # centring before a name: one column
                return j, True
            n, k = j - i, j
            while k < len(s) and 0x30 <= s[k] <= 0x39:
                k += 1
            if k > j:                                   # digits: right-align
                if n >= 2 or after_name:
                    tab((k - start) * 8 - (k - j) * DIGIT_ADV)
                    return j, True
            elif n >= 2 or (after_name and j < len(s) and s[j] >= 0x80):
                tab((j - start) * 8)
                return j, True
            return i, False

        while i < len(s):
            c = s[i]
            if not is_lead(c) or i + 1 >= len(s):
                if c == 0x0A:
                    cell_end()
                    start, esc, ci = i + 1, False, 0
                elif c == 0x1B:
                    esc = True
                elif c in (CELL, CELL_REF):             # table cell (tools/cells.py)
                    cell_end()
                    if ci < len(self.layout):
                        cell[0] = len(out)
                        out += bytes([TAB, TAB_BASE, TAB_BASE])
                        ci += 1
                        esc = True                      # plain spaces inside cells
                    i += 1
                    if c == CELL_REF:
                        a = RAM | (s[i] & 0x7F) << 14 | (s[i + 1] & 0x7F) << 7 | s[i + 2] & 0x7F
                        out += self.deref(a)[:CAP - 4 - len(out)]
                        i += 3
                    continue
                elif c == 0x20:
                    i, done = spaces(i, False)
                    if done:
                        continue
                elif (0x41 <= c <= 0x5A and len(out) >= 2 and 0x61 <= out[-1] <= 0x7A
                      and out[-2] != 0x1B):
                    out.append(0x20)                    # "BbbbBbbb": Latin surname + given name
                out.append(c)
                i += 1
                continue
            hit = self.lookup(s, i, F_ANY)
            if not hit:
                out += s[i:i + 2]
                i += 2
                continue
            out += hit[0]
            i += hit[1]
            if hit[2] & F_S:
                g = self.lookup(s, i, F_G)
                if g:
                    out += b' ' + g[0]
                    i += g[1]
            if i < len(s) and s[i] == 0x20:
                i, _ = spaces(i, True)
        cell_end()
        return bytes(out)


def fig_space(buf, i):
    """The space at buf[i] is drawn digit-wide: digits follow (after more spaces) and it is
    not prose (the byte before is no letter and none of , - .; read even at i = 0)."""
    p = (buf[i - 1] if i else 0) | 0x20
    if 0x61 <= p < 0x7B or 0x2C <= p < 0x2F:
        return False
    j = i + 1
    while j < len(buf) and buf[j] == 0x20:
        j += 1
    return j < len(buf) and 0x30 <= buf[j] <= 0x39


def measure_px(buf, widths):
    """Pixel width of the widest line of an expanded string, as measure() counts it."""
    x, i, widest = 0, 0, 0
    while i < len(buf):
        c = buf[i]
        i += 1
        if c == 0x0A:
            widest, x = max(widest, x), 0
        elif c == 0x1B:
            while i < len(buf) and buf[i] != 0x43:
                i += 1
            i += 1
        elif c == TAB:
            x = max(x + SPACE_W, (buf[i] - TAB_BASE) << 6 | (buf[i + 1] - TAB_BASE))
            i += 2
        elif c == 0x20:
            x += SPACE_W + (FIG_EXTRA if fig_space(buf, i - 1) else 0)
        elif c < 0x20:
            pass
        elif c < 0x7F:
            x += widths[c - 0x20]
        elif 0xA0 <= c < 0xE0:
            x += vwf.DEFAULT_W
        else:
            x += 16
            i += 1
    return max(widest, x)


def build(pairs, surnames, givens):
    """pairs {jp str: en str}; surnames/givens: sets of jp keys that compose."""
    d = Dict()
    enc = {text.encode(j): e.encode('ascii') for j, e in pairs.items()}
    # a one-kanji given name (登, 表, 武…) matches only after a surname, never on its own: alone it is
    # far more often part of a Japanese word left in a string (登録 -> "Deng録")
    after_only = {k for k in enc if len(text.decode(k)) == 1 and text.decode(k) in givens
                  and text.decode(k) not in surnames}
    for k, e in enc.items():
        j = text.decode(k)
        f = (0 if k in after_only else F_ANY) | (F_S if j in surnames else 0) | (F_G if j in givens else 0)
        if f != F_ANY:
            d.ent[k] = (e, f)
    enc = {k: e for k, e in enc.items() if k not in after_only}
    for _ in range(3):                    # literal entries for what does not compose
        bad = [k for k, e in enc.items() if d.expand(k) != e]
        for k in bad:
            d.ent[k] = (enc[k], d.ent[k][1] if k in d.ent else F_ANY)
        if not bad:
            break
    bad = [text.decode(k) for k, e in enc.items() if d.expand(k) != e]
    if bad:
        raise ValueError('names do not expand to their English: %s' % bad[:10])
    return d


def pack(d, base):
    """(idx bytes, blob bytes); idx at base, blob right after it."""
    groups = {}
    for k, (e, f) in d.ent.items():
        if len(k) - 2 >= 32:
            raise ValueError('name key too long: %s' % text.decode(k))
        groups.setdefault(k[:2], []).append((k, e, f))
    blob, idx = bytearray(), []
    for first in sorted(groups):
        idx.append((first[0] << 8 | first[1], len(blob)))
        for k, e, f in sorted(groups[first], key=lambda x: -len(x[0])):
            blob += bytes([f | (len(k) - 2)]) + k[2:] + e + b'\0'
        blob.append(0)
    if len(blob) > 0xFFFF:
        raise ValueError('name dictionary too large')
    head = struct.pack('<I', len(idx)) + b''.join(struct.pack('<HH', c, o) for c, o in idx)
    return head, bytes(blob)


def lines(idx, blob, buf, wrapper, layout):
    """print_all(a0 = string); measure(a0 = string) -> v0 = width of the widest line in
    8-px units (replaces strlen / the longest-line count where boxes are sized by bytes x 8); expand(a0) -> v0 = BUF;
    lookup(a0 = text, a1 = flag mask) -> v0 English, v1 key length, t7 header."""
    return [
        'print_all:',
        ('addiu', 'sp', 'sp', -40),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 's1', 24, 'sp'),
        ('sw', 's2', 28, 'sp'),
        ('lh', 't0', vwf.GP_WRAP, 'gp'),      # word-wrap width = buffer width, but the dialogue
        ('lh', 't1', GP_BUF_H, 'gp'),         # text objects (240 or 256 x 160) wrap 8 px short
        ('addiu', 'at', 'zero', 160),         # (DIALOGUE_PX at 240): the window's margins assume
        ('bne', 't1', 'at', 'pa'),            # Japanese lines a little shorter than the buffer
        ('addiu', 'at', 'zero', 240),
        ('beq', 't0', 'at', 'pw'),
        ('addiu', 'at', 'zero', 256),
        ('bne', 't0', 'at', 'pa'),
        ('nop',),
        'pw:',
        ('addiu', 't0', 't0', DIALOGUE_PX - 240),
        'pa:',
        ('lui', 'at', asm.hi(vwf.wrap_limit(wrapper))),
        ('sh', 't0', asm.lo(vwf.wrap_limit(wrapper)), 'at'),
        ('lh', 's1', vwf.GP_X, 'gp'),         # where the first line starts (tabs)
        ('lh', 's2', vwf.GP_Y, 'gp'),
        ('jal', 'expand'),
        ('sw', 's0', 20, 'sp'),
        ('addu', 's0', 'v0', 'zero'),
        'pl:',
        ('lbu', 'a0', 0, 's0'),
        ('lh', 't0', vwf.GP_Y, 'gp'),
        ('beqz', 'a0', 'done'),
        ('lh', 't1', GP_CELL_H, 'gp'),
        ('lh', 't2', GP_BUF_H, 'gp'),
        ('blez', 't0', 'go'),
        ('addu', 't0', 't0', 't1'),
        ('slt', 'at', 't2', 't0'),
        ('bnez', 'at', 'done'),
        ('nop',),
        'go:',
        ('addiu', 'at', 'zero', TAB),
        ('beq', 'a0', 'at', 'tab'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 'a0', 'at', 'space'),
        ('nop',),
        'put:',
        ('jal', wrapper),
        ('nop',),
        'next:',
        ('b', 'pl'),
        ('addiu', 's0', 's0', 1),
        'tab:',                               # x = max(x + space, line start + px)
        ('lbu', 't0', 1, 's0'),
        ('lbu', 't3', 2, 's0'),
        ('lh', 't1', vwf.GP_X, 'gp'),
        ('lh', 't2', vwf.GP_Y, 'gp'),
        ('addiu', 't0', 't0', -TAB_BASE),
        ('sll', 't0', 't0', 6),
        ('addiu', 't3', 't3', -TAB_BASE),
        ('or', 't0', 't0', 't3'),
        ('bne', 't2', 's2', 'tab0'),
        ('addiu', 's0', 's0', 2),
        ('addu', 't0', 't0', 's1'),
        'tab0:',
        ('addiu', 't1', 't1', SPACE_W),
        ('slt', 'at', 't1', 't0'),
        ('beqz', 'at', 'tabset'),
        ('nop',),
        ('addu', 't1', 't0', 'zero'),
        'tabset:',
        ('b', 'next'),
        ('sh', 't1', vwf.GP_X, 'gp'),
        'space:',                             # spaces before a digit: digit width
        ('lbu', 't1', -1, 's0'),              # (not after a letter or , - . : prose)
        ('nop',),
        ('ori', 't1', 't1', 0x20),
        ('addiu', 't0', 't1', -0x61),
        ('sltiu', 't0', 't0', 26),
        ('bnez', 't0', 'put'),
        ('addiu', 't1', 't1', -0x2c),
        ('sltiu', 't1', 't1', 3),
        ('bnez', 't1', 'put'),
        ('addu', 't0', 's0', 'zero'),
        'sp1:',
        ('addiu', 't0', 't0', 1),
        ('lbu', 't1', 0, 't0'),
        ('nop',),
        ('beq', 't1', 'at', 'sp1'),
        ('addiu', 't1', 't1', -0x30),
        ('sltiu', 't1', 't1', 10),
        ('beqz', 't1', 'put'),
        ('lh', 't3', vwf.GP_Y, 'gp'),
        ('nop',),
        ('sw', 't3', 32, 'sp'),
        ('jal', wrapper),
        ('nop',),
        ('lh', 't0', vwf.GP_Y, 'gp'),
        ('lw', 't1', 32, 'sp'),
        ('lh', 't2', vwf.GP_X, 'gp'),
        ('bne', 't0', 't1', 'next'),          # wrapped: no extra width at the line start
        ('addiu', 't2', 't2', FIG_EXTRA),
        ('b', 'next'),
        ('sh', 't2', vwf.GP_X, 'gp'),
        'done:',
        ('lw', 'ra', 16, 'sp'),
        ('lw', 's0', 20, 'sp'),
        ('lw', 's1', 24, 'sp'),
        ('lw', 's2', 28, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 40),

        'measure:',
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('jal', 'expand'),
        ('nop',),
        ('lui', 't5', hi(vwf.WIDTHS)),
        ('addiu', 't5', 't5', lo(vwf.WIDTHS)),
        ('addu', 't4', 'zero', 'zero'),       # pixels of this line
        ('addu', 't6', 'zero', 'zero'),       # widest line so far
        'mloop:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'mdone'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't0', 'at', 'mnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't0', 'at', 'mesc'),
        ('addiu', 'at', 'zero', TAB),
        ('beq', 't0', 'at', 'mtab'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'mspace'),
        ('sltiu', 'at', 't0', 0x20),
        ('bnez', 'at', 'mloop'),
        ('sltiu', 'at', 't0', 0x7F),
        ('beqz', 'at', 'mhigh'),
        ('addu', 't1', 't5', 't0'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'mloop'),
        ('addu', 't4', 't4', 't1'),
        'mhigh:',
        ('addiu', 't1', 't0', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('bnez', 'at', 'mloop'),
        ('addiu', 't4', 't4', vwf.DEFAULT_W),
        ('addiu', 'v0', 'v0', 1),             # 2-byte character
        ('b', 'mloop'),
        ('addiu', 't4', 't4', 16 - vwf.DEFAULT_W),
        'mtab:',
        ('lbu', 't0', 0, 'v0'),
        ('lbu', 't1', 1, 'v0'),
        ('addiu', 'v0', 'v0', 2),
        ('addiu', 't4', 't4', SPACE_W),
        ('addiu', 't0', 't0', -TAB_BASE),
        ('sll', 't0', 't0', 6),
        ('addiu', 't1', 't1', -TAB_BASE),
        ('or', 't0', 't0', 't1'),
        ('slt', 'at', 't4', 't0'),
        ('beqz', 'at', 'mloop'),
        ('nop',),
        ('b', 'mloop'),
        ('addu', 't4', 't0', 'zero'),
        'mspace:',                            # (at was reused by the delay slot)
        ('lbu', 't0', -2, 'v0'),              # after a letter or , - . : plain (prose)
        ('addiu', 't4', 't4', SPACE_W),
        ('ori', 't0', 't0', 0x20),
        ('addiu', 't1', 't0', -0x61),
        ('sltiu', 't1', 't1', 26),
        ('bnez', 't1', 'mloop'),
        ('addiu', 't0', 't0', -0x2c),
        ('sltiu', 't0', 't0', 3),
        ('bnez', 't0', 'mloop'),
        ('addu', 't1', 'v0', 'zero'),
        ('addiu', 't2', 'zero', 0x20),
        'msp1:',
        ('lbu', 't0', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('beq', 't0', 't2', 'msp1'),
        ('addiu', 't0', 't0', -0x30),
        ('sltiu', 't0', 't0', 10),
        ('beqz', 't0', 'mloop'),
        ('nop',),
        ('b', 'mloop'),
        ('addiu', 't4', 't4', FIG_EXTRA),
        'mesc:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'mdone'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't0', 'at', 'mesc'),
        ('nop',),
        ('b', 'mloop'),
        ('nop',),
        'mnl:',
        ('slt', 'at', 't6', 't4'),
        ('beqz', 'at', 'mnl2'),
        ('nop',),
        ('addu', 't6', 't4', 'zero'),
        'mnl2:',
        ('b', 'mloop'),
        ('addu', 't4', 'zero', 'zero'),
        'mdone:',
        ('slt', 'at', 't4', 't6'),
        ('beqz', 'at', 'mdone2'),
        ('lw', 'ra', 16, 'sp'),
        ('addu', 't4', 't6', 'zero'),
        'mdone2:',
        ('addiu', 'v0', 't4', 7),
        ('srl', 'v0', 'v0', 3),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24),

        # wrapm(a0 = string, a1 = dst, a2 = width px, a3 = dst size): dst = the expanded string
        # with a newline where print_all + the word-wrap wrapper + putc will break it at that
        # width (replaces the byte line-breaker 0x80030db8 that sizes boxes / pre-wraps text)
        'wrapm8:',                            # the line-breaker's arguments: a2 = characters (8 px)
        ('sll', 'a2', 'a2', 3),
        ('addiu', 'a3', 'zero', 256),         # its output never passed 256 characters
        'wrapm:',
        ('addiu', 'sp', 'sp', -32),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 'a1', 20, 'sp'),
        ('sw', 'a2', 24, 'sp'),
        ('jal', 'expand'),
        ('sw', 'a3', 28, 'sp'),
        ('lw', 'a1', 20, 'sp'),
        ('lw', 't3', 24, 'sp'),
        ('lw', 't9', 28, 'sp'),
        ('lui', 't5', hi(vwf.WIDTHS)),
        ('addiu', 't5', 't5', lo(vwf.WIDTHS)),
        ('addu', 't9', 't9', 'a1'),
        ('addiu', 't9', 't9', -WRAPM_SLACK),  # room for a newline + 2-byte char / tab / ESC run + NUL
        ('addu', 't4', 'zero', 'zero'),       # x
        'wl:',
        ('lbu', 't0', 0, 'v0'),
        ('sltu', 'at', 'a1', 't9'),
        ('beqz', 't0', 'wdone'),
        ('nop',),
        ('beqz', 'at', 'wdone'),
        ('addiu', 'v0', 'v0', 1),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't0', 'at', 'wnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't0', 'at', 'wesc'),
        ('addiu', 'at', 'zero', TAB),
        ('beq', 't0', 'at', 'wtab'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'wsp'),
        ('sltiu', 'at', 't0', 0x20),
        ('bnez', 'at', 'wput'),               # other control bytes: no width
        ('addu', 't1', 'zero', 'zero'),
        ('sltiu', 'at', 't0', 0x7F),
        ('beqz', 'at', 'whigh'),
        ('addu', 't1', 't5', 't0'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'wchar'),
        ('nop',),
        'whigh:',
        ('addiu', 't1', 't0', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('bnez', 'at', 'wchar'),
        ('addiu', 't1', 'zero', vwf.DEFAULT_W),
        ('addiu', 't1', 'zero', 16),          # 2-byte character
        ('addu', 't2', 't4', 't1'),
        ('slt', 'at', 't3', 't2'),
        ('beqz', 'at', 'w2'),
        ('addiu', 't2', 'zero', 0x0A),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't4', 'zero', 'zero'),
        'w2:',
        ('lbu', 't2', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('sb', 't0', 0, 'a1'),
        ('sb', 't2', 1, 'a1'),
        ('addiu', 'a1', 'a1', 2),
        ('b', 'wl'),
        ('addu', 't4', 't4', 't1'),
        'wchar:',                             # putc wraps when x + width > wrap
        ('addu', 't2', 't4', 't1'),
        ('slt', 'at', 't3', 't2'),
        ('beqz', 'at', 'wput'),
        ('addiu', 't2', 'zero', 0x0A),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't4', 'zero', 'zero'),
        'wput:',
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('addu', 't4', 't4', 't1'),
        'wnl:',
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('addu', 't4', 'zero', 'zero'),
        'wesc:',                              # ESC ... 'C' copied, no width
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        'we1:',
        ('lbu', 't0', 0, 'v0'),
        ('sltu', 'at', 'a1', 't9'),
        ('beqz', 't0', 'wl'),
        ('nop',),
        ('beqz', 'at', 'wdone'),
        ('addiu', 'v0', 'v0', 1),
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't0', 'at', 'we1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('nop',),
        'wtab:',                              # x = max(x + space, px) (the box starts at x 0)
        ('lbu', 't1', 0, 'v0'),
        ('lbu', 't2', 1, 'v0'),
        ('addiu', 'v0', 'v0', 2),
        ('sb', 't0', 0, 'a1'),
        ('sb', 't1', 1, 'a1'),
        ('sb', 't2', 2, 'a1'),
        ('addiu', 'a1', 'a1', 3),
        ('addiu', 't1', 't1', -TAB_BASE),
        ('sll', 't1', 't1', 6),
        ('addiu', 't2', 't2', -TAB_BASE),
        ('or', 't1', 't1', 't2'),
        ('addiu', 't4', 't4', SPACE_W),
        ('slt', 'at', 't4', 't1'),
        ('beqz', 'at', 'wl'),
        ('nop',),
        ('b', 'wl'),
        ('addu', 't4', 't1', 'zero'),
        'wsp:',                               # the wrapper: x + space + next word > wrap -> newline
        ('addu', 't6', 'v0', 'zero'),
        ('addu', 't7', 'zero', 'zero'),
        'ws1:',
        ('lbu', 't2', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't2', 'at', 'wsesc'),
        ('sltiu', 'at', 't2', 0x21),
        ('bnez', 'at', 'wsend'),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'wshigh'),
        ('addu', 't1', 't5', 't2'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'ws1'),
        ('addu', 't7', 't7', 't1'),
        'wshigh:',
        ('addiu', 't1', 't2', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('beqz', 'at', 'wslead'),
        ('nop',),
        ('b', 'ws1'),
        ('addiu', 't7', 't7', vwf.DEFAULT_W),
        'wslead:',
        ('addiu', 't6', 't6', 1),
        ('b', 'ws1'),
        ('addiu', 't7', 't7', 16),
        'wsesc:',
        ('lbu', 't2', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('beqz', 't2', 'wsend'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't2', 'at', 'wsesc'),
        ('nop',),
        ('b', 'ws1'),
        ('nop',),
        'wsend:',
        ('lbu', 't1', 0, 't5'),               # width of ' '
        ('nop',),
        ('addu', 't2', 't4', 't1'),
        ('addu', 't2', 't2', 't7'),
        ('slt', 'at', 't3', 't2'),
        ('beqz', 'at', 'wsp2'),
        ('addiu', 't2', 'zero', 0x0A),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('addu', 't4', 'zero', 'zero'),
        'wsp2:',                              # print_all: a space before a digit is digit-wide
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't4', 't4', 't1'),
        ('lbu', 't2', -2, 'v0'),              # after a letter or , - . : plain (prose)
        ('nop',),
        ('ori', 't2', 't2', 0x20),
        ('addiu', 't7', 't2', -0x61),
        ('sltiu', 't7', 't7', 26),
        ('bnez', 't7', 'wl'),
        ('addiu', 't2', 't2', -0x2c),
        ('sltiu', 't2', 't2', 3),
        ('bnez', 't2', 'wl'),
        ('nop',),
        ('addu', 't6', 'v0', 'zero'),
        ('addiu', 't7', 'zero', 0x20),
        'wsp3:',
        ('lbu', 't2', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('beq', 't2', 't7', 'wsp3'),
        ('addiu', 't2', 't2', -0x30),
        ('sltiu', 't2', 't2', 10),
        ('beqz', 't2', 'wl'),
        ('nop',),
        ('b', 'wl'),
        ('addiu', 't4', 't4', FIG_EXTRA),
        'wdone:',
        ('lw', 'ra', 16, 'sp'),
        ('sb', 'zero', 0, 'a1'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 32),

        'expand:',
        ('addiu', 'sp', 'sp', -48),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 's0', 20, 'sp'),
        ('sw', 's1', 24, 'sp'),
        ('sw', 's2', 28, 'sp'),
        ('sw', 's3', 32, 'sp'),
        ('sw', 's4', 36, 'sp'),
        ('addu', 's0', 'a0', 'zero'),
        ('addu', 's3', 'a0', 'zero'),         # start of the source line
        ('addu', 's4', 'zero', 'zero'),       # 1: ESC on this source line (no columns)
        ('lui', 't9', hi(layout)),
        ('sw', 'zero', lo(layout) + L_CI, 't9'),
        ('sw', 'zero', lo(layout) + L_P, 't9'),
        ('lui', 's1', hi(buf)),
        ('addiu', 's1', 's1', lo(buf)),
        ('addiu', 's2', 's1', CAP - 4),      # room for a tab (3 bytes) + NUL
        'exp:',
        ('lbu', 't0', 0, 's0'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'expdone'),
        ('nop',),
        ('beqz', 'at', 'expdone'),
        ('addiu', 't1', 't0', -0x81),
        ('sltiu', 'at', 't1', 0x1F),
        ('bnez', 'at', 'lead'),
        ('addiu', 't1', 't0', -0xE0),
        ('sltiu', 'at', 't1', 0x1D),
        ('bnez', 'at', 'lead'),
        ('addiu', 'at', 'zero', 0x0A),
        ('bne', 't0', 'at', 'notnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('jal', 'cellend'),
        ('nop',),
        ('lui', 't9', hi(layout)),
        ('sw', 'zero', lo(layout) + L_CI, 't9'),
        ('addiu', 't0', 'zero', 0x0A),
        ('addiu', 's3', 's0', 1),
        ('b', 'single'),
        ('addu', 's4', 'zero', 'zero'),
        'notnl:',
        ('bne', 't0', 'at', 'notesc'),
        ('addiu', 'at', 'zero', 0x20),
        ('b', 'single'),
        ('addiu', 's4', 'zero', 1),
        'notesc:',
        ('beq', 't0', 'at', 'isspace'),
        ('addiu', 'at', 'zero', CELL),
        ('beq', 't0', 'at', 'iscell'),
        ('addiu', 'at', 'zero', CELL_REF),
        ('bne', 't0', 'at', 'plain'),
        'iscell:',
        ('sw', 't0', 40, 'sp'),               # the marker
        ('jal', 'cellend'),                   # table cell (tools/cells.py)
        ('nop',),
        ('lui', 't9', hi(layout)),
        ('addiu', 't9', 't9', lo(layout)),
        ('lbu', 't1', 0, 't9'),
        ('lw', 't2', L_CI, 't9'),
        ('nop',),
        ('sltu', 'at', 't2', 't1'),
        ('beqz', 'at', 'mskip'),
        ('addiu', 't2', 't2', 1),
        ('sw', 't2', L_CI, 't9'),
        ('sw', 's1', L_P, 't9'),
        ('addiu', 't0', 'zero', TAB),
        ('sb', 't0', 0, 's1'),
        ('addiu', 't0', 'zero', TAB_BASE),    # filled in by cellend
        ('sb', 't0', 1, 's1'),
        ('sb', 't0', 2, 's1'),
        ('addiu', 's1', 's1', 3),
        ('addiu', 's4', 'zero', 1),           # plain spaces inside cells
        'mskip:',
        ('lw', 't0', 40, 'sp'),
        ('addiu', 'at', 'zero', CELL_REF),
        ('bne', 't0', 'at', 'exp'),
        ('addiu', 's0', 's0', 1),
        ('lbu', 't1', 0, 's0'),               # CELL_REF: copy the string at the address
        ('lbu', 't2', 1, 's0'),
        ('lbu', 't3', 2, 's0'),
        ('andi', 't1', 't1', 0x7F),
        ('andi', 't2', 't2', 0x7F),
        ('andi', 't3', 't3', 0x7F),
        ('sll', 't1', 't1', 14),
        ('sll', 't2', 't2', 7),
        ('or', 't1', 't1', 't2'),
        ('or', 't1', 't1', 't3'),
        ('lui', 't2', 0x8000),
        ('or', 't1', 't1', 't2'),
        ('addiu', 's0', 's0', 3),
        'rcopy:',
        ('lbu', 't0', 0, 't1'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'exp'),
        ('addiu', 't1', 't1', 1),
        ('beqz', 'at', 'exp'),
        ('nop',),
        ('sb', 't0', 0, 's1'),
        ('b', 'rcopy'),
        ('addiu', 's1', 's1', 1),
        'isspace:',
        ('b', 'spaces'),
        ('addu', 't8', 'zero', 'zero'),       # not after a name
        'plain:',                             # upper case after lower case: a space ("BbbbBbbb",
        ('addiu', 't1', 't0', -0x41),         # the game joins a custom officer's names)
        ('sltiu', 'at', 't1', 26),
        ('beqz', 'at', 'single'),
        ('lui', 't9', hi(buf + 2)),
        ('addiu', 't9', 't9', lo(buf + 2)),
        ('sltu', 'at', 's1', 't9'),
        ('bnez', 'at', 'single'),
        ('nop',),
        ('lbu', 't1', -1, 's1'),
        ('lbu', 't2', -2, 's1'),
        ('addiu', 't1', 't1', -0x61),
        ('sltiu', 'at', 't1', 26),
        ('beqz', 'at', 'single'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't2', 'at', 'single'),
        ('addiu', 't1', 'zero', 0x20),
        ('sb', 't1', 0, 's1'),
        ('b', 'single'),
        ('addiu', 's1', 's1', 1),
        'single1:',
        ('addiu', 't0', 'zero', 0x20),
        'single:',
        ('sb', 't0', 0, 's1'),
        ('addiu', 's0', 's0', 1),
        ('b', 'exp'),
        ('addiu', 's1', 's1', 1),
        'lead:',
        ('addu', 'a0', 's0', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_ANY),
        ('beqz', 'v0', 'raw2'),
        ('nop',),
        ('addu', 's0', 's0', 'v1'),
        ('addu', 'a3', 't7', 'zero'),        # header of the hit
        'copy:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'copied'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'expdone'),
        ('nop',),
        ('sb', 't0', 0, 's1'),
        ('b', 'copy'),
        ('addiu', 's1', 's1', 1),
        'copied:',
        ('andi', 'at', 'a3', F_S),
        ('beqz', 'at', 'after'),
        ('addu', 'a0', 's0', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_G),
        ('beqz', 'v0', 'after'),
        ('addu', 'a3', 'zero', 'zero'),
        ('addu', 's0', 's0', 'v1'),
        ('addiu', 't0', 'zero', 0x20),
        ('sb', 't0', 0, 's1'),
        ('b', 'copy'),
        ('addiu', 's1', 's1', 1),
        'after:',                             # a name ends here
        ('lbu', 't0', 0, 's0'),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 't0', 'at', 'exp'),
        ('nop',),
        ('addiu', 't8', 'zero', 1),
        'spaces:',                            # s0 at a space (t8 = 1: right after a name)
        ('bnez', 's4', 'single1'),            # ESC on this line: columns unknown
        ('addu', 't1', 's0', 'zero'),
        'sp:',
        ('addiu', 't1', 't1', 1),
        ('lbu', 't0', 0, 't1'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'sp'),
        ('subu', 't2', 't1', 's0'),           # spaces in the run
        ('bne', 's0', 's3', 'notlead'),       # at the line start, before a name: one column
        ('addiu', 'at', 't0', -0x81),
        ('sltiu', 'at', 'at', 0x1F),
        ('bnez', 'at', 'lsp'),
        ('addiu', 'at', 't0', -0xE0),
        ('sltiu', 'at', 'at', 0x1D),
        ('beqz', 'at', 'notlead'),
        ('nop',),
        'lsp:',
        ('addu', 'a3', 't1', 'zero'),
        ('addu', 'a0', 't1', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_ANY),
        ('addu', 't1', 'a3', 'zero'),
        ('beqz', 'v0', 'spre'),
        ('addu', 't8', 'zero', 'zero'),
        ('b', 'emit'),
        ('addiu', 't6', 'zero', 8),
        'spre:',                              # no name: back to the run
        ('lbu', 't0', 0, 't1'),
        ('subu', 't2', 't1', 's0'),
        'notlead:',
        ('addiu', 't3', 't0', -0x30),
        ('sltiu', 't3', 't3', 10),
        ('beqz', 't3', 'word'),
        ('addu', 't4', 't1', 'zero'),
        'dg:',                                # digits follow: t4 = their end, t5 = count
        ('addiu', 't4', 't4', 1),
        ('lbu', 't0', 0, 't4'),
        ('nop',),
        ('addiu', 't0', 't0', -0x30),
        ('sltiu', 't0', 't0', 10),
        ('bnez', 't0', 'dg'),
        ('subu', 't5', 't4', 't1'),
        ('sltiu', 'at', 't2', 2),
        ('beqz', 'at', 'rtab'),
        ('nop',),
        ('beqz', 't8', 'single1'),            # one space: digit-wide at draw time
        ('nop',),
        'rtab:',                              # right-align the number on its Japanese end column
        ('subu', 't6', 't4', 's3'),
        ('sll', 't6', 't6', 3),
        ('sll', 't7', 't5', 1),
        ('subu', 't6', 't6', 't7'),
        ('sll', 't7', 't5', 2),
        ('b', 'emit'),
        ('subu', 't6', 't6', 't7'),
        'word:',
        ('sltiu', 'at', 't2', 2),
        ('beqz', 'at', 'ltab'),
        ('nop',),
        ('beqz', 't8', 'single1'),
        ('sltiu', 'at', 't0', 0x80),          # name + one space: a column only before Japanese
        ('bnez', 'at', 'single1'),
        ('nop',),
        'ltab:',                              # left-align on the Japanese column after the spaces
        ('subu', 't6', 't1', 's3'),
        ('sll', 't6', 't6', 3),
        'emit:',                              # TAB, 0x20 + px >> 6, 0x20 + (px & 63)
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'single1'),
        ('srl', 't7', 't6', 6),
        ('addiu', 't7', 't7', TAB_BASE),
        ('andi', 't6', 't6', 63),
        ('addiu', 't6', 't6', TAB_BASE),
        ('addiu', 't0', 'zero', TAB),
        ('sb', 't0', 0, 's1'),
        ('sb', 't7', 1, 's1'),
        ('sb', 't6', 2, 's1'),
        ('addiu', 's1', 's1', 3),
        ('b', 'exp'),
        ('addu', 's0', 't1', 'zero'),
        'raw2:',
        ('lbu', 't0', 0, 's0'),
        ('lbu', 't1', 1, 's0'),
        ('sb', 't0', 0, 's1'),
        ('beqz', 't1', 'expdone'),
        ('addiu', 's1', 's1', 1),
        ('sb', 't1', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('b', 'exp'),
        ('addiu', 's0', 's0', 2),
        'expdone:',
        ('jal', 'cellend'),
        ('nop',),
        ('sb', 'zero', 0, 's1'),
        ('lui', 'v0', hi(buf)),
        ('addiu', 'v0', 'v0', lo(buf)),
        ('lw', 'ra', 16, 'sp'),
        ('lw', 's0', 20, 'sp'),
        ('lw', 's1', 24, 'sp'),
        ('lw', 's2', 28, 'sp'),
        ('lw', 's3', 32, 'sp'),
        ('lw', 's4', 36, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 48),

        'cellend:',                           # align the open cell: fill its TAB
        ('lui', 't9', hi(layout)),
        ('addiu', 't9', 't9', lo(layout)),
        ('lw', 't0', L_P, 't9'),
        ('lw', 't1', L_CI, 't9'),
        ('nop',),
        ('beqz', 't0', 'ceret'),
        ('addiu', 't1', 't1', -1),
        ('sw', 'zero', L_P, 't9'),
        ('sll', 't1', 't1', 2),
        ('addu', 't1', 't9', 't1'),
        ('lhu', 't2', L_COLS, 't1'),          # column px
        ('lbu', 't3', L_COLS + 2, 't1'),      # width
        ('lbu', 't4', L_COLS + 3, 't1'),      # flags
        ('addiu', 't5', 't0', 3),
        ('addu', 't6', 'zero', 'zero'),       # px of the cell
        ('lui', 't7', hi(vwf.WIDTHS)),
        ('addiu', 't7', 't7', lo(vwf.WIDTHS)),
        'cm:',
        ('sltu', 'at', 't5', 's1'),
        ('beqz', 'at', 'cmdone'),
        ('nop',),
        ('lbu', 'v1', 0, 't5'),
        ('addiu', 't5', 't5', 1),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 'v1', 'at', 'cm1'),
        ('addu', 'a0', 't5', 'zero'),
        'cms:',                               # a space: digit-wide before digits
        ('sltu', 'at', 'a0', 's1'),
        ('beqz', 'at', 'cmsp'),
        ('nop',),
        ('lbu', 'a1', 0, 'a0'),
        ('addiu', 'a0', 'a0', 1),
        ('addiu', 'a2', 'zero', 0x20),
        ('beq', 'a1', 'a2', 'cms'),
        ('addiu', 'a1', 'a1', -0x30),
        ('sltiu', 'a1', 'a1', 10),
        ('beqz', 'a1', 'cmsp'),
        ('nop',),
        ('addiu', 't6', 't6', FIG_EXTRA),
        'cmsp:',
        ('b', 'cm'),
        ('addiu', 't6', 't6', SPACE_W),
        'cm1:',
        ('sltiu', 'at', 'v1', 0x20),
        ('bnez', 'at', 'cm'),
        ('sltiu', 'at', 'v1', 0x7F),
        ('beqz', 'at', 'cm2'),
        ('addu', 'a0', 't7', 'v1'),
        ('lbu', 'a0', -0x20, 'a0'),
        ('b', 'cm'),
        ('addu', 't6', 't6', 'a0'),
        'cm2:',
        ('addiu', 'a0', 'v1', -0xA0),
        ('sltiu', 'a0', 'a0', 0x40),
        ('beqz', 'a0', 'cm3'),
        ('nop',),
        ('b', 'cm'),
        ('addiu', 't6', 't6', vwf.DEFAULT_W),
        'cm3:',
        ('addiu', 't5', 't5', 1),
        ('b', 'cm'),
        ('addiu', 't6', 't6', 16),
        'cmdone:',
        ('andi', 'v1', 't4', 3),
        ('subu', 'a0', 't3', 't6'),           # free px
        ('addiu', 'at', 'zero', 1),
        ('bne', 'v1', 'at', 'c2'),
        ('addu', 'a1', 'zero', 'zero'),
        ('b', 'coff'),
        ('addu', 'a1', 'a0', 'zero'),
        'c2:',
        ('addiu', 'at', 'zero', 2),
        ('bne', 'v1', 'at', 'c3'),
        ('nop',),
        ('b', 'coff'),
        ('sra', 'a1', 'a0', 1),
        'c3:',
        ('addiu', 'at', 'zero', 3),
        ('bne', 'v1', 'at', 'coff'),
        ('andi', 'a1', 't4', 0x30),
        ('srl', 'a1', 'a1', 1),               # trailing spaces x 8
        ('subu', 'a1', 'a0', 'a1'),
        'coff:',
        ('slti', 'at', 'a1', 0),
        ('bnez', 'at', 'cpos'),
        ('nop',),
        ('addu', 't2', 't2', 'a1'),
        'cpos:',
        ('srl', 'a2', 't2', 6),
        ('addiu', 'a2', 'a2', TAB_BASE),
        ('sb', 'a2', 1, 't0'),
        ('andi', 'a2', 't2', 63),
        ('addiu', 'a2', 'a2', TAB_BASE),
        ('sb', 'a2', 2, 't0'),
        'ceret:',
        ('jr', 'ra'),
        ('nop',),

        'lookup:',
        ('lbu', 't0', 0, 'a0'),
        ('lbu', 't1', 1, 'a0'),
        ('sll', 't0', 't0', 8),
        ('or', 't0', 't0', 't1'),
        ('lui', 't2', hi(idx)),
        ('addiu', 't2', 't2', lo(idx)),
        ('lw', 't3', 0, 't2'),                # hi = n
        ('addiu', 't2', 't2', 4),
        ('addu', 't4', 'zero', 'zero'),       # lo
        'bs:',
        ('sltu', 'at', 't4', 't3'),
        ('beqz', 'at', 'none'),
        ('addu', 't5', 't4', 't3'),
        ('srl', 't5', 't5', 1),
        ('sll', 't6', 't5', 2),
        ('addu', 't6', 't2', 't6'),
        ('lhu', 't7', 0, 't6'),
        ('nop',),
        ('beq', 't7', 't0', 'found'),
        ('sltu', 'at', 't7', 't0'),
        ('beqz', 'at', 'lower'),
        ('nop',),
        ('b', 'bs'),
        ('addiu', 't4', 't5', 1),
        'lower:',
        ('b', 'bs'),
        ('addu', 't3', 't5', 'zero'),
        'found:',
        ('lhu', 't6', 2, 't6'),
        ('lui', 't7', hi(blob)),
        ('addiu', 't7', 't7', lo(blob)),
        ('addu', 't6', 't7', 't6'),
        'ent:',
        ('lbu', 't7', 0, 't6'),
        ('nop',),
        ('beqz', 't7', 'none'),
        ('addiu', 't6', 't6', 1),
        ('andi', 't8', 't7', 0x1F),
        ('addu', 't9', 'zero', 'zero'),
        'cmp:',
        ('beq', 't9', 't8', 'match'),
        ('addu', 'at', 'a0', 't9'),
        ('lbu', 'v0', 2, 'at'),
        ('addu', 'at', 't6', 't9'),
        ('lbu', 'v1', 0, 'at'),
        ('addiu', 't9', 't9', 1),
        ('bne', 'v0', 'v1', 'skip'),
        ('nop',),
        ('b', 'cmp'),
        ('nop',),
        'match:',
        ('and', 'at', 't7', 'a1'),
        ('beqz', 'at', 'skip'),
        ('nop',),
        ('addu', 'v0', 't6', 't8'),
        ('jr', 'ra'),
        ('addiu', 'v1', 't8', 2),
        'skip:',
        ('addu', 't6', 't6', 't8'),
        'sk2:',
        ('lbu', 'at', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('bnez', 'at', 'sk2'),
        ('nop',),
        ('b', 'ent'),
        ('nop',),
        'none:',
        ('jr', 'ra'),
        ('addu', 'v0', 'zero', 'zero'),
    ]


STRLEN = 0x80014dc0
OVL_BASE = 0x80199000
# jal strlen whose result sizes or places a box (bytes x 8): -> measure
MEASURE_SITES = [
    ('PROG', 0x800be084),   # speaker label left of a right-hand face: x = right - (len * 8 + 0x45)
    ('PROG', 0x800bcdfc),   # same formula, other dialogue window
    ('PROG', 0x80081418),   # box width = len * 8
    ('PROG', 0x800c6564),   # list of names joined into lines of a byte budget
    ('HEX', 0x801a88a4),    # centred: x = (0xe0 - len * 8) / 2
    ('COMP', 0x801aaa94),   # centred: (0x70 - (len * 8 + 0x30)) / 2
    ('COMP', 0x801abca4),   # width = len * 8
    # longest line in bytes (0x8002e9ec) x 8:
    ('PROG', 0x8002e8c4),   # text object alignment (centred / right-aligned labels)
    ('PROG', 0x800b7afc), ('PROG', 0x800bcdc4), ('PROG', 0x800ce2bc), ('PROG', 0x800ce30c),
    ('PROG', 0x800cec20), ('PROG', 0x800d7fd8),   # window / box widths
]
MAXLINE = 0x8002e9ec
LINEBRK = 0x80030db8                     # byte line-breaker (src, dst, characters per line)
WRAPM_SLACK = 12
# its callers -> wrapm: the dialogue window (0x800bcd24) sizes itself from the pre-wrapped text
# (widest line, line count), the text object 0xe9 prints the message at DIALOGUE_PX; the others keep
# their width (characters x 8). INIT prints the pre-wrapped text itself.
DIALOGUE_WRAP = (0x800bcda4, [0x04c10003, 0x0006c8c3, 0x24c20007, 0x0002c8c3,
                              0x00e02021, 0x27a5003c, 0x0c00c36e, 0x03203021])
DIALOGUE_BUF = 0x240                     # sp+0x3c .. end of the 0x280 frame
DIALOGUE_PX = 232                        # wrap of the 240-px dialogue text (print_all + window sizing; the 256-px no-face narration wraps at 248): at 240
                                         # English lines touched the right border (face on the left)
# text x in the dialogue window, face on the left: 0x3d -> 0x41, on the right: 5 -> 9 (the Japanese
# opening 「 had a blank left half; English started 1 px from the face frame / the border)
TEXT_X = [(0x800bdfec, 0x2402003d, 0x41), (0x800bdff4, 0x24020005, 9)]
LINEBRK_SITES = [('PROG', 0x800d7fd0), ('INIT', 0x801a90b8)]
# The dialogue object pages its message: 0x800bcbfc(obj, text, 3) strcpy'd the text up to every 3rd
# newline into 6 page buffers of 128 bytes (obj+0x3a4 + 0x80 * i) without a length check. English
# has no newlines (wrapped at draw time), so text > 127 bytes ran on into the next pages (a bogus
# page repeating the tail, lines lost under the window). split() replaces it: wrapm at DIALOGUE_PX,
# then pages of up to `lines` wrapped lines and <= 127 bytes, at most 6. Like the original it
# leaves the other pages alone: 0x800bdf00 splits each page again (into page 0) to show it, and
# the caller that splits the whole message clears the pages first.
SPLIT = (0x800bcbfc, [0x27bdfd80, 0xafbf0030])
PAGES, PAGE_SIZE, PAGE_OFF = 6, 0x80, 0x3a4
# Event messages with "Msg Speed" auto-advance: after showing a message the event VM runs a wait
# command (0x800688ac / 0x800689f8: vm+0x848 = frames from 0x80068a3c, one fixed time per message);
# when the countdown ends while its dialogue object (vm+0x8cc) is up, 0x80065b90 calls the object's
# close method (vtable +0x18) and the VM goes on: only page 0 of a paged English message was seen.
# The wait commands also record the wait and set a flag (AUTO_VARS: flag, wait); the close call goes
# to auto_close: after a wait, while the object (class DIALOGUE_VT) has a next page, show it
# (0x800bdda4, what Circle does) and restart the wait instead of closing.
WAIT_SITE1 = (0x800688ac, [0x10000055, 0xae020848])      # b 0x80068a04 / sw v0, 0x848(s0)
WAIT_SITE2 = (0x80068a00, [0xae020848])                  # sw v0, 0x848(s0) (falls into 0x80068a04)
WAIT_END = 0x80068a04
CLOSE_SITE = (0x80065b90, [0x0320f809, 0x00000000])      # jalr t9 (a0 = vm+0x8cc, s0 = vm)
ADVANCE, DIALOGUE_VT = 0x800bdda4, 0x80110290


def auto_code(vars_):
    """wait_set1 / wait_set2: the VM's wait commands; auto_close: the countdown's close call."""
    hi, lo = asm.hi(vars_), asm.lo(vars_)
    return [
        'wait_set1:',                         # (the sw to vm+0x848 ran in the delay slot)
        ('lui', 'at', hi),
        ('sw', 'v0', lo + 4, 'at'),
        ('addiu', 't0', 'zero', 1),
        ('j', WAIT_END),
        ('sw', 't0', lo, 'at'),
        'wait_set2:',
        ('sw', 'v0', 0x848, 's0'),
        ('lui', 'at', hi),
        ('sw', 'v0', lo + 4, 'at'),
        ('addiu', 't0', 'zero', 1),
        ('jr', 'ra'),
        ('sw', 't0', lo, 'at'),
        'auto_close:',                        # a0 = dialogue object, t9 = its close method, s0 = VM
        ('lui', 'at', hi),
        ('lw', 't0', lo, 'at'),
        ('nop',),
        ('beqz', 't0', 'close'),
        ('sw', 'zero', lo, 'at'),
        ('lw', 't1', 0, 'a0'),
        ('lui', 'at', asm.hi(DIALOGUE_VT)),
        ('addiu', 'at', 'at', asm.lo(DIALOGUE_VT)),
        ('bne', 't1', 'at', 'close'),
        ('lw', 't1', 0x700, 'a0'),            # page shown
        ('nop',),
        ('addiu', 't1', 't1', 1),
        ('slti', 'at', 't1', PAGES),
        ('beqz', 'at', 'close'),
        ('sll', 't1', 't1', 7),
        ('addu', 't1', 't1', 'a0'),
        ('lbu', 't1', PAGE_OFF, 't1'),
        ('nop',),
        ('beqz', 't1', 'close'),
        ('nop',),
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('jal', ADVANCE),
        ('nop',),
        ('lw', 'ra', 16, 'sp'),
        ('lui', 'at', hi),
        ('lw', 't0', lo + 4, 'at'),
        ('addiu', 't1', 'zero', 1),
        ('sw', 't1', lo, 'at'),
        ('sw', 't0', 0x848, 's0'),            # the caller reads it next: countdown, no close
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24),
        'close:',
        ('jr', 't9'),
        ('nop',),
    ]


def split_code(wrapm):
    return [
        ('addiu', 'sp', 'sp', -0x270),
        ('sw', 'ra', 0x10, 'sp'),
        ('sw', 's0', 0x14, 'sp'),
        ('sw', 's1', 0x18, 'sp'),
        ('sw', 's2', 0x1c, 'sp'),
        ('addu', 's0', 'a0', 'zero'),         # the message object
        ('addu', 's2', 'a2', 'zero'),         # lines per page
        ('addu', 'a0', 'a1', 'zero'),
        ('addiu', 'a1', 'sp', 0x20),
        ('addiu', 'a2', 'zero', DIALOGUE_PX),
        ('jal', wrapm),
        ('addiu', 'a3', 'zero', 0x240),
        ('addiu', 't0', 'sp', 0x20),          # t0 = start of the page in the wrapped text
        ('addu', 's1', 'zero', 'zero'),       # pages written
        'page:',
        ('lbu', 't1', 0, 't0'),
        ('slti', 'at', 's1', PAGES),
        ('beqz', 't1', 'done'),
        ('nop',),
        ('beqz', 'at', 'done'),
        ('addu', 't2', 't0', 'zero'),         # start of the next line
        ('addu', 't3', 't0', 'zero'),         # end of the lines taken
        ('addu', 't4', 'zero', 'zero'),       # lines taken
        'line:',
        ('addu', 't5', 't2', 'zero'),
        'le:',
        ('lbu', 't6', 0, 't5'),
        ('nop',),
        ('beqz', 't6', 'lend'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't6', 'at', 'lend'),
        ('nop',),
        ('b', 'le'),
        ('addiu', 't5', 't5', 1),
        'lend:',                              # t5 = the line's newline / NUL
        ('subu', 't7', 't5', 't0'),
        ('sltiu', 'at', 't7', PAGE_SIZE),
        ('beqz', 'at', 'cut'),
        ('nop',),
        ('addu', 't3', 't5', 'zero'),
        ('addiu', 't4', 't4', 1),
        ('beqz', 't6', 'emit'),
        ('slt', 'at', 't4', 's2'),
        ('beqz', 'at', 'emit'),
        ('nop',),
        ('b', 'line'),
        ('addiu', 't2', 't5', 1),
        'cut:',                               # this line does not fit any more
        ('bnez', 't4', 'emit'),
        ('nop',),
        ('addiu', 't3', 't0', PAGE_SIZE - 1), # one line > 127 bytes (cannot happen at 232 px)
        'emit:',
        ('sll', 't7', 's1', 7),
        ('addu', 't7', 't7', 's0'),
        ('addiu', 't7', 't7', PAGE_OFF),
        'cp:',
        ('beq', 't0', 't3', 'cpend'),
        ('nop',),
        ('lbu', 't6', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 't6', 0, 't7'),
        ('b', 'cp'),
        ('addiu', 't7', 't7', 1),
        'cpend:',
        ('sb', 'zero', 0, 't7'),
        ('addiu', 's1', 's1', 1),
        ('lbu', 't6', 0, 't0'),
        ('addiu', 'at', 'zero', 0x0A),
        ('bne', 't6', 'at', 'page'),
        ('nop',),
        ('b', 'page'),
        ('addiu', 't0', 't0', 1),             # skip the newline between pages
        'done:',
        ('addu', 'v0', 's1', 'zero'),
        ('lw', 'ra', 0x10, 'sp'),
        ('lw', 's0', 0x14, 'sp'),
        ('lw', 's1', 0x18, 'sp'),
        ('lw', 's2', 0x1c, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 0x270),
    ]


def apply(exe, heap, d, wrapper, progs, layout):
    """progs: {image tag: bytearray} (PROG = exe) for the measuring sites in overlays;
    layout: the LAYOUT block (LAYOUT_SIZE bytes, shared with tools/cells.py)."""
    """Put the dictionary + print_all into the heap, hook print_string and the measuring sites."""
    assert vwf.word(exe, PRINT_CALL) == 0x10000005, 'print_string loop entry'
    assert vwf.word(exe, PRINT_EXIT) == 0x24020001, 'print_string loop exit'
    head, blob = pack(d, 0)
    idx = heap.put(head)
    blob_at = heap.put(blob)
    buf = heap.alloc(CAP)
    lst = lines(idx, blob_at, buf, wrapper, layout)
    n = len(asm.assemble(0x80000000, lst))
    at = heap.alloc(n)
    vwf.put(exe, at, asm.assemble(at, lst))
    vwf.put(exe, PRINT_CALL, asm.assemble(PRINT_CALL, [('jal', at), ('addu', 'a0', 's0', 'zero'),
                                                       ('b', PRINT_EXIT), ('nop',)]))
    measure = asm.labels(at, lst)['measure']
    for tag, site in MEASURE_SITES:
        img = progs[tag]
        off = site - vwf.EXE_BASE if tag == 'PROG' else site - OVL_BASE
        assert img[off:off + 4] in [struct.pack('<I', 0x0c000000 | (f >> 2) & 0x3FFFFFF) for f in (STRLEN, MAXLINE)], \
            'strlen / longest-line call at %s:%08x' % (tag, site)
        img[off:off + 4] = asm.assemble(site, [('jal', measure)])
    labels = asm.labels(at, lst)
    va, words = DIALOGUE_WRAP
    assert [vwf.word(exe, va + 4 * i) for i in range(len(words))] == words, 'dialogue window line-breaker'
    vwf.put(exe, va, asm.assemble(va, [('addu', 'a0', 'a3', 'zero'), ('addiu', 'a1', 'sp', 0x3c),
                                       ('addiu', 'a2', 'a2', DIALOGUE_PX - 240), ('addiu', 'a3', 'zero', DIALOGUE_BUF),
                                       ('jal', labels['wrapm']), ('nop',), ('nop',), ('nop',)]))
    for va, word, x in TEXT_X:
        assert vwf.word(exe, va) == word, 'dialogue text x at %08x' % va
        vwf.put(exe, va, asm.assemble(va, [('addiu', 'v0', 'zero', x)]))
    va, words = SPLIT
    assert [vwf.word(exe, va + 4 * i) for i in range(len(words))] == words, 'dialogue page splitter'
    code = split_code(labels['wrapm'])
    split_at = heap.alloc(4 * len(code) - sum(isinstance(x, str) for x in code))
    vwf.put(exe, split_at, asm.assemble(split_at, code))
    vwf.put(exe, va, asm.assemble(va, [('j', split_at), ('nop',)]))
    n += 4 * len(code) - sum(isinstance(x, str) for x in code)
    for va, words in (WAIT_SITE1, WAIT_SITE2, CLOSE_SITE):
        assert [vwf.word(exe, va + 4 * i) for i in range(len(words))] == words, 'event VM wait at %08x' % va
    vars_ = heap.alloc(8)
    code = auto_code(vars_)
    size = 4 * len(code) - sum(isinstance(x, str) for x in code)
    at_ = heap.alloc(size)
    vwf.put(exe, at_, asm.assemble(at_, code))
    auto = asm.labels(at_, code)
    vwf.put(exe, WAIT_SITE1[0], asm.assemble(WAIT_SITE1[0], [('j', auto['wait_set1']), ('sw', 'v0', 0x848, 's0')]))
    vwf.put(exe, WAIT_SITE2[0], asm.assemble(WAIT_SITE2[0], [('jal', auto['wait_set2'])]))
    vwf.put(exe, CLOSE_SITE[0], asm.assemble(CLOSE_SITE[0], [('jal', auto['auto_close']), ('nop',)]))
    n += size + 8
    for tag, site in LINEBRK_SITES:
        img = progs[tag]
        off = site - vwf.EXE_BASE if tag == 'PROG' else site - OVL_BASE
        assert img[off:off + 4] == struct.pack('<I', 0x0c000000 | (LINEBRK >> 2) & 0x3FFFFFF), \
            'line-breaker call at %s:%08x' % (tag, site)
        img[off:off + 4] = asm.assemble(site, [('jal', labels['wrapm8'])])
    return len(head) + len(blob) + CAP + n
