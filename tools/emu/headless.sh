#!/bin/bash
# headless.sh image.cue [state.sav] : run DuckStation on a disc inside a headless gamescope
# (no window on the desktop) with a virtual gamepad (tools/emu/vpad.py, seen only by
# game-controller readers). Drive it with hpress.sh, look with hshot.sh, read RAM with ram.py.
# Config/data: build/dsh (tools/emu/datapath.py). Log: build/dsh/headless.log
# Stop: tools/emu/headless.sh stop
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY="$ROOT/.venv/bin/python"
T="$ROOT/build/dsh"
if [ "$1" = stop ]; then
  pkill -TERM -x AppRun; timeout 3 tail -f /dev/null; pkill -x gamescope; pkill -x gamescopereaper
  "$PY" "$ROOT/tools/emu/vpad.py" stop 2>/dev/null; exit 0
fi
[ -f "$T/duckstation/settings.ini" ] || "$PY" "$ROOT/tools/emu/datapath.py" "$T" >/dev/null
[ -S "$XDG_RUNTIME_DIR/s6-vpad.sock" ] || ("$PY" "$ROOT/tools/emu/vpad.py" serve >/dev/null 2>&1 &)
timeout 1 tail -f /dev/null
state=(); [ -n "$2" ] && state=(-statefile "$(realpath "$2")")
(XDG_CONFIG_HOME="$T" env -u WAYLAND_DISPLAY QT_QPA_PLATFORM=xcb gamescope --backend headless -W 640 -H 480 -- \
  ~/.local/bin/DuckStation-x64.AppImage -batch -fastboot -nogui "${state[@]}" -- "$(realpath "$1")" \
  > "$T/headless.log" 2>&1 &)
