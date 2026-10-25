#!/usr/bin/env bash
# stasis-all.sh - inside the lab VM, run by stasis-all.service at every boot.
# Runs the whole Phase 5 campaign unattended: experiment A (155), a 2-run
# pilot of C, B (45) and C (15), in batches of $BATCH runs with a reboot
# between batches (fresh kernel state, as the README asks). It is resumable:
# campaign.sh skips finished runs, so a reboot or crash just continues.
#
# Two guards on top of campaign.sh:
#  - host trouble is flagged, never silently fixed: a sampler gap over
#    $GAP_S seconds (laptop sleep, or a VM too starved to sample) writes
#    sampler_gap.txt, and soft lockups write host_stall.txt, in the run dir.
#  - scheduler smoke test: before any STASIS run, a small scxonly run and a
#    small full-STASIS run (in /srv/gates, not scored) must end OK, not
#    SCHED_EJECTED or CTRL_DIED. If either fails, nothing more runs.
#
# Experiment A's EEVDF runs come first and calibrate PFF_HI (what
# redo-eevdf.sh did); then it waits for PFF_HI in /etc/stasis-all.env (the
# value frozen in docs/00-prereg.md; freezing it is the team's call).
set -uo pipefail
LOG=/home/stasis/all.log
exec >>"$LOG" 2>&1
REPO=/home/stasis/stasis SIZE=700 PASSES=5874
BATCH=${BATCH:-40} GAP_S=${GAP_S:-300}
STATE=/var/lib/stasis-all SLEPT=/srv/superseded/host-sleep
mkdir -p "$STATE"
log() { echo "$(date -Is) $*"; }

[[ -f $STATE/done ]] && { log "campaign already complete"; exit 0; }
log "boot: stasis-all starting (repo $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null))"
sleep 60                                   # let boot-time activity settle

while pgrep -f '^(/bin/)?bash \./redo-eevdf' > /dev/null; do sleep 60; done
if pgrep -f 'dashboard/server.py' > /dev/null; then log "dashboard running: refusing"; exit 1; fi
cd "$REPO"
A=/srv/results
[[ -f $A/plan.full.txt ]] || cp "$A/plan.txt" "$A/plan.full.txt"

slept() {                                  # run dir -> exit 0 if the host slept
  python3 - "$1" "$GAP_S" <<'EOF'
import json, sys
ts = []
for line in open(sys.argv[1] + "/samples.jsonl"):
    try: ts.append(json.loads(line)["t"])
    except Exception: pass
gap = max((b - a for a, b in zip(ts, ts[1:])), default=0)
sys.exit(0 if gap > float(sys.argv[2]) else 1)
EOF
}
check_sleep() {                            # every finished, unchecked run
  local s d dest
  for s in $(find /srv/results /srv/results-b /srv/results-c /srv/pilot-c -name status 2>/dev/null); do
    d=${s%/status}
    [[ $(cat "$s") == RUNNING ]] && continue
    grep -qxF "$d" "$STATE/checked" 2>/dev/null && continue
    # A sampler gap over GAP_S is either the laptop sleeping or the VM so
    # starved (thrashing plus host stalls) that the sampler couldn't run.
    # The guest's clock follows the host's through sleep, so the VM can't
    # tell them apart: flag it, and check against Windows' power log
    # (events 506/507) afterwards.
    if [[ -f $d/samples.jsonl ]] && slept "$d"; then
      echo "sampler gap over ${GAP_S}s: check the host's sleep log" > "$d/sampler_gap.txt"
      log "$d: sampler gap over ${GAP_S}s (flagged)"
    fi
    if :; then
      # Windows paging the VM's own RAM shows in the guest as soft lockups,
      # even of idle CPUs. Recorded, not discarded: rerunning only the runs
      # it hit would bias the sample, so it's for the team to judge.
      if [[ -f $d/t_launch && -f $d/t_end ]]; then
        n=$(journalctl _TRANSPORT=kernel -q --since "@$(cut -d. -f1 "$d/t_launch")" --until "@$(cut -d. -f1 "$d/t_end")" \
            | grep -c 'soft lockup')
        (( n > 0 )) && { echo "$n soft lockups (host paging the VM)" > "$d/host_stall.txt"; log "$d: $n soft lockups"; }
      fi
      echo "$d" >> "$STATE/checked"
    fi
  done
}

