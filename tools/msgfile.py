"""DATA/MESSAGE.S6: blocks of message entries, and the block table in the program.

File = blocks, each starting on a 2 KB boundary (zero padding). The program holds the
byte offset of every block (u32, 2 KB aligned) plus the file size at TABLE; the loader
counts entries while they are 2 KB aligned (notes/engine.md). Block = u16 n, n u16
offsets (from the block start), then the entries. Message id = block * 1000 + index;
block 0 (grammar word lists) stays resident, other blocks are loaded on demand into one
buffer of BLOCK_MAX bytes (the whole padded block is read).

Entry = bytecode. Text runs are `01 22 <bytes>` up to the next byte 00-06; `01 43 u16`
(insert a block 0 entry) and `01 4A u16` (message reference) take a 2-byte operand that
must not be mistaken for text. split() cuts an entry into ('code', bytes) and
('text', bytes) parts; b''.join of the parts is the entry again.
"""
import os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm

NAME = 'DATA/MESSAGE.S6'
TABLE = 0x80110058                 # va of the u32 block table in SLPM_863.12
EXE_BASE = 0x80010000 - 0x800      # va - file offset
NBLOCKS = 65
BLOCK0_MAX = 0x35A0                # block 0 buffer (0x80170f94)
BUF_RESERVE = 0x8000               # relocated block buffer (build.py), unless a block is larger
BLOCK_MAX = 0x5A60                 # current-block buffer (0x80174534 .. 0x80179f94)
BLOCK0_RELOC = 0x9000              # block 0 limit once relocate() moved the current-block buffer
                                   # out: the whole arena 0x80170f94 .. 0x80179f94 (padded block read)
ALIGN = 2048


def table(exe):
    off = TABLE - EXE_BASE
    t = list(struct.unpack_from('<%dI' % (NBLOCKS + 1), exe, off))
    assert struct.unpack_from('<I', exe, off + 4 * (NBLOCKS + 1))[0] % ALIGN, 'table end marker'
    return t


def set_table(exe, offsets):
    assert len(offsets) == NBLOCKS + 1 and all(o % ALIGN == 0 for o in offsets)
    struct.pack_into('<%dI' % (NBLOCKS + 1), exe, TABLE - EXE_BASE, *offsets)


def parse_block(data):
    """Block bytes (with padding) -> list of entry bytes. The last entry ends at the
    first zero byte after its start (entries never contain 00 as their final byte...)."""
    n = struct.unpack_from('<H', data, 0)[0]
    offs = struct.unpack_from('<%dH' % n, data, 2)
    ents = []
    for i in range(n):
        a = offs[i]
        if i + 1 < n:
            e = offs[i + 1]
        else:
            e = len(data.rstrip(b'\0'))
        ents.append(data[a:e])
    return ents


def build_block(ents):
    """Entries -> block bytes without padding (header layout as on the disc)."""
    n = len(ents)
    out = bytearray(struct.pack('<H', n) + bytes(2 * n))
    for i, e in enumerate(ents):
        if len(out) > 0xFFFF:
            raise ValueError('block offset %d > 64 KB' % len(out))
        struct.pack_into('<H', out, 2 + 2 * i, len(out))
        out += e
    return bytes(out)


def parse(data, exe):
    t = table(exe)
    assert t[-1] == len(data), 'table does not match MESSAGE.S6'
    return [parse_block(data[t[b]:t[b + 1]]) for b in range(NBLOCKS)]


def build(blocks, tail=None):
    """-> (file bytes, offset table). tail: {block: bytes} padding of the clean file
    (the clean disc keeps junk after the last entry of some blocks; kept so an
    unchanged block is byte-identical)."""
    out, offs = bytearray(), []
    for b, ents in enumerate(blocks):
        offs.append(len(out))
        blk = build_block(ents)
        if tail and b in tail and tail[b][0] == blk:
            blk = tail[b][1]
        out += blk
        out += bytes(-len(out) % ALIGN)
    offs.append(len(out))
    return bytes(out), offs


