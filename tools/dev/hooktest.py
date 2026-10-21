"""Run the namedict MIPS routines (expand, measure, print_all) in unicorn and compare
them with the Python models in tools/namedict.py. Dev only (pip install unicorn).

unicorn does not model the R3000 load delay slot: asm.check_delay covers that.

usage: .venv/bin/python tools/dev/hooktest.py [n random strings]
"""
import os, random, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unicorn
from unicorn import mips_const as M
import asm, cells, iso, kanji, namedict, names, pixfont, setup, text, vwf

GP = 0x8011da2c
STUB = 0x80001000          # stands for the word-wrap wrapper + putc
RET = 0x80001100           # return address: stops the run
STACK = 0x801ff000


class Cpu:
    WIDTHS = bytes(pixfont.glyphs()[chr(c)][1] for c in range(0x20, 0x7F))

    def __init__(self, exe):
        self.u = unicorn.Uc(unicorn.UC_ARCH_MIPS, unicorn.UC_MODE_MIPS32 | unicorn.UC_MODE_LITTLE_ENDIAN)
        self.u.mem_map(0, 0x200000)                    # kseg0 0x80000000 = physical 0
        self.u.mem_write(0x10000, bytes(exe[0x800:]))
        self.u.mem_write(STUB & 0x1FFFFFFF, b'\0' * 0x200)
        self.drawn = []
        self.consts = {}
        self.u.hook_add(unicorn.UC_HOOK_CODE, self._stub, begin=STUB, end=STUB)
        self.u.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=RET, end=RET)
        self.widths = bytes(pixfont.glyphs()[chr(c)][1] for c in range(0x20, 0x7F))

    def static(self, t):
        """a constant string in the program image (CELL_REF); address of one already placed"""
        t = t.encode()
        if t not in self.consts:
            a = 0x80118000 + sum(len(k) + 1 for k in self.consts)
            self.u.mem_write(a & 0x1FFFFFFF, t + b'\0')
            self.consts[t] = a
        return t, self.consts[t]

    def h(self, off, v=None):
        a = (GP + off) & 0x1FFFFFFF
        if v is None:
            return struct.unpack('<h', self.u.mem_read(a, 2))[0]
        self.u.mem_write(a, struct.pack('<h', v))

    def _stub(self, u, addr, size, data):
        c = u.reg_read(M.UC_MIPS_REG_A0)
        x, y = self.h(vwf.GP_X), self.h(vwf.GP_Y)
        if c == 0x0A:
            x, y = 0, y + 16
        else:
            self.drawn.append((c, x, y))
            x += self.widths[c - 0x20] if 0x20 <= c < 0x7F else vwf.DEFAULT_W
        self.h(vwf.GP_X, x)
        self.h(vwf.GP_Y, y)
        u.reg_write(M.UC_MIPS_REG_PC, u.reg_read(M.UC_MIPS_REG_RA))

    def call(self, fn, a0):
        u = self.u
        u.mem_write(0x1f0000, a0 + b'\0')
        u.reg_write(M.UC_MIPS_REG_A0, 0x801f0000)
        u.reg_write(M.UC_MIPS_REG_GP, GP)
        u.reg_write(M.UC_MIPS_REG_SP, STACK)
        u.reg_write(M.UC_MIPS_REG_RA, RET)
        u.emu_start(fn, RET, count=2_000_000)
        return u.reg_read(M.UC_MIPS_REG_V0)

    def cstr(self, va):
        b = self.u.mem_read(va & 0x1FFFFFFF, namedict.CAP)
        return bytes(b[:b.index(0)])


def layout_model(buf, widths, x0=0, y0=0):
    """(char, x, y) the stubbed print_all should draw (no wrapping)."""
    out, x, y, i = [], x0, y0, 0
    while i < len(buf):
        c = buf[i]
        if c == namedict.TAB:
            base = x0 if y == y0 else 0
            px = (buf[i + 1] - namedict.TAB_BASE) << 6 | (buf[i + 2] - namedict.TAB_BASE)
            x = max(x + namedict.SPACE_W, base + px)
            i += 3
            continue
        if c == 0x0A:
            x, y = 0, y + 16
        else:
            out.append((c, x, y))
            x += widths[c - 0x20] if 0x20 <= c < 0x7F else vwf.DEFAULT_W
            if c == 0x20 and namedict.fig_space(buf, i):
                x += namedict.FIG_EXTRA
        i += 1
    return out


