#!/bin/bash
# hshot.sh out.png : native-resolution screenshot of the headless DuckStation (Screenshot
# hotkey = vpad Mode button -> build/dsh/duckstation/screenshots/, moved to out.png)
D=$(cd "$(dirname "$0")" && pwd); PY="$D/../../.venv/bin/python"
S="$D/../../build/dsh/duckstation/screenshots"
out=$(realpath -m "$1"); rm -f "$out"
before=$(ls -t "$S" 2>/dev/null | head -1)
"$PY" "$D/vpad.py" hold Mode 150 >/dev/null
for i in $(seq 1 40); do
  new=$(ls -t "$S" 2>/dev/null | head -1)
  if [ -n "$new" ] && [ "$new" != "$before" ]; then for j in $(seq 1 30); do  # wait until the PNG is complete
      "$PY" -c "import sys; from PIL import Image; Image.open(sys.argv[1]).load()" "$S/$new" 2>/dev/null && break
      timeout 0.1 tail -f /dev/null
    done
    mv "$S/$new" "$out"; exit 0; fi
  timeout 0.1 tail -f /dev/null
done
echo "no screenshot"; exit 1
