#!/usr/bin/env bash
# test_controller.sh - regression tests for stasisd's control law.
#
# Runs stasisd against fake cgroup trees (plain files in a temp directory), so
# no jobs, no swap and no sched_ext are needed. Run as root: stasisd locks
# itself into memory. The PSI trigger is pointed at a plain file, which never
# signals, so the machine's own memory pressure can't interfere: pressure in
# these tests comes only from the fake PFF export.
#
#   sudo tests/test_controller.sh          (KEEP=1 keeps the logs for inspection)
#
# Each test prints PASS or FAIL; the exit status is the number of failures.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
STASISD="$REPO/controller/stasisd.py"
T=$(mktemp -d)
trap 'kill $(jobs -p) 2>/dev/null; if [[ ${KEEP:-} ]]; then echo "logs kept in $T"; else rm -rf "$T"; fi' EXIT
fails=0
touch "$T/psi"

# mkcg DIR N: N fake job cgroups, 400 MB resident of a 700 MB working set
mkcg() {
  rm -rf "$1"
  for j in $(seq 1 "$2"); do
    local d=$1/stasis-job-$j.service; mkdir -p "$d"
    echo 0 > "$d/cgroup.freeze"; echo "usage_usec 0" > "$d/cpu.stat"
    echo 734003200 > "$d/memory.peak"; echo 400000000 > "$d/memory.current"
    echo 0 > "$d/memory.swap.current"
    printf "workingset_refault_anon 0\nswapcached 0\n" > "$d/memory.stat"
    echo $((100 + j)) > "$d/cgroup.procs"
  done
}
# put FILE VALUE: replace a file's content atomically, as cgroupfs reads are
put() { printf '%s\n' "$2" > "$1.tmp" && mv "$1.tmp" "$1"; }
# cpufeed DIR SECONDS: unfrozen jobs accumulate CPU time, like real ones
cpufeed() {
  local end=$((SECONDS + $2))
  while (( SECONDS < end )); do
    for d in "$1"/stasis-job-*.service; do
      if [[ $(cat "$d/cgroup.freeze") == 0 ]]; then
        local u; u=$(awk '{print $2}' "$d/cpu.stat"); put "$d/cpu.stat" "usage_usec $((u + 100000))"
      fi
    done
    sleep 0.1
  done
}
# pfffeed DIR SECONDS GROW1 GROW2 STOP2: scheduler-style PFF export. Job 1
# faults hard; jobs 1 and 2 grow by GROW1/GROW2 bytes per 100 ms (job 2 stops
# after STOP2 iterations).
pfffeed() {
  local f=0 n=0 c1=400000000 c2=400000000
  for i in $(seq 1 $(( $2 * 10 ))); do
    f=$((f + 500)); n=$((n + 100000000)); c1=$((c1 + $3)); (( i < $5 )) && c2=$((c2 + $4))
    put "$1/stasis-job-1.service/memory.current" $c1; put "$1/stasis-job-2.service/memory.current" $c2
    printf "101 %d %d\n102 1 %d\n103 1 %d\n104 1 %d\n" $f $n $n $n $n > "$T/pff.tmp"; mv "$T/pff.tmp" "$T/pff"
    sleep 0.1
  done
}
run() {   # run DIR SECONDS LOG ARGS...: stasisd for a while, then SIGTERM
  local dir=$1 secs=$2 log=$3; shift 3
  STASIS_CG=$dir STASIS_PFF=$T/pff STASIS_PSI=$T/psi timeout -s TERM "$secs" python3 -I "$STASISD" \
    --window-ms 2000 --log "$log" "$@" > "$T/stasisd.out" 2>&1
}
check() {  # check NAME PYTHON-EXPRESSION-OVER-ev-AND-t0
  local name=$1 expr=$2 log=$3
  if python3 - "$log" "$expr" <<'EOF'
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1])]
t0 = ev[0]["t"]
rel = lambda e: e["t"] - t0
sys.exit(0 if eval(sys.argv[2]) else 1)
EOF
  then echo "PASS  $name"; else echo "FAIL  $name"; fails=$((fails + 1)); fi
}

