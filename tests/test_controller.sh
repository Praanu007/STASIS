#!/usr/bin/env bash
# test_controller.sh - regression tests for stasisd's control law.
#
# Runs stasisd against fake cgroup trees (plain files in a temp directory), so
# no jobs, no swap and no sched_ext are needed. Run as root: stasisd locks
# itself into memory. The PSI trigger is pointed at a plain file, which never
# signals, and /proc/meminfo at a fake 16 GB machine, so the real machine's
# memory can't interfere: pressure in these tests comes only from the fake PFF
# export. /proc is a fake too: the working-set sampler only ever sees fake
# processes, and without fake smaps files it falls back to footprints. The
# feeders compute their values from the clock, not from loop counts, so a slow
# machine (an emulated VM) doesn't stretch the timeline.
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
mkdir -p "$T/noproc"                     # no smaps here: working sets are footprints

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
# cpufeed DIR SECONDS [JOB...]: unfrozen jobs (all, or the numbered ones)
# accumulate CPU time, one CPU-second per second, like real ones
cpufeed() {
  local dir=$1 end=$((SECONDS + $2)) last now d v; shift 2; declare -A cpu=()
  local only=" $* "
  usec last
  while (( SECONDS < end )); do
    sleep 0.1
    usec now
    for d in "$dir"/stasis-job-*.service; do
      if [[ $only != "  " ]]; then local n=${d##*stasis-job-}; [[ $only == *" ${n%.service} "* ]] || continue; fi
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
# fakeproc DIR REFERENCED_KB JOB...: fake /proc in which the jobs' processes
# (pid 100+N) reference REFERENCED_KB, and the cgroups' memory.pressure
# files exist, so the working-set sampler can run
fakeproc() {
  local p=$1 kb=$2; shift 2
  mkdir -p "$p/self"; printf "Rss: 1 kB\nReferenced: 1 kB\n" > "$p/self/smaps_rollup"; : > "$p/self/clear_refs"
  for n in "$@"; do
    mkdir -p "$p/$((100 + n))"; : > "$p/$((100 + n))/clear_refs"
    printf "Rss: %d kB\nReferenced: %d kB\n" 400000 "$kb" > "$p/$((100 + n))/smaps_rollup"
  done
}
# pressure DIR: every job cgroup gets a memory.pressure file with no stalls
pressure() {
  for d in "$1"/stasis-job-*.service; do
    printf "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n" > "$d/memory.pressure"
  done
}
# stallfeed DIR SECONDS JOB...: those jobs stall on memory half the time
# (STALL_DIV=1: all the time)
stallfeed() {
  local dir=$1 end=$((SECONDS + $2)) div=${STALL_DIV:-2} t0 now n; shift 2
  usec t0
  while (( SECONDS < end )); do
    usec now
    for n in "$@"; do
      put "$dir/stasis-job-$n.service/memory.pressure" "some avg10=50.00 avg60=0.00 avg300=0.00 total=$(( (now - t0) / div ))"
    done
    sleep 0.1
  done
}
run() {   # run DIR SECONDS LOG ARGS...: stasisd for a while, then SIGTERM
  local dir=$1 secs=$2 log=$3; shift 3
  STASIS_CG=$dir STASIS_PFF=$T/pff STASIS_PSI=$T/psi STASIS_MEMINFO=${STASIS_MEMINFO:-$T/meminfo-16g} \
    STASIS_PROC=${STASIS_PROC:-$T/noproc} \
    timeout -s TERM "$secs" python3 -I "$STASISD" --window-ms 2000 --log "$log" "$@" > "$T/stasisd.out" 2>&1
}
check() {  # check NAME PYTHON-EXPRESSION-OVER-ev-AND-t0
  local name=$1 expr=$2 log=$3
  if python3 - "$log" "$expr" <<'EOF'
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1])]
t0 = ev[0]["t"]
rel = lambda e: e["t"] - t0
thawed = lambda why: [e["job"] for e in ev if e.get("frozen") is False and e.get("why") == why]
parked = lambda why: [e["job"] for e in ev if e.get("frozen") is True and e.get("why") == why]
def fifo(ev):
    """Every resume took the job that had waited longest (ties: by name). Jobs
    whose first logged move is a thaw were parked when stasisd started."""
    since, seen = {}, set()
    for e in ev:
        if "frozen" in e and e["job"] not in seen:
            seen.add(e["job"])
            if not e["frozen"]:
                since[e["job"]] = ev[0]["t"]
    for e in ev:
        if "frozen" not in e:
            continue
        if e["frozen"]:
            since[e["job"]] = e["t"]
        else:
            if e["why"] in ("rotate-in", "starve-in", "admit"):
                head = min(since, key=lambda j: (since[j], j))
                if e["job"] != head:
                    return False
            since.pop(e["job"], None)
    return True
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

