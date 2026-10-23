#!/usr/bin/env bash
# Pilot 2 for experiment C: 6 jobs of 800 MB, 40% hot (1.9 GB of hot sets
# fit). Jobs 1-4 jump to 100% hot after pass 250 of 400, late enough that
# every job has run and been measured; 4 x 800 + 2 x 320 MB = 3.8 GB doesn't
# fit. full STASIS against controller-only.
set -u
exec > >(tee -a /root/pilotC2.log) 2>&1
cd /root/stasis
for cfg in ${CFGS:-stasis ctrl}; do
  out=/srv/pilotC2/$cfg/N6/run${RUN:-1}
  rm -rf "$out"
  echo "== $(date +%T) $cfg"
  STASIS_HOT=40 STASIS_JUMP=250:4 STASISD_EXTRA=--pff-hi=0.029 STASIS_TIMEOUT=1800 \
    scripts/run_mix.sh 6 800 400 "$out" "$cfg"
  echo "== $(date +%T) $cfg done: $(cat $out/status)"
done
echo "== $(date +%T) all done"
sync
