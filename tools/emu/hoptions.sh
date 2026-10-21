#!/bin/bash
# hoptions.sh : from a fresh boot (headless.sh image.cue), go to the new-game options screen
# (192 scenario, Cao Cao) and press Start: "watch the scenario opening?" message (64074)
D=$(cd "$(dirname "$0")" && pwd)
timeout 22 tail -f /dev/null
"$D/hpress.sh" /dev/null Return w w w Return w w w w w w w w X w w w X w w w X w w w X w w Down Down Down Down Down Down Down X w w w Start w w w
