"""PS1 TIM chains: the graphics *.S6 files are plain runs of TIMs (4bpp/8bpp + CLUT), some
padded to 2 KB per image. Only pixel indexes are rewritten; sizes, palettes and padding stay.

  Texture(data, off): one TIM; pal = [(r, g, b, a)] of CLUT row `row` (a = 0 for the
    transparent colour 0x0000, else 0x80, like the PS2 tools' alpha scale), get/idx/put.
  chain(data) -> [offset of each TIM]
  python tools/tim.py FILE [out_dir]   list TIMs (and dump PNGs)
"""
import os, struct, sys

def parse(d, o):
    """-> dict(bpp, clut=(off, w, h), img=off, w=pixel width, h) or None."""
    if o + 8 > len(d):
        return None
    magic, flags = struct.unpack_from('<II', d, o)
    if magic != 0x10 or flags not in (8, 9):
        return None
    p = o + 8
    ln, cx, cy, cw, ch = struct.unpack_from('<IHHHH', d, p)
    if ln != 12 + cw * ch * 2:
        return None
    clut = (p + 12, cw, ch)
    p += ln
    ln, ix, iy, iw, ih = struct.unpack_from('<IHHHH', d, p)
    if ln != 12 + iw * ih * 2 or p + ln > len(d) or not iw or not ih:
        return None
    bpp = 4 if flags == 8 else 8
    return dict(bpp=bpp, clut=clut, img=p + 12, w=iw * (4 if bpp == 4 else 2), h=ih, end=p + ln)

def chain(d):
    """Offsets of the TIMs in d (zero or junk padding between them is skipped)."""
    out, o = [], 0
    while o < len(d):
        t = parse(d, o)
        if t:
            out.append(o)
            o = t['end']
            continue
        o = (o + 4) & ~3
        while o < len(d) and not parse(d, o):
            o += 4
    return out

def colour(c):
    r, g, b = (c & 31) * 255 // 31, (c >> 5 & 31) * 255 // 31, (c >> 10 & 31) * 255 // 31
    return (r, g, b, 0 if c == 0 else 0x80)

class Texture:
    def __init__(self, data, off, row=0):
        t = parse(data, off)
        assert t, hex(off)
        self.data, self.off, self.row = data, off, row
        self.bpp, self.w, self.h, self.img = t['bpp'], t['w'], t['h'], t['img']
        co, cw, ch = t['clut']
        assert row < ch
        self.raw_pal = struct.unpack_from('<%dH' % cw, data, co + 2 * cw * row)
        self.pal = [colour(c) for c in self.raw_pal]
        n = self.w * self.h // (2 if self.bpp == 4 else 1)
        raw = data[self.img:self.img + n]
        self.pix = list(raw) if self.bpp == 8 else [v for b in raw for v in (b & 15, b >> 4)]

    def get(self, x, y):
        return self.pal[self.pix[y * self.w + x]]

    def idx(self, x, y):
        return self.pix[y * self.w + x]

    def put(self, x, y, i):
        assert 0 <= i < len(self.pal)
        self.pix[y * self.w + x] = i

    def pixels(self):
        if self.bpp == 8:
            return bytes(self.pix)
        p = self.pix
        return bytes(p[k] | p[k + 1] << 4 for k in range(0, len(p), 2))

    def store(self, buf):
        """Write the pixels back into buf (a bytearray copy of the file)."""
        out = self.pixels()
        buf[self.img:self.img + len(out)] = out

    def image(self):
        from PIL import Image
        im = Image.new('RGBA', (self.w, self.h))
        im.putdata([c[:3] + (255 if c[3] else 0,) for c in (self.pal[i] for i in self.pix)])
        return im

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    for k, o in enumerate(chain(d)):
        t = parse(d, o)
        print('%3d %06x %dbpp %3dx%-3d clut %dx%d' % (k, o, t['bpp'], t['w'], t['h'], t['clut'][1], t['clut'][2]))
        if len(sys.argv) > 2:
            os.makedirs(sys.argv[2], exist_ok=True)
            Texture(d, o).image().save(os.path.join(sys.argv[2], '%s_%03d.png' % (os.path.basename(sys.argv[1]).split('.')[0], k)))
