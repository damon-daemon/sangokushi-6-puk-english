"""Size limits of the sprintf targets reached by program/overlay format strings.

Every call of the game's sprintf (SPRINTF) whose format is a constant string (lui/addiu
pair or gp+offset in a1) is followed to its target buffer (a0):
  - sp + off in the calling function: size = distance to the next stack offset the
    function uses (or the frame end) — the real buffer is not larger;
  - an argument of the calling function (a0..a3 passed through unchanged, or via a
    saved register set from it at entry): the callers of that function (jal to its
    start in the same image, and from the overlays for a program function) are
    followed the same way, up to three levels; the smallest size counts;
  - anything else (struct fields, globals): unknown.
limits(images) -> {format key: [(site, size or None)]}; progstr.py checks the English
against them (expand() = longest output with NAME bytes per %s).
"""
import collections, re, struct

SPRINTF = 0x8003fc28
NAME = 16            # %s: longest English name/label passed as an argument (names phase: confirm)
NUMBER = 5           # %d without a width (game values stay below 100,000)
# formats whose %s arguments are known to be shorter than NAME: {format key: longest %s}
SHORT_S = {
    'PROG:8011882c': 2,      # '%s門' (HEX gate label): %s = direction PROG:80116bc0.. (N/E/S/W)
    'REVENT:801e4958': 10,   # trade income row: city name, trade route (PROG:801172b4..), both <= 9
    'PROG:80116c80': 8,      # health label: status from 0x800550b0 (Good / Ill / Very Ill / -)
}
# targets traced by hand: {(image, function start): buffer size} for a0 passed down from a caller
KNOWN = {
    # advisor reports (block 46): dispatchers 0x801b08ac / 0x801b4d54, both called only from
    # 0x801aace0 with its stack buffer sp+0x40..0x284, handed down to these builders
    ('COMP', 0x801b2f3c): 580, ('COMP', 0x801b1610): 580, ('COMP', 0x801b0d44): 580,
    ('COMP', 0x801b4e9c): 580, ('COMP', 0x801b09d4): 580,
}
# global buffers: {address: size} (size = distance to the next referenced global)
GLOBALS = {0x80170d54: 256, 0x80170e54: 256, 0x801971a4: 256, 0x801972f4: 16}
# sprintf/copy sites whose target the backward scan misses: {'TAG:call': size}
SITES = {'INIT:801ac8a4': 16,   # a0 = sp+0x24 set in the delay slot of the branch before
         # HEX policy labels: 5 x 16 B at sp+0x34 (a0 = sp moved, then + 0x34 + 16i)
         'HEX:801b9b3c': 16, 'HEX:801b9b64': 16, 'HEX:801b9c30': 16}
# function arguments that are buffers, called from another image: {(image, function start, arg): size}
ARGS = {('HEX', 0x801be458, 1): 64}   # battle report builders: PROG 0x80081400 passes sp+0x48..0x88
CONV = re.compile(rb'%([-+ #0]*)(\d*)(?:\.\d+)?([hl]?)([diouxXcsfeEgGp%])')


def s16(x):
    return x - 0x10000 if x & 0x8000 else x


def writes(w):
    op = w >> 26
    if op == 0:
        return (w >> 11) & 31
    if op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F) or 0x20 <= op <= 0x26:
        return (w >> 16) & 31
    if op == 3:
        return 31
    return None


def expand(b, name=NAME):
    """Longest output of a format string (bytes); name = longest %s argument."""
    n, pos = 0, 0
    for m in CONV.finditer(b):
        n += m.start() - pos
        pos = m.end()
        width = int(m.group(2) or 0)
        c = m.group(4)
        if c == b's':
            n += max(width, name)
        elif c == b'%':
            n += 1
        elif c == b'c':
            n += max(width, 1)
        else:
            n += max(width, 7 if m.group(3) == b'l' else NUMBER)
    return n + len(b) - pos