def samples(pairs, n, rnd):
    keys = [text.encode(j) for j in pairs]
    fixed = [b'', b'Cao Cao  12', '曹操'.encode('cp932') + b'   75', '統率 75\n統率100'.encode('cp932'),
             'Lead ' .encode() + '曹操'.encode('cp932') + b'   9', b'\x1b1C' + '劉備'.encode('cp932') + b'  3',
             '劉備'.encode('cp932') + b' ' * 60 + b'x', '  曹純   征服  23   99 '.encode('cp932'),
             ' 夏侯惇  義侠  36  100 '.encode('cp932'), b'Gold  120 \x1b1C  5', b'in 200 AD',
             '曹操 の'.encode('cp932'), '曹操 5'.encode('cp932'), '曹操 has'.encode('cp932'),
             b'BbbbBbbb', b'aB', b'Ab', b'\x1bkCao', b'x\x1bkCao', b'LiuBei  12']
    out = list(fixed)
    pieces = [b' ', b'  ', b'   ', b'\n', b'12', b'7', b'Gold', '人徳'.encode('cp932'), 'の'.encode('cp932'), b'\xb1']
    for _ in range(n):
        out.append(b''.join(rnd.choice(keys) if rnd.random() < 0.4 else rnd.choice(pieces)
                            for _ in range(rnd.randint(1, 8)))[:400])
    return out


ROWS = [
    [('曹操'.encode('cp932'), 64, 2), (b'None', 32, 2), (b'Empire', 32, 2), (b'100', 32, 0x13),
     (b'\x81\x9b', 16, 2), (b' 88', 32, 0x13), (b'98', 32, 0x13)],
    [('夏侯惇'.encode('cp932'), 64, 2), (b'Spying', 32, 2), (b'Advancement', 32, 2), (b' 70', 32, 0x23),
     (b'', 16, 2), (b'7', 24, 1), (b'Left', 40, 0)],
    [(b'Public Safety and more', 48, 0), ('曹純'.encode('cp932'), 64, 3), (b'5', 32, 1)],
    [('夏侯惇'.encode('cp932'), 64, 2), ('Spying', 32, 2), ('Advancement', 32, 2), (b' 70', 32, 0x23),
     ('Early Bloomer', 16, 2), ('Left', 40, 0), (b'99', 32, 0x13)],     # str = constant (CELL_REF)
    [('劉表'.encode('cp932'), 64, 2), (b'Xiangyang', 32, 2), ('Xiangyang', 32, 2), (b'3160', 64, 0x13),
     (b'10390', 64, 0x13)],           # ruler list: a pooled city name borrows the later cells' room
    [(b'A much too long pooled string here', 32, 2), (b'12345', 64, 0x13), ('Xiangyang', 32, 2)],
]


