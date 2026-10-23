#!/usr/bin/env bash
# sweep.sh CONFIG "N_VALUES" RUNS SIZE_MB PASSES RESULTS_ROOT
# e.g. Phase 1:  sudo scripts/sweep.sh eevdf "1 2 3 4 5 6 7 8" 5 700 40 /srv/results
# Writes RESULTS_ROOT/LABEL/N<n>/run<k>/ (LABEL defaults to CONFIG) and drops
# caches before each run. N=0 is an unloaded run (probe only).
set -euo pipefail
CONFIG=$1 NS=$2 RUNS=$3 SIZE=$4 PASSES=$5 ROOT=$6
HERE=$(cd "$(dirname "$0")" && pwd)
for k in $(seq 1 "$RUNS"); do
  for n in $NS; do
    out="$ROOT/${LABEL:-$CONFIG}/N$n/run$k"
    [[ -f $out/status ]] && { echo "skip $out"; continue; }
    sync; echo 3 > /proc/sys/vm/drop_caches
    "$HERE/manifest.sh" "$out" "$CONFIG"
    "$HERE/run_mix.sh" "$n" "$SIZE" "$PASSES" "$out" "$CONFIG"
  done
done
