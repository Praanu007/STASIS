#!/usr/bin/env bash
# Gate 3 in miniature: no memory pressure (3 x 250 MB, every page hot), the
# scheduler alone (scxonly) against EEVDF, two runs each, alternating.
set -u
exec > >(tee -a /root/gate3mini.log) 2>&1
cd /root/stasis
for k in 1 2; do
  for cfg in eevdf scxonly; do
    out=/srv/gate3mini/$cfg/N3/run$k
    rm -rf "$out"
    echo "== $(date +%T) $cfg run$k"
    STASIS_TIMEOUT=900 scripts/run_mix.sh 3 250 800 "$out" "$cfg"
    echo "== $(date +%T) $cfg run$k done: $(cat $out/status)"
  done
done
echo "== $(date +%T) all done"
sync
