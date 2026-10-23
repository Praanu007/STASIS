#!/usr/bin/env bash
# preflight.sh - go/no-go: can this kernel and VM run STASIS and its baselines?
# Run as root before building anything else. It checks each layer's
# prerequisites, tries the kernel interfaces for real (in a throwaway cgroup),
# and prints which parts of the evaluation can run here:
#
#   layers 1-3, the controller   PSI, cgroup v2 memory/cpu, freezer, swap,
#                                memory.reclaim. Without these, nothing runs.
#   measured working sets        /proc/PID/clear_refs + smaps_rollup. Without
#                                them stasisd falls back to footprints.
#   layer 4, the scheduler       sched_ext, BPF, BTF. Without it, the
#                                controller still runs under EEVDF (ctrl).
#   baselines                    MGLRU min_ttl_ms, systemd-oomd, thrash-protect;
#                                DAMON_RECLAIM is reported, not required.
#
# Exit status: 0 if the controller can run here, 1 if not.
set -uo pipefail
ok=0 warn=0 bad=0
pass() { printf '  [ok]   %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; warn=$((warn + 1)); }
fail() { printf '  [FAIL] %s\n' "$1"; bad=$((bad + 1)); }
rd() { cat "$1" 2>/dev/null; }
config() {   # config NAME: y, m, n (not set), or nothing if no config is readable
  local f=/boot/config-$(uname -r) v
  if [[ -r $f ]]; then v=$(sed -n "s/^CONFIG_$1=//p" "$f")
  elif [[ -r /proc/config.gz ]]; then v=$(zcat /proc/config.gz | sed -n "s/^CONFIG_$1=//p")
  else return; fi
  echo "${v:-n}"
}
verdict() { printf '  => %-32s %s\n' "$1" "$2"; }

[[ $EUID == 0 ]] || { echo "run as root"; exit 1; }
echo "kernel $(uname -r), $(nproc) CPUs, $(awk '/MemTotal/ {printf "%.1f GiB RAM", $2/1048576}' /proc/meminfo)"

echo "Layers 1-3: sensing, load control, parking"
L123=GO
if [[ $(stat -fc %T /sys/fs/cgroup) == cgroup2fs ]]; then pass "cgroup v2 at /sys/fs/cgroup"
else fail "/sys/fs/cgroup is not cgroup v2"; L123=NO-GO; fi
for c in memory cpu; do
  grep -qw $c /sys/fs/cgroup/cgroup.controllers 2>/dev/null && pass "cgroup $c controller" \
    || { fail "no cgroup $c controller"; L123=NO-GO; }
done
if [[ -r /proc/pressure/memory ]]; then
  if python3 - <<'EOF' 2>/dev/null
import os
fd = os.open("/proc/pressure/memory", os.O_RDWR | os.O_NONBLOCK)
os.write(fd, b"full 100000 1000000\0")      # the trigger stasisd registers
EOF
  then pass "PSI memory trigger (100 ms per 1 s window)"
  else fail "can't register a PSI trigger on /proc/pressure/memory"; L123=NO-GO; fi
else fail "no /proc/pressure/memory (boot with psi=1)"; L123=NO-GO; fi
swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if (( swap_kb >= 8000000 )); then pass "swap $((swap_kb / 1024)) MiB"
elif (( swap_kb > 0 )); then warn "swap is only $((swap_kb / 1024)) MiB (setup_vm.sh makes 8 GiB)"
else fail "no swap: parked jobs' memory has nowhere to go"; L123=NO-GO; fi
[[ $(rd /sys/module/zswap/parameters/enabled) == Y ]] && warn "zswap is on (setup_vm.sh turns it off)" || pass "zswap off"

# Measured working sets: clear a real process's referenced bits, read them back.
REFS=GO
sleep 30 & sp=$!
if echo 1 > "/proc/$sp/clear_refs" 2>/dev/null && grep -q '^Referenced:' "/proc/$sp/smaps_rollup"; then
  pass "clear_refs + smaps_rollup Referenced (measured working sets)"
else warn "no clear_refs/smaps_rollup Referenced: working sets fall back to footprints"; REFS=NO-GO; fi
kill "$sp" 2>/dev/null; wait "$sp" 2>/dev/null

# Try the per-cgroup interfaces in a throwaway cgroup with a real process
# (only on a real cgroup v2 mount: anywhere else mkdir makes a plain directory).
CGT=/sys/fs/cgroup/stasis-preflight
if [[ $(stat -fc %T /sys/fs/cgroup) != cgroup2fs ]]; then
  fail "skipped the freezer and memory.reclaim trials: no cgroup v2"; L123=NO-GO
elif mkdir "$CGT" 2>/dev/null || [[ -d $CGT ]]; then
  sleep 30 & sp=$!
  echo "$sp" > "$CGT/cgroup.procs"
  for f in cgroup.freeze memory.reclaim memory.peak memory.swap.current memory.pressure memory.stat cpu.stat; do
    [[ -e $CGT/$f ]] && pass "cgroup file $f" || { fail "no cgroup file $f"; L123=NO-GO; }
  done
  grep -q '^user_usec' "$CGT/cpu.stat" 2>/dev/null && pass "cpu.stat user_usec" || { fail "no user_usec in cpu.stat"; L123=NO-GO; }
  echo 1 > "$CGT/cgroup.freeze"
  for _ in $(seq 20); do grep -q '^frozen 1' "$CGT/cgroup.events" && break; sleep 0.1; done
  if grep -q '^frozen 1' "$CGT/cgroup.events"; then pass "freezer: froze a test process"
  else fail "freezer: cgroup.events never said frozen"; L123=NO-GO; fi
  echo 0 > "$CGT/cgroup.freeze"
  echo 1M > "$CGT/memory.reclaim" 2>/dev/null; rc=$?
  # EAGAIN (nothing to reclaim from a sleeping process) still proves it exists
  (( rc == 0 )) && pass "memory.reclaim accepts writes" || pass "memory.reclaim answered (nothing to reclaim)"
  kill "$sp" 2>/dev/null; wait "$sp" 2>/dev/null
  rmdir "$CGT" 2>/dev/null || true
else fail "can't create a test cgroup under /sys/fs/cgroup"; L123=NO-GO; fi

echo "Layer 4: the scheduler"
L4=GO
for c in SCHED_CLASS_EXT BPF_SYSCALL DEBUG_INFO_BTF; do
  v=$(config $c)
  case $v in
    y) pass "CONFIG_$c=y" ;;
    "") warn "CONFIG_$c unknown (no kernel config readable)" ;;
    *) fail "CONFIG_$c=$v"; L4=NO-GO ;;
  esac
