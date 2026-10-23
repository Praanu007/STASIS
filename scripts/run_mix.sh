#!/usr/bin/env bash
# run_mix.sh N SIZE_MB PASSES OUTDIR(absolute) [CONFIG]
#
# One run = N jobs + the interactive probe + the sampler, with a hard timeout.
# CONFIG selects what runs alongside the jobs (default: eevdf):
#   eevdf          stock kernel scheduler, nothing else
#   stasis         scx_stasis + stasisd --signal=bpf           (full STASIS)
#   ctrl           EEVDF + stasisd --signal=memstat           (controller-only)
#   oomd           EEVDF + systemd-oomd killing in stasis.slice
#   simple         scx_simple, no controller
#   scxonly        scx_stasis, no controller (Gate 3: scheduler alone changes nothing)
#   thrashprotect  EEVDF + thrash-protect ($THRASH_PROTECT, pinned commit)
# stasisd flags: $STASISD_EXTRA (e.g. the frozen --pff-hi=X) and $STASISD_ARGS
# (sensitivity sweeps). $DEMO_SHELL, if set, is also protected by scx_stasis.
set -euo pipefail
N=$1 SIZE=$2 PASSES=$3 OUT=$4 CONFIG=${5:-eevdf}
HOME_DIR=${STASIS_HOME:-/opt/stasis}
TIMEOUT=${STASIS_TIMEOUT:-1200}
mkdir -p "$OUT" /run/stasis
echo RUNNING > "$OUT/status"
ln -sfn "$OUT" /run/stasis/current          # the dashboard follows this link
systemctl reset-failed 'stasis-*' 2>/dev/null || true
echo "$CONFIG N=$N SIZE=$SIZE PASSES=$PASSES STASISD_EXTRA=${STASISD_EXTRA:-} STASISD_ARGS=${STASISD_ARGS:-}" > "$OUT/config"

# Start from a quiet machine: wait (up to 3 min) for the memory-stall average
# left by a previous run to decay to zero. Otherwise this run's pressure onset
# (Jain's window, time-to-first-park) is the last run's tail: on the rehearsal
# VM a run started 22 s after another had its onset about 24 s before any job
# stalled.
quiet() { awk '/^full/ { split($2, a, "="); exit !(a[2] + 0 == 0) }' /proc/pressure/memory; }
for waited in $(seq 0 180); do quiet && break; sleep 1; done
echo "$waited" > "$OUT/quiesce_s"

