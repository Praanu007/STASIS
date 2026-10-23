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
mkdir -p "$OUT"
systemctl reset-failed 'stasis-*' 2>/dev/null || true
echo "$CONFIG N=$N SIZE=$SIZE PASSES=$PASSES STASISD_EXTRA=${STASISD_EXTRA:-} STASISD_ARGS=${STASISD_ARGS:-}" > "$OUT/config"

SCHED_PID= CTRL_PID= TP_PID= SAMPLER=
cleanup() {
  [[ -n $SAMPLER ]] && kill "$SAMPLER" 2>/dev/null || true
  [[ -n $CTRL_PID ]] && kill -TERM "$CTRL_PID" 2>/dev/null && wait "$CTRL_PID" 2>/dev/null || true
  [[ -n $TP_PID ]] && kill -TERM "$TP_PID" 2>/dev/null || true
  systemctl stop stasis-probe 'stasis-job-*' 2>/dev/null || true
  [[ -n $SCHED_PID ]] && kill -INT "$SCHED_PID" 2>/dev/null && wait "$SCHED_PID" 2>/dev/null || true
  [[ $CONFIG == oomd ]] && systemctl set-property --runtime stasis.slice ManagedOOMMemoryPressure=auto || true
  # Belt and braces: never leave a job frozen behind us.
  for f in /sys/fs/cgroup/stasis.slice/*/cgroup.freeze; do [[ -e $f ]] && echo 0 > "$f"; done 2>/dev/null || true
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
chrt -p "$PROBE_PID" > "$OUT/probe_policy.txt"   # must say SCHED_OTHER (H4)

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

date +%s.%N > "$OUT/t0"
for i in $(seq 1 "$N"); do
  systemd-run --unit="stasis-job-$i" --slice=stasis.slice \
    -p StandardOutput=file:"$OUT/job-$i.log" \
    "$HOME_DIR/wss" "$SIZE" "$PASSES"
done

deadline=$((SECONDS + TIMEOUT))
echo OK > "$OUT/status"
(( N == 0 )) && sleep "${IDLE_SECONDS:-60}"     # unloaded reference run for F4
while systemctl list-units --state=active,activating --plain --no-legend \
      'stasis-job-*' | grep -q . ; do
  if (( SECONDS > deadline )); then echo TIMEOUT > "$OUT/status"; break; fi
  if [[ -n $SCHED_PID ]] && ! kill -0 "$SCHED_PID" 2>/dev/null; then
    echo SCHED_EJECTED > "$OUT/status"; break
  fi
  sleep 2
done
date +%s.%N > "$OUT/t_end"
for i in $(seq 1 "$N"); do
  systemctl show -p Result,ExecMainStatus --value "stasis-job-$i" 2>/dev/null \
    | paste -sd' ' | sed "s/^/job-$i /" >> "$OUT/job_results.txt" || true
done
cleanup; trap - EXIT
journalctl -k --since "-25 min" | grep -i "killed process" > "$OUT/kills.txt" || true
journalctl -u systemd-oomd --since "-25 min" | grep -i "killed" > "$OUT/oomd_kills.txt" || true
dmesg | grep -i sched_ext | tail -20 > "$OUT/sched_ext_dmesg.txt" || true
echo "done: $(cat "$OUT/status") -> $OUT"
