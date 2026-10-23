#!/usr/bin/env bash
# Pilot for experiment C on kernel 7.0: 6 jobs, 50% hot (2.1 GB of hot sets
# fit); jobs 1-3 jump to 100% hot after pass 50 (3.15 GB do not).
set -u
exec > >(tee -a /root/pilotC.log) 2>&1
cd /root/stasis
echo "== $(date +%T) install"
cc -O2 -Wall -o /opt/stasis/wss workloads/wss.c
install -m 755 controller/stasisd.py /opt/stasis/stasisd.py
md5sum /opt/stasis/stasisd.py controller/stasisd.py
for cfg in ${CFGS:-stasis ctrl}; do
  out=/srv/pilotC/$cfg/N6/run${RUN:-1}
  rm -rf "$out"
  echo "== $(date +%T) $cfg"
  STASIS_HOT=50 STASIS_JUMP=50:3 STASISD_EXTRA=--pff-hi=0.029 STASIS_TIMEOUT=1500 \
    scripts/run_mix.sh 6 700 100 "$out" "$cfg"
  echo "== $(date +%T) $cfg done: $(cat $out/status)"
done
echo "== $(date +%T) all done"
sync
