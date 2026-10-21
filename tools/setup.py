"""Create the working folder script/ from your own disc image.

script/ holds the Japanese text extracted from the disc next to the English, so it is
generated on each machine and never distributed. Run this once after cloning (and
again after editing tl/), then tools/build.py.

usage: .venv/bin/python tools/setup.py [track1.bin]
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'script')
TL = os.path.join(ROOT, 'tl')
CLEAN = os.path.join(ROOT, 'sangokushi_6_puk_track1.bin')
CLEAN_TRACK2 = os.path.join(ROOT, 'sangokushi_6_puk_track2.bin')
CLEAN_SHA1 = 'e4c4506ce78a8327a27506a7301c910dc185db6e'
TRACK2_SHA1 = '7c8508996d34d2a39fd75a8fab58e2c9b766b09b'


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def check_clean(path, want=CLEAN_SHA1):
    digest = sha1(path)
    if digest != want:
        sys.exit('%s: SHA-1 %s does not match the clean dump (%s)' % (path, digest, want))


def msg_json(b):
    return os.path.join(SCRIPT, 'msg', 'block%02d.json' % b)


def export_messages(disc, recs):
    """script/msg/blockNN.json = [{'id', 'jp': [text units]}] (entries with text only)."""
    os.makedirs(os.path.join(SCRIPT, 'msg'), exist_ok=True)
    blocks = msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    for b, ents in enumerate(blocks):
        rows = []
        for i, e in enumerate(ents):
            units = msgfile.texts(e)
            if units:
                rows.append({'id': b * 1000 + i, 'jp': [text.decode(u) for u in units]})
        with open(msg_json(b), 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=0)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CLEAN
    check_clean(path)
    disc = iso.Disc(path)
    recs = disc.records()
    os.makedirs(SCRIPT, exist_ok=True)
    export_messages(disc, recs)
    print('script/ ready; now run tools/build.py')


if __name__ == '__main__':
    main()
