"""Game text bytes <-> editable strings.

Shift-JIS; single-byte half-width katakana (0xA1-0xDF) are kept as such (the game
draws most of them as hiragana). Newline 0x0A is '\\n'. Every other byte that is not
printable text is written {XX} (hex), as are '{' and '}' themselves, so decode() is
lossless for any input: encode(decode(b)) == b.
"""
import re

def _char(b, i):
    c = b[i]
    if c == 0x0A:
        return '\n', 1
    if 0x20 <= c <= 0x7E and c not in (0x7B, 0x7D):
        return chr(c), 1
    if 0xA1 <= c <= 0xDF:
        return bytes([c]).decode('cp932'), 1
    if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC) and i + 1 < len(b):
        pair = b[i:i + 2]
        try:
            ch = pair.decode('cp932')
        except UnicodeDecodeError:
            return None, 0
        if len(ch) == 1 and ch.encode('cp932') == pair:
            return ch, 2
    return None, 0

def decode(b):
    out, i = [], 0
    while i < len(b):
        ch, n = _char(b, i)
        if n:
            out.append(ch)
            i += n
        else:
            out.append('{%02X}' % b[i])
            i += 1
    return ''.join(out)

_TOKEN = re.compile(r'\{([0-9A-Fa-f]{2})\}|\n|.', re.S)

def encode(s):
    out = bytearray()
    for m in _TOKEN.finditer(s):
        if m.group(1):
            out.append(int(m.group(1), 16))
        elif m.group() == '\n':
            out.append(0x0A)
        else:
            ch = m.group()
            if ch == '\\':
                out.append(0x5C)
                continue
            try:
                out += ch.encode('cp932')
            except UnicodeEncodeError:
                raise ValueError('character %r cannot be encoded' % ch)
    return bytes(out)

def readable(s):
    """Display form of decode() output: half-width katakana -> hiragana (this game draws
    them as hiragana), {1B}K..{1B}H brackets (real katakana) shown as full-width katakana."""
    import unicodedata
    def kata(m):
        return unicodedata.normalize('NFKC', m.group(1))
    s = re.sub(r'\{1B\}K(.*?)\{1B\}H', kata, s, flags=re.S)
    def hira(m):
        t = unicodedata.normalize('NFKC', m.group())
        return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in t)
    return gaiji_tags(re.sub('[ｦ-ﾟ]+', hira, s))


def gaiji_tags(s):
    """Gaiji (decoded to U+E0xx, no Unicode form) shown as {gXX}; names.py find takes the same tags."""
    return re.sub('[\ue000-\uf8ff]', lambda m: '{g%02X}' % (ord(m.group()) & 0xFF), s)
