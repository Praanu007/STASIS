#!/usr/bin/env bash
# redo-eevdf.sh - inside the VM, started detached. Deviation 23: set the 45
# barrier-free EEVDF runs aside (kept, unscored), redo experiment A's EEVDF
# runs under the start barrier, then calibrate PFF_HI from them.
set -euo pipefail
ROOT=/srv/results PASSES=5874 SIZE=700
OLD=/srv/superseded/nobarrier
cd ~/stasis
echo "start $(date -Is); repo $(git rev-parse --short HEAD)"
[[ $(cat "$ROOT/experiment") == A ]] || { echo "not experiment A"; exit 1; }
pgrep -f 'dashboard/server.py' >/dev/null && { echo "dashboard running: refusing"; exit 1; }

if [[ -d $ROOT/eevdf ]]; then
  sudo mkdir -p "$OLD"
  sudo mv "$ROOT/eevdf" "$OLD/eevdf"
  for f in metrics.csv NOTE-dashboard-during-runs.txt; do [[ -f $ROOT/$f ]] && sudo mv "$ROOT/$f" "$OLD/$f"; done
  echo "The 45 EEVDF runs of experiment A made before deviation 23 (no start barrier)." | sudo tee "$OLD/README" >/dev/null
  echo "Kept and unscored; see docs/00-prereg.md, deviation 23." | sudo tee -a "$OLD/README" >/dev/null
  echo "old runs moved to $OLD"
fi

sudo cp "$ROOT/plan.txt" "$ROOT/plan.full.txt"
restore() { sudo cp "$ROOT/plan.full.txt" "$ROOT/plan.txt"; echo "full plan restored ($(wc -l < "$ROOT/plan.txt") lines)"; }
trap restore EXIT
grep '^eevdf ' "$ROOT/plan.full.txt" | sudo tee "$ROOT/plan.txt" > /dev/null
echo "EEVDF-only plan: $(wc -l < "$ROOT/plan.txt") runs, with the start barrier (experiment A)"

# EEVDF never starts stasisd; the value is deliberately unparseable.
sudo STASISD_EXTRA=--pff-hi=NOT-CALIBRATED-eevdf-only eval/campaign.sh run "$ROOT" "$SIZE" "$PASSES"

echo "== analyze.py metrics + calibrate"
sudo python3 eval/analyze.py metrics "$ROOT"
sudo python3 eval/analyze.py calibrate "$ROOT" --config eevdf | tee ~/pff_calibration.txt
echo "REDO DONE $(date -Is)"
