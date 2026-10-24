#!/usr/bin/env bash
# run_mix.sh N SIZE_MB PASSES OUTDIR(absolute) [CONFIG]
#
# One run = N jobs + the interactive probe + the sampler, with a hard timeout.
# CONFIG selects what runs alongside the jobs (default: eevdf):
#   eevdf          stock kernel scheduler, nothing else
#   stasis         scx_stasis + stasisd --signal=bpf           (full STASIS)
#   ctrl           EEVDF + stasisd --signal=memstat           (controller-only)
#   mglru          EEVDF + MGLRU thrashing protection (min_ttl_ms = 1000)
#   oomd           EEVDF + systemd-oomd killing in stasis.slice
#   thrashprotect  EEVDF + thrash-protect 1.1.2 with PSI ($THRASH_PROTECT)
#   scxonly        scx_stasis, no controller (Gate 3: scheduler alone changes nothing)
#   simple         scx_simple, no controller (not a baseline: Gate 0 only)
#   earlyoom       EEVDF + earlyoom ($EARLYOOM)              [arm 2, 2026-10-24]
# stasisd flags: $STASISD_EXTRA (e.g. the frozen --pff-hi=X) and $STASISD_ARGS
# (ablations and sensitivity sweeps). $STASIS_HOT is the share of each job's
# memory it keeps touching (percent, default 100). $STASIS_JUMP=P:K makes jobs
# 1..K change phase after P passes, every page hot from then on (experiment C).
# $DEMO_SHELL, if set, is also protected by scx_stasis.
# $STASIS_WORKLOAD [arm 3, 2026-10-24] runs scripts/workload.sh's rocksdb,
# memcached or compile instead of wss. The default, wss, is unchanged and is
# not recorded in the config line, so every other arm's config is as before.
# HOT and JUMP apply to wss only.
set -euo pipefail
N=$1 SIZE=$2 PASSES=$3 OUT=$4 CONFIG=${5:-eevdf}
HOME_DIR=${STASIS_HOME:-/opt/stasis}
TIMEOUT=${STASIS_TIMEOUT:-1200}
HOT=${STASIS_HOT:-100}
JUMP=${STASIS_JUMP:-}
[[ -z $JUMP || $JUMP =~ ^[0-9]+:[0-9]+$ ]] || { echo "STASIS_JUMP must be PASS:JOBS" >&2; exit 2; }
THRASH_PROTECT=${THRASH_PROTECT:-/opt/thrash-protect/bin/thrash-protect}
EARLYOOM=${EARLYOOM:-/usr/bin/earlyoom}
MGLRU_TTL=/sys/kernel/mm/lru_gen/min_ttl_ms
WORKLOAD=${STASIS_WORKLOAD:-wss}
[[ $WORKLOAD =~ ^(wss|rocksdb|memcached|compile)$ ]] || { echo "unknown STASIS_WORKLOAD $WORKLOAD" >&2; exit 2; }
WL_TAG=; [[ $WORKLOAD != wss ]] && WL_TAG=" WORKLOAD=$WORKLOAD"
mkdir -p "$OUT" /run/stasis
echo RUNNING > "$OUT/status"
ln -sfn "$OUT" /run/stasis/current          # the dashboard follows this link
systemctl reset-failed 'stasis-*' 2>/dev/null || true
echo "$CONFIG N=$N SIZE=$SIZE PASSES=$PASSES HOT=$HOT${JUMP:+ JUMP=$JUMP}$WL_TAG STASISD_EXTRA=${STASISD_EXTRA:-} STASISD_ARGS=${STASISD_ARGS:-}" > "$OUT/config"

# A baseline this machine can't run is recorded as such, never as a result.
unavailable() { echo "$CONFIG unavailable: $1" | tee "$OUT/unavailable.txt" >&2; echo UNAVAILABLE > "$OUT/status"; exit 3; }
case $CONFIG in
  stasis|scxonly|simple) [[ -e /sys/kernel/sched_ext/state ]] || unavailable "no sched_ext in this kernel" ;;
  mglru) [[ -w $MGLRU_TTL && $(( $(cat /sys/kernel/mm/lru_gen/enabled) & 1 )) == 1 ]] \
           || unavailable "MGLRU is off or has no min_ttl_ms" ;;
  thrashprotect) [[ -x $THRASH_PROTECT ]] || unavailable "no thrash-protect at $THRASH_PROTECT (setup_vm.sh installs it)" ;;
  oomd) systemctl is-active --quiet systemd-oomd || unavailable "systemd-oomd is not running" ;;
  earlyoom) [[ -x $EARLYOOM ]] || unavailable "no earlyoom at $EARLYOOM (apt install earlyoom; keep its service disabled)" ;;
esac
# Arm 3: a real workload whose tools are missing is unavailable, not a run in
# which every job failed at once.
has() { command -v "$1" >/dev/null 2>&1; }
case $WORKLOAD in
  rocksdb)   has db_bench || unavailable "workload rocksdb needs db_bench" ;;
  memcached) { has memcached && has memtier_benchmark; } || unavailable "workload memcached needs memcached and memtier_benchmark" ;;
  compile)   [[ -d ${STASIS_COMPILE_SRC:-} ]] || unavailable "workload compile needs STASIS_COMPILE_SRC" ;;
esac
[[ $WORKLOAD == wss || -x $HOME_DIR/workload.sh ]] || unavailable "no $HOME_DIR/workload.sh (setup_vm.sh installs it)"

# Start from a quiet machine: wait (up to 3 min) for the memory-stall average
# left by a previous run to decay to zero. Otherwise this run's pressure onset
# (for time-to-first-park and Jain's window) is the last run's tail: on the rehearsal
# VM a run started 22 s after another had its onset about 24 s before any job
# stalled.
quiet() { awk '/^full/ { split($2, a, "="); exit !(a[2] + 0 == 0) }' /proc/pressure/memory; }
for waited in $(seq 0 180); do quiet && break; sleep 1; done
echo "$waited" > "$OUT/quiesce_s"

