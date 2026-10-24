#!/usr/bin/env bash
# zram_tier.sh on [SIZE_MB] | off | status        (arm 10, 2026-10-24)
#
# A compressed in-RAM swap tier in front of the 8 GB swap file: the honest,
# single-VM stand-in for tiered memory. CXL tiering cannot be evaluated on one
# 4 GB VM against TPP, HybridTier, xTier and TierBPF, and the thesis should not
# pretend otherwise. What a zram tier *can* test is the question those systems
# raise for load control: does a fast second tier make thrashing control
# obsolete, or does the control problem survive it?
#
# There is a real, citable phenomenon to measure here. thrash-protect's own
# PR #42 reports a zram laptop that "froze processes 37 + 4 times in eleven
# hours without real pressure", because a fault-counting signal cannot tell a
# cheap zram fault from an expensive disk one. PSI measures lost time, not
# fault events, so STASIS should not misfire the same way. That is a
# falsifiable difference between the two baselines, on one machine.
#
# Arm 1 runs with zswap OFF (setup_vm.sh disables it, and Gate 0 checks it).
# This script must never be left on for an arm-1 run: manifest.sh writes a
# "zram tier:" line into every run's MANIFEST, which must read "off" for
# arm 1, and analyze.py's results are only comparable within one memory
# configuration.
set -euo pipefail
cmd=${1:-status}
SIZE=${2:-2048}

status() {
  if [[ -e /sys/block/zram0/disksize ]] && swapon --show=NAME --noheadings | grep -q zram0; then
    echo "zram tier ON: $(( $(cat /sys/block/zram0/disksize) / 1048576 )) MB," \
         "algorithm $(sed 's/.*\[\(.*\)\].*/\1/' /sys/block/zram0/comp_algorithm)"
    swapon --show
  else
    echo "zram tier OFF"
  fi
}

case $cmd in
on)
  modprobe zram num_devices=1
  if swapon --show=NAME --noheadings | grep -q zram0; then
    echo "already on"; status; exit 0
  fi
  echo 1 > /sys/block/zram0/reset 2>/dev/null || true
  # zstd if the kernel has it: the usual default for a memory tier, and the
  # ratio is what decides how much "free" memory the tier appears to add.
  grep -q zstd /sys/block/zram0/comp_algorithm && echo zstd > /sys/block/zram0/comp_algorithm
  echo "${SIZE}M" > /sys/block/zram0/disksize
  mkswap /dev/zram0 >/dev/null
  # Higher priority than the swap file, so the kernel fills zram first and only
  # spills to disk past it: a two-tier hierarchy, fast then slow.
  swapon -p 100 /dev/zram0
  status ;;
off)
  swapoff /dev/zram0 2>/dev/null || true
  echo 1 > /sys/block/zram0/reset 2>/dev/null || true
  status ;;
status) status ;;
*) echo "usage: $0 on [SIZE_MB] | off | status" >&2; exit 2 ;;
esac
