"""PS1 disc image (track 1, raw 2352-byte MODE2 sectors): read files, write a patched copy.

The game finds its files through libcd CdSearchFile (ISO directory at run time; no LBA
table in the program), so a file may move. A rebuilt file that still fits its sectors is
rewritten in place; a bigger one moves to the end of the data area (after SLPM_863.12),
which grows track 1: the 150-sector postgap follows it, and the ISO entry of the CD-DA
track (SILENCE.DA) and the volume size shift by the same amount. Track 2 (its own .bin)
is untouched. Every rewritten Form 1 sector gets a new EDC/ECC; sectors of files that do
not change are copied as they are.

  list <bin>              -> path, LBA, size, sector count, form of first sector
  extract <bin> <outdir>  -> dump every file (Form 2 / XA files raw 2336 B/sector)
"""
import os, struct, sys

RAW = 2352
SECTOR = 2048
PREGAP = 150                             # LBA 0 = MSF 00:02:00
POSTGAP = 150                            # empty Mode 2 sectors at the end of track 1
EXE_NAME = 'SLPM_863.12'
CDDA_NAME = 'SILENCE.DA'                 # ISO entry that points at track 2
SUB_DATA, SUB_EOF = b'\0\0\x08\0', b'\0\0\x89\0'
SYNC = b'\0' + b'\xff' * 10 + b'\0'

# ---- EDC / ECC (CD-ROM Mode 2 Form 1) ----
EDC_LUT = []
for _i in range(256):
    _e = _i
    for _ in range(8):
        _e = (_e >> 1) ^ (0xD8018001 if _e & 1 else 0)
    EDC_LUT.append(_e)
ECC_F = [((i << 1) ^ (0x11D if i & 0x80 else 0)) & 0xFF for i in range(256)]
ECC_B = [0] * 256
for _i in range(256):
    ECC_B[_i ^ ECC_F[_i]] = _i


def edc(data):
    e = 0
    for b in data:
        e = (e >> 8) ^ EDC_LUT[(e ^ b) & 0xFF]
    return e


def _ecc_block(src, major_count, minor_count, major_mult, minor_inc):
    size = major_count * minor_count
    out = bytearray(2 * major_count)
    f, bl = ECC_F, ECC_B
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            t = src[index]
            index += minor_inc
            if index >= size:
                index -= size
            a = f[a ^ t]
            b ^= t
        a = bl[f[a] ^ b]
        out[major] = a
        out[major + major_count] = a ^ b
    return out


