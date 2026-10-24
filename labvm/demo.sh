#!/usr/bin/env bash
# demo.sh - inside the VM: start the Control Room, then one DEMO run of full
# STASIS for the Live view. Not a result: PFF_HI is the placeholder 1.0 (not
# yet calibrated, docs/00-prereg.md) and the run is filed under demo/.
set -euo pipefail
cd ~/stasis
sudo mkdir -p /srv/results/demo
if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:8686/api/info; then
  sudo setsid nohup python3 dashboard/server.py --results /srv/results \
    > ~/dashboard.log 2>&1 < /dev/null &
  for _ in $(seq 20); do curl -s -o /dev/null --max-time 1 http://127.0.0.1:8686/api/info && break; sleep 0.5; done
fi
echo "dashboard: $(curl -s --max-time 3 http://127.0.0.1:8686/api/info)"
OUT=/srv/results/demo/N7/run1
sudo rm -rf "$OUT"
sudo STASISD_EXTRA=--pff-hi=1.0 setsid nohup scripts/run_mix.sh 7 700 60 "$OUT" stasis \
  > ~/demo-run.log 2>&1 < /dev/null &
sleep 8
echo "run status: $(cat $OUT/status 2>/dev/null || echo starting)"
echo "live link -> $(readlink /run/stasis/current 2>/dev/null || echo none yet)"
tail -3 ~/demo-run.log 2>/dev/null || true
