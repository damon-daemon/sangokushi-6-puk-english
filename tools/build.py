"""Rebuild track 1 of the disc with the translation and produce a patch.

Output: build/sangokushi_6_puk_en_track1.bin, a copy of track 2 and a .cue next to
them (boots in an emulator), and build/sangokushi_6_puk_en.xdelta (clean track 1 ->
English track 1; track 2 is not changed).

usage: .venv/bin/python tools/build.py [clean_track1.bin] [out_track1.bin]
"""
import os, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(__file__))
import cells, datefix, gfx, iso, kanji, msgfile, namedict, nameentry, names, progstr, setup, strpool, tl, uipatch, vwf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'build', 'sangokushi_6_puk_en_track1.bin')
FIXED = (iso.EXE_NAME,)                  # must keep their place (the BIOS boots the program by name, but keep it)


def build_files(disc, recs):
    """{iso path: bytes} of every rebuilt file."""
    clean = progstr.disc_files(disc, recs)
    progs = {t: bytearray(b) for t, b in clean.items()}
    exe = progs['PROG']
    msg = disc.file(msgfile.NAME, recs)
    blocks = msgfile.parse(msg, exe)
    heap = kanji.apply(exe)
    wrapper = vwf.apply(exe, heap)
    try:
        d = namedict.build(names.pairs(disc, recs), *names.compose_sets(disc, recs))
    except ValueError as e:
        sys.exit('tl/names:\n%s' % e)
    layout = heap.put(bytes(namedict.LAYOUT_SIZE))       # table columns (cells -> namedict)
    size = namedict.apply(exe, heap, d, wrapper, progs, layout)
    print('names: %d dictionary entries, %d bytes of RAM' % (len(d.ent), size))
    print('name entry: Latin grid, %d bytes of RAM' % nameentry.apply(progs, heap))
    print('table cells: pixel columns, %d bytes of RAM' % cells.apply(exe, heap, layout))
    print('map dates: month names, %d bytes of RAM' % datefix.apply(progs, heap))
    print('ui patches: %d instructions' % uipatch.apply(progs))
    print('field labels: %d bytes of RAM' % uipatch.fields(progs, heap))
    print('string pools: %d bytes of RAM' % strpool.apply(exe, heap))
    try:
        n, moved = progstr.apply(progs, clean, heap)
    except ValueError as e:
        sys.exit('tl/progstr:\n%s' % e)
    print('program/overlay strings: %d in English (%d moved to free RAM)' % (n, moved))
    done, errs = tl.english_entries(blocks)
    if errs:
        sys.exit('tl/msg:\n' + '\n'.join(errs))
    print('messages: %d segments in English' % done)
    test = os.environ.get('S6_TEST_MSG')          # debug: 'id=text' replaces the text of one message
    if test:
        mid, t = test.split('=', 1)
        b, i = divmod(int(mid), 1000)
        parts = msgfile.split(blocks[b][i])
        k = next(n for n, (kind, _) in enumerate(parts) if kind == 'text')
        parts[k] = ('text', t.replace('|', '\n').encode('ascii'))
        blocks[b][i] = b''.join(v for _, v in parts)
    data, offs = msgfile.build(blocks, msgfile.raw_blocks(msg, exe))
    block_max, reloc = msgfile.BLOCK_MAX, False
    # a block > 23 KB, or block 0 > 13.4 KB (it then gets the whole arena), or S6_MSGBUF=1 (test)
    if (msgfile.max_block(offs) > block_max or msgfile.block0_used(data, offs) > msgfile.BLOCK0_MAX
            or os.environ.get('S6_MSGBUF')):
        # at the end of the free RAM, fixed size: the address stays put from build to build
        # (tools/emu/inject.py keeps the block loaded in a save state)
        block_max, reloc = max(msgfile.max_block(offs), msgfile.BUF_RESERVE), True
        buf = heap.alloc_top(block_max)
        msgfile.relocate(exe, buf)
        print('message block buffer moved to %08x (%d bytes)' % (buf, block_max))
    bad = msgfile.check_sizes(data, offs, block_max, reloc)
    if bad:
        sys.exit('MESSAGE.S6:\n' + '\n'.join(bad))
    msgfile.set_table(exe, offs)
    print('free RAM used: %d / %d bytes' % heap.used())
    out = {iso.EXE_NAME: bytes(exe), msgfile.NAME: data}
    for t in progstr.OVERLAYS:
        if progs[t] != clean[t]:
            out[progstr.ovl_name(t)] = bytes(progs[t])
    try:
        imgs = gfx.apply(lambda path: disc.file(path, recs))
    except ValueError as e:
        sys.exit('tl/gfx: %s' % e)
    print('images: %d files redrawn' % len(imgs))
    out.update(imgs)
    return out


def write(clean, out, files):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    moved = iso.write_image(clean, out, files)
    print('rebuilt %s%s' % (out, ' (moved: %s)' % ', '.join(moved) if moved else ''))
    if set(moved) & set(FIXED):
        sys.exit('%s moved on the disc' % ', '.join(set(moved) & set(FIXED)))
    iso.verify(out, files)
    track2 = out.replace('_track1.bin', '_track2.bin')
    if not os.path.exists(track2) or os.path.getsize(track2) != os.path.getsize(setup.CLEAN_TRACK2):
        shutil.copyfile(setup.CLEAN_TRACK2, track2)
    iso.write_cue(out.replace('_track1.bin', '.cue'), out, track2)
    patch = os.path.join(os.path.dirname(out), 'sangokushi_6_puk_en.xdelta')
    try:
        subprocess.run(['xdelta3', '-e', '-f', '-9', '-S', 'djw', '-B', str(1 << 28), '-s', clean, out, patch], check=True)
        print('patch %s (%d bytes)' % (patch, os.path.getsize(patch)))
    except FileNotFoundError:
        print('xdelta3 not installed; skipped patch')
    return moved


def main():
    clean = sys.argv[1] if len(sys.argv) > 1 else setup.CLEAN
    out = sys.argv[2] if len(sys.argv) > 2 else OUT
    disc = iso.Disc(clean)
    files = build_files(disc, disc.records())
    write(clean, out, files)


if __name__ == '__main__':
    main()