def raw_blocks(data, exe):
    """{block: (rebuilt-without-padding bytes, clean padded bytes)} for byte-identical rebuilds."""
    t = table(exe)
    out = {}
    for b in range(NBLOCKS):
        clean = data[t[b]:t[b + 1]]
        out[b] = (build_block(parse_block(clean)), clean)
    return out


def max_block(offs):
    return max(offs[b + 1] - offs[b] for b in range(1, NBLOCKS))


def block0_used(data, offs):
    return len(data[offs[0]:offs[1]].rstrip(b'\0'))


def check_sizes(data, offs, block_max=BLOCK_MAX, relocated=False):
    """Errors for blocks that do not fit the game's buffers."""
    bad = []
    for b in range(NBLOCKS):
        size = offs[b + 1] - offs[b]
        limit = (BLOCK0_RELOC if relocated else BLOCK0_MAX) if b == 0 else block_max
        used = len(data[offs[b]:offs[b + 1]].rstrip(b'\0'))
        if relocated and b == 0:
            used = size                    # the padded block is read whole
        if (b == 0 and used > limit) or (b and size > limit):
            bad.append('block %d: %d bytes > %d' % (b, size if b else used, limit))
    return bad


ARENA = 0x80170f94                 # block 0 buffer; reader positions (m+0x40c) are relative to it
MEMCPY = 0x80066fb4
# (va, original word) of the instructions relocate() rewrites (notes/engine.md "Message loader")
RELOC_SITES = {
    0x800b3d40: 0x241235a0, 0x800b3d48: 0x00009021,                          # msg_load_block: s2
    0x800b3d90: 0x3c028017, 0x800b3d94: 0x24420f94, 0x800b3d98: 0x00523021,  # read dst
    0x800b3e2c: 0x26020001, 0x800b3e30: 0x00021840, 0x800b3e34: 0x3c028017,  # msg_entry block 0
    0x800b3e38: 0x24420f94, 0x800b3e3c: 0x00432821,
    0x800b3e90: 0x3c058017, 0x800b3e9c: 0x24a54534,                          # block header
    0x800b3ed8: 0x244335a0, 0x800b3efc: 0x244235a0,                          # entry offset
}


def relocate(exe, buf):
    """Move the current-block buffer (BLOCK_MAX bytes after block 0) to buf (free RAM).
    Reader positions stay relative to ARENA (u32, wrap-around): block positions get
    buf - ARENA added instead of 0x35A0, and the high half of m+0x40c (the u16 offset is
    memcpy'd into its low half) is cleared first on both paths."""
    def w(va):
        return struct.unpack_from('<I', exe, va - EXE_BASE)[0]

    def put(va, lines):
        code = asm.assemble(va, lines)
        exe[va - EXE_BASE:va - EXE_BASE + len(code)] = code

    for va, old in RELOC_SITES.items():
        assert w(va) == old, 'msg loader %08x' % va
    hi, lo = asm.hi, asm.lo
    delta = (buf - ARENA) & 0xFFFFFFFF
    put(0x800b3d40, [('lui', 's2', hi(buf))])
    put(0x800b3d48, [('lui', 's2', hi(ARENA))])
    put(0x800b3d90, [('beqz', 's0', 0x800b3d9c), ('addiu', 'a2', 's2', lo(ARENA)), ('addiu', 'a2', 's2', lo(buf))])
    put(0x800b3e2c, [('sll', 'v1', 's0', 1), ('lui', 'v0', hi(ARENA)), ('addu', 'a1', 'v0', 'v1'),
                     ('addiu', 'a1', 'a1', lo(ARENA) + 2), ('sw', 'zero', 0x40c, 's1')])
    put(0x800b3e90, [('lui', 'a1', hi(buf))])
    put(0x800b3e9c, [('addiu', 'a1', 'a1', lo(buf))])
    put(0x800b3ed0, [
        ('addiu', 'v0', 's0', 1),
        ('sll', 'v0', 'v0', 1),
        ('lui', 'v1', hi(buf)),
        ('addiu', 'v1', 'v1', lo(buf)),
        ('addu', 'a1', 'v1', 'v0'),
        ('sw', 'zero', 0x40c, 's1'),
        ('addiu', 'a0', 's1', 0x40c),
        ('jal', MEMCPY),
        ('addiu', 'a2', 'zero', 2),
        ('lw', 'v0', 0x40c, 's1'),
        ('lui', 'v1', hi(delta)),
        ('addiu', 'v1', 'v1', lo(delta)),
        ('addu', 'v0', 'v0', 'v1'),
        ('sw', 'v0', 0x40c, 's1'),
        ('lhu', 'v0', 0x26, 'sp'),
    ])
    assert w(0x800b3f0c) == 0x8fbf001c