done
[[ -e /sys/kernel/sched_ext/state ]] && pass "sched_ext state: $(rd /sys/kernel/sched_ext/state)" \
  || { fail "no /sys/kernel/sched_ext: kernel lacks CONFIG_SCHED_CLASS_EXT"; L4=NO-GO; }
[[ -r /sys/kernel/btf/vmlinux ]] && pass "BTF at /sys/kernel/btf/vmlinux" || { fail "no /sys/kernel/btf/vmlinux"; L4=NO-GO; }
command -v clang >/dev/null && pass "clang $(clang --version | sed -n 's/.*version \([0-9.]*\).*/\1/p' | head -1)" \
  || warn "no clang yet (setup_vm.sh installs it)"
command -v bpftool >/dev/null && pass "bpftool $(bpftool version 2>/dev/null | awk 'NR==1 {print $2}')" \
  || warn "no bpftool yet (setup_vm.sh installs it)"

echo "Baselines"
mg=$(rd /sys/kernel/mm/lru_gen/enabled)
if [[ -n $mg && -w /sys/kernel/mm/lru_gen/min_ttl_ms ]] && (( mg & 1 )); then
  MG=GO; pass "MGLRU on ($mg), min_ttl_ms writable"
else MG=NO-GO; warn "MGLRU off or no min_ttl_ms: the mglru baseline can't run"; fi
if systemctl is-active --quiet systemd-oomd 2>/dev/null; then OOMD=GO; pass "systemd-oomd running"
else OOMD=NO-GO; warn "systemd-oomd not running (setup_vm.sh enables it)"; fi
TP=${THRASH_PROTECT:-/opt/thrash-protect/bin/thrash-protect}
if [[ -x $TP ]]; then TPV=GO; pass "thrash-protect at $TP"
else TPV=NO-GO; warn "no thrash-protect at $TP (setup_vm.sh installs 1.1.2)"; fi
if [[ -d /sys/module/damon_reclaim ]]; then pass "DAMON_RECLAIM available (not part of the plan)"
else pass "DAMON_RECLAIM not in this kernel (CONFIG_DAMON=$(config DAMON)): not a baseline here"; fi

echo "Measurement"
for t in cyclictest stress-ng; do
  command -v $t >/dev/null && pass "$t" || warn "no $t yet (setup_vm.sh installs it)"
done

echo "Verdict"
verdict "layers 1-3 (controller)" "$L123"
verdict "measured working sets" "$REFS"
verdict "layer 4 (sched_ext)" "$L4$( [[ $L4 == NO-GO && $L123 == GO ]] && echo ': run the controller under EEVDF (ctrl)' )"
verdict "baseline mglru" "$MG"
verdict "baseline oomd" "$OOMD"
verdict "baseline thrash-protect" "$TPV"
echo "$bad failed, $warn warnings"
[[ $L123 == GO ]]