# T1: SIGTERM thaws every job
mkcg "$T/c1" 2; echo 1 > "$T/c1/stasis-job-2.service/cgroup.freeze"
echo 99999999999999 > "$T/c1/stasis-job-2.service/memory.peak"     # never admitted
run "$T/c1" 3 "$T/d1" --quantum 100
check "SIGTERM thaws parked jobs" \
  '[e["job"] for e in ev if e.get("why") == "shutdown"] and open("'"$T"'/c1/stasis-job-2.service/cgroup.freeze").read().strip() == "0"' "$T/d1"

# T2: a parked job whose working set fits is admitted after 5 s of calm
mkcg "$T/c2" 2; echo 1 > "$T/c2/stasis-job-2.service/cgroup.freeze"
run "$T/c2" 8 "$T/d2" --quantum 100
check "calm admits a parked job that fits" 'any(e.get("why") == "admit" and rel(e) >= 4.9 for e in ev)' "$T/d2"

# T3: admission waits while the active set's memory is still growing
mkcg "$T/c8" 2; echo 1 > "$T/c8/stasis-job-2.service/cgroup.freeze"
pfffeed "$T/c8" 8 4000000 0 0 & run "$T/c8" 12 "$T/d8" --quantum 100; wait
check "admission waits until the active set stops growing" \
  'min([rel(e) for e in ev if e.get("why") == "admit"] or [0]) > 7.5' "$T/d8"

# T3b: rotation continues after the calm point when the parked job doesn't fit
mkcg "$T/c3" 4; echo 1 > "$T/c3/stasis-job-4.service/cgroup.freeze"
echo 99999999999999 > "$T/c3/stasis-job-4.service/memory.peak"
cpufeed "$T/c3" 15 & run "$T/c3" 14 "$T/d3" --quantum 2; wait
check "rotation keeps going after the calm point" \
  'sum(1 for e in ev if e.get("why") == "rotate-out" and rel(e) > 5.5) >= 2' "$T/d3"

# T4: no rotation while no active job has had a quantum of CPU since its thaw
mkcg "$T/c7" 4; echo 1 > "$T/c7/stasis-job-4.service/cgroup.freeze"
echo 99999999999999 > "$T/c7/stasis-job-4.service/memory.peak"
run "$T/c7" 8 "$T/d7" --quantum 2
check "the quantum is CPU time: no rotation without CPU" 'not any(e.get("why") == "rotate-out" for e in ev)' "$T/d7"

# T5: a job faulting hard without growing triggers the PFF early warning
mkcg "$T/c4" 4
pfffeed "$T/c4" 6 0 0 0 & run "$T/c4" 6 "$T/d4" --signal=bpf --pff-hi 0.03; wait
check "PFF early warning parks a thrashing job" 'any(e.get("why") == "pressure-pff" for e in ev)' "$T/d4"

# T6: a job faulting hard while its memory grows is warming up, not thrashing
mkcg "$T/c5" 4
pfffeed "$T/c5" 6 4000000 0 0 & run "$T/c5" 6 "$T/d5" --signal=bpf --pff-hi 0.03; wait
check "PFF ignores a job that is warming up" 'not any(e.get("event") == "pff_hi" for e in ev)' "$T/d5"

# T7: pressure while the active set is still growing is held off; once growth
# stops, the active set is halved
mkcg "$T/c6" 4
pfffeed "$T/c6" 8 0 4000000 40 & run "$T/c6" 8 "$T/d6" --signal=bpf --pff-hi 0.03; wait
check "warm-up guard holds off, then halves once growth stops" \
  'any(e.get("event") == "pressure" and e.get("warming") for e in ev) and min([rel(e) for e in ev if e.get("frozen") is True] or [0]) > 3.9' "$T/d6"

echo "$fails failed"
exit "$fails"