def split(entry):
    parts, i, n = [], 0, len(entry)
    code = bytearray()
    while i < n:
        c = entry[i]
        if c == 0x01 and i + 1 < n:
            op = entry[i + 1]
            if op == 0x22:
                j = i + 2
                while j < n and entry[j] > 0x06:
                    j += 1
                code += entry[i:i + 2]
                parts.append(('code', bytes(code)))
                parts.append(('text', entry[i + 2:j]))
                code = bytearray()
                i = j
                continue
            if op in (0x43, 0x4A):
                code += entry[i:i + 4]
                i += 4
                continue
        code.append(c)
        i += 1
    if code:
        parts.append(('code', bytes(code)))
    return parts


def texts(entry):
    return [p for k, p in split(entry) if k == 'text']


# ---------------------------------------------------------------- segments
# A segment is a chain of text runs joined by inline codes only: a bare `01 22` (the
# Japanese starts a new run for every line), `01 43 a b` (insert a block-0 word) and
# `02 v 65` / `02 v 66` (print variable v: a name). Everything else (05 ends, 04..06
# conditions, other commands) is structure and stays byte for byte. A segment is the
# unit translated as a whole: English text and the inline tokens in any order. Tokens
# at the start of the code after a segment belong to it (droppable word endings).
TOKEN = re.compile(rb'\x01\x43..|\x02.[\x65\x66]', re.S)
_LEAD = re.compile(rb'(?:\x01\x43..|\x02.[\x65\x66])*', re.S)
_TAIL = re.compile(rb'(?:\x01\x43..|\x02.[\x65\x66])*\x01\x22', re.S)


def _tail(code):
    """Start of the inline tail of a code part (tokens + the final 01 22), or None."""
    if not code.endswith(b'\x01\x22'):
        return None
    for i in range(len(code) - 1):
        if _TAIL.fullmatch(code, i):
            return i
    return None


def segments(entry):
    """-> list of ('code', bytes) and ('seg', [('text', bytes) | ('tok', bytes)])."""
    out, cur = [], None
    for kind, v in split(entry):
        if kind == 'text':
            if cur is None:
                cur = []
                out.append(('seg', cur))
            cur.append(('text', v))
            continue
        lead = _LEAD.match(v).end() if cur is not None else 0
        if lead and lead < len(v) and _tail(v) != 0:
            # tokens right after a segment (word endings before a variant end) join it
            cur += [('tok', m.group()) for m in TOKEN.finditer(v, 0, lead)]
            v = v[lead:]
        i = _tail(v)
        if i is None:
            out.append(('code', v))
            cur = None
            continue
        if i > 0 or cur is None:
            if i > 0:
                out.append(('code', v[:i]))
            cur = []
            out.append(('seg', cur))
        cur += [('tok', m.group()) for m in TOKEN.finditer(v, i, len(v) - 2)]
    return out


def seg_bytes(pieces):
    """Segment pieces -> bytes (every text piece starts a run with 01 22)."""
    out = bytearray()
    for kind, v in pieces:
        out += v if kind == 'tok' else b'\x01\x22' + v
    return bytes(out)


def join_segments(items):
    return b''.join(v if kind == 'code' else seg_bytes(v) for kind, v in items)
