#!/usr/bin/env bash
# check_faults.sh [SIZE_MB] [PASSES] - Phase 3 rung 2, "counts are correct".
# With scx_stasis loaded, run one wss under memory pressure, capture majflt
# (field 12 of /proc/PID/stat) just before it exits, and compare with the
# scheduler's maj_flt total for that tgid in /run/stasis/pff.
# Create the pressure first, e.g. in another shell:  sudo scripts/run_mix.sh 6 ...
set -euo pipefail
SIZE=${1:-700} PASSES=${2:-5}
[[ $(cat /sys/kernel/sched_ext/root/ops 2>/dev/null) == stasis ]] || { echo "load scx_stasis first" >&2; exit 1; }
"${STASIS_HOME:-/opt/stasis}/wss" "$SIZE" "$PASSES" > /dev/null &
pid=$!
last=0
while stat=$(cat /proc/$pid/stat 2>/dev/null); do
  f=(${stat##*) })                    # fields from 3 onwards
  last=${f[9]}                        # field 12: majflt
  sleep 0.05
done
wait $pid || true
sleep 0.3                             # one more 100 ms export
bpf=$(awk -v p=$pid '$1 == p {print $2}' /run/stasis/pff)
[[ -n $bpf ]] || { echo "pid $pid missing from /run/stasis/pff" >&2; exit 1; }
# The last /proc read can be up to 50 ms before exit, so allow 1% (or 20 faults).
awk -v b="$bpf" -v p="$last" -v pid="$pid" 'BEGIN {
  d = b - p; tol = (p * 0.01 > 20) ? p * 0.01 : 20
  printf "pid %s: /proc majflt=%d  bpf maj_flt=%d  diff=%+d (%.2f%%)\n", pid, p, b, d, p ? 100 * d / p : 0
  if (d >= -tol && d <= tol) print "PASS: counts agree"; else { print "CHECK: counts differ by more than 1%"; exit 1 }
}'
