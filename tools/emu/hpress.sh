#!/bin/bash
# hpress.sh out.png key ... : press buttons on the virtual pad, then take a screenshot
#   keys: X=Circle(confirm) x=Circle short tap (dialogue pages: the 120 ms hold can skip pages) Z=Cross(cancel) S=Triangle A=Square Return=Start BackSpace=Select
#   Q/W=L1/R1 E/R=L2/R2 Up/Down/Left/Right; 'w' alone = wait 1s; out.png = /dev/null for none
D=$(cd "$(dirname "$0")" && pwd); PY="$D/../../.venv/bin/python"
for k in "${@:2}"; do
  case "$k" in
    w) timeout 1 tail -f /dev/null; continue;;
    X) b=Circle;; x) b=Circle; t=40;; Z) b=Cross;; S) b=Triangle;; A) b=Square;; Return) b=Start;; BackSpace) b=Select;;
    Q) b=L1;; W) b=R1;; E) b=L2;; R) b=R2;; *) b=$k;;
  esac
  "$PY" "$D/vpad.py" hold "$b" "${t:-120}" >/dev/null; t=; timeout 0.2 tail -f /dev/null
done
timeout 0.5 tail -f /dev/null
[ "$1" = /dev/null ] || "$D/hshot.sh" "$1"