class Func:
    def __init__(self, img, va):
        a = va
        while a > img.base and not (img.w(a) >> 16 == 0x27bd and img.w(a) & 0x8000):
            a -= 4
        self.start, self.frame = a, -s16(img.w(a) & 0xFFFF) if a > img.base else 0
        for b in range(a - 4, a - 20, -4):         # a few instructions before the frame (lw v0, gp..)
            if b - 8 >= img.base and img.w(b - 8) == 0x03e00008:
                self.start = b
                break
            if img.w(b - 8) >> 26 == 3 or img.w(b) == 0x03e00008:
                break
        e = a
        while e < img.code_end and img.w(e) != 0x03e00008:
            e += 4
        self.end = e + 8
        offs = set()
        for x in range(self.start, self.end, 4):
            w = img.w(x)
            if (w >> 21) & 31 == 29 and (w >> 26 == 0x09 or 0x20 <= w >> 26 <= 0x2E):
                offs.add(s16(w & 0xFFFF))
        self.offs = sorted(offs)

    def size_at(self, off):
        if off not in self.offs:
            return None
        i = self.offs.index(off)
        return (self.offs[i + 1] if i + 1 < len(self.offs) else self.frame) - off


def source(img, call, reg, depth=40):
    """Where reg comes from at the call at `call` (the delay slot included):
    ('sp', off) | ('arg', n) | ('const', address) | None."""
    seq = [call + 4] + list(range(call - 4, call - 4 * depth, -4))
    lo, top = None, max(img.base, Func(img, call).start)
    for a in seq:
        if a < top:
            break
        w = img.w(a)
        if writes(w) != reg:
            continue
        op, rs = w >> 26, (w >> 21) & 31
        if op == 0x09 and rs == 29 and lo is None:
            return ('sp', s16(w & 0xFFFF))
        if op == 0x09 and rs == 28 and lo is None:
            return ('const', (0x8011da2c + s16(w & 0xFFFF)) & 0xFFFFFFFF)
        if op == 0x09 and rs == reg and lo is None:
            lo = s16(w & 0xFFFF)
            continue
        if op == 0x0F and lo is not None:
            return ('const', ((w & 0xFFFF) << 16) + lo & 0xFFFFFFFF)
        if op == 0 and w & 0x3F == 0x21 and (w >> 16) & 31 == 0 and lo is None:   # move reg, rs
            if 4 <= rs <= 7:
                f = Func(img, a)
                if not any(writes(img.w(x)) == rs for x in range(f.start, a, 4)):
                    return ('arg', rs - 4)
                return None
            reg = rs
            continue
        return None
    if 4 <= reg <= 7 and lo is None:                     # unchanged since the function start?
        f = Func(img, call)
        if not any(writes(img.w(x)) == reg for x in range(f.start, call, 4)):
            return ('arg', reg - 4)
    if 16 <= reg <= 23 and lo is None:                   # saved register set once from an argument
        f = Func(img, call)
        ws = [x for x in range(f.start, call, 4) if writes(img.w(x)) == reg]
        if len(ws) == 1:
            w = img.w(ws[0])
            rs = (w >> 21) & 31
            if (w >> 26 == 0 and w & 0x3F == 0x21 and (w >> 16) & 31 == 0 and 4 <= rs <= 7
                    and not any(writes(img.w(x)) == rs for x in range(f.start, ws[0], 4))):
                return ('arg', rs - 4)
    return None


def jals(img):
    out = collections.defaultdict(list)
    for a in range(img.base, img.code_end, 4):
        w = img.w(a)
        if w >> 26 == 3:
            out[(w & 0x3FFFFFF) << 2 | 0x80000000].append(a)
    return out


def limits(imgs):
    """{format key 'TAG:va': [(site 'TAG:va', size or None)]} for every constant format."""
    imgs = [i for i in imgs]
    calls = {i.tag: jals(i) for i in imgs}
    prog = next(i for i in imgs if i.tag == 'PROG')
    out = collections.defaultdict(list)
    for img in imgs:
        for call in calls[img.tag].get(SPRINTF, []):
            fmt = source(img, call, 5)
            if not fmt or fmt[0] != 'const':
                continue
            va = fmt[1]
            fimg = prog if va < 0x80199000 else img
            if not fimg.base <= va < fimg.end:
                continue
            key = '%s:%08x' % (fimg.tag, va)
            out[key].append(('%s:%08x' % (img.tag, call), dst_size(img, call, calls, imgs)))
    return out


