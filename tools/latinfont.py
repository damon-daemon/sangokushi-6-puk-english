"""Original proportional Latin font for the game's half-width text, drawn as strokes.

Glyphs are polylines in font units: x from 0 (left ink edge), y up from the
baseline, cap height 10, x-height 7, descenders to -3. render() rasterises them
with antialiasing for one of the game's font sizes, giving 4-bit coverage
bitmaps (the game's glyph format) and an advance width per character.

  SIZES[name] = (cell_w, cell_h, baseline_row, cap_px, stroke_px, max_level)
"""
import math
from PIL import Image, ImageDraw

SIZES = {
    'msg': (11, 22, 18.0, 13.8, 1.3, 14),       # NB10 36 half-width sheet (11x22 cells)
    'label': (16, 26, 20.5, 16.0, 2.0, 15),     # labels drawn into textures
}

def arc(cx, cy, rx, ry, a0, a1, n=None):
    """Points on an ellipse from angle a0 to a1 (degrees, counter-clockwise from +x)."""
    n = n or max(4, int(abs(a1 - a0) / 10))
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]

def O(cx, cy, rx, ry):
    return arc(cx, cy, rx, ry, 0, 360, 36)

X, C, D = 7, 10, -3          # x-height, cap height, descender

# Lowercase bowls are ellipses of half-width 2.4 centred at x-height/2.
B = 3.5                       # bowl centre y
GLYPHS = {
    ' ': (2.6, []),
    '!': (0, [[(0, C), (0, 2.6)], [(0, 0.15), (0, 0)]]),
    '"': (1.8, [[(0, C), (0, 7.6)], [(1.8, C), (1.8, 7.6)]]),
    '#': (5.6, [[(1.8, 9), (1.2, 1)], [(4.4, 9), (3.8, 1)], [(0, 6.6), (5.6, 6.6)], [(0, 3.4), (5.6, 3.4)]]),
    '$': (5, [arc(2.5, 7.4, 2.5, 2.1, 20, 270) + arc(2.5, 2.8, 2.5, 2.5, 90, -180)[1:], [(2.5, 11), (2.5, -1)]]),
    '%': (7, [O(1.5, 8, 1.5, 2), O(5.5, 2, 1.5, 2), [(6, C), (1, 0)]]),
    "'": (0, [[(0, C), (0, 7.6)]]),
    '(': (2.2, [arc(5.2, 4, 5.2, 7.4, 145, 215)]),
    ')': (2.2, [arc(-3, 4, 5.2, 7.4, 35, -35)]),
    '*': (4.6, [[(2.3, C), (2.3, 5.4)], [(0, 8.6), (4.6, 6.8)], [(0, 6.8), (4.6, 8.6)]]),
    '+': (5.6, [[(0, 4), (5.6, 4)], [(2.8, 1.2), (2.8, 6.8)]]),
    ',': (1.2, [[(1.2, 0.8), (1.2, 0), (0, -2.4)]]),
    '-': (3.6, [[(0, 3.8), (3.6, 3.8)]]),
    '.': (0, [[(0, 0.15), (0, 0)]]),
    '/': (4.6, [[(4.6, C), (0, -1)]]),
    '0': (5, [O(2.5, 5, 2.5, 5)]),
    '1': (4, [[(0.6, 8), (2.6, C), (2.6, 0)], [(0.4, 0), (4.6, 0)]]),
    '2': (5, [arc(2.5, 7.5, 2.4, 2.5, 160, -30) + [(0, 0), (5, 0)]]),
    '3': (5, [arc(2.4, 7.6, 2.3, 2.4, 160, -90) + arc(2.5, 2.6, 2.5, 2.6, 90, -160)[1:]]),
    '4': (5.4, [[(4, 0), (4, C), (0, 3), (5.4, 3)]]),
    '5': (5, [[(4.6, C), (0.6, C), (0.2, 5.6)] + arc(2.5, 3.1, 2.5, 3.1, 130, -160)[1:]]),
    '6': (5, [arc(5.4, 4, 5.4, 6, 105, 180) + arc(2.5, 3, 2.5, 3, 180, 540)[1:]]),
    '7': (5, [[(0, C), (5, C), (1.8, 0)]]),
    '8': (5, [O(2.5, 7.65, 2.2, 2.35), O(2.5, 2.65, 2.5, 2.65)]),
    '9': (5, [arc(2.5, 7, 2.5, 3, 0, 360) + arc(-0.4, 6, 5.4, 6, 0, -75)[1:]]),
    ':': (0, [[(0, 6.15), (0, 6)], [(0, 0.15), (0, 0)]]),
    ';': (1.2, [[(1.2, 6.15), (1.2, 6)], [(1.2, 0.8), (1.2, 0), (0, -2.4)]]),
    '<': (5, [[(5, 7.4), (0, 4), (5, 0.6)]]),
    '=': (5.4, [[(0, 5.4), (5.4, 5.4)], [(0, 2.6), (5.4, 2.6)]]),
    '>': (5, [[(0, 7.4), (5, 4), (0, 0.6)]]),
    '?': (4.6, [arc(2.3, 7.6, 2.3, 2.4, 160, -90) + [(2.3, 4.2), (2.3, 2.8)], [(2.3, 0.15), (2.3, 0)]]),
    '@': (8, [arc(4.6, 3.8, 1.8, 2.3, 0, 360), [(6.4, 6.2), (6.4, 2.4)] + arc(7.2, 2.4, 0.8, 1, 180, 360)[1:] +
             arc(4, 4, 4, 5.4, 0, 300)[1:]]),
    '[': (2, [[(2, 11), (0, 11), (0, -2), (2, -2)]]),
    '\\': (4.6, [[(0, C), (4.6, -1)]]),
    ']': (2, [[(0, 11), (2, 11), (2, -2), (0, -2)]]),
    '^': (4.6, [[(0, 7.4), (2.3, C), (4.6, 7.4)]]),
    '_': (6, [[(0, -2.2), (6, -2.2)]]),
    '`': (1.4, [[(0, C), (1.4, 8.6)]]),
    '{': (2.8, [[(2.8, 11), (2.2, 11), (1.4, 10.2), (1.4, 5.4), (0, 4.5), (1.4, 3.6), (1.4, -1.2), (2.2, -2), (2.8, -2)]]),
    '|': (0, [[(0, 11), (0, -2)]]),
    '}': (2.8, [[(0, 11), (0.6, 11), (1.4, 10.2), (1.4, 5.4), (2.8, 4.5), (1.4, 3.6), (1.4, -1.2), (0.6, -2), (0, -2)]]),
    '~': (5.6, [arc(1.4, 3.6, 1.4, 1.1, 180, 0) + arc(4.2, 4.4, 1.4, 1.1, 180, 360)[1:]]),

    'A': (6.4, [[(0, 0), (3.2, C), (6.4, 0)], [(1.1, 3.4), (5.3, 3.4)]]),
    'B': (5.4, [[(0, 5.2), (3, 5.2)] + arc(3, 7.6, 2.2, 2.4, -90, 90)[1:] + [(0, C), (0, 0), (3.2, 0)] +
                arc(3.2, 2.6, 2.2, 2.6, -90, 90)[1:] + [(3, 5.2)]]),
    'C': (5.8, [arc(3.3, 5, 3.3, 5, 45, 315)]),
    'D': (6, [[(0, 0), (0, C), (1.6, C)] + arc(1.6, 5, 4.4, 5, 90, -90)[1:] + [(0, 0)]]),
    'E': (4.8, [[(4.8, C), (0, C), (0, 0), (4.8, 0)], [(0, 5.2), (4.2, 5.2)]]),
    'F': (4.6, [[(4.6, C), (0, C), (0, 0)], [(0, 5.2), (4.2, 5.2)]]),
    'G': (6, [arc(3.3, 5, 3.3, 5, 45, 340)[:-1] + [(6, 1.2), (6, 4.6), (3.6, 4.6)]]),
    'H': (5.8, [[(0, 0), (0, C)], [(5.8, 0), (5.8, C)], [(0, 5.2), (5.8, 5.2)]]),
    'I': (0, [[(0, 0), (0, C)]]),
    'J': (4.2, [[(4.2, C), (4.2, 2.4)] + arc(2.1, 2.4, 2.1, 2.4, 0, -180)[1:]]),
    'K': (5.4, [[(0, 0), (0, C)], [(5.4, C), (0, 3.6)], [(1.8, 5.6), (5.6, 0)]]),
    'L': (4.6, [[(0, C), (0, 0), (4.6, 0)]]),
    'M': (7.6, [[(0, 0), (0, C), (3.8, 2), (7.6, C), (7.6, 0)]]),
    'N': (5.8, [[(0, 0), (0, C), (5.8, 0), (5.8, C)]]),
    'O': (6.6, [O(3.3, 5, 3.3, 5)]),
    'P': (5.2, [[(0, 0), (0, C), (2.8, C)] + arc(2.8, 7.4, 2.4, 2.6, 90, -90)[1:] + [(0, 4.8)]]),
    'Q': (6.6, [O(3.3, 5, 3.3, 5), [(3.8, 2.4), (6.8, -0.6)]]),
    'R': (5.4, [[(0, 0), (0, C), (2.8, C)] + arc(2.8, 7.4, 2.4, 2.6, 90, -90)[1:] + [(0, 4.8)], [(2.6, 4.8), (5.4, 0)]]),
    'S': (5.2, [arc(2.6, 7.5, 2.5, 2.5, 20, 270) + arc(2.6, 2.5, 2.6, 2.5, 90, -160)[1:]]),
    'T': (6, [[(0, C), (6, C)], [(3, C), (3, 0)]]),
    'U': (5.6, [[(0, C), (0, 2.8)] + arc(2.8, 2.8, 2.8, 2.8, 180, 360)[1:] + [(5.6, C)]]),
    'V': (6.2, [[(0, C), (3.1, 0), (6.2, C)]]),
    'W': (9, [[(0, C), (2.2, 0), (4.5, 8.4), (6.8, 0), (9, C)]]),
    'X': (5.8, [[(0, C), (5.8, 0)], [(5.8, C), (0, 0)]]),
    'Y': (6, [[(0, C), (3, 5), (6, C)], [(3, 5), (3, 0)]]),
    'Z': (5.4, [[(0.2, C), (5.4, C), (0, 0), (5.4, 0)]]),

    'a': (4.6, [arc(2.2, 5.4, 2.1, 1.6, 160, 0) + [(4.4, 3), (4.4, 0)],
                [(4.4, 3.8), (2, 3.8)] + arc(2, 1.9, 2, 1.9, 90, 270)[1:] + [(2.6, 0)] + arc(2.6, 1.6, 1.8, 1.6, 270, 360)[1:]]),
    'b': (4.8, [[(0, C), (0, 0)], arc(2.4, B, 2.4, B, 0, 360)]),
    'c': (4.4, [arc(2.5, B, 2.5, B, 50, 310)]),
    'd': (4.8, [[(4.8, C), (4.8, 0)], arc(2.4, B, 2.4, B, 0, 360)]),
    'e': (4.8, [[(0.1, 3.8), (4.8, 3.8)] + arc(2.4, B, 2.4, B, 0, 315)[1:]]),
    'f': (3.4, [arc(3.6, 8, 1.8, 2, 140, 180)[0:1] + arc(3.2, 8.2, 1.8, 1.8, 50, 180)[1:] + [(1.4, 0)], [(0, X), (3.4, X)]]),
    'g': (4.8, [O(2.4, 4, 2.4, 3), [(4.8, X), (4.8, -0.6)] + arc(2.4, -0.6, 2.4, 2.4, 0, -160)[1:]]),
    'h': (4.6, [[(0, C), (0, 0)], [(0, 4.6)] + arc(2.3, 4.6, 2.3, 2.4, 180, 0)[1:] + [(4.6, 0)]]),
    'i': (0, [[(0, X), (0, 0)], [(0, 9.9), (0, 9.7)]]),
    'j': (2, [[(2, X), (2, -1.4)] + arc(0.4, -1.4, 1.6, 1.6, 0, -120)[1:], [(2, 9.9), (2, 9.7)]]),
    'k': (4.4, [[(0, C), (0, 0)], [(4.2, X), (0, 2.6)], [(1.6, 3.8), (4.4, 0)]]),
    'l': (0, [[(0, C), (0, 0)]]),
    'm': (7.6, [[(0, X), (0, 0)], [(0, 4.8)] + arc(1.9, 4.8, 1.9, 2.2, 180, 0)[1:] + [(3.8, 0)],
                [(3.8, 4.8)] + arc(5.7, 4.8, 1.9, 2.2, 180, 0)[1:] + [(7.6, 0)]]),
    'n': (4.6, [[(0, X), (0, 0)], [(0, 4.6)] + arc(2.3, 4.6, 2.3, 2.4, 180, 0)[1:] + [(4.6, 0)]]),
    'o': (5, [O(2.5, B, 2.5, B)]),
    'p': (4.8, [[(0, X), (0, D)], arc(2.4, B, 2.4, B, 0, 360)]),
    'q': (4.8, [[(4.8, X), (4.8, D)], arc(2.4, B, 2.4, B, 0, 360)]),
    'r': (3.2, [[(0, X), (0, 0)], [(0, 4.2)] + arc(2.6, 4.4, 2.6, 2.6, 180, 80)[1:]]),
    's': (4.2, [arc(2.1, 5.2, 2, 1.8, 30, 270) + arc(2.1, 1.8, 2.1, 1.8, 90, -150)[1:]]),
    't': (3.4, [[(1.2, 9), (1.2, 1.4)] + arc(2.6, 1.4, 1.4, 1.4, 180, 300)[1:], [(0, X), (3.4, X)]]),
    'u': (4.6, [[(0, X), (0, 2.4)] + arc(2.3, 2.4, 2.3, 2.4, 180, 360)[1:], [(4.6, X), (4.6, 0)]]),
    'v': (5, [[(0, X), (2.5, 0), (5, X)]]),
    'w': (7.6, [[(0, X), (1.9, 0), (3.8, 5.6), (5.7, 0), (7.6, X)]]),
    'x': (4.8, [[(0, X), (4.8, 0)], [(4.8, X), (0, 0)]]),
    'y': (5, [[(0, X), (2.6, 0.4)], [(5, X), (1.8, D + 0.2), (0.6, D + 0.2)]]),
    'z': (4.4, [[(0.2, X), (4.4, X), (0, 0), (4.4, 0)]]),
}
GLYPHS['&'] = (6.2, [[(6.2, 0)] + [(1.4, 6.4)] + arc(2.6, 8.2, 1.4, 1.8, 215, -35)[1:] +
                     [(0.8, 3.2)] + arc(2.4, 2.2, 2.4, 2.2, 150, 280)[1:] + [(5.6, 3.8)]])
