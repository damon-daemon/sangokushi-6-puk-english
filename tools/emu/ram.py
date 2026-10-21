"""PS1 RAM of the running headless DuckStation (shared memory export, live, no pause),
or of a save state file.

  ram.py read ADDR LEN [--state f.sav]   hex dump
  ram.py dump out.bin  [--state f.sav]   whole 2 MB
  ram.py str ADDR      [--state f.sav]   Shift-JIS string at ADDR
"""
import glob, os, struct, sys


def shm():
    fs = sorted(glob.glob('/dev/shm/duckstation_*'), key=os.path.getmtime)
    if not fs:
        sys.exit('no /dev/shm/duckstation_* (is the headless emulator running?)')
    with open(fs[-1], 'rb') as f:
        return f.read(0x200000)


def state_ram(path):
    """RAM of a .sav: zstd data; RAM starts 0x43 bytes after the 'Bus' section marker."""
    d = open(path, 'rb').read()
    assert d[:4] == b'DUCC'
    v = struct.unpack_from('<13I', d, 0xA8)
    ctype, csize, usize, off = v[8], v[9], v[10], v[11]
    data = d[off:off + csize]
    if ctype == 2:
        from compression import zstd
        data = zstd.decompress(data)
    i = data.find(b'Bus')
    ram = data[i + 0x43:i + 0x43 + 0x200000]
    return ram


def main():
    a = sys.argv[1:]
    state = None
    if '--state' in a:
        k = a.index('--state'); state = a[k + 1]; del a[k:k + 2]
    ram = state_ram(state) if state else shm()
    if a[0] == 'dump':
        open(a[1], 'wb').write(ram)
    elif a[0] == 'read':
        addr, n = int(a[1], 0) & 0x1FFFFF, int(a[2], 0)
        for p in range(addr, addr + n, 16):
            print('%08x  %s' % (0x80000000 | p, ram[p:min(p + 16, addr + n)].hex(' ')))
    elif a[0] == 'str':
        addr = int(a[1], 0) & 0x1FFFFF
        print(ram[addr:ram.index(b'\0', addr)].decode('cp932', 'replace'))


if __name__ == '__main__':
    main()
