#!/bin/bash
# hsave.sh out.sav : save state of the headless DuckStation (hotkey = vpad R3), moved to out.sav
D=$(cd "$(dirname "$0")" && pwd); PY="$D/../../.venv/bin/python"
S="$D/../../build/dsh/duckstation/savestates"
out=$(realpath -m "$1")
before=$(ls -t "$S" 2>/dev/null | head -1); bt=$(stat -c %Y "$S/$before" 2>/dev/null)
"$PY" "$D/vpad.py" hold R3 150 >/dev/null
for i in $(seq 1 50); do
  new=$(ls -t "$S" 2>/dev/null | head -1)
  if [ -n "$new" ] && { [ "$new" != "$before" ] || [ "$(stat -c %Y "$S/$new")" != "$bt" ]; }; then
    timeout 0.5 tail -f /dev/null; cp "$S/$new" "$out"; exit 0; fi
  timeout 0.1 tail -f /dev/null
done
echo "no state"; exit 1