smoke() {
  [[ -f $STATE/smoke-ok ]] && return 0
  local c st
  # scxonly: 4 x 300 MB, no pressure (the scheduler attaches and stays);
  # stasis: 6 x 700 MB, over the 4 GB (scheduler and controller under load)
  for c in "scxonly 4 300" "stasis 6 700"; do
    set -- $c; c=$1
    rm -rf "/srv/gates/smoke-$c"
    scripts/run_mix.sh "$2" "$3" 400 "/srv/gates/smoke-$c" "$c" < /dev/null
    st=$(cat "/srv/gates/smoke-$c/status" 2>/dev/null)
    log "smoke $c: ${st:-no status}"
    [[ $st == OK ]] || return 1
  done
  touch "$STATE/smoke-ok"
}

count=$(cat "$STATE/count" 2>/dev/null || echo 0)
# one run at a time: check it for host sleep, count it, reboot every BATCH
run_root() {                               # ROOT -> 0 when it has no runs left
  local root=$1 out
  while :; do
    out=$(eval/campaign.sh run "$root" "$SIZE" "$PASSES" 1 2>&1)
    echo "$out"
    check_sleep
    [[ $out == *"batch done: 0 runs this batch"* ]] && return 0
    count=$((count + 1)); echo "$count" > "$STATE/count"
    if (( count >= BATCH )); then
      echo 0 > "$STATE/count"
      log "batch of $BATCH done: rebooting"
      sync; systemctl reboot; sleep 600; exit 0
    fi
  done
}

check_sleep

# 1. Experiment A's EEVDF runs first (what redo-eevdf.sh did), then PFF_HI's
#    calibration from them. EEVDF never starts stasisd; the value is
#    deliberately unparseable.
if [[ ! -f $STATE/eevdf-done ]]; then
  log "== experiment A, EEVDF runs (start barrier)"
  grep '^eevdf ' "$A/plan.full.txt" > "$A/plan.txt"
  export STASISD_EXTRA=--pff-hi=NOT-CALIBRATED-eevdf-only
  run_root "$A"
  unset STASISD_EXTRA
  cp "$A/plan.full.txt" "$A/plan.txt"
  python3 eval/analyze.py metrics "$A"
  python3 eval/analyze.py calibrate "$A" --config eevdf | tee /home/stasis/pff_calibration.txt
  touch "$STATE/eevdf-done"
  log "EEVDF done; calibration in /home/stasis/pff_calibration.txt"
fi
cp "$A/plan.full.txt" "$A/plan.txt"

# 2. PFF_HI, frozen by the team in docs/00-prereg.md
PFF_HI=
while :; do
  [[ -f /etc/stasis-all.env ]] && . /etc/stasis-all.env
  [[ ${PFF_HI:-} =~ ^[0-9]+(\.[0-9]+)?$ ]] && break
  [[ -f $STATE/waiting ]] || { log "waiting for PFF_HI in /etc/stasis-all.env"; touch "$STATE/waiting"; }
  sleep 60
done
rm -f "$STATE/waiting"
export STASISD_EXTRA=--pff-hi=$PFF_HI
log "PFF_HI=$PFF_HI"

smoke || { log "SMOKE TEST FAILED: scheduler or controller did not survive; stopping"; exit 1; }

log "== experiment A"
run_root "$A"

if [[ ! -f /srv/pilot-c/done ]]; then
  log "== pilot C (README: check the jumps land while every job runs)"
  for c in stasis ctrl; do
    d=/srv/pilot-c/$c/N8/run1
    [[ -f $d/status && $(cat "$d/status") != RUNNING ]] && continue
    rm -rf "$d"
    STASIS_HOT=40 STASIS_JUMP=$((PASSES * 6 / 10)):4 scripts/run_mix.sh 8 "$SIZE" "$PASSES" "$d" "$c" < /dev/null
    log "pilot C $c: $(cat "$d/status" 2>/dev/null)"
  done
  check_sleep
  [[ -f /srv/pilot-c/stasis/N8/run1/status && -f /srv/pilot-c/ctrl/N8/run1/status ]] && touch /srv/pilot-c/done
fi

for e in b c; do
  root=/srv/results-$e
  [[ -f $root/plan.txt ]] || eval/campaign.sh plan "$root" --experiment "${e^^}"
  log "== experiment ${e^^}"
  run_root "$root"
done

for r in /srv/results /srv/results-b /srv/results-c; do
  python3 eval/analyze.py metrics "$r" || log "metrics failed for $r"
done
touch "$STATE/done"
log "ALL DONE"
