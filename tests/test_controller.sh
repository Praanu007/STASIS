#!/usr/bin/env bash
# test_controller.sh - regression tests for stasisd's control law.
#
# Runs stasisd against fake cgroup trees (plain files in a temp directory), so
# no jobs, no swap and no sched_ext are needed. Run as root: stasisd locks
# itself into memory. The PSI trigger is pointed at a plain file, which never
# signals, and /proc/meminfo at a fake 16 GB machine, so the real machine's
# memory can't interfere: pressure in these tests comes only from the fake PFF
# export. The feeders compute their values from the clock, not from loop
# counts, so a slow machine (an emulated VM) doesn't stretch the timeline.
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
printf "MemTotal:       16000000 kB\nMemAvailable:   12000000 kB\n" > "$T/meminfo-16g"

# mkcg DIR N [PEAK]: N fake job cgroups, 400 MB resident of a 700 MB working
# set (so 300 MB of it is swapped out), or of a PEAK-byte one
mkcg() {
  rm -rf "$1"
  for j in $(seq 1 "$2"); do
    local d=$1/stasis-job-$j.service; mkdir -p "$d"
    echo 0 > "$d/cgroup.freeze"; printf "usage_usec 0\nuser_usec 0\n" > "$d/cpu.stat"
    echo "${3:-734003200}" > "$d/memory.peak"; echo 400000000 > "$d/memory.current"
    echo 0 > "$d/memory.swap.current"
    printf "workingset_refault_anon 0\nswapcached 0\n" > "$d/memory.stat"
    echo $((100 + j)) > "$d/cgroup.procs"
  done
}
# put FILE VALUE: replace a file's content atomically, as cgroupfs reads are
put() { printf '%s\n' "$2" > "$1.tmp" && mv "$1.tmp" "$1"; }
# usec: wall-clock microseconds, without forking (bash 5)
usec() { local -n out=$1; out=${EPOCHREALTIME//[!0-9]/}; }
# cpufeed DIR SECONDS: unfrozen jobs accumulate CPU time, one CPU-second per
# second, like real ones
cpufeed() {
  local end=$((SECONDS + $2)) last now d v; declare -A cpu=()
  usec last
  while (( SECONDS < end )); do
    sleep 0.1
    usec now
    for d in "$1"/stasis-job-*.service; do
      read -r v < "$d/cgroup.freeze"
      if [[ $v == 0 ]]; then
        cpu[$d]=$(( ${cpu[$d]:-0} + now - last ))
        printf "usage_usec %d\nuser_usec %d\n" ${cpu[$d]} ${cpu[$d]} > "$d/cpu.stat.tmp"; mv "$d/cpu.stat.tmp" "$d/cpu.stat"
      fi
    done
    last=$now
  done
}
# pfffeed DIR SECONDS GROW1 GROW2 STOP2: scheduler-style PFF export. Job 1
# faults hard (5 faults per CPU-ms); jobs 1 and 2 grow by GROW1/GROW2 bytes
# per 100 ms (job 2 stops growing after STOP2 tenths of a second).
pfffeed() {
  local t0 now ms n
  usec t0
  while :; do
    usec now; ms=$(( (now - t0) / 1000 ))
    (( ms < $2 * 1000 )) || break
    n=$((ms * 1000000))
    put "$1/stasis-job-1.service/memory.current" $((400000000 + $3 * ms / 100))
    put "$1/stasis-job-2.service/memory.current" $((400000000 + $4 * (ms < $5 * 100 ? ms : $5 * 100) / 100))
    printf "101 %d %d\n102 1 %d\n103 1 %d\n104 1 %d\n" $((ms * 5)) $n $n $n $n > "$T/pff.tmp"; mv "$T/pff.tmp" "$T/pff"
    sleep 0.1
  done
}
run() {   # run DIR SECONDS LOG ARGS...: stasisd for a while, then SIGTERM
  local dir=$1 secs=$2 log=$3; shift 3
  STASIS_CG=$dir STASIS_PFF=$T/pff STASIS_PSI=$T/psi STASIS_MEMINFO=${STASIS_MEMINFO:-$T/meminfo-16g} \
    timeout -s TERM "$secs" python3 -I "$STASISD" --window-ms 2000 --log "$log" "$@" > "$T/stasisd.out" 2>&1
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
pfffeed "$T/c8" 8 4000000 0 0 & run "$T/c8" 18 "$T/d8" --quantum 100; wait
check "admission waits until the active set stops growing" \
  'min([rel(e) for e in ev if e.get("why") == "admit"] or [0]) > 7.5' "$T/d8"

# T3b: rotation continues after the calm point when the parked job doesn't fit
mkcg "$T/c3" 4; echo 1 > "$T/c3/stasis-job-4.service/cgroup.freeze"
echo 99999999999999 > "$T/c3/stasis-job-4.service/memory.peak"
cpufeed "$T/c3" 15 & run "$T/c3" 14 "$T/d3" --quantum 2; wait
check "rotation keeps going after the calm point" \
  'sum(1 for e in ev if e.get("why") == "rotate-out" and rel(e) > 5.5) >= 2' "$T/d3"

# T4: no rotation while no active job has had a quantum of user CPU since its thaw
mkcg "$T/c7" 4; echo 1 > "$T/c7/stasis-job-4.service/cgroup.freeze"
echo 99999999999999 > "$T/c7/stasis-job-4.service/memory.peak"
run "$T/c7" 8 "$T/d7" --quantum 2
check "the quantum is CPU time: no rotation without CPU" 'not any(e.get("why") == "rotate-out" for e in ev)' "$T/d7"

# T5: a job faulting hard without growing triggers the PFF early warning (on
# a 2.5 GB machine, where the four 700 MB working sets don't fit)
mkcg "$T/c4" 4
printf "MemTotal:        2500000 kB\nMemAvailable:     500000 kB\n" > "$T/meminfo-2g5"
pfffeed "$T/c4" 6 0 0 0 & STASIS_MEMINFO=$T/meminfo-2g5 run "$T/c4" 6 "$T/d4" --signal=bpf --pff-hi 0.03; wait
check "PFF early warning parks a thrashing job" 'any(e.get("why") == "pressure-pff" for e in ev)' "$T/d4"

# T6: a job faulting hard while its memory grows is warming up, not thrashing
mkcg "$T/c5" 4
pfffeed "$T/c5" 6 4000000 0 0 & run "$T/c5" 6 "$T/d5" --signal=bpf --pff-hi 0.03; wait
check "PFF ignores a job that is warming up" 'not any(e.get("event") == "pff_hi" for e in ev)' "$T/d5"

# T7: pressure while the active set is still growing is held off; once growth
# stops, the set is halved even though it fits: it is resident, nothing is
# parked, so pressure means an estimate is wrong
mkcg "$T/c6" 4 400000000
pfffeed "$T/c6" 14 0 4000000 40 & run "$T/c6" 14 "$T/d6" --signal=bpf --pff-hi 0.03; wait
check "warm-up guard holds off, then halves once growth stops" \
  'any(e.get("event") == "pressure" and e.get("warming") for e in ev) and min([rel(e) for e in ev if e.get("frozen") is True] or [0]) > 3.9' "$T/d6"

# T9: when the active jobs need room, a parked job is paged out through memory.reclaim
mkcg "$T/c9" 3; echo 1 > "$T/c9/stasis-job-3.service/cgroup.freeze"; : > "$T/c9/stasis-job-3.service/memory.reclaim"
printf "MemTotal:        4000000 kB\nMemAvailable:      50000 kB\n" > "$T/meminfo"
STASIS_MEMINFO=$T/meminfo run "$T/c9" 6 "$T/d9" --quantum 100
check "a parked job is paged out to make room, logged once" \
  'sum(1 for e in ev if e.get("event") == "reclaim" and e.get("job") == "stasis-job-3.service") == 1 and int(open("'"$T"'/c9/stasis-job-3.service/memory.reclaim").read() or 0) > 0' "$T/d9"

# T10: pressure while the set fits but is still reloading swapped-out pages is
# warm-up: nothing is halved
mkcg "$T/c10" 4
pfffeed "$T/c10" 8 0 0 0 & run "$T/c10" 8 "$T/d10" --signal=bpf --pff-hi 0.03; wait
check "a set that fits and is reloading is not halved" \
  'any(e.get("hold") == "reloading" for e in ev) and not any(e.get("frozen") is True for e in ev)' "$T/d10"

# T11: memory.reclaim gets whole bytes even when the amount isn't a whole chunk
# ("20971520.0" is EINVAL on a real kernel)
mkcg "$T/c11" 3; echo 1 > "$T/c11/stasis-job-3.service/cgroup.freeze"; : > "$T/c11/stasis-job-3.service/memory.reclaim"
printf "MemTotal:        4000000 kB\nMemAvailable:    1031870 kB\n" > "$T/meminfo-11"
STASIS_MEMINFO=$T/meminfo-11 run "$T/c11" 6 "$T/d11" --quantum 100
check "reclaim writes whole bytes" \
  'open("'"$T"'/c11/stasis-job-3.service/memory.reclaim").read().strip() == "20971520"' "$T/d11"

echo "$fails failed"
exit "$fails"
