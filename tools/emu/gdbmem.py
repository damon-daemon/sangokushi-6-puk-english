"""Minimal GDB remote-protocol client for DuckStation's GDB server.
usage: gdbmem.py read ADDR LEN | regs | cont   (port 2345)"""
import socket, sys
class G:
    def __init__(s, port=2345):
        s.s = socket.create_connection(('127.0.0.1', port), timeout=5); s.buf = b''
    def _recv(s):
        while True:
            i = s.buf.find(b'$'); j = s.buf.find(b'#', i)
            if i >= 0 and j >= 0 and len(s.buf) >= j + 3:
                pkt = s.buf[i+1:j]; s.buf = s.buf[j+3:]; s.s.sendall(b'+'); return pkt.decode()
            s.buf += s.s.recv(65536)
    def cmd(s, c):
        s.s.sendall(b'$%s#%02x' % (c.encode(), sum(c.encode()) & 0xff)); return s._recv()
    def read(s, addr, n):
        out = b''
        while n:
            k = min(n, 0x400); out += bytes.fromhex(s.cmd('m%x,%x' % (addr, k))); addr += k; n -= k
        return out
if __name__ == '__main__':
    g = G(); a = sys.argv[1:]
    if a[0] == 'read': print(g.read(int(a[1], 0), int(a[2], 0)).hex())
    elif a[0] == 'regs': print(g.cmd('?')); print(g.cmd('g')[:400])
    elif a[0] == 'raw': print(g.cmd(a[1]))
    if a[0] != 'raw' or a[1] != 'c': g.s.sendall(b'$c#63')   # resume on detach


def regs(g):
    """{'r0'..'r31', 'sr', 'lo', 'hi', 'bad', 'cause', 'pc'} from a 'g' packet."""
    h = g.cmd('g')
    vals = [int.from_bytes(bytes.fromhex(h[i:i + 8]), 'little') for i in range(0, min(len(h), 38 * 8), 8)]
    names = ['r%d' % i for i in range(32)] + ['sr', 'lo', 'hi', 'bad', 'cause', 'pc']
    return dict(zip(names, vals))


def wait_break(g, addr, timeout=30):
    """Set a breakpoint at addr, continue, wait for the stop; returns regs (CPU halted)."""
    print(g.cmd('Z0,%x,4' % addr))
    g.s.settimeout(timeout)
    g.s.sendall(b'$c#63')
    pkt = g._recv()
    r = regs(g)
    g.cmd('z0,%x,4' % addr)
    return pkt, r
