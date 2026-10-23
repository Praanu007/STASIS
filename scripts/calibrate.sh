#!/usr/bin/env bash
# calibrate.sh SIZE_MB [TARGET_SECONDS]
# Phase 1 step 4: time one job alone and suggest PASSES so it runs ~60 s.
set -euo pipefail
SIZE=$1 TARGET=${2:-60}
BIN=${STASIS_HOME:-/opt/stasis}/wss
log=$(mktemp)
"$BIN" "$SIZE" 3 > "$log"
# lines: "pass wallclock"; pass 0 is the start marker after the initial fill
per_pass=$(awk 'NR==1 {t0=$2} END {print ($2 - t0) / $1}' "$log")
passes=$(awk -v p="$per_pass" -v t="$TARGET" 'BEGIN {n = int(t / p + 0.5); print (n < 1 ? 1 : n)}')
echo "one pass over ${SIZE} MB takes ${per_pass}s alone -> PASSES=$passes for ~${TARGET}s"
rm -f "$log"
