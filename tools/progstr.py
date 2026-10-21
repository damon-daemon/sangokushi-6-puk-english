"""Strings of the program and the overlays (menu labels, help lines, system messages ...).

tl/progstr/*.txt: "key<TAB>English" lines, key = <IMAGE>:<vaddr hex> with IMAGE = PROG
(SLPM_863.12) or an overlay name (USER, INIT, ... = BIN/USER.BIN ...); \\n = newline;
ASCII text ({fw} prefix = written full-width); English "-" = keep the Japanese (a literal minus sign alone: "\\-") (debug output never shown); '#' starts a
comment line. A string without English takes the English of another string with the
same Japanese (translation memory).

English that fits the string's slot (its bytes plus the compiler's zero padding to the
next 4-byte boundary) is written in place. Longer English goes into the free RAM of the
program (kanji.Heap: resident, so overlay strings can go there too) and every reference
is repointed:
  - 32-bit data words equal to the address (pointer tables) in the same image (and,
    for a program string, in the overlays),
  - lui/addiu (or load/store) pairs in the code that build the address, when that lui
    has no other user (else the pair cannot be changed alone: shorten the English).
Blockers (the English must fit the slot): an overlay address referenced from the
program (every overlay loads at OVL_BASE: ambiguous), gp-relative references, a lui
with other users. A string with no reference found must fit its slot. printf
conversions must match.

  progstr.py export            -> script/progstr.json (every candidate, with flags)
  progstr.py show [KEY KEY] [--todo]   strings to translate between two keys
  progstr.py stats | check
"""
import collections, json, os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import fmtlimit, iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'progstr')
SCRIPT = os.path.join(ROOT, 'script', 'progstr.json')
FMT = re.compile(rb'%[-+ #0]*\d*(?:\.\d+)?[hl]?[diouxXcsfeEgGp%]')


def convs(b):
    """printf conversions; a %s or %d may change its pad width (column layout: fmtlimit measures it)"""
    return [re.sub(rb'\d+([sd])$', rb'\1', c) for c in FMT.findall(b)]
PROG_BASE, PROG_FOFF = 0x80010000, 0x800
OVL_BASE = 0x80199000                  # every overlay (load table at 0x800e485c)
OVL_END = 0x801E4A60                   # end of the largest overlay (REVENT)
OVERLAYS = ('COMP', 'END', 'HEX', 'IKKI', 'INIT', 'MC', 'MOVIE', 'REVCOL', 'REVENT', 'SSLGREV', 'USER')
GP = 0x8011da2c
RAM_LO, RAM_HI = 0x80010000, 0x80200000
# program areas that are not strings: 8x8 + half-width fonts and code tables (vwf.py), kanji font +
# codes (kanji.py). Between them (0x800f39dc..0x800f6668) are real strings (options screen, items, quiz).
PROG_SKIP = [(0x800f2e68, 0x800f39dc), (0x800f6668, 0x8010c838)]
KEY = re.compile(r'(PROG|%s):([0-9a-f]{8})' % '|'.join(OVERLAYS))
JR_RA = 0x03e00008
# MOVIE.BIN: after its small code, the file is filled with leftovers from the developers'
# machine (source text, stale data): no strings of the game
NO_STRINGS = ('MOVIE',)
# IKKI.BIN and MC.BIN: the real image (code + data) is followed by stale copies of older builds
# (string tables, code, C source); the overlay's bss lies over them. File offset where they start:
# the survey and the reference scan stop there (the code end is the last jr ra before it).
STALE = {'IKKI': 0x1e000, 'MC': 0x9b00}
# pointer tables in the program that belong to one overlay (the MC.BIN memory-card messages are
# reached through a table in the program's data): words there are references of that overlay
OVL_TABLES = {'MC': [(0x80118f38, 0x80118fb8)]}
# formats whose arguments are no longer printed (sprintf ignores extra arguments): the month of
# the map dates, printed with the year by tools/datefix.py
DROP_ARGS = {'PROG:801172e8', 'PROG:80118348'}


