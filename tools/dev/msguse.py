"""Where expanded messages go: every call of msg_get (0x800b4dc4, returns the expanded
text of message a1) followed forward to the first copy of its result into a buffer
(sprintf / strcpy / strcat / strncpy / memcpy) or to a call that takes it otherwise.

  msguse.py [IMAGE ...]    summary + every copy site (IMAGE = PROG or an overlay name)

For a copy into the stack (a0 = sp+off) the buffer size is estimated as the distance to
the next stack offset the function uses (lower bound of the real size).
"""
import collections, os, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import progstr

MSG_GET = 0x800b4dc4
COPIERS = {0x8003fc28: 'sprintf', 0x80014da0: 'strcpy', 0x80014d70: 'strcat', 0x80014db0: 'strncpy',
           0x80014df0: 'memcpy'}
R = 'zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra'.split()


def s16(x):
    return x - 0x10000 if x & 0x8000 else x


def writes(w):
    op = w >> 26
    if op == 0:
        return (w >> 11) & 31
    if op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F) or 0x20 <= op <= 0x26:
        return (w >> 16) & 31
    return None


def func_start(img, va):
    """Address of the addiu sp, sp, -N that opens the function containing va."""
    a = va
    while a > img.base:
        w = img.w(a)
        if w >> 16 == 0x27bd and w & 0x8000:
            return a, -s16(w & 0xFFFF)
        a -= 4
    return img.base, 0


def stack_offsets(img, start, end):
    out = set()
    for a in range(start, end, 4):
        w = img.w(a)
        if ((w >> 21) & 31) == 29 and (w >> 26 == 0x09 or 0x20 <= w >> 26 <= 0x2E):
            out.add(s16(w & 0xFFFF))
    return sorted(out)


def func_end(img, start):
    a = start
    while a < img.code_end:
        if img.w(a) == 0x03e00008:
            return a + 8
        a += 4
    return img.code_end


def arg_const(img, va, reg):
    """Constant loaded into reg shortly before va (li / addiu zero / ori zero), else None."""
    for a in range(va + 4, va - 40, -4):
        w = img.w(a)
        if writes(w) == reg:
            if w >> 26 in (0x09, 0x0D) and (w >> 21) & 31 == 0:
                return w & 0xFFFF if w >> 26 == 0x0D else s16(w & 0xFFFF)
            return None
    return None


def stack_arg(img, va, reg):
    """sp offset if reg = sp + off was set shortly before the call at va (or in its delay slot)."""
    for a in (va + 4,) + tuple(range(va - 4, va - 60, -4)):
        w = img.w(a)
        if writes(w) == reg:
            if w >> 26 == 0x09 and (w >> 21) & 31 == 29:
                return s16(w & 0xFFFF)
            return None
    return None


def follow(img, call):
    """(kind, detail) of the first use of the msg_get result after the call at `call`."""
    hold = {2}
    a = call + 8
    end = min(call + 4 * 80, img.code_end)
    while a < end:
        w = img.w(a)
        op = w >> 26
        if op == 3:
            t = (w & 0x3FFFFFF) << 2 | 0x80000000
            ds = img.w(a + 4)
            h = set(hold)
            if op == 0 and False:
                pass
            # the delay slot executes before the call
            if ds >> 26 == 0 and ds & 0x3F == 0x21 and ((ds >> 16) & 31) == 0 and ((ds >> 21) & 31) in h:
                h.add((ds >> 11) & 31)
            elif writes(ds) in h:
                h.discard(writes(ds))
            args = [r for r in (4, 5, 6, 7) if r in h]
            if not args:
                if t == MSG_GET:
                    return 'lost', 'another msg_get'
                a += 8
                hold = {r for r in h if r >= 16}       # saved registers survive the call
                if not hold:
                    return 'lost', 'call %08x' % t
                continue
            name = COPIERS.get(t, '%08x' % t)
            return name, (args, stack_arg(img, a, 4))
        if op == 2 or (op == 0 and (w & 0x3F) in (8, 9)):
            return 'jump', '%08x' % a
        if op == 0 and w & 0x3F == 0x21 and ((w >> 16) & 31) == 0 and ((w >> 21) & 31) in hold:
            hold.add((w >> 11) & 31)
        elif op == 0x2B and ((w >> 16) & 31) in hold:            # stored: give up
            return 'stored', '%08x' % a
        elif writes(w) in hold:
            hold.discard(writes(w))
            if not hold:
                return 'lost', 'overwritten %08x' % a
        a += 4
    return 'lost', 'far'


def scan(img):
    out = []
    for a in range(img.base, img.code_end, 4):
        w = img.w(a)
        if w >> 26 == 3 and ((w & 0x3FFFFFF) << 2 | 0x80000000) == MSG_GET:
            mid = arg_const(img, a, 5)
            kind, det = follow(img, a)
            out.append((a, mid, kind, det))
    return out


def main(tags):
    imgs = {i.tag: i for i in progstr.images({t: bytearray(b) for t, b in progstr.clean_files().items()})}
    for tag in tags or ['PROG'] + [t for t in progstr.OVERLAYS if t not in progstr.NO_STRINGS]:
        img = imgs[tag]
        rows = scan(img)
        c = collections.Counter(k for _, _, k, _ in rows)
        print('== %s: %d calls; %s' % (tag, len(rows), ', '.join('%s %d' % kv for kv in c.most_common(12))))
        for a, mid, kind, det in rows:
            if kind in COPIERS.values():
                args, off = det
                st, size = func_start(img, a)
                offs = stack_offsets(img, st, func_end(img, st))
                buf = ''
                if off is not None and off in offs:
                    i = offs.index(off)
                    buf = 'sp+%#x size<=%d' % (off, (offs[i + 1] if i + 1 < len(offs) else size) - off)
                print('  %08x msg %-6s %-8s as %s  dst %s' % (a, mid, kind, '/'.join(R[r] for r in args), buf or 'non-stack'))


if __name__ == '__main__':
    main(sys.argv[1:])


def reg_value(img, va, reg, depth=24):
    """Constant address in reg at the call at va (lui/addiu pair, gp+off), else None."""
    lo = None
    for a in (va + 4,) + tuple(range(va - 4, va - 4 * depth, -4)):
        w = img.w(a)
        if writes(w) != reg:
            continue
        op, rs = w >> 26, (w >> 21) & 31
        if op == 0x09 and rs == 28:
            return (progstr.GP + s16(w & 0xFFFF)) & 0xFFFFFFFF
        if op == 0x09 and rs == reg and lo is None:
            lo = s16(w & 0xFFFF)
            continue
        if op == 0x0F and lo is not None:
            return ((w & 0xFFFF) << 16) + lo & 0xFFFFFFFF
        if op == 0 and w & 0x3F == 0x21 and (w >> 16) & 31 == 0:   # move reg, rs
            reg = rs
            continue
        return None
    return None


def sprintf_sites(img):
    """[(call va, fmt va or None, dst sp offset or None, size estimate)] of sprintf calls."""
    out = []
    for a in range(img.base, img.code_end, 4):
        w = img.w(a)
        if w >> 26 == 3 and ((w & 0x3FFFFFF) << 2 | 0x80000000) in (0x8003fc28,):
            fmt = reg_value(img, a, 5)
            off = stack_arg(img, a, 4)
            size = None
            if off is not None:
                st, fs = func_start(img, a)
                offs = stack_offsets(img, st, func_end(img, st))
                if off in offs:
                    i = offs.index(off)
                    size = (offs[i + 1] if i + 1 < len(offs) else fs) - off
            out.append((a, fmt, off, size))
    return out