def dst_size(img, call, calls, imgs, reg=4, depth=3):
    """Size of the buffer in `reg` at `call`: a stack buffer of this function, or of a
    caller when it is passed down unchanged as an argument (up to `depth` levels)."""
    if reg == 4 and '%s:%08x' % (img.tag, call) in SITES:
        return SITES['%s:%08x' % (img.tag, call)]
    src = source(img, call, reg)
    if src is None:
        return None
    if src[0] == 'sp':
        return Func(img, call).size_at(src[1])
    if src[0] == 'const':
        return GLOBALS.get(src[1])
    if src[0] != 'arg':
        return None
    f = Func(img, call)
    if (img.tag, f.start, src[1]) in ARGS:
        return ARGS[(img.tag, f.start, src[1])]
    if depth == 0:
        return None
    callers = [(img, c) for c in calls[img.tag].get(f.start, [])]
    if img.tag == 'PROG':
        callers += [(i, c) for i in imgs if i.tag != 'PROG' for c in calls[i.tag].get(f.start, [])]
    sizes = [dst_size(ci, c, calls, imgs, 4 + src[1], depth - 1) for ci, c in callers]
    if not sizes or None in sizes:
        return None
    return min(sizes)


# ---------------------------------------------------------------- messages
MSG_GET = 0x800b4dc4                      # msg_get(m, id): a1 = id
MSG_ARGS = range(0x800c4558, 0x800c4730, 4)  # message-with-arguments family: a0 = id
COPIERS = {SPRINTF: 'sprintf', 0x80014da0: 'strcpy', 0x80014d70: 'strcat'}


def follow(img, call):
    """(function, argument registers) of the first call after `call` that takes its
    result (v0, also through moves), or None (overwritten, stored, jump, far)."""
    hold = {2}
    a, end = call + 8, min(call + 4 * 80, img.code_end)
    while a < end:
        w = img.w(a)
        op = w >> 26
        if op == 3:
            ds = img.w(a + 4)
            h = set(hold)
            if ds >> 26 == 0 and ds & 0x3F == 0x21 and (ds >> 16) & 31 == 0 and (ds >> 21) & 31 in h:
                h.add((ds >> 11) & 31)
            elif writes(ds) in h:
                h.discard(writes(ds))
            args = [r for r in (4, 5, 6, 7) if r in h]
            if args:
                return (w & 0x3FFFFFF) << 2 | 0x80000000, args
            hold = {r for r in h if r >= 16}
            if not hold:
                return None
            a += 8
            continue
        if op == 2 or (op == 0 and w & 0x3F in (8, 9)) or op == 0x2B and (w >> 16) & 31 in hold:
            return None
        if op == 0 and w & 0x3F == 0x21 and (w >> 16) & 31 == 0 and (w >> 21) & 31 in hold:
            hold.add((w >> 11) & 31)
        elif writes(w) in hold:
            hold.discard(writes(w))
            if not hold:
                return None
        a += 4
    return None


def msg_sites(imgs):
    """[(site, message id or None, copier, size or None)] of message results copied into
    a buffer (sprintf format or argument, strcpy/strcat source)."""
    calls = {i.tag: jals(i) for i in imgs}
    out = []
    for img in imgs:
        for t, sites in calls[img.tag].items():
            if t != MSG_GET and t not in MSG_ARGS:
                continue
            for call in sites:
                src = source(img, call, 5 if t == MSG_GET else 4)
                mid = None
                w = img.w(call + 4) if src is None else 0
                for a in (call + 4,) + tuple(range(call - 4, call - 40, -4)):
                    x = img.w(a)
                    if writes(x) == (5 if t == MSG_GET else 4):
                        if x >> 26 in (0x09, 0x0D) and (x >> 21) & 31 == 0:
                            mid = x & 0xFFFF if x >> 26 == 0x0D else s16(x & 0xFFFF)
                        break
                f = follow(img, call)
                if not f or f[0] not in COPIERS:
                    continue
                fn, args = f
                use = None
                for c in range(call + 8, call + 4 * 80, 4):
                    x = img.w(c)
                    if x >> 26 == 3 and (x & 0x3FFFFFF) << 2 | 0x80000000 == fn:
                        use = c
                        break
                size = dst_size(img, use, calls, imgs) if use else None
                if size is None and use:
                    size = KNOWN.get((img.tag, Func(img, use).start))
                out.append(('%s:%08x' % (img.tag, use or call), mid, COPIERS[fn], size))
    return out