def ovl_name(tag):
    return 'BIN/%s.BIN' % tag


class Image:
    """One loaded file: data[foff + (va - base)] for va in [base, end)."""

    def __init__(self, tag, data, base, foff):
        self.tag, self.data, self.base, self.foff = tag, data, base, foff
        self.end = base + min(len(data) - foff, STALE.get(tag, len(data)))
        n = (self.end - base) // 4
        ws = struct.unpack_from('<%dI' % n, data, foff)
        last = max(i for i, w in enumerate(ws) if w == JR_RA)
        self.code_end = base + 4 * last + 8          # code: [base, code_end); data after it
        self.skip = PROG_SKIP if tag == 'PROG' else []

    def f(self, va):
        assert self.base <= va < self.end, '%s %x' % (self.tag, va)
        return va - self.base + self.foff

    def w(self, va):
        return struct.unpack_from('<I', self.data, self.f(va))[0]

    def put(self, va, b):
        o = self.f(va)
        self.data[o:o + len(b)] = b


def images(files):
    """[Image] over {tag: bytearray} (PROG and the overlays)."""
    out = []
    for tag, b in files.items():
        out.append(Image(tag, b, PROG_BASE, PROG_FOFF) if tag == 'PROG' else Image(tag, b, OVL_BASE, 0))
    return out


def _s16(x):
    return x - 0x10000 if x & 0x8000 else x


def _writes(w):
    """Register written by instruction w (None if none / unknown)."""
    op = w >> 26
    if op in (0, 0x10, 0x12):
        return (w >> 11) & 31 if op == 0 else None
    if op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F) or 0x20 <= op <= 0x26:
        return (w >> 16) & 31
    if op == 0x03:
        return 31
    return None


def _is_jump(w):
    op = w >> 26
    return op in (2, 3) or (op == 0 and (w & 0x3F) in (8, 9))


def code_pairs(img):
    """({target: [(lui va, user va)]}, {lui va: number of users}, {target: [gp user va]})
    for the image's code. A lui register is followed until it is overwritten or a jump's
    delay slot ends; any other read of it counts as a user too (so such a lui is never
    changed)."""
    pairs, users, gps = collections.defaultdict(list), collections.Counter(), collections.defaultdict(list)
    lui, end_after = {}, None
    for va in range(img.base, img.code_end, 4):
        w = img.w(va)
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if rs == 28 and (op == 0x09 or 0x20 <= op <= 0x3F):
            gps[(GP + _s16(imm)) & 0xFFFFFFFF].append(va)
        if op == 0x0F:
            lui[rt] = (imm << 16, va)
        else:
            if rs in lui and (op in (0x09, 0x0D) or 0x20 <= op <= 0x3F):
                t = (lui[rs][0] + (imm if op == 0x0D else _s16(imm))) & 0xFFFFFFFF
                pairs[t].append((lui[rs][1], va))
                users[lui[rs][1]] += 1
                if 0x28 <= op <= 0x3F and rt in lui and rt != rs:     # stores the lui value
                    users[lui[rt][1]] += 2
            else:
                for r in {rs, rt}:
                    if r in lui:
                        users[lui[r][1]] += 2
            wr = _writes(w)
            if wr in lui:
                del lui[wr]
        if end_after is not None and va >= end_after:
            lui.clear()
            end_after = None
        if _is_jump(w):
            end_after = va + 4
    return pairs, users, gps


def data_words(img):
    """{value: [vaddr]} of the aligned words of the image's data pointing into RAM."""
    out = collections.defaultdict(list)
    for va in range(img.code_end, img.end - 3, 4):
        v = img.w(va)
        if RAM_LO <= v < RAM_HI:
            out[v].append(va)
    return out


