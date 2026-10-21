"""Fixed buffers the English text reaches (audit: notes/overflow_audit.md).

Messages: msg_get (0x800b4dc4) expands into the message object's 512-byte buffer (cut
at 510 bytes, error flag). Most overlay callers hand the result to the string class
(0x80021b54: heap). The rest copy it into fixed buffers: sprintf (the expanded message
is the format) / strcpy / strcat into stack buffers. fmtlimit.msg_sites() finds those
copies and their sizes in the clean program/overlays; limits() = {message id: largest
expansion in bytes} = the smallest target - 1, plus the hand-traced STATIC ones.
tl.py checks the longest English expansion (measure) of every limited entry: it must
fit, or at least not exceed the Japanese's (the estimate is conservative).

Expansion model (measure): text bytes (half-width kana count 2: the interpreter turns
them into 2-byte hiragana) with sprintf conversions expanded as in fmtlimit.expand;
`01 43` inserts count the longest expansion of the inserted entry; `02 v 65/66`
variable prints count fmtlimit.NAME; the longest variant (variants end in 05 05 05) counts.
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import fmtlimit

# USER 0x801b4f18: strcpy(row record, msg_get(62077 + 7 * page + row)) for the 26 battlefield
# labels (戦場名＜都市→都市＞): records moved to 64-byte rows in free RAM (uipatch.fields; index
# computed: not found by msg_sites)
STATIC = {i: 63 for i in range(62077, 62103)}
# Historical Events list: each page copies its 8 titles (58000-58099) into char[37] records on
# the stack (0x801ffbc6.., stride 37, then a live object at 0x801ffcf0; found by a crash, copy
# site not traced)
STATIC.update({i: 36 for i in range(58000, 58100)})
# Short-scenario info (PROG 0x800942c0..): msg 64097/64109/64121/64133/64145 + scenario index
# (0-11, computed) strcpy'd / sprintf'd into a 128-byte stack buffer sp+0x20
STATIC.update({b + i: 127 for b in (64097, 64109, 64121, 64133, 64145) for i in range(12)})
# Info > Maps help lines (PROG 0x800bbfe4): msg 64084 + k strcpy'd into 32-byte slots obj+0x5ca4+32k
STATIC.update({i: 31 for i in range(64084, 64097)})
# Tactics battle list (INIT 0x801cf2b8): 64157 + 6 * ruler + k strcpy'd into 16-byte slots
# obj+0x234+16k (labels keep the pointer: a longer name runs into the next one)
STATIC.update({i: 15 for i in range(64157, 64181)})
MSG_BUF = 510
_LIMITS = None


def limits():
    """{message id: byte limit} (computed once from the clean disc)."""
    global _LIMITS
    if _LIMITS is None:
        import progstr
        imgs = progstr.images({t: bytearray(b) for t, b in progstr.clean_files().items() if t not in progstr.NO_STRINGS})
        out = dict(STATIC)
        for site, mid, fn, size in fmtlimit.msg_sites(imgs):
            if mid is not None and size is not None:
                out[mid] = min(out.get(mid, MSG_BUF), size - 1)
        _LIMITS = out
    return _LIMITS


_CODE = re.compile(rb'\x01\x43(..)|\x02.[\x65\x66]', re.S)


def measure(entry, get, memo=None, depth=0):
    """Longest expansion (bytes) of an entry; get(id) -> entry bytes or None. The
    variants of an entry end in 05 05 05 (return): the longest one counts."""
    memo = {} if memo is None else memo
    return max(_measure(p, get, memo, depth) for p in entry.split(b'\x05\x05\x05'))


def _measure(entry, get, memo, depth):
    import msgfile
    n = 0
    for kind, v in msgfile.split(entry):
        if kind == 'text':
            kana = sum(0xA6 <= c <= 0xDF for c in _sjis_single(v))
            n += fmtlimit.expand(v) + kana
            continue
        for m in _CODE.finditer(v):
            if m.group(1) is None:
                n += fmtlimit.NAME
                continue
            cid = m.group(1)[0] | m.group(1)[1] << 8
            if cid not in memo:
                memo[cid] = 0
                e = get(cid) if depth < 8 else None
                memo[cid] = measure(e, get, memo, depth + 1) if e else 0
            n += memo[cid]
    return n


def _sjis_single(b):
    """Single-byte characters of Shift-JIS text (lead/trail pairs skipped)."""
    i = 0
    while i < len(b):
        c = b[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            i += 2
            continue
        yield c
        i += 1
