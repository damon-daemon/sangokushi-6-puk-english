"""Put a new build into a running (state-loaded) headless DuckStation instead of booting.

A save state holds the RAM of the build it was taken on, program included. This halts
the CPU (GDB connect; reconnected until pc and ra are outside the text renderer and the
heap, so none of our hook code is running), then writes through the GDB server, where RAM
differs from the new build:
  - program code and the freed font area (static at run time) in full,
  - other program bytes the build changes (strings, tables in .data), and every
    pointer-table word that references a string (an older build may have moved it),
  - in the overlay in RAM (the one whose code matches RAM best, >= 95 %), the bytes
    the build changes and its string pointer-table words (other patches an older build
    made there and this one drops stay),
and continues. Message blocks already loaded (the relocated block buffer in the heap is left
alone) and file positions read at boot are not
refreshed (re-enter the screen / reboot for MESSAGE.S6 changes).

usage: .venv/bin/python tools/emu/inject.py [built_track1.bin]
"""
import os, struct, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile, progstr, setup
import gdbmem, ram as ramlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILT = os.path.join(ROOT, 'build', 'sangokushi_6_puk_en_track1.bin')
CODE_END = 0x800e4798
BUSY = [(0x8003f000, 0x80041000), (0x800f2e68, 0x8010c838)]  # print_string..putc/draw_char; heap
STATIC = [(0x80010000, CODE_END), (0x800f2e68, 0x8010c838)]   # code; kanji/half-width font area = heap


def ranges_diff(a, b, base):
    """[(va, va_end)] where a and b differ (merged when < 16 bytes apart)."""
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and (a[j] != b[j] or any(a[k] != b[k] for k in range(j, min(j + 16, n)))):
                j += 1
            out.append((base + i, base + j))
            i = j
        else:
            i += 1
    return out


def msg_buffer(prog_c, prog_n):
    """(lo, hi) of the message block buffer when the build moved it into the heap, else None
    (its contents = the block loaded in RAM: never overwritten)."""
    def w(img, va):
        return struct.unpack_from('<I', img, va - msgfile.EXE_BASE)[0]
    at = 0x800b3e90                             # lui a1, hi(buf) ... addiu a1, a1, lo(buf) (msgfile.relocate)
    if w(prog_c, at) == w(prog_n, at):
        return None
    buf = ((w(prog_n, at) & 0xFFFF) << 16) + ((w(prog_n, at + 12) & 0xFFFF) ^ 0x8000) - 0x8000
    return buf, buf + max(msgfile.max_block(msgfile.table(prog_n)), msgfile.BUF_RESERVE)


def plan(mem, clean, new):
    """[(va, bytes)] to write. mem = 2 MB RAM, clean/new = {tag: image bytes}."""
    want = {}                                   # va -> byte
    exe_c, exe_n = clean['PROG'][0x800:], new['PROG'][0x800:]
    keep = msg_buffer(clean['PROG'], new['PROG']) or (0, 0)

    def take(img, base, lo, hi):
        for va in range(lo, hi):
            if not keep[0] <= va < keep[1]:
                want[va] = img[va - base]

    for lo, hi in STATIC:
        take(exe_n, 0x80010000, lo, hi)
    # whole words: a pointer whose high bytes equal the clean ones but not the old build's in
    # RAM (0x8010xxxx in the build, 0x800fxxxx in the state) would otherwise come out mixed
    for lo, hi in ranges_diff(exe_c, exe_n, 0x80010000):
        take(exe_n, 0x80010000, lo & ~3, (hi + 3) & ~3)
    # pointer-table words that reference strings (static tables): an older build may have
    # repointed one that this build leaves clean (string back in its slot)
    cimgs = progstr.images({t: bytearray(b) for t, b in clean.items()})
    refs = progstr.Refs(cimgs)
    ptrs = [w for r in progstr.survey(cimgs) for w in refs.of(r['tag'], r['va'])[1]]
    for tag, va in ptrs:
        if tag == 'PROG':
            take(exe_n, 0x80010000, va, va + 4)
    ovl, best, at = None, 0.95, progstr.OVL_BASE & 0x1FFFFF
    for t in progstr.OVERLAYS:                  # the overlay whose code matches RAM best
        c = clean.get(t)
        if not c:
            continue
        n = c.rfind(b'\x08\x00\xe0\x03') + 8
        same = sum(x == y for x, y in zip(c[:n], mem[at:at + n])) / max(n, 1)
        if same > best:
            ovl, best = t, same
    if ovl and ovl in new:
        for lo, hi in ranges_diff(clean[ovl], new[ovl], progstr.OVL_BASE):
            take(new[ovl], progstr.OVL_BASE, lo & ~3, (hi + 3) & ~3)
        for tag, va in ptrs:                    # its pointer-table words too (same reason)
            if tag == ovl:
                take(new[ovl], progstr.OVL_BASE, va, va + 4)
    out, run = [], None                         # runs where RAM differs, gaps < 32 bytes merged
    for va in sorted(want):
        if mem[va & 0x1FFFFF] == want[va]:
            continue
        if run and va - run[1] < 32 and all(v in want for v in range(run[1], va)):
            run[1] = va + 1
        else:
            run = [va, va + 1]
            out.append(run)
    # whole aligned words (DuckStation's GDB server does not answer unaligned writes)
    out = [(lo & ~3, bytes(want.get(v, mem[v & 0x1FFFFF]) for v in range(lo & ~3, (hi + 3) & ~3)))
           for lo, hi in out]
    return out, ovl


def images(path):
    disc = iso.Disc(path)
    return progstr.disc_files(disc, disc.records())


def main():
    built = sys.argv[1] if len(sys.argv) > 1 else BUILT
    clean, new = images(setup.CLEAN), images(built)
    for tries in range(50):                      # connecting halts the CPU: retry until it is
        g = gdbmem.G()                           # outside our hooks and the text renderer
        g.s.settimeout(5)
        g.cmd('?')
        r = gdbmem.regs(g)
        if not any(lo <= r[k] < hi for k in ('pc', 'r31') for lo, hi in BUSY):
            break
        g.s.sendall(b'$c#63')
        g.s.close()
        time.sleep(0.05)
    else:
        sys.exit('CPU always busy in the text code; try again')
    where = 'pc %08x ra %08x' % (r['pc'], r['r31'])
    mem = ramlib.shm()
    writes, ovl = plan(mem, clean, new)
    total = 0
    for va, b in writes:
        for k in range(0, len(b), 0x400):
            part = b[k:k + 0x400]
            r = g.cmd('M%x,%x:%s' % (va + k, len(part), part.hex()))
            if r != 'OK':
                sys.exit('write %08x failed: %r' % (va + k, r))
        total += len(b)
    g.s.sendall(b'$c#63')
    print('halted at %s; wrote %d bytes in %d runs (overlay in RAM: %s)' % (where, total, len(writes), ovl))


if __name__ == '__main__':
    main()