CHARS = ''.join(chr(c) for c in range(0x20, 0x7F))
assert set(CHARS) <= set(GLYPHS), set(CHARS) - set(GLYPHS)

SS = 8                                   # supersampling factor
# Ink must start in column 0: with a blank column 0 narrow glyphs (i, l) vanished in the
# game (tried against the PCSX2 upscaling lines, which come from elsewhere).
MARGIN = 0
TIGHT = 'rfty'                           # open on the right: one pixel less after them
# Stem-only glyphs get a full-strength core column and an 80% neighbour: UI labels wider
# than their box are squeezed by sampling, and a faint anti-aliased stem can drop out
# ('Mni-Game'); fully solid stems looked blocky when PCSX2 upscales.
STEMS = "iIjl!|.,:;'"
# UI labels are drawn with -1 extra spacing (measured: 'Mine' read 'Mne'): the widest
# glyphs keep two blank columns so a gap survives it.
WIDE = 'MWmw'

def _draw(c, size, f):
    """Rows of glyph c (levels 0..top), x scaled by f."""
    cw, ch, base, cap, stroke, top = SIZES[size]
    s = (cap - stroke) / C
    w, strokes = GLYPHS[c]
    img = Image.new('L', (cw * SS, ch * SS), 0)
    dr = ImageDraw.Draw(img)
    left = 0.5 + stroke / 2
    r = stroke * SS / 2
    for st in strokes:
        pts = [((left + x * s * f) * SS, (base - stroke / 2 - y * s) * SS) for x, y in st]
        if len(pts) > 1:
            dr.line(pts, fill=255, width=max(1, round(stroke * SS)), joint='curve')
        for px, py in pts[:1] + pts[-1:]:
            dr.ellipse((px - r, py - r, px + r, py + r), fill=255)
    small = img.resize((cw, ch), Image.BOX)
    rows = [[min(top, round(small.getpixel((x, y)) * top / 255 * 1.15)) for x in range(cw)] for y in range(ch)]
    if c in STEMS:
        # full strength in the stem's core column only: the edges stay anti-aliased
        for r in rows:
            if max(r) * 2 >= top:
                k = r.index(max(r))
                r[k] = top
                # and the stronger neighbour at 80%: squeezing may drop either column
                n = max((j for j in (k - 1, k + 1) if 0 <= j < len(r)), key=lambda j: r[j])
                r[n] = max(r[n], round(top * 0.8))
    return rows

