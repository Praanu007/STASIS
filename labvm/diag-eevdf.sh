#!/usr/bin/env bash
# diag-eevdf.sh - inside the VM, started detached. Diagnostic EEVDF runs at
# N=8, OUTSIDE the campaign: results go to /srv/diag, never /srv/results, and
# nothing is frozen. Question: why does EEVDF not collapse on this VM (jobs
# run in waves instead)? One factor changed per condition:
#   control    nothing changed
#   mglru-off  MGLRU disabled  (/sys/kernel/mm/lru_gen/enabled = n)
#   slowswap   stasis.slice disk I/O capped at 3 MB/s each way, emulating the
#              rehearsal VM's ~3 MB/s swap. Swap I/O is charged to the job's
#              cgroup (cgroup v2 io controller), so this throttles the jobs'
#              swap, not the sampler or the system.
# Every change is undone by the EXIT trap, and both are runtime-only anyway.
set -uo pipefail
ROOT=/srv/diag SIZE=700 PASSES=5874 N=8
cd ~/stasis
DEV=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")         # disk holding / (and /swapfile)
MGLRU0=$(cat /sys/kernel/mm/lru_gen/enabled)

restore() {
  echo "restoring: MGLRU -> $MGLRU0, stasis.slice I/O limits -> none"
  if [[ $MGLRU0 == 0x0000 ]]; then echo n; else echo y; fi | sudo tee /sys/kernel/mm/lru_gen/enabled >/dev/null
  sudo systemctl set-property --runtime stasis.slice IOReadBandwidthMax= IOWriteBandwidthMax=
  echo "now: MGLRU $(cat /sys/kernel/mm/lru_gen/enabled), io.max '$(cat /sys/fs/cgroup/stasis.slice/io.max 2>/dev/null)'"
}
trap restore EXIT

pgrep -f 'dashboard/server.py' >/dev/null && { echo "dashboard running: refusing"; exit 1; }
systemctl list-units --plain --no-legend 'stasis-job-*' | grep -q . && { echo "jobs running: refusing"; exit 1; }
echo "start $(date -Is); disk /dev/$DEV; MGLRU was $MGLRU0"

run() {  # run CONDITION K
  local out="$ROOT/$1/N$N/run$2"
  # Owned by this user, so the run_mix.log redirect below (made by this
  # shell, not by sudo) can write into it; run_mix.sh itself runs as root.
  sudo rm -rf "$out"; sudo mkdir -p "$out"; sudo chown "$(id -un)" "$out"
  {
    echo "condition: $1"
    echo "mglru: $(cat /sys/kernel/mm/lru_gen/enabled)"
    echo "stasis.slice io.max: $(cat /sys/fs/cgroup/stasis.slice/io.max 2>/dev/null)"
    echo "date: $(date -Is)"
  } | sudo tee "$out/CONDITION" >/dev/null
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  echo "== $(date +%T) $1 run$2  ($(tr '\n' ' ' < "$out/CONDITION"))"
  sudo scripts/run_mix.sh "$N" "$SIZE" "$PASSES" "$out" eevdf < /dev/null > "$out/run_mix.log" 2>&1
  echo "   -> $(cat "$out/status")"
}

run control 1

echo n | sudo tee /sys/kernel/mm/lru_gen/enabled >/dev/null
run mglru-off 1
run mglru-off 2
if [[ $MGLRU0 == 0x0000 ]]; then echo n; else echo y; fi | sudo tee /sys/kernel/mm/lru_gen/enabled >/dev/null

sudo systemctl set-property --runtime stasis.slice IOAccounting=yes \
  "IOReadBandwidthMax=/dev/$DEV 3M" "IOWriteBandwidthMax=/dev/$DEV 3M"
run slowswap 1
run slowswap 2
sudo systemctl set-property --runtime stasis.slice IOReadBandwidthMax= IOWriteBandwidthMax=

echo "== analyze.py metrics on /srv/diag"
sudo python3 eval/analyze.py metrics "$ROOT"
echo "DIAG DONE $(date -Is)"