# T12: measured working sets let more jobs in. Four jobs, each with a 700 MB
# footprint but referencing only 300 MB, on a machine where three footprints
# fit and four don't. Measured, all four run; counted by footprint, one waits.
mk12() {
  mkcg "$1" 4; pressure "$1"
  echo 1 > "$1/stasis-job-3.service/cgroup.freeze"; echo 1 > "$1/stasis-job-4.service/cgroup.freeze"
  # 3 x 734 MB fit in 90% of 2.6 GB with nothing else using memory; 4 x don't
  printf "MemTotal:        2600000 kB\nMemAvailable:    1100000 kB\n" > "$T/meminfo-12"
}
mk12 "$T/c12"; fakeproc "$T/p12" 307200 1 2 3 4
cpufeed "$T/c12" 16 & STASIS_MEMINFO=$T/meminfo-12 STASIS_PROC=$T/p12 run "$T/c12" 16 "$T/d12" --quantum 100; wait
mk12 "$T/c12f"
cpufeed "$T/c12f" 16 & STASIS_MEMINFO=$T/meminfo-12 STASIS_PROC=$T/p12 run "$T/c12f" 16 "$T/d12f" --quantum 100 --ws footprint; wait
check "measured working sets admit what footprints can't" \
  'ev[0]["ws"] == "refs" and "stasis-job-4.service" in thawed("admit")' "$T/d12"
check "counted by footprint, the fourth job waits" \
  'ev[0]["ws"] == "footprint" and thawed("admit") == ["stasis-job-3.service"]' "$T/d12f"

# T13: a job that stalls on memory isn't measured (its window would be
# discarded, and clearing its referenced bits would help reclaim take its hot
# pages), so its footprint stands and the fourth job still waits
mk12 "$T/c13"; for n in 1 2 3 4; do : > "$T/p12/$((100 + n))/clear_refs"; done
# (the stalls outlast stasisd: bash's SECONDS can end a feeder up to 1 s early)
cpufeed "$T/c13" 16 & stallfeed "$T/c13" 18 1 2 & STASIS_MEMINFO=$T/meminfo-12 STASIS_PROC=$T/p12 run "$T/c13" 16 "$T/d13" --quantum 100; wait
check "a stalled job's working set isn't trusted" \
  '"stasis-job-4.service" not in thawed("admit") and not any(e.get("event") == "ws" and (e["jobs"].get("stasis-job-1.service") or {}).get("m") for e in ev)' "$T/d13"
check "a job stalling on memory isn't sampled: its referenced bits are left alone" \
  'open("'"$T"'/p12/101/clear_refs").read() == "" and open("'"$T"'/p12/102/clear_refs").read() == ""' "$T/d13"

# T14: rotation resumes the job that has waited longest
mkcg "$T/c14" 4 99999999999999; echo 1 > "$T/c14/stasis-job-3.service/cgroup.freeze"; echo 1 > "$T/c14/stasis-job-4.service/cgroup.freeze"
cpufeed "$T/c14" 16 & run "$T/c14" 16 "$T/d14" --quantum 2; wait
check "rotation resumes the longest-waiting job first" \
  'len(thawed("rotate-in")) >= 4 and fifo(ev)' "$T/d14"