def cell_rows(cpu, at, layout, d):
    """tools/cells.py: rows (and the LAYOUT block) built by the MIPS vs the model."""
    u, bad, rows = cpu.u, 0, []
    for r, row in enumerate(ROWS):
        row = [(cpu.static(t), w, f) if isinstance(t, str) else (t, w, f) for t, w, f in row]
        out = 0x801e0000 + r * 0x400            # rows apart: each starts a new row
        a1 = out
        obj, desc = 0x801d0000, 0x801d1000      # the table object and its column descriptors (width at +2)
        for k, (_, w, _) in enumerate(row):
            u.mem_write((desc + 16 * k + 2) & 0x1FFFFFFF, struct.pack('<h', w))
        u.mem_write((obj + 0x1dc) & 0x1FFFFFFF, struct.pack('<I', desc))
        u.mem_write((obj + 0x731) & 0x1FFFFFFF, bytes([len(row) - 1, 1]))
        for k, (t, w, flags) in enumerate(row):
            if isinstance(t, tuple):
                p = t[1]
            else:
                p = 0x801f0000
                u.mem_write(0x1f0000, t + b'\0')
            u.mem_write((STACK + 0x10) & 0x1FFFFFFF, struct.pack('<I', p))
            ret = cells.FIRST_RET if k == 0 else 0x800360a4
            for reg, v in ((M.UC_MIPS_REG_A1, a1), (M.UC_MIPS_REG_A2, w), (M.UC_MIPS_REG_A3, flags),
                           (M.UC_MIPS_REG_SP, STACK), (M.UC_MIPS_REG_RA, ret), (M.UC_MIPS_REG_S0, obj),
                           (M.UC_MIPS_REG_S1, desc + 16 * k), (M.UC_MIPS_REG_S2, len(row) - k)):
                u.reg_write(reg, v)
            u.emu_start(at, ret, count=100_000)
            a1 = u.reg_read(M.UC_MIPS_REG_V0)
        got = bytes(u.mem_read(out & 0x1FFFFFFF, a1 - out))
        want, lay = cells.model([row])
        lb = bytes(u.mem_read(layout & 0x1FFFFFFF, 4 + 4 * len(lay)))
        glay = [struct.unpack_from('<HBB', lb, 4 + 4 * k) for k in range(lb[0])]
        if got != want[0] or glay != lay:
            bad += 1
            print('cells row %d\n  mips  %r %r\n  model %r %r' % (r, got, glay, want[0], lay))
        rows.append((got, lay))
    return bad, rows


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    disc = iso.Disc(setup.CLEAN)
    recs = disc.records()
    exe = bytearray(disc.file(iso.EXE_NAME, recs))
    heap = kanji.apply(exe)
    vwf.apply(exe, heap)
    pairs = names.pairs(disc, recs)
    d = namedict.build(pairs, *names.compose_sets(disc, recs))
    head, blob = namedict.pack(d, 0)
    idx, blob_at, buf = heap.put(head), heap.put(blob), heap.alloc(namedict.CAP)
    layout = heap.put(bytes(namedict.LAYOUT_SIZE))
    lst = namedict.lines(idx, blob_at, buf, STUB, layout)
    at = heap.alloc(len(asm.assemble(0x80000000, lst)))
    vwf.put(exe, at, asm.assemble(at, lst))
    lab = asm.labels(at, lst)
    cl = cells.routine(heap.put(bytes(20)), layout)
    cell_at = heap.alloc(len(asm.assemble(0x80000000, cl)))
    vwf.put(exe, cell_at, asm.assemble(cell_at, cl))
    cpu = Cpu(exe)
    bad, rows = cell_rows(cpu, cell_at, layout, d)
    cpu.h(namedict.GP_CELL_H, 16)
    cpu.h(namedict.GP_BUF_H, 0x7000)
    for s in samples(pairs, n, random.Random(1)):
        want = d.expand(s)
        got = cpu.cstr(cpu.call(lab['expand'], s))
        if got != want:
            bad += 1
            print('expand %r\n  mips  %r\n  model %r' % (s, got, want))
            continue
        m = cpu.call(lab['measure'], s)
        if m != (namedict.measure_px(want, cpu.widths) + 7) // 8:
            bad += 1
            print('measure %r: mips %d model %d' % (s, m, (namedict.measure_px(want, cpu.widths) + 7) // 8))
        for x0 in (0, 8):
            cpu.drawn = []
            cpu.h(vwf.GP_X, x0)
            cpu.h(vwf.GP_Y, 0)
            cpu.call(lab['print_all'], s)
            lm = layout_model(want, cpu.widths, x0)
            if cpu.drawn != lm:
                bad += 1
                print('print_all %r x0=%d\n  mips  %r\n  model %r' % (s, x0, cpu.drawn[:12], lm[:12]))
    for row, lay in rows:                       # each row with its own table's layout
        cpu.u.mem_write(layout & 0x1FFFFFFF, bytes([len(lay), 0, 0, 0]) +
                        b''.join(struct.pack('<HBB', *c) for c in lay))
        d.layout = lay
        d.deref = lambda a: bytes(cpu.u.mem_read(a & 0x1FFFFFFF, 64)).split(b'\0')[0]
        want = d.expand(row)
        got = cpu.cstr(cpu.call(lab['expand'], row))
        print('row %r\n  -> %r' % (row, want))
        if got != want:
            bad += 1
            print('  mips %r' % got)
        cpu.drawn = []
        cpu.h(vwf.GP_X, 0)
        cpu.h(vwf.GP_Y, 0)
        cpu.call(lab['print_all'], row)
        if cpu.drawn != layout_model(want, cpu.widths, 0):
            bad += 1
            print('  print_all differs')
    d.layout = []
    print('%d strings, %d mismatches' % (n + 14 + len(ROWS), bad))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
