#!/usr/bin/env bash
# Why do the jobs barely swap? Look at every limit between a job and the swap file.
echo "=== system ==="
echo "swappiness:        $(cat /proc/sys/vm/swappiness)"
echo "swap:              $(swapon --show=NAME,SIZE,USED,PRIO --noheadings | paste -sd' ')"
echo "MGLRU enabled:     $(cat /sys/kernel/mm/lru_gen/enabled)   min_ttl_ms: $(cat /sys/kernel/mm/lru_gen/min_ttl_ms)"
echo "overcommit:        $(cat /proc/sys/vm/overcommit_memory)"
echo
echo "=== cgroup limits on the path to a job ==="
for c in /sys/fs/cgroup /sys/fs/cgroup/stasis.slice /sys/fs/cgroup/stasis.slice/stasis-job-1.service; do
  [[ -d $c ]] || { echo "$c: absent (no job running?)"; continue; }
  printf '%-52s swap.max=%-10s memory.max=%-10s memory.high=%-10s zswap.max=%s\n' "$c" \
    "$(cat $c/memory.swap.max 2>/dev/null || echo -)" "$(cat $c/memory.max 2>/dev/null || echo -)" \
    "$(cat $c/memory.high 2>/dev/null || echo -)" "$(cat $c/memory.zswap.max 2>/dev/null || echo -)"
done
echo
echo "=== what systemd sets on the slice and a job unit ==="
systemctl show stasis.slice -p MemorySwapMax -p MemoryMax -p MemoryHigh -p ManagedOOMSwap -p ManagedOOMMemoryPressure 2>&1
systemctl show stasis-job-1.service -p MemorySwapMax -p MemoryMax -p MemoryHigh -p MemoryZSwapMax 2>&1
echo
echo "=== a running job's own memory ==="
j=/sys/fs/cgroup/stasis.slice/stasis-job-1.service
[[ -d $j ]] && grep -E '^(anon|file|swapcached|pgmajfault|workingset_refault_anon|workingset_refault_file) ' $j/memory.stat
[[ -d $j ]] && echo "memory.swap.current: $(cat $j/memory.swap.current)  memory.current: $(cat $j/memory.current)"
echo
echo "=== kernel's reclaim counters since boot ==="
grep -E '^(pswpin|pswpout|pgsteal_anon|pgsteal_file|workingset_refault_file|allocstall_normal|oom_kill) ' /proc/vmstat
