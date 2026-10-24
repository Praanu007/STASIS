#!/usr/bin/env bash
# Pilot 3 for experiment C, scaled to a 1.5 GB VM so reloads take seconds on
# emulated swap: 8 jobs of 250 MB, 30% hot (600 MB of hot sets fit); after
# 60% of 800 passes jobs 1-4 make every page hot (1.3 GB doesn't fit).
set -u
exec > >(tee -a /root/pilotC3.log) 2>&1
cd /root/stasis
free -m
for cfg in ${CFGS:-stasis ctrl}; do
  out=/srv/pilotC3/$cfg/N8/run${RUN:-1}
  rm -rf "$out"
  echo "== $(date +%T) $cfg"
  STASIS_HOT=30 STASIS_JUMP=480:4 STASISD_EXTRA=--pff-hi=0.029 STASIS_TIMEOUT=1800 \
    scripts/run_mix.sh 8 250 800 "$out" "$cfg"
  echo "== $(date +%T) $cfg done: $(cat $out/status)"
done
echo "== $(date +%T) all done"
sync