# T15: the starvation bound: no turn ever ends (no CPU), yet the parked job is
# resumed once it has waited --max-wait seconds
mkcg "$T/c15" 3 99999999999999; echo 1 > "$T/c15/stasis-job-3.service/cgroup.freeze"
run "$T/c15" 9 "$T/d15" --quantum 2 --max-wait 4; wait
check "a job parked for --max-wait is resumed without a finished turn" \
  'any(e.get("why") == "starve-in" and e["job"] == "stasis-job-3.service" and e["waited"] >= 4 for e in ev) and not thawed("rotate-in")' "$T/d15"

# T16: --no-rotation: neither rotation nor the bound
mkcg "$T/c16" 4 99999999999999; echo 1 > "$T/c16/stasis-job-4.service/cgroup.freeze"
cpufeed "$T/c16" 12 & run "$T/c16" 12 "$T/d16" --quantum 2 --max-wait 4 --no-rotation; wait
check "--no-rotation turns rotation and the bound off" \
  'not thawed("rotate-in") and not thawed("starve-in") and ev[0]["rotation"] is False' "$T/d16"

# T17: a halving parks the jobs that have had the most of their turn, not the
# one faulting hardest (job 1 faults; jobs 2 and 3 have had CPU)
mkcg "$T/c17" 4
cpufeed "$T/c17" 8 2 3 & pfffeed "$T/c17" 8 0 0 0 & STASIS_MEMINFO=$T/meminfo-2g5 run "$T/c17" 8 "$T/d17" --signal=bpf --pff-hi 0.03; wait
check "a halving parks the jobs with the most of their turn" \
  'sorted(parked("pressure-pff")) == ["stasis-job-2.service", "stasis-job-3.service"]' "$T/d17"

# T18: the bound swaps out the job that has had the most of its turn, even one
# that stalls on memory throughout (job 1 has had the CPU and the stalls; job 2
# has had neither, and preferring unstalled jobs would swap it out instead)
mkcg "$T/c18" 3 99999999999999; echo 1 > "$T/c18/stasis-job-3.service/cgroup.freeze"; pressure "$T/c18"
cpufeed "$T/c18" 10 1 & STALL_DIV=1 stallfeed "$T/c18" 10 1 & run "$T/c18" 10 "$T/d18" --quantum 100 --max-wait 4; wait
check "the bound swaps out the job with the most of its turn" \
  'parked("starve-out")[:1] == ["stasis-job-1.service"]' "$T/d18"

# T19: one swap at a time: while the job the bound swapped in is still stalled
# reloading, the next waiting job isn't swapped in (until --max-wait has passed)
mkcg "$T/c19" 4 99999999999999; for n in 3 4; do echo 1 > "$T/c19/stasis-job-$n.service/cgroup.freeze"; done; pressure "$T/c19"
STALL_DIV=1 stallfeed "$T/c19" 18 3 4 & run "$T/c19" 18 "$T/d19" --quantum 2 --max-wait 10; wait
check "the bound waits for the job it swapped in to reload" \
  'thawed("starve-in") == ["stasis-job-3.service"]' "$T/d19"

# T20: one swap at a time for rotation too: while the job rotated in is still
# stalled reloading, no other finished turn is swapped out
mkcg "$T/c20" 3 99999999999999; echo 1 > "$T/c20/stasis-job-3.service/cgroup.freeze"; pressure "$T/c20"
cpufeed "$T/c20" 10 & STALL_DIV=1 stallfeed "$T/c20" 10 3 & run "$T/c20" 10 "$T/d20" --quantum 2; wait
check "rotation waits for the job it swapped in to reload" \
  'thawed("rotate-in") == ["stasis-job-3.service"]' "$T/d20"

echo "$fails failed"
exit "$fails"
