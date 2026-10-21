"""Make an isolated DuckStation config dir for headless tests: build/dsh/duckstation
(run with XDG_CONFIG_HOME=build/dsh). Copies the user's settings.ini (read only), changes
what automation needs, links the BIOS folder and copies the user's PUK memory card once.

usage: datapath.py [outdir] [pad id]"""
import os, re, sys, shutil
USER = os.path.expanduser('~/.config/duckstation')
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "dsh")
PADID = sys.argv[2] if len(sys.argv) > 2 else "SDL-0"
D = os.path.join(OUT, 'duckstation'); os.makedirs(os.path.join(D, 'memcards'), exist_ok=True)
for sub in ('savestates', 'screenshots'): os.makedirs(os.path.join(D, sub), exist_ok=True)
if not os.path.islink(os.path.join(D, 'bios')): os.symlink(os.path.join(USER, 'bios'), os.path.join(D, 'bios'))
SDL = {'Up': 'DPadUp', 'Down': 'DPadDown', 'Left': 'DPadLeft', 'Right': 'DPadRight',
       'Circle': 'B', 'Cross': 'A', 'Triangle': 'Y', 'Square': 'X',
       'Start': 'Start', 'Select': 'Back', 'L1': 'LeftShoulder', 'R1': 'RightShoulder',
       'L2': '+LeftTrigger', 'R2': '+RightTrigger'}
SET = {'Main': {'ConfirmPowerOff': 'false', 'SaveStateOnExit': 'false', 'CreateSaveStateBackups': 'false',
                'PauseOnFocusLoss': 'false', 'StartFullscreen': 'false', 'SetupWizardIncomplete': 'false'},
       'AutoUpdater': {'CheckAtStartup': 'false'},
       'GPU': {'ResolutionScale': '1', 'Renderer': 'Software'},
       'Display': {'ScreenshotMode': 'InternalResolution', 'ScreenshotFormat': 'PNG'},
       'Debug': {'EnableGDBServer': 'true', 'GDBServerPort': '2345'},
       'Hacks': {'ExportSharedMemory': 'true'},
       'Audio': {'Backend': 'Null'},
       'Hotkeys': {'Screenshot': '%s/Guide' % PADID, 'SaveSelectedSaveState': '%s/RightStick' % PADID, 'LoadSelectedSaveState': '%s/LeftStick' % PADID},
       'GameList': {'RecursivePaths': '', 'Paths': ''},
       'Pad1': dict({k: '%s/%s' % (PADID, v) for k, v in SDL.items()}, Type='DigitalController'),
       'Logging': {'LogLevel': 'Info', 'LogToFile': 'true', 'LogToConsole': 'true'}}
ini = open(os.path.join(USER, 'settings.ini'), encoding='utf-8').read()
out, sec, seen = [], None, set()
for line in ini.splitlines():
    m = re.match(r'\[(.+)\]$', line)
    if m:
        sec = m.group(1); seen.add(sec); out.append(line)
        out += ['%s = %s' % kv for kv in SET.get(sec, {}).items()]
        continue
    k = line.split('=')[0].strip()
    if sec == 'Pad1' or k in SET.get(sec, {}): continue
    out.append(line)
for s in SET:
    if s not in seen: out += ['', '[%s]' % s] + ['%s = %s' % kv for kv in SET[s].items()]
open(os.path.join(D, 'settings.ini'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print(D)
CARD = 'Sangokushi VI with Power-Up Kit (Japan)_1.mcd'
src, dst = os.path.join(USER, 'memcards', CARD), os.path.join(D, 'memcards', CARD)
if os.path.exists(src) and not os.path.exists(dst):
    shutil.copyfile(src, dst)