class Refs:
    """Every reference to a string of each image, by image tag."""

    def __init__(self, imgs):
        self.imgs = {i.tag: i for i in imgs}
        self.code = {i.tag: code_pairs(i) for i in imgs}
        self.words = {i.tag: data_words(i) for i in imgs}

    def of(self, tag, va):
        """(code pairs [(img tag, lui, user)], data words [(img tag, at)], blockers [text])."""
        code, data, block = [], [], []
        if tag == 'PROG':
            srcs = list(self.imgs)
        else:
            srcs = [tag]
            own = [at for at in self.words['PROG'].get(va, [])
                   if any(lo <= at < hi for lo, hi in OVL_TABLES.get(tag, []))]
            data += [('PROG', at) for at in own]
            if self.code['PROG'][0].get(va) or len(self.words['PROG'].get(va, [])) > len(own):
                block.append('referenced from the program (ambiguous overlay address)')
        for s in srcs:
            for l, u in self.code[s][0].get(va, []):
                code.append((s, l, u))
                if self.code[s][1][l] != 1:
                    block.append('lui %s:%x has other users' % (s, l))
            for at in self.words[s].get(va, []):
                data.append((s, at))
            if self.code[s][2].get(va):
                block.append('gp-relative reference %s:%x' % (s, self.code[s][2][va][0]))
        return code, data, block

    def gp_users(self, tag, va):
        """[(img tag, instruction va)] of the gp-relative references to va."""
        srcs = list(self.imgs) if tag == 'PROG' else [tag]
        return [(s, u) for s in srcs for u in self.code[s][2].get(va, [])]