SCHED_PID= CTRL_PID= TP_PID= EO_PID= SAMPLER= TTL0=
cleanup() {
  [[ -n $SAMPLER ]] && kill "$SAMPLER" 2>/dev/null || true
  [[ -n $CTRL_PID ]] && kill -TERM "$CTRL_PID" 2>/dev/null && wait "$CTRL_PID" 2>/dev/null || true
  [[ -n $TP_PID ]] && kill -TERM "$TP_PID" 2>/dev/null && wait "$TP_PID" 2>/dev/null || true
  [[ -n $EO_PID ]] && kill -TERM "$EO_PID" 2>/dev/null && wait "$EO_PID" 2>/dev/null || true
  # Never leave a job frozen or stopped behind us. Thaw before stopping:
  # SIGTERM stays pending on a frozen task, so systemd would wait 90 s before
  # SIGKILL; and thrash-protect parks with SIGSTOP, which SIGCONT undoes.
  for f in /sys/fs/cgroup/stasis.slice/*/cgroup.freeze; do [[ -e $f ]] && echo 0 > "$f"; done 2>/dev/null || true
  systemctl kill -s CONT 'stasis-job-*' 2>/dev/null || true
  systemctl stop stasis-probe 'stasis-job-*' 2>/dev/null || true
  [[ -n $SCHED_PID ]] && kill -INT "$SCHED_PID" 2>/dev/null && wait "$SCHED_PID" 2>/dev/null || true
  [[ $CONFIG == oomd ]] && systemctl set-property --runtime stasis.slice ManagedOOMMemoryPressure=auto || true
  [[ -n $TTL0 ]] && echo "$TTL0" > "$MGLRU_TTL" || true
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
  mglru)
    # "N=1000 usually eliminates intolerable janks due to thrashing" (kernel
    # docs, multigen_lru); the OOM killer fires if that working set can't stay
    TTL0=$(cat "$MGLRU_TTL"); echo 1000 > "$MGLRU_TTL" ;;
  oomd)
    systemctl set-property --runtime stasis.slice ManagedOOMMemoryPressure=kill ;;
  simple)
    "$HOME_DIR/scx_simple" > "$OUT/scx.log" 2>&1 &
    SCHED_PID=$!; wait_sched simple ;;
  scxonly)
    "$HOME_DIR/scx_stasis" -p "$PROBE_PID" > "$OUT/scx.log" 2>&1 &
    SCHED_PID=$!; wait_sched stasis ;;
  thrashprotect)
    # Its defaults (swap-traffic trigger weighted up by PSI, OOM prediction on), plus the
    # measurement processes and remote shell on its whitelist so it doesn't
    # pick the probe. Against system services it parks with SIGSTOP (it
    # freezes cgroups only for user-session scopes such as tmux).
    "$THRASH_PROTECT" --version > "$OUT/tp_version.txt" 2>&1 || true
    "$THRASH_PROTECT" --use-psi --cmd-whitelist sshd bash sh dash sudo tmux screen \
      systemd systemd-journald cyclictest python3 > "$OUT/tp.log" 2>&1 &
    TP_PID=$! ;;
  earlyoom)
    # Arm 2. Kills on free-memory and free-swap percentages, not PSI, and
    # reports each kill on stderr. -r 0 silences its periodic report, so
    # eo.log holds kills only. --avoid keeps it off the probe and the
    # measurement processes, as thrash-protect's whitelist does above.
    "$EARLYOOM" --version > "$OUT/eo_version.txt" 2>&1 || true
    "$EARLYOOM" -m "${EARLYOOM_MEM:-10}" -s "${EARLYOOM_SWAP:-10}" -r 0 \
      --avoid '^(cyclictest|python3|sshd|systemd|systemd-journal)$' > "$OUT/eo.log" 2>&1 &
    EO_PID=$! ;;
  *) echo "unknown CONFIG $CONFIG" >&2; exit 2 ;;
esac

python3 "$HOME_DIR/sample.py" "$OUT/samples.jsonl" &
SAMPLER=$!
sleep 1

T0=$(date +%s.%N); echo "$T0" > "$OUT/t0"
for i in $(seq 1 "$N"); do
  jump=()
  [[ -n $JUMP ]] && (( i <= ${JUMP#*:} )) && jump=("${JUMP%:*}")
  if [[ $WORKLOAD == wss ]]; then
    job=("$HOME_DIR/wss" "$SIZE" "$PASSES" "$HOT" "${jump[@]}")
  else                                       # arm 3; HOT and JUMP don't apply
    job=("$HOME_DIR/workload.sh" "$WORKLOAD" "$SIZE" "$PASSES")
  fi
  systemd-run --unit="stasis-job-$i" --slice=stasis.slice \
    -p StandardOutput=file:"$OUT/job-$i.log" \
    "${job[@]}"
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
# earlyoom is not a unit here, so its own stderr is the only record of its
# kills. It sends SIGTERM first and escalates to SIGKILL only if the process
# survives (wss has no handler, so SIGTERM usually suffices): count each
# process once, by PID, whichever signal it took.
grep -oiE 'sending sig(term|kill) to process [0-9]+' "$OUT/eo.log" 2>/dev/null \
  | grep -oE '[0-9]+$' | sort -u > "$OUT/earlyoom_kills.txt" || true
journalctl -k --since "@${T0%.*}" | grep -i sched_ext | tail -20 > "$OUT/sched_ext_dmesg.txt" || true
echo "$STATUS" > "$OUT/status"
echo "done: $STATUS -> $OUT"
