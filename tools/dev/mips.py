"""PS1 (R3000) disassembly helpers for reverse engineering (capstone MIPS32).

Images: the main program (PS-EXE, loaded at 0x80010000, file offset 0x800) or an
overlay from BIN/ (raw, loaded at 0x80199000 in the JP program).

  refs <img> lo hi     -> lui+addiu/ori/load/store pairs and 32-bit data words pointing into [lo, hi)
  dis <img> vaddr [n]  -> print n instructions
  jals <img> target    -> callers of a function
img: jp | us | jp:USER (overlay) | us:USER
usage: .venv/bin/python tools/dev/mips.py refs jp 0x800f9800 0x80100000
"""
import os, struct, sys
import capstone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = {'jp': 'extract/jp/SLPM_863.12', 'us': 'extract/us/SLUS_009.18'}
OVL_BASE = {'jp': 0x80199000, 'us': 0x80190000}
md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)


class Image:
    def __init__(self, name):
        ver, _, ovl = name.partition(':')
        if ovl:
            self.data = open(os.path.join(ROOT, 'extract', ver, 'BIN', ovl + '.BIN'), 'rb').read()
            self.base, self.foff = OVL_BASE[ver], 0
        else:
            self.data = open(os.path.join(ROOT, EXE[ver]), 'rb').read()
            self.base, self.foff = 0x80010000, 0x800
        self.end = self.base + len(self.data) - self.foff

    def word(self, va):
        return struct.unpack_from('<I', self.data, va - self.base + self.foff)[0]


def s16(x):
    return x - 0x10000 if x & 0x8000 else x


def refs(img, lo, hi):
    out, lui = [], {}
    for va in range(img.base, img.end - 3, 4):
        w = img.word(va)
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 0x0F:
            lui[rt] = (imm << 16, va)
            continue
        if op in (0x09, 0x0D) or 0x20 <= op <= 0x3F:      # addiu, ori, loads/stores
            if rs in lui and va - lui[rs][1] < 64:
                t = (lui[rs][0] + (imm if op == 0x0D else s16(imm))) & 0xFFFFFFFF
                if lo <= t < hi:
                    out.append((va, t))
        if op in (0x02, 0x03) or (op == 0 and (w & 0x3F) in (8, 9)):
            lui.clear()
        if lo <= w < hi:
            out.append((va, w))
    return out


def dis(img, va, n):
    for a in range(va, va + 4 * n, 4):
        w = img.word(a)
        ins = next(md.disasm(struct.pack('<I', w), a), None)
        x = (ins.mnemonic, ins.op_str) if ins else ('.word', '0x%08x' % w)
        print('%08x  %08x  %-8s %s' % ((a, w) + x))


def jals(img, target):
    want = 0x0C000000 | ((target >> 2) & 0x3FFFFFF)
    return [va for va in range(img.base, img.end - 3, 4) if img.word(va) == want]


if __name__ == '__main__':
    cmd, img, a = sys.argv[1], Image(sys.argv[2]), [int(x, 0) for x in sys.argv[3:]]
    if cmd == 'refs':
        for va, t in refs(img, a[0], a[1]):
            print('%08x -> %08x' % (va, t))
    elif cmd == 'jals':
        print(' '.join('%08x' % v for v in jals(img, a[0])))
    else:
        dis(img, a[0], a[1] if len(a) > 1 else 40)