def render(size):
    """-> {char: (rows, advance)}; rows = cell_h lists of cell_w levels 0..max.
    Wide glyphs (M, W, m, w) are condensed until they leave a blank column inside the
    cell: ink up to the cell edge made the next letter touch them ('Mi' read as 'M')."""
    cw, ch, base, cap, stroke, top = SIZES[size]
    s = (cap - stroke) / C
    out = {}
    for c in CHARS:
        f = 1.0
        while True:
            rows = _draw(c, size, f)
            ink = [x for x in range(cw) if any(rows[y][x] for y in range(ch))]
            if not ink or max(ink) <= cw - (4 if c in WIDE else 3):
                break
            f *= 0.96
        adv = (max(ink) + 2 - MARGIN + (c in WIDE) - (c in TIGHT)) if ink else round(GLYPHS[c][0] * s + 1)
        out[c] = (rows, min(adv, cw - 1))
    return out

def preview(path, text='The quick brown fox jumps over the lazy dog! 0123456789 (Oda Nobunaga) "Kinoshita" #$%&*+,-./:;<=>?@[\\]^_`{|}~'):
    """Write a preview image of both sizes, scaled 3x, white on dark blue."""
    lines = []
    for size in SIZES:
        g = render(size)
        cw, ch = SIZES[size][:2]
        for t in (CHARS, text[:60], text[60:]):
            width = sum(g[c][1] for c in t)
            im = Image.new('L', (width + 4, ch), 0)
            x = 2
            for c in t:
                rows, adv = g[c]
                for y in range(ch):
                    for i in range(cw):
                        if rows[y][i] and x + i < im.width:
                            im.putpixel((x + i, y), max(im.getpixel((x + i, y)), rows[y][i] * 17))
                x += adv
            lines.append(im)
    W = max(l.width for l in lines)
    out = Image.new('RGB', (W, sum(l.height + 4 for l in lines)), (30, 40, 70))
    y = 0
    for l in lines:
        out.paste(Image.merge('RGB', [l] * 3), (0, y), l)
        y += l.height + 4
    out.resize((out.width * 3, out.height * 3), Image.NEAREST).save(path)

if __name__ == '__main__':
    import sys
    preview(sys.argv[1] if len(sys.argv) > 1 else 'build/dev/latinfont.png')
