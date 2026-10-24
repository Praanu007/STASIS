#!/usr/bin/env bash
# Pilot 4: pilot 3's workload again, full STASIS with PFF_HI 1.0 instead of
# 0.029, and a second controller-only run for run-to-run noise.
set -u
exec > >(tee -a /root/pilotC4.log) 2>&1
cd /root/stasis
for spec in "stasis --pff-hi=1.0" "ctrl --pff-hi=1.0"; do
  cfg=${spec%% *} extra=${spec#* }
  out=/srv/pilotC3/$cfg/N8/run2
  rm -rf "$out"
  echo "== $(date +%T) $cfg $extra"
  STASIS_HOT=30 STASIS_JUMP=480:4 STASISD_EXTRA=$extra STASIS_TIMEOUT=1800 \
    scripts/run_mix.sh 8 250 800 "$out" "$cfg"
  echo "== $(date +%T) $cfg done: $(cat $out/status)"
done
echo "== $(date +%T) all done"
sync
