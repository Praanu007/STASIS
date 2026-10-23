#!/usr/bin/env bash
# check_gate0.sh - Phase 0 exit criteria. Run as root. Exits non-zero on failure.
set -uo pipefail
fail=0
ok()  { printf '  [ok]   %s\n' "$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; fail=1; }
rd()  { cat "$1" 2>/dev/null; }

# Every layer's prerequisites, tried for real (scripts/preflight.sh)
"$(dirname "$0")/preflight.sh" || bad "preflight: the controller can't run on this machine"
[[ -e /sys/kernel/sched_ext/state ]] || bad "no sched_ext: Gate 0 needs the scheduler too"
swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
(( swap_kb >= 8000000 )) || bad "swap is only $((swap_kb / 1024)) MB (want 8 GB)"
[[ $(rd /sys/module/zswap/parameters/enabled) != Y ]] || bad "zswap is on"
echo "  info   MGLRU=$(rd /sys/kernel/mm/lru_gen/enabled) THP=$(rd /sys/kernel/mm/transparent_hugepage/enabled) swappiness=$(rd /proc/sys/vm/swappiness)"

for b in wss scx_simple scx_stasis sample.py stasisd.py; do
  [[ -x /opt/stasis/$b ]] && ok "/opt/stasis/$b" || bad "/opt/stasis/$b missing (run setup_vm.sh)"
done

if [[ -x /opt/stasis/scx_simple && -e /sys/kernel/sched_ext/state ]]; then
  /opt/stasis/scx_simple > /dev/null 2>&1 & pid=$!
  for _ in $(seq 25); do [[ $(rd /sys/kernel/sched_ext/state) == enabled ]] && break; sleep 0.2; done
  [[ $(rd /sys/kernel/sched_ext/state) == enabled && $(rd /sys/kernel/sched_ext/root/ops) == simple ]] \
    && ok "scx_simple loads (state=enabled, ops=simple)" || bad "scx_simple did not attach"
  kill -INT $pid; wait $pid 2>/dev/null
  sleep 0.5
  [[ $(rd /sys/kernel/sched_ext/state) == disabled ]] && ok "Ctrl-C returns to EEVDF" || bad "sched_ext still $(rd /sys/kernel/sched_ext/state)"
fi

echo "Reminder: take the VM snapshot named 'clean' after this passes."
exit $fail
