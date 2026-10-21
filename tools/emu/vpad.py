"""Virtual gamepad for headless tests (uinput; looks like an Xbox 360 pad to SDL).

Only programs that read game controllers see it (DuckStation's SDL input); it types nothing
into desktop windows. Start the server before DuckStation so SDL finds the pad as SDL-0:

  vpad.py serve &                      create the pad, listen on $XDG_RUNTIME_DIR/s6-vpad.sock
  vpad.py press Circle Down Down ...   tap buttons in order (~120 ms down, 120 ms up)
  vpad.py hold Start 2000              hold one button for N ms
  vpad.py stop

Buttons: Up Down Left Right Cross Circle Square Triangle Start Select L1 R1 L2 R2.
"""
import fcntl, os, socket, struct, sys, time

SOCK = os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 's6-vpad.sock')
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_SETUP, UI_ABS_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x401C5504, 0x5501, 0x5502
# xpad codes: 0x133 = BTN_X = SDL X (DuckStation Square), 0x134 = BTN_Y = SDL Y (Triangle)
BTN = {'Cross': 0x130, 'Circle': 0x131, 'Triangle': 0x134, 'Square': 0x133, 'L1': 0x136, 'R1': 0x137,
       'Select': 0x13A, 'Start': 0x13B, 'Mode': 0x13C, 'L3': 0x13D, 'R3': 0x13E}
HAT = {'Left': (0x10, -1), 'Right': (0x10, 1), 'Up': (0x11, -1), 'Down': (0x11, 1)}
TRIG = {'L2': 0x02, 'R2': 0x05}
AXES = {0x00: (-32768, 32767), 0x01: (-32768, 32767), 0x02: (0, 255), 0x03: (-32768, 32767),
        0x04: (-32768, 32767), 0x05: (0, 255), 0x10: (-1, 1), 0x11: (-1, 1)}

class Pad:
    def __init__(self):
        self.fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        for code in BTN.values():
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        for code, (lo, hi) in AXES.items():
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            # uinput_abs_setup: u16 code, pad, input_absinfo(value, min, max, fuzz, flat, res)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack('<HHiiiiii', code, 0, 0, lo, hi, 0, 0, 0))
        setup = struct.pack('<HHHH80sI', 3, 0x045E, 0x028E, 0x0110, b'S6 test pad', 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def emit(self, typ, code, value):
        t = time.time()
        os.write(self.fd, struct.pack('<qqHHi', int(t), int(t % 1 * 1e6), typ, code, value))

    def set(self, name, down):
        if name in BTN:
            self.emit(EV_KEY, BTN[name], int(down))
        elif name in HAT:
            code, v = HAT[name]
            self.emit(EV_ABS, code, v if down else 0)
        elif name in TRIG:
            self.emit(EV_ABS, TRIG[name], 255 if down else 0)
        else:
            raise ValueError(name)
        self.emit(EV_SYN, 0, 0)

    def close(self):
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)

def serve():
    pad = Pad()
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(1)
    try:
        while True:
            c, _ = srv.accept()
            cmd = c.recv(4096).decode().split()
            if cmd and cmd[0] == 'stop':
                c.sendall(b'ok')
                c.close()
                break
            try:
                if cmd[0] == 'press':
                    for b in cmd[1:]:
                        pad.set(b, True); time.sleep(0.12)
                        pad.set(b, False); time.sleep(0.12)
                elif cmd[0] == 'hold':
                    pad.set(cmd[1], True); time.sleep(int(cmd[2]) / 1000)
                    pad.set(cmd[1], False)
                c.sendall(b'ok')
            except Exception as e:
                c.sendall(('error %s' % e).encode())
            c.close()
    finally:
        pad.close()
        srv.close()
        os.unlink(SOCK)

def send(words):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(' '.join(words).encode())
    r = s.recv(256).decode()
    s.close()
    return r

if __name__ == '__main__':
    if sys.argv[1] == 'serve':
        serve()
    else:
        print(send(sys.argv[1:]))
