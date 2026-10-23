#!/usr/bin/env bash
# manifest.sh OUTDIR CONFIG - record everything needed to reproduce one run.
# Writes OUTDIR/MANIFEST and appends a one-line summary to the repo MANIFEST.
set -euo pipefail
OUT=$1 CONFIG=$2
REPO=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$OUT"
rd() { cat "$1" 2>/dev/null || echo n/a; }
{
  echo "date:          $(date -Is)"
  echo "config:        $CONFIG"
  echo "kernel:        $(uname -r)"
  echo "stasis commit: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo n/a)$(git -C "$REPO" diff --quiet 2>/dev/null || echo -dirty)"
  echo "scx commit:    $(git -C "${SCX_DIR:-$HOME/scx}" rev-parse --short HEAD 2>/dev/null || echo n/a)"
  echo "zswap:         $(rd /sys/module/zswap/parameters/enabled)"
  echo "mglru:         $(rd /sys/kernel/mm/lru_gen/enabled) min_ttl_ms=$(rd /sys/kernel/mm/lru_gen/min_ttl_ms)"
  echo "thrash-prot:   $(/opt/thrash-protect/bin/pip show thrash-protect 2>/dev/null | sed -n 's/^Version: //p' || echo n/a)"
  echo "thp:           $(rd /sys/kernel/mm/transparent_hugepage/enabled)"
  echo "swappiness:    $(rd /proc/sys/vm/swappiness)"
  echo "swap:          $(swapon --show=NAME,SIZE --noheadings | paste -sd' ')"
  echo "mem total:     $(awk '/MemTotal/ {print $2" kB"}' /proc/meminfo)"
  echo "cpus:          $(nproc)"
  echo "sched_ext:     $(rd /sys/kernel/sched_ext/state)"
} > "$OUT/MANIFEST"
echo "$(date -Is) $CONFIG $OUT kernel=$(uname -r) $(sed -n 's/^stasis commit: *//p' "$OUT/MANIFEST")" >> "$REPO/MANIFEST"
