"""Make the running game reload its current message block (after inject.py: loaded blocks
are not refreshed). Waits for the next msg_entry call (0x800b3dcc, a0 = message object),
sets the object's current block (m+0x408) to -1 so that call loads the block from the new
disc image, and continues. Run it, then open a screen that shows a message.

usage: .venv/bin/python tools/emu/msgreload.py [timeout_s]
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import gdbmem

MSG_ENTRY = 0x800b3dcc

g = gdbmem.G()
_, r = gdbmem.wait_break(g, MSG_ENTRY, float(sys.argv[1]) if len(sys.argv) > 1 else 60)
m = r['r4']
print('message object %08x, block %d -> reload' % (m, int.from_bytes(g.read(m + 0x408, 4), 'little')))
g.cmd('M%x,4:ffffffff' % (m + 0x408))
g.s.sendall(b'$c#63')