def msf(lba):
    m, s = divmod(lba + PREGAP, 75 * 60)
    s, f = divmod(s, 75)
    bcd = lambda v: (v // 10) << 4 | v % 10
    return bytes((bcd(m), bcd(s), bcd(f)))


def form1_sector(lba, sub, data):
    """Full raw Mode 2 Form 1 sector: header, subheader x2, 2048 bytes, EDC, ECC."""
    assert len(data) == SECTOR and len(sub) == 4
    s = bytearray(SYNC + msf(lba) + b'\x02' + sub + sub + data + bytes(4 + 276))
    struct.pack_into('<I', s, 2072, edc(s[16:2072]))
    hdr = bytes(s[12:16])
    s[12:16] = bytes(4)                  # Mode 2: the header is not part of the ECC
    s[2076:2248] = _ecc_block(s[12:], 86, 24, 2, 86)
    s[2248:2352] = _ecc_block(s[12:], 52, 43, 86, 88)
    s[12:16] = hdr
    return bytes(s)


# ---- reading ----
class Record:
    def __init__(self, path, dir_lba, pos, lba, size, is_dir):
        self.path, self.dir_lba, self.pos, self.lba, self.size, self.is_dir = path, dir_lba, pos, lba, size, is_dir

    @property
    def sectors(self):
        return (self.size + SECTOR - 1) // SECTOR


class Disc:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.nsec = os.path.getsize(path) // RAW

    def raw(self, lba):
        self.f.seek(lba * RAW)
        return self.f.read(RAW)

    def sub(self, lba):
        """XA subheader (file, channel, submode, coding)."""
        return self.raw(lba)[16:20]

    def form2(self, lba):
        if lba >= self.nsec:
            return False                 # outside this file (CD-DA track)
        return bool(self.sub(lba)[2] & 0x20)

    def user(self, lba):
        return self.raw(lba)[24:24 + SECTOR]

    def read(self, lba, size):
        out = bytearray()
        for i in range((size + SECTOR - 1) // SECTOR):
            out += self.user(lba + i)
        return bytes(out[:size])

    def records(self):
        """{'DATA/MESSAGE.S6': Record, 'GRP': Record, ...} (directories included)."""
        pvd = self.user(16)
        assert pvd[1:6] == b'CD001', 'no PVD'
        lba, size = struct.unpack_from('<I4xI', pvd, 156 + 2)
        out = {}
        self._dir(lba, size, '', out)
        return out

    def _dir(self, lba, size, prefix, out):
        data = self.read(lba, size)
        pos = 0
        while pos < len(data):
            ln = data[pos]
            if ln == 0:
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            elba, esize = struct.unpack_from('<I4xI', data, pos + 2)
            flags, nl = data[pos + 25], data[pos + 32]
            name = data[pos + 33:pos + 33 + nl]
            if name not in (b'\0', b'\1'):
                name = name.decode('ascii').split(';')[0]
                rec = Record(prefix + name, lba + pos // SECTOR, pos % SECTOR, elba, esize, bool(flags & 2))
                out[rec.path] = rec
                if rec.is_dir:
                    self._dir(elba, esize, rec.path + '/', out)
            pos += ln

    def walk(self):
        for r in self.records().values():
            if not r.is_dir:
                yield r.path, r.lba, r.size

    def file(self, name, recs=None):
        r = (recs or self.records())[name]
        return self.read(r.lba, r.size)


def read_file(path, name):
    return Disc(path).file(name)


# ---- writing ----
def _file_sectors(lba, data, n):
    """n raw sectors holding data (zero padded); subheader EOF on the last data sector,
    like the clean disc (unused tail sectors of a shrunk file keep the data submode)."""
    last = max(1, (len(data) + SECTOR - 1) // SECTOR) - 1
    out = []
    for i in range(n):
        chunk = data[i * SECTOR:(i + 1) * SECTOR].ljust(SECTOR, b'\0')
        out.append(form1_sector(lba + i, SUB_EOF if i == last else SUB_DATA, chunk))
    return out


def write_image(clean, out, files):
    """Copy track 1 of the clean disc to out with files ({iso path: bytes}) replaced.
    Returns the list of files that moved."""
    disc = Disc(clean)
    recs = disc.records()
    for name in files:
        assert name in recs and not recs[name].is_dir, name
        assert not disc.form2(recs[name].lba), '%s: Form 2 files are not supported' % name
    exe = recs[EXE_NAME]
    data_end = exe.lba + exe.sectors     # first postgap sector
    assert data_end + POSTGAP == disc.nsec, 'unexpected track 1 layout'
    place = {}                           # name -> (lba, sectors to write)
    moved = []
    tail = data_end
    for name in sorted(files, key=lambda n: recs[n].lba):
        r, n = recs[name], (len(files[name]) + SECTOR - 1) // SECTOR
        if n <= r.sectors:
            place[name] = (r.lba, r.sectors)
        else:
            place[name] = (tail, n)
            tail += n
            moved.append(name)
    shift = tail - data_end
    # directory and PVD sectors to patch: lba -> bytearray(user data)
    patch = {}

    def user(lba):
        if lba not in patch:
            patch[lba] = bytearray(disc.user(lba))
        return patch[lba]

    def set_rec(r, lba, size):
        u = user(r.dir_lba)
        struct.pack_into('<I', u, r.pos + 2, lba)
        struct.pack_into('>I', u, r.pos + 6, lba)
        struct.pack_into('<I', u, r.pos + 10, size)
        struct.pack_into('>I', u, r.pos + 14, size)

    for name, (lba, _) in place.items():
        if (lba, len(files[name])) != (recs[name].lba, recs[name].size):
            set_rec(recs[name], lba, len(files[name]))
    if shift:
        cd = recs[CDDA_NAME]
        set_rec(cd, cd.lba + shift, cd.size)
        pvd = user(16)
        vol = struct.unpack_from('<I', pvd, 80)[0] + shift
        struct.pack_into('<I', pvd, 80, vol)
        struct.pack_into('>I', pvd, 84, vol)
    for lba in patch:
        assert lba < data_end and not disc.form2(lba)
    new = {}                             # lba -> raw sector
    for name, (lba, n) in place.items():
        if lba < data_end and disc.read(lba, len(files[name])) == files[name] and recs[name].size == len(files[name]):
            continue                     # unchanged
        for i, s in enumerate(_file_sectors(lba, files[name], n)):
            new[lba + i] = s
    for lba, u in patch.items():
        if bytes(u) != disc.user(lba):
            new[lba] = form1_sector(lba, disc.sub(lba), bytes(u))
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        for lba in range(data_end):
            f.write(new.get(lba) or disc.raw(lba))
        for lba in range(data_end, tail):
            f.write(new[lba])
        for i in range(POSTGAP):
            s = bytearray(disc.raw(data_end + i))
            s[12:15] = msf(tail + i)
            f.write(s)
    os.replace(tmp, out)
    return moved


def verify(out, files):
    """Every rebuilt file reads back from out with valid EDC/ECC."""
    disc = Disc(out)
    recs = disc.records()
    for name, data in files.items():
        r = recs[name]
        assert r.size == len(data) and disc.read(r.lba, r.size) == data, '%s: read back differs' % name
        for i in range(r.sectors):
            raw = disc.raw(r.lba + i)
            assert raw == form1_sector(r.lba + i, raw[16:20], raw[24:24 + SECTOR]), '%s: bad sector %d' % (name, i)


def write_cue(cue, track1, track2):
    with open(cue, 'w', newline='\r\n') as f:
        f.write('FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % os.path.basename(track1))
        f.write('FILE "%s" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n' % os.path.basename(track2))


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    d = Disc(path)
    if cmd == 'list':
        for name, lba, size in sorted(d.walk(), key=lambda e: e[1]):
            n = (size + SECTOR - 1) // SECTOR
            print('%-24s %6d %10d %6d %s' % (name, lba, size, n, 'F2' if d.form2(lba) else 'F1'))
    elif cmd == 'extract':
        out = sys.argv[3]
        for name, lba, size in d.walk():
            if name.endswith('.DA'):
                continue                 # CD-DA track entry, not data
            p = os.path.join(out, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            n = (size + SECTOR - 1) // SECTOR
            with open(p, 'wb') as f:
                if any(d.form2(lba + i) for i in range(min(16, n))):
                    for i in range(n):
                        f.write(d.raw(lba + i)[16:16 + 2336])
                else:
                    f.write(d.read(lba, size))


if __name__ == '__main__':
    main()