SCHED_PID= CTRL_PID= TP_PID= SAMPLER=
cleanup() {
  [[ -n $SAMPLER ]] && kill "$SAMPLER" 2>/dev/null || true
  [[ -n $CTRL_PID ]] && kill -TERM "$CTRL_PID" 2>/dev/null && wait "$CTRL_PID" 2>/dev/null || true
  [[ -n $TP_PID ]] && kill -TERM "$TP_PID" 2>/dev/null || true
  # Never leave a job frozen behind us. Thaw before stopping: SIGTERM stays
  # pending on a frozen task, so systemd would wait 90 s before SIGKILL.
  for f in /sys/fs/cgroup/stasis.slice/*/cgroup.freeze; do [[ -e $f ]] && echo 0 > "$f"; done 2>/dev/null || true
  systemctl stop stasis-probe 'stasis-job-*' 2>/dev/null || true
  [[ -n $SCHED_PID ]] && kill -INT "$SCHED_PID" 2>/dev/null && wait "$SCHED_PID" 2>/dev/null || true
  [[ $CONFIG == oomd ]] && systemctl set-property --runtime stasis.slice ManagedOOMMemoryPressure=auto || true
}
trap cleanup EXIT

wait_sched() {   # wait until the named sched_ext scheduler is attached
  for _ in $(seq 50); do
    [[ $(cat /sys/kernel/sched_ext/root/ops 2>/dev/null) == "$1" ]] && return 0
    sleep 0.2
  done
  echo "scheduler $1 failed to attach" >&2; exit 1
}

# interactive probe: protected pages, normal (non-RT) policy
systemd-run --unit=stasis-probe -p MemoryMin=64M \
  -p StandardOutput=file:"$OUT/probe.txt" \
  cyclictest --policy=other -i 10000 -D "$TIMEOUT" -q -h 20000
sleep 0.5
PROBE_PID=$(systemctl show -p MainPID --value stasis-probe)
chrt -a -p "$PROBE_PID" > "$OUT/probe_policy.txt" # every thread must say SCHED_OTHER (H4)

case $CONFIG in
  eevdf) ;;
  stasis)
    "$HOME_DIR/scx_stasis" -p "$PROBE_PID" ${DEMO_SHELL:+-p "$DEMO_SHELL"} > "$OUT/scx.log" 2>&1 &
    SCHED_PID=$!; wait_sched stasis
    python3 "$HOME_DIR/stasisd.py" --signal=bpf --log "$OUT/decisions.jsonl" ${STASISD_EXTRA:-} ${STASISD_ARGS:-} \
      > "$OUT/stasisd.log" 2>&1 &
    CTRL_PID=$! ;;
  ctrl)
    python3 "$HOME_DIR/stasisd.py" --signal=memstat --log "$OUT/decisions.jsonl" ${STASISD_ARGS:-} \
      > "$OUT/stasisd.log" 2>&1 &
    CTRL_PID=$! ;;
  oomd)
    systemctl set-property --runtime stasis.slice ManagedOOMMemoryPressure=kill ;;
  simple)
    "$HOME_DIR/scx_simple" > "$OUT/scx.log" 2>&1 &
    SCHED_PID=$!; wait_sched simple ;;
  scxonly)
    "$HOME_DIR/scx_stasis" -p "$PROBE_PID" > "$OUT/scx.log" 2>&1 &
    SCHED_PID=$!; wait_sched stasis ;;
  thrashprotect)
    "${THRASH_PROTECT:?set THRASH_PROTECT to the pinned thrash-protect script}" > "$OUT/tp.log" 2>&1 &
    TP_PID=$! ;;
  *) echo "unknown CONFIG $CONFIG" >&2; exit 2 ;;
esac

python3 "$HOME_DIR/sample.py" "$OUT/samples.jsonl" &
SAMPLER=$!
sleep 1

T0=$(date +%s.%N); echo "$T0" > "$OUT/t0"
for i in $(seq 1 "$N"); do
  systemd-run --unit="stasis-job-$i" --slice=stasis.slice \
    -p StandardOutput=file:"$OUT/job-$i.log" \
    "$HOME_DIR/wss" "$SIZE" "$PASSES"
done

# status stays RUNNING until the run really ends, so a run cut short by a
# wedged or reset guest is never mistaken for a finished one.
deadline=$((SECONDS + TIMEOUT))
STATUS=OK
(( N == 0 )) && sleep "${IDLE_SECONDS:-60}"     # unloaded reference run for F4
while systemctl list-units --state=active,activating --plain --no-legend \
      'stasis-job-*' | grep -q . ; do
  if (( SECONDS > deadline )); then STATUS=TIMEOUT; break; fi
  if [[ -n $SCHED_PID ]] && ! kill -0 "$SCHED_PID" 2>/dev/null; then STATUS=SCHED_EJECTED; break; fi
  if [[ -n $CTRL_PID ]] && ! kill -0 "$CTRL_PID" 2>/dev/null; then STATUS=CTRL_DIED; break; fi
  sleep 2
done
date +%s.%N > "$OUT/t_end"
for i in $(seq 1 "$N"); do
  systemctl show -p Result,ExecMainStatus --value "stasis-job-$i" 2>/dev/null \
    | paste -sd' ' | sed "s/^/job-$i /" >> "$OUT/job_results.txt" || true
done
cleanup; trap - EXIT
# Only this run's kernel log: earlier runs' kills must not be counted again.
journalctl -k --since "@${T0%.*}" | grep -i "killed process" > "$OUT/kills.txt" || true
journalctl -u systemd-oomd --since "@${T0%.*}" | grep -i "killed" > "$OUT/oomd_kills.txt" || true
journalctl -k --since "@${T0%.*}" | grep -i sched_ext | tail -20 > "$OUT/sched_ext_dmesg.txt" || true
echo "$STATUS" > "$OUT/status"
echo "done: $STATUS -> $OUT"
