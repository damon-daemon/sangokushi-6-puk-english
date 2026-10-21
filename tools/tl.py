"""Translation files for MESSAGE.S6: show, apply, check, stats; used by build.py.

The unit is a segment (msgfile.segments): a chain of Japanese text runs and inline
codes, between structural codes (conditions, variant ends). The English replaces the
whole segment; the structure around it is kept byte for byte.

tl/msg/BB.txt holds the English (no Japanese) of block BB, one record per segment:

    # comment (a line starting with #; English text never does)
    @<id>            segment 0 of message <id> (= block * 1000 + index)
    @<id>.<n>        segment n
    English text, possibly over several physical lines (joined with spaces).

  \\n          forced line break (the game word-wraps by itself; JP line breaks are dropped)
  \\s          a space (for a space at the start/end of a segment that continues another
              one, e.g. around a `02 xx bf` code; plain edge spaces are stripped)
  =           (whole record) keep the Japanese of this segment
  {C21026}    call message 21026 (a name, a title or a grammar word of block 0):
              may move or be dropped (Japanese word endings), not added
  {e02} {f15} print variable 02 / 15 (names, numbers): may move, not added
  %s %d %u    printf fields: same ones in the same order; no other '%'

Translation memory: a segment without English takes the English of a segment with the
same Japanese (explicit English always wins).

usage:
    tl.py show  BB [from] [to] [--todo]   # Japanese segments (ids are local indexes)
    tl.py apply BB batch.txt              # validate a batch, merge it into tl/msg/BB.txt
    tl.py check [BB]                      # validate everything in tl/
    tl.py stats
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import msgfile, overflow, pixfont, setup, text

TL_MSG = os.path.join(setup.TL, 'msg')
SEG_MAX = 400                      # bytes per segment (expanded message buffer 510, m+0x410)
_TOK = re.compile(r'\{C(\d+)\}|\{([ef])([0-9A-Fa-f]{2})\}')
_FIELD = re.compile(r'%[-0-9]*[sdu]')
_REC = re.compile(r'@(\d+)(?:\.(\d+))?\s*$')
# Line estimate (warning only; the window of a message is not known yet). Measured
# wrap widths: speaker dialogue 240 px x 3 lines (face + name), name label 192.
WRAP_PX, MIN_LINES = 232, 3
VAR_PX, CALL_PX, FIELD_PX = 64, 40, 24     # guesses for {e..}/{f..}, {C..}, %s/%d
_W = None
# Messages drawn into a text buffer wider than their window: the game's wrap (buffer width)
# runs past the frame, so the build breaks the lines itself. (first id, last id, px, lines)
BOXES = [(40000, 40035, 160, 4)]           # item descriptions (Item window, 4 x 10 kanji)


# ---------------------------------------------------------------- Japanese side
def tok_str(b):
    if b[0] == 0x01:
        return '{C%d}' % (b[2] | b[3] << 8)
    return '{%s%02X}' % (chr(b[2]), b[1])


def seg_str(pieces):
    return ''.join(text.decode(v) if k == 'text' else tok_str(v) for k, v in pieces)


def jp_block(blocks, b):
    """{(id, n): (pieces, Japanese string)} of block b."""
    out = {}
    for i, e in enumerate(blocks[b]):
        n = 0
        for kind, v in msgfile.segments(e):
            if kind == 'seg':
                out[(b * 1000 + i, n)] = (v, seg_str(v))
                n += 1
    return out


def load_blocks():
    disc_exe = os.path.join(setup.ROOT, 'extract', 'jp', 'SLPM_863.12')
    if not os.path.exists(disc_exe):
        import iso
        disc = iso.Disc(setup.CLEAN)
        recs = disc.records()
        return msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    return msgfile.parse(open(os.path.join(setup.ROOT, 'extract', 'jp', 'DATA', 'MESSAGE.S6'), 'rb').read(),
                         open(disc_exe, 'rb').read())


# ---------------------------------------------------------------- tl files
def tl_path(b):
    return os.path.join(TL_MSG, '%02d.txt' % b)


def parse_tl(path):
    """-> {(id, n): English or '='}"""
    out, key, buf = {}, None, []

    def flush():
        if key is not None:
            out[key] = ' '.join(x.strip() for x in buf if x.strip())
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if ln.startswith('#'):
            continue
        m = _REC.match(ln)
        if m:
            flush()
            key, buf = (int(m.group(1)), int(m.group(2) or 0)), []
            if key in out:
                raise ValueError('%s: @%d.%d twice' % (path, *key))
        elif key is not None:
            buf.append(ln)
        elif ln.strip():
            raise ValueError('%s: text before the first @ line: %r' % (path, ln))
    flush()
    return out


def write_tl(b, recs):
    os.makedirs(TL_MSG, exist_ok=True)
    with open(tl_path(b), 'w', encoding='utf-8') as f:
        for (mid, n) in sorted(recs):
            f.write('@%d%s\n%s\n\n' % (mid, '.%d' % n if n else '', recs[(mid, n)]))


# ---------------------------------------------------------------- English side
def english_pieces(en):
    """English string -> segment pieces (text bytes / token bytes)."""
    out, pos = [], 0
    for m in _TOK.finditer(en):
        if m.start() > pos:
            out.append(('text', encode(en[pos:m.start()])))
        if m.group(1):
            v = int(m.group(1))
            out.append(('tok', bytes([0x01, 0x43, v & 0xFF, v >> 8])))
        else:
            out.append(('tok', bytes([0x02, int(m.group(3), 16), ord(m.group(2))])))
        pos = m.end()
    if pos < len(en) or not out:
        out.append(('text', encode(en[pos:])))
    return out


def encode(s):
    return s.replace('\\n', '\n').replace('\\s', ' ').encode('ascii')


def check_seg(jp, en):
    """Errors of one English segment against its Japanese string."""
    errs = []
    plain = _TOK.sub('', en)
    bad = sorted(set(c for c in plain.replace('\\n', '').replace('\\s', '') if not (' ' <= c <= '~') or c in '{}\\'))
    if bad:
        errs.append('characters not allowed: %s' % ''.join(bad))
    if _FIELD.findall(plain) != _FIELD.findall(_TOK.sub('', jp)):
        errs.append('printf fields %s, Japanese has %s' % (_FIELD.findall(plain), _FIELD.findall(_TOK.sub('', jp))))
    if '%' in _FIELD.sub('', plain):
        errs.append("bare '%' (write 'percent')")
    jt = [m.group() for m in _TOK.finditer(jp)]
    for t in set(m.group() for m in _TOK.finditer(en)):
        if en.count(t) > jt.count(t):
            errs.append('%s used %d times, Japanese has %d' % (t, en.count(t), jt.count(t)))
    if not errs:
        n = len(msgfile.seg_bytes(english_pieces(en)))
        if n > SEG_MAX:
            errs.append('%d bytes > %d' % (n, SEG_MAX))
    return errs


def box(mid):
    """(px, lines) of the fixed box message mid is drawn in (BOXES), or None."""
    for a, z, px, n in BOXES:
        if a <= mid <= z:
            return px, n
    return None


def prewrap(en, width):
    """en with a forced line break wherever the in-game wrap at width would break it."""
    global _W
    if _W is None:
        _W = {c: w for c, (_, w) in pixfont.glyphs().items()}
    size = lambda w: sum(VAR_PX if c == '\x01' else FIELD_PX if c == '\x02' else _W.get(c, 8)
                         for c in _TOK.sub('\x01', _FIELD.sub('\x02', w)))
    out = []
    for para in en.split('\\n'):
        line, x = [], 0
        for w in para.split(' '):
            add = size(w) + (_W[' '] if line else 0)
            if line and x + add > width:
                out.append(' '.join(line))
                line, x = [w], size(w)
            else:
                line.append(w)
                x += add
        out.append(' '.join(line))
    return '\\n'.join(out)


def est_lines(en, width=WRAP_PX):
    """Lines the in-game word wrap (tools/vwf.py) gives, inserts at guessed widths."""
    global _W
    if _W is None:
        _W = {c: w for c, (_, w) in pixfont.glyphs().items()}
    s = _TOK.sub('\x01', _FIELD.sub('\x02', en)).replace('\\n', '\n')
    size = lambda w: sum(VAR_PX if c == '\x01' else FIELD_PX if c == '\x02' else _W.get(c, 8) for c in w)
    lines = 0
    for para in s.split('\n'):
        lines += 1
        x = 0
        for i, w in enumerate(para.split(' ')):
            add = size(w) + (_W[' '] if i else 0)
            if i and x + add > width:
                lines, x = lines + 1, size(w)
            else:
                x += add
    return lines


def jp_width(jp):
    """Widest line of a Japanese segment in px (16 per full-width character, 8 ASCII)."""
    s = _TOK.sub('\x01', text.readable(jp))
    return max(sum(VAR_PX if c == '\x01' else 8 if ' ' <= c <= '~' else 16 for c in ln)
               for ln in s.split('\n'))


def warn_seg(jp, en):
    # the window is at least as wide as the widest Japanese line (bios: 256 px)
    w = max(WRAP_PX, jp_width(jp))
    n, jl = est_lines(en, w), jp.count('\n') + 1
    if n > max(MIN_LINES, jl):
        return ['~%d lines at %d px (Japanese %d)' % (n, w, jl)]
    return []


def memory(jps, tls):
    """{Japanese string: English} from every explicit record."""
    tm = {}
    for b, recs in tls.items():
        for k, en in recs.items():
            if en != '=' and k in jps.get(b, {}):
                tm.setdefault(jps[b][k][1], en)
    return tm


def english_entries(blocks, tls=None, log=None):
    """Replace the translated segments in blocks (list of entry lists, in place).
    Returns (translated segment count, errors)."""
    tls = tls if tls is not None else {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS)}
    jps = {b: jp_block(blocks, b) for b in tls if tls[b]}
    tm = memory(jps, tls)
    errs, done, touched = [], 0, []
    orig = [list(es) for es in blocks]
    for b in range(msgfile.NBLOCKS):
        recs = tls.get(b, {})
        for k in recs:
            if k not in jps.get(b, {}):
                errs.append('@%d.%d: no such segment' % k)
        if not recs and not tm:
            continue
        for i, e in enumerate(blocks[b]):
            items = msgfile.segments(e)
            n, changed = 0, False
            for j, (kind, v) in enumerate(items):
                if kind != 'seg':
                    continue
                key = (b * 1000 + i, n)
                n += 1
                jp = seg_str(v)
                en = recs.get(key)
                if en is None:
                    en = tm.get(jp)
                if en is None or en == '=':
                    continue
                bx = box(key[0])
                if bx:
                    en = prewrap(en, bx[0])
                    if en.count('\\n') >= bx[1]:
                        errs.append('@%d.%d: %d lines in a %d-line box (%d px)' % (*key, en.count('\\n') + 1, bx[1], bx[0]))
                        continue
                es = check_seg(jp, en)
                if es:
                    errs += ['@%d.%d: %s' % (*key, x) for x in es]
                    continue
                items[j] = ('seg', english_pieces(en))
                changed = True
                done += 1
            if changed:
                blocks[b][i] = msgfile.join_segments(items)
                touched.append(b * 1000 + i)
    errs += overflow_errors(blocks, orig, touched)
    return done, errs


def overflow_errors(blocks, orig, ids):
    """Entries (and entries inserting them) whose English expansion can overflow a fixed
    buffer (tools/overflow.py)."""
    lims = overflow.limits()
    get = lambda bs: (lambda m: bs[m // 1000][m % 1000] if m // 1000 < len(bs) and m % 1000 < len(bs[m // 1000]) else None)
    errs, memo_en, memo_jp = [], {}, {}
    check = set(i for i in ids if i in lims)
    if any(i < 1000 for i in ids):                     # a block-0 word changed: every limited entry
        check = set(lims)
    for mid in sorted(check):
        e = get(blocks)(mid)
        if e is None:
            continue
        n = overflow.measure(e, get(blocks), memo_en)
        j = overflow.measure(get(orig)(mid), get(orig), memo_jp)
        if n > max(lims[mid], j):
            errs.append('%d: up to %d bytes into a fixed buffer of %d (Japanese %d; tools/overflow.py)' % (mid, n, lims[mid] + 1, j))
    return errs


# ---------------------------------------------------------------- commands
def joined(entry):
    """Indexes n of the segments that run straight on into segment n+1 (a print code
    such as `02 01 bf` = style name between them, no variant end)."""
    out, n, items = set(), 0, msgfile.segments(entry)
    for j, (kind, v) in enumerate(items):
        if kind != 'seg':
            continue
        if (j + 2 < len(items) and items[j + 1][0] == 'code' and items[j + 2][0] == 'seg'
                and not items[j + 1][1].startswith(b'\x05')):
            out.add(n)
        n += 1
    return out


def show(b, a=0, z=None, todo=False):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    joins = {(b * 1000 + i, n) for i, e in enumerate(blocks[b]) for n in joined(e)}
    recs = parse_tl(tl_path(b))
    tls = {c: parse_tl(tl_path(c)) for c in range(msgfile.NBLOCKS)}
    tm = memory({c: jp if c == b else jp_block(blocks, c) for c in tls if tls[c]}, tls)
    calls = set()
    for (mid, n), (pieces, s) in sorted(jp.items()):
        i = mid % 1000
        if i < a or (z is not None and i > z):
            continue
        if todo and ((mid, n) in recs or s in tm):
            continue
        cont = '  [+ a printed value, then the next segment follows directly: use \\s for edge spaces]' if (mid, n) in joins else ''
        print('@%d%s\n%s%s\n' % (mid, '.%d' % n if n else '', text.readable(s).replace('\n', '\\n'), cont))
        calls.update(int(m.group(1)) for m in _TOK.finditer(s) if m.group(1))
    if calls:
        print('# calls:')
        for c in sorted(calls):
            cb, ci = divmod(c, 1000)
            segs = [s for (mid, n), (_, s) in sorted(jp_block(blocks, cb).items()) if mid == c] if cb < msgfile.NBLOCKS and ci < len(blocks[cb]) else []
            print('#   {C%d} = %s' % (c, ' | '.join(text.readable(s).replace('\n', '\\n') for s in segs)[:200]))


def apply(b, batch):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    new = parse_tl(batch)
    errs = []
    for k, en in new.items():
        if k not in jp:
            errs.append('@%d.%d: no such segment in block %d' % (*k, b))
        elif en != '=':
            errs += ['@%d.%d: %s' % (*k, x) for x in check_seg(jp[k][1], en)]
    if errs:
        sys.exit('\n'.join(errs) + '\n%d errors, nothing written' % len(errs))
    for k, en in new.items():
        if en != '=':
            for w in warn_seg(jp[k][1], en):
                print('warning @%d.%d: %s' % (*k, w))
    recs = parse_tl(tl_path(b))
    recs.update(new)
    write_tl(b, recs)
    print('block %d: %d records merged (%d in file)' % (b, len(new), len(recs)))


def check(only=None):
    blocks = load_blocks()
    tls = {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS) if only is None or b == only}
    done, errs = english_entries(blocks, tls)
    jps = {b: jp_block(blocks, b) for b in tls if tls[b]}
    for b, recs in tls.items():
        for k, en in sorted(recs.items()):
            if en != '=' and k in jps.get(b, {}) and not box(k[0]):
                for w in warn_seg(jps[b][k][1], en):
                    print('warning @%d.%d: %s' % (*k, w))
    data, offs = msgfile.build(blocks)
    errs += msgfile.check_sizes(data, offs, max(msgfile.BLOCK_MAX, msgfile.max_block(offs)), True)  # build relocates as needed
    print('\n'.join(errs))
    print('%d segments translated, %d errors' % (done, len(errs)))
    return not errs


def stats():
    blocks = load_blocks()
    tls = {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS)}
    jps = {b: jp_block(blocks, b) for b in range(msgfile.NBLOCKS)}
    tm = memory(jps, tls)
    tot = done = 0
    for b in range(msgfile.NBLOCKS):
        n = len(jps[b])
        d = sum(1 for k, (_, s) in jps[b].items() if k in tls[b] or s in tm)
        tot, done = tot + n, done + d
        if n:
            print('block %02d: %5d / %5d' % (b, d, n))
    print('total   : %5d / %5d (%.1f%%)' % (done, tot, 100.0 * done / max(tot, 1)))


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == 'show':
        nums = [int(x) for x in a[2:] if not x.startswith('--')]
        show(int(a[1]), *(nums + [0, None])[:2], todo='--todo' in a)
    elif a[0] == 'apply':
        apply(int(a[1]), a[2])
    elif a[0] == 'check':
        sys.exit(0 if check(int(a[1]) if len(a) > 1 else None) else 1)
    elif a[0] == 'stats':
        stats()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
