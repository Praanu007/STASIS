#!/usr/bin/env bash
# diag-barrier.sh - inside the VM, started detached. Exploratory, OUTSIDE the
# campaign (/srv/diag only; /opt/stasis and /srv/results untouched).
#
# Hypothesis: EEVDF runs in waves on this fast VM because each wss job fills
# its 700 MB at start, the first ~4 fill RAM and then keep every page hot
# (each pass touches them all every ~10 ms), so the kernel won't evict them
# and the later jobs stall mid-fill until the early ones finish.
# Test: a copy of wss that, after filling, waits for /run/stasis/go. Every job
# fills (the idle, waiting ones are cold and can be swapped out), then all
# start working together. If the hypothesis is right, N=8 now collapses.
set -uo pipefail
ROOT=/srv/diag SIZE=700 PASSES=5874
BIN=/srv/diag/bin GO=/run/stasis/go
cd ~/stasis

pgrep -f 'dashboard/server.py' >/dev/null && { echo "dashboard running: refusing"; exit 1; }
systemctl list-units --plain --no-legend 'stasis-job-*' | grep -q . && { echo "jobs running: refusing"; exit 1; }

# The test binary: wss.c plus a wait for $GO after the fill and start marker.
sudo mkdir -p "$BIN"
# The wait goes after the FIRST fflush, the one that sends the start marker:
# waiting before it leaves the marker unsent, and the release below waits for
# the marker - a deadlock (the first attempt did exactly that).
sed -e 's|#include <time.h>|#include <time.h>\n#include <unistd.h>|' \
    -e '0,/fflush(stdout);/s//fflush(stdout);\n\twhile (access("\/run\/stasis\/go", F_OK) != 0) { struct timespec w = {0, 10000000}; nanosleep(\&w, NULL); }/' \
    workloads/wss.c > /tmp/wss-barrier.c
grep -A1 'start marker' /tmp/wss-barrier.c | grep -q 'fflush' && \
  grep -A2 'start marker' /tmp/wss-barrier.c | grep -q 'access("/run/stasis/go"' || { echo "patch not where it must be"; exit 1; }
sudo cc -O2 -o "$BIN/wss" /tmp/wss-barrier.c || exit 1
sudo cp /opt/stasis/sample.py /opt/stasis/stasisd.py "$BIN/"   # run_mix takes everything from STASIS_HOME

# Self-test before trusting it: marker visible while waiting, finishes on go.
sudo rm -f "$GO"
"$BIN/wss" 10 2 > /tmp/wt.log & wp=$!
sleep 1
grep -q '^0 ' /tmp/wt.log || { echo "self-test FAILED: no start marker while waiting"; kill $wp; exit 1; }
kill -0 $wp 2>/dev/null || { echo "self-test FAILED: did not wait"; exit 1; }
sudo touch "$GO"; wait $wp; sudo rm -f "$GO"
[[ $(tail -1 /tmp/wt.log | cut -d' ' -f1) == 2 ]] || { echo "self-test FAILED: did not finish after go"; exit 1; }
echo "built $BIN/wss (wss + barrier); self-test passed"

run() {  # run N K
  local n=$1 out="$ROOT/barrier/N$1/run$2"
  sudo rm -rf "$out"; sudo mkdir -p "$out"; sudo chown "$(id -un)" "$out"
  sudo rm -f "$GO"
  echo "condition: barrier (all jobs fill, then start together)" > "$out/CONDITION"
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  echo "== $(date +%T) barrier N=$n run$2"
  sudo STASIS_HOME="$BIN" scripts/run_mix.sh "$n" "$SIZE" "$PASSES" "$out" eevdf < /dev/null > "$out/run_mix.log" 2>&1 &
  local rm=$!
  # Release the barrier once every job has printed its start marker ("0 t"),
  # or after 15 min if filling never completes (logged either way).
  local t=0
  until (( $(grep -l '^0 ' "$out"/job-*.log 2>/dev/null | wc -l) >= n )) || (( t >= 900 )); do sleep 2; t=$((t + 2)); done
  echo "   all $n filled after ${t}s? $(grep -l '^0 ' "$out"/job-*.log 2>/dev/null | wc -l)/$n -> go"
  echo "released after ${t}s with $(grep -l '^0 ' "$out"/job-*.log 2>/dev/null | wc -l)/$n filled" >> "$out/CONDITION"
  sudo touch "$GO"
  wait $rm
  sudo rm -f "$GO"
  echo "   -> $(cat "$out/status")"
}

run 4 1       # fits in RAM: the barrier alone should cost little
run 8 1
run 8 2
sudo rm -f "$GO"
echo "== analyze.py metrics on /srv/diag"
sudo python3 eval/analyze.py metrics "$ROOT"
echo "BARRIER DONE $(date -Is)"