def slot(img, va):
    """(Japanese bytes, room) of the string at va: room = bytes up to the next 4-byte
    boundary after its NUL (the compiler's padding), never past the next non-zero byte,
    minus one for the NUL."""
    b = img.data
    o = img.f(va)
    assert b[o - 1] == 0 or va % 4 == 0, '%s:%x is not the start of a string' % (img.tag, va)
    e = b.index(b'\0', o)
    k = e
    while k < len(b) and b[k] == 0:
        k += 1
    end = min(k, (va + (e - o) + 1 + 3) // 4 * 4 - va + o)
    return bytes(b[o:e]), end - o - 1


def parse_key(k):
    m = KEY.fullmatch(k)
    return m.group(1), int(m.group(2), 16)


def load_tl(d=TL):
    """{key: English} of every tl/progstr file ('-' entries included)."""
    out = {}
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith('.txt'):
            continue
        path = os.path.join(d, fn)
        for n, line in enumerate(open(path, encoding='utf-8').read().splitlines(), 1):
            if line.strip() and not line.startswith('#'):
                k, tab, v = line.partition('\t')
                if not (tab and KEY.fullmatch(k)):
                    raise ValueError('%s line %d: expected key<TAB>English: %r' % (fn, n, line))
                if k in out:
                    raise ValueError('%s line %d: %s given twice' % (fn, n, k))
                out[k] = v.replace('\\n', '\n')
    return out


def resolved(tl, rows):
    """{key: English} to write: explicit English, else translation memory by Japanese;
    '-' entries dropped. rows = export rows (key, jp, ui)."""
    jp = {r['key']: r['jp'] for r in rows}
    mem = {}
    for k, v in tl.items():
        if v != '-' and k in jp:
            mem.setdefault(jp[k], v)
    out = {k: v for k, v in tl.items() if v != '-'}
    for r in rows:
        if r['ui'] and r['key'] not in tl and r['jp'] in mem:
            out[r['key']] = mem[r['jp']]
    return out


def fmt_error(key, en, jp, sites):
    """Error text if the English format can overflow a sprintf target of known size
    (fmtlimit.py): its longest output must fit, or at least not exceed the Japanese's."""
    name = fmtlimit.SHORT_S.get(key, fmtlimit.NAME)
    n, j = fmtlimit.expand(en, name), fmtlimit.expand(jp, name)
    for site, size in sites:
        if size is not None and n > max(size - 1, j):
            return 'progstr %s: sprintf at %s writes up to %d bytes into a %d-byte buffer (Japanese %d)' % (key, site, n, size, j)
    return None


def fmt_unknown(tl):
    """[(key, en bytes, jp bytes, sites)] of formats longer than the Japanese whose target
    size is unknown (check in the emulator, or trace the buffer)."""
    rs = {r['key']: r for r in rows()}
    imgs = images({t: bytearray(b) for t, b in clean_files().items() if t not in NO_STRINGS})
    out = []
    for key, sites in sorted(fmtlimit.limits(imgs).items()):
        en = tl.get(key)
        if en and en != '-' and key in rs and any(s is None for _, s in sites):
            n, j = fmtlimit.expand(encode(en)), fmtlimit.expand(rs[key]['jp'].encode('cp932'))
            if n > j:
                out.append((key, n, j, [x for x, s in sites if s is None]))
    return out


FW = '{fw}'    # English prefix: write the text as full-width Shift-JIS (memory card titles: the BIOS shows no ASCII)


def encode(en):
    if en.startswith(FW):
        en.encode('ascii')
        parts = re.split(r'(%[-\d]*[sd])', en[len(FW):])
        return b''.join(t.encode('ascii') if i % 2 else
                        ''.join('\u3000' if c == ' ' else chr(ord(c) + 0xFEE0) for c in t).encode('cp932')
                        for i, t in enumerate(parts))
    return en.replace('\\-', '-').encode('ascii')


def apply(files, clean, heap, tl=None):
    """Write tl/progstr into files ({tag: bytearray}); clean = {tag: bytes}; longer
    strings go to heap (kanji.Heap over files['PROG']). Returns (strings, moved)."""
    cimgs = images({t: bytearray(b) for t, b in clean.items()})
    tl = resolved(load_tl() if tl is None else tl, survey(cimgs))
    if not tl:
        return 0, 0
    imgs = {i.tag: i for i in images(files)}
    refs = Refs(cimgs)
    lims = fmtlimit.limits([i for i in cimgs if i.tag not in NO_STRINGS])
    ctag = {i.tag: i for i in cimgs}
    errors, moved, pooled = [], 0, {}
    near, freed = [], []          # gp-only blocked strings; slots in the gp window left by moved strings
    for key, en in sorted(tl.items()):
        tag, va = parse_key(key)
        jp, room = slot(ctag[tag], va)
        try:
            b = encode(en)
        except UnicodeEncodeError:
            errors.append('progstr %s: not ASCII: %r' % (key, en))
            continue
        if convs(b) != convs(jp) and not (key in DROP_ARGS and not FMT.findall(b)):
            errors.append('progstr %s: conversions %s, Japanese has %s' % (key, FMT.findall(b), FMT.findall(jp)))
            continue
        err = fmt_error(key, b, jp, lims.get(key, []))
        if err:
            errors.append(err)
            continue
        if len(b) <= room:
            imgs[tag].put(va, b + bytes(room + 1 - len(b)))
            continue
        code, data, block = refs.of(tag, va)
        if not code and not data and not block:
            errors.append('progstr %s: %d bytes, room %d and no reference to repoint' % (key, len(b), room))
            continue
        if block and all(x.startswith('gp-relative') for x in block):
            near.append((key, b, code, data, refs.gp_users(tag, va)))
            continue
        if block:
            errors.append('progstr %s: %d bytes, room %d; %s' % (key, len(b), room, '; '.join(block)))
            continue
        if b not in pooled:
            pooled[b] = heap.put(b + b'\0', 1)
        _repoint(imgs, ctag, key, pooled[b], code, data)
        if tag == 'PROG' and GP - 0x8000 <= va and va + room + 1 <= GP + 0x8000:
            freed.append([va, room + 1])
        moved += 1
    # gp-relative references reach only GP +- 32 KB: those strings go into the slots
    # the moved strings left there (first fit, shared by equal English)
    near_pool, merged = {}, []
    for va, n in sorted(freed):                           # adjacent slots form one run
        if merged and merged[-1][0] + merged[-1][1] == va:
            merged[-1][1] += n
        else:
            merged.append([va, n])
    freed = merged
    for key, b, code, data, gps in near:
        if b not in near_pool:
            f = next((f for f in freed if f[1] >= len(b) + 1), None)
            if f is None:
                errors.append('progstr %s: %d bytes, gp-relative references and no free slot near gp left'
                              % (key, len(b)))
                continue
            imgs['PROG'].put(f[0], b + b'\0')
            near_pool[b] = f[0]
            f[0] += len(b) + 1
            f[1] -= len(b) + 1
        new = near_pool[b]
        _repoint(imgs, ctag, key, new, code, data)
        for s, u in gps:
            uw = ctag[s].w(u)
            assert imgs[s].w(u) == uw, 'progstr %s: gp reference at %s:%x already patched' % (key, s, u)
            imgs[s].put(u, struct.pack('<I', (uw & 0xFFFF0000) | ((new - GP) & 0xFFFF)))
        moved += 1
    if errors:
        raise ValueError('\n'.join(errors))
    return len(tl), moved


def _repoint(imgs, ctag, key, new, code, data):
    """Point the data words and lui/user code pairs of a string at new."""
    for s, at in data:
        imgs[s].put(at, struct.pack('<I', new))
    for s, l, u in code:
        lw, uw = ctag[s].w(l), ctag[s].w(u)
        assert (imgs[s].w(u) == uw), 'progstr %s: code at %s:%x already patched' % (key, s, u)
        hi = (new + 0x8000) >> 16 & 0xFFFF
        lo = (new - (hi << 16)) & 0xFFFF
        if uw >> 26 == 0x0D:                                  # ori: unsigned low half
            hi, lo = new >> 16, new & 0xFFFF
        imgs[s].put(l, struct.pack('<I', (lw & 0xFFFF0000) | hi))
        imgs[s].put(u, struct.pack('<I', (uw & 0xFFFF0000) | lo))


# ---------------------------------------------------------------- survey

JP = re.compile('[぀-ヿ一-鿿！-～　-〿｡-ﾟ]')
JP_FULL = re.compile('[぀-ヿ一-鿿！-～　-〿]')


def _is_text(b):
    i, jp = 0, 0
    while i < len(b):
        c = b[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            if i + 1 >= len(b) or not (0x40 <= b[i + 1] <= 0xFC and b[i + 1] != 0x7F):
                return False
            jp += 1
            i += 2
            continue
        if 0xA1 <= c <= 0xDF:
            jp += 1
        elif not (0x20 <= c < 0x7F or c in (0x0A, 0x1B, 0x09)):
            return False
        i += 1
    return jp > 0


# strings made of gaiji only (lead byte 0xF0, no Shift-JIS kanji: the scan cannot tell them
# from data): city names 鄴 Ye, 譙 Qiao, 氐 Di in the city table
GAIJI_STRINGS = {'PROG': (0x80116fd0, 0x80117014, 0x8011712c)}


def candidates(img):
    """[(vaddr, bytes)] of the Shift-JIS strings in the image's data (mostly Japanese)."""
    out, b = [], img.data
    for va in GAIJI_STRINGS.get(img.tag, ()):
        out.append((va, bytes(b[img.f(va):b.index(b'\0', img.f(va))])))
    lo = img.code_end
    for s_lo, s_hi in img.skip + [(img.end, img.end)]:
        if s_lo > lo:
            out += _scan(img, img.f(lo), img.f(s_lo - 1) + 1)
        lo = max(lo, s_hi)
    return sorted(out)


def _scan(img, o, hi):
    out, b = [], img.data
    while o < hi:
        if b[o] == 0:
            o += 1
            continue
        e = b.find(b'\0', o)
        if e < 0:
            break
        # a string starts after a NUL or, right after a pointer word, at a 4-aligned address
        starts = [o] if b[o - 1] == 0 else []
        starts += [p for p in range(o + 1, e) if (p - img.foff + img.base) % 4 == 0]
        for p in starts:
            s = bytes(b[p:e])
            if _is_text(s):
                t = s.decode('cp932', 'replace')
                fw = len(JP.findall(t))
                if '�' not in t and (fw >= 2 or JP_FULL.search(t)) and (fw * 2 >= len(t) * 0.5 or FMT.search(s)):
                    out.append((p - img.foff + img.base, s))
                break
        o = e + 1
    return out


_FMT_S = re.compile(r'%[-+ #0]*\d*[hl]?[dsxuc]')
# likely debug output (never shown): workers still see these, flagged
DEBUG = re.compile(r'Algo|－.*処理|処理$|\*\*\*|===|<<|>>|\$\$|->|Phase|Buffer|Error|[:：]%d|^\s')


def _junk(t):
    """One kanji + ASCII bytes: an instruction or a number that looks like text."""
    rest = _FMT_S.sub('', t)
    # labels like ' 金', '金:%d', '残  %3d' are text: only stray letters make it an instruction
    return (len(JP.findall(t)) == 1 and len(rest) > 1 and all(c.isascii() for c in rest if not JP.match(c))
            and any(c.isalpha() and c.isascii() for c in rest))


GRID = re.compile('あいうえお|アイウエオ')       # name-entry kana grids (names phase)


def _array(t, va, tag):
    """A string without a reference that looks like real text (an array element);
    lone kanji outside the program are data words."""
    return va % 4 == 0 and (len(t) > 1 or tag == 'PROG') and all(JP_FULL.match(c) or c in ' \n%d' for c in t)


def survey(imgs):
    """[row] of every candidate string: key, tag, va, room, code, data, block, jp (text),
    ui (to translate), debug (flag), kind (ui / noref / junk)."""
    refs = Refs(imgs)
    rows = []
    for img in imgs:
        if img.tag in NO_STRINGS:
            continue
        for va, s in candidates(img):
            jp, room = slot(img, va)
            code, data, block = refs.of(img.tag, va)
            t = jp.decode('cp932')
            gp = any('gp-relative' in x for x in block)
            if code or data or gp:
                kind = 'junk' if _junk(t) else 'ui'
            elif _array(t, va, img.tag):
                kind = 'grid' if GRID.search(t) else 'array'
                block = block + ['no reference (element of a fixed-size array?)']
            else:
                kind = 'noref'
            rows.append({'key': '%s:%08x' % (img.tag, va), 'tag': img.tag, 'va': va, 'room': room,
                         'code': len(code), 'data': len(data), 'block': block, 'jp': t,
                         'ui': kind in ('ui', 'array'), 'debug': kind == 'ui' and bool(DEBUG.search(t) or t.endswith('\n')),
                         'kind': kind})
    return rows


def disc_files(disc, recs=None):
    """{tag: bytes} of the program and the overlays from the disc."""
    recs = recs or disc.records()
    out = {'PROG': disc.file(iso.EXE_NAME, recs)}
    for t in OVERLAYS:
        out[t] = disc.file(ovl_name(t), recs)
    return out


def clean_files():
    import setup
    return disc_files(iso.Disc(setup.CLEAN))


def export():
    rows = survey(images({t: bytearray(b) for t, b in clean_files().items()}))
    os.makedirs(os.path.dirname(SCRIPT), exist_ok=True)
    json.dump(rows, open(SCRIPT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return rows


def rows():
    if not os.path.exists(SCRIPT):
        export()
    return json.load(open(SCRIPT, encoding='utf-8'))


def _order(key):
    tag, va = parse_key(key)
    return (('PROG',) + OVERLAYS).index(tag), va


def show(lo=None, hi=None, todo=False):
    """Print the strings to translate in [lo, hi] (keys), address order:
    key  room  M (movable if the English is longer) / F (fixed: must fit room)  D (debug?)  Japanese  => English"""
    tl = load_tl()
    rs = [r for r in rows() if r['ui']]
    res = resolved(tl, rs)
    for r in rs:
        if lo and _order(r['key']) < _order(lo) or hi and _order(r['key']) > _order(hi):
            continue
        en = tl.get(r['key'])
        if todo and en is not None:
            continue
        mark = '' if en is not None else '[tm] ' + res[r['key']] if r['key'] in res else ''
        print('%s\t%d\t%s%s\t%s%s' % (r['key'], r['room'], 'F' if r['block'] else 'M', ' D' if r['debug'] else '',
                                    r['jp'].replace('\n', '\\n'), '\t=> ' + (en.replace('\n', '\\n') if en is not None else mark) if en is not None or mark else ''))


def stats():
    tl = load_tl()
    rs = rows()
    ui = [r for r in rs if r['ui']]
    res = resolved(tl, ui)
    done = sum(r['key'] in tl or r['key'] in res for r in ui)
    print('candidates %d (%s)' % (len(rs), ', '.join('%s %d' % kv for kv in sorted(collections.Counter(r['kind'] for r in rs).items()))))
    for tag in ('PROG',) + OVERLAYS:
        t = [r for r in ui if r['tag'] == tag]
        print('  %-8s ui %4d  fixed %4d  chars %5d' % (tag, len(t), sum(bool(r['block']) for r in t), sum(len(r['jp']) for r in t)))
    print('ui strings %d: done %d (%d explicit, %d kept Japanese "-"), todo %d; debug-flagged %d'
          % (len(ui), done, sum(r['key'] in tl and tl[r['key']] != '-' for r in ui), sum(tl.get(r['key']) == '-' for r in ui),
             len(ui) - done, sum(r['debug'] for r in ui)))
    keys = {r['key'] for r in ui}
    stray = [k for k in tl if k not in keys]
    if stray:
        print('keys not in the ui list:', ' '.join(stray[:20]))


_W = None


def px(t):
    """Pixel width of the widest line: English with the VWF font (tools/pixfont.py),
    Japanese 16 per full-width and 8 per half-width character."""
    global _W
    if t.startswith(FW):
        return 16 * len(t) - 16 * len(FW)
    if _W is None:
        import pixfont
        _W = {c: g[1] for c, g in pixfont.glyphs().items()}
    return max(sum(_W.get(c, 8) if c.isascii() else 8 if '\uff61' <= c <= '\uff9f' else 16 for c in line)
               for line in t.split('\n'))


def wide(tl=None):
    """[(key, en px, jp px, en, jp)] of English wider than its Japanese (labels may be cut)."""
    tl = load_tl() if tl is None else tl
    out = []
    for r in rows():
        en = tl.get(r['key'])
        if r['ui'] and en and en != '-' and px(en) > px(r['jp']):
            out.append((r['key'], px(en), px(r['jp']), en, r['jp']))
    return out


def check():
    """Dry-run apply on the clean files; prints errors. True if clean."""
    import kanji
    clean = clean_files()
    files = {t: bytearray(b) for t, b in clean.items()}
    heap = kanji.apply(files['PROG'])
    try:
        n, moved = apply(files, clean, heap)
    except ValueError as e:
        print(e)
        return False
    print('progstr: %d strings OK (%d moved to free RAM)' % (n, moved))
    u = fmt_unknown(resolved(load_tl(), [r for r in rows() if r['ui']]))
    if u:
        print('%d formats longer than the Japanese with an unknown sprintf target (check):' % len(u))
        for k, n, j, sites in u:
            print('  %s %d > %d bytes  sprintf at %s' % (k, n, j, ' '.join(sites)))
    w = wide()
    if w:
        print('%d strings wider than the Japanese (may be cut or wrapped; shorten labels where possible):' % len(w))
        for k, a, b, en, jp in w:
            print('  %s %d > %d px  %s  (%s)' % (k, a, b, en.replace('\n', '\\n'), jp.replace('\n', '\\n')))
    return True


if __name__ == '__main__':
    a = sys.argv[1:]
    if a == ['export']:
        print(len(export()), 'strings ->', SCRIPT)
    elif a and a[0] == 'show':
        todo = '--todo' in a
        a = [x for x in a[1:] if x != '--todo']
        show(a[0] if a else None, a[1] if len(a) > 1 else None, todo)
    elif a == ['stats']:
        stats()
    elif a == ['check']:
        sys.exit(0 if check() else 1)
    else:
        print(__doc__)
