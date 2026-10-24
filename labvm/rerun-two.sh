#!/usr/bin/env bash
# rerun-two.sh - inside the VM, started detached. Waits for run-eevdf.sh to
# finish, then re-runs the two campaign runs that were made while the
# dashboard was on (NOTE-dashboard-during-runs.txt), with it off, and
# recalibrates PFF_HI on the full set of 45 EEVDF runs.
set -euo pipefail
ROOT=/srv/results PASSES=5874 SIZE=700
RERUN="eevdf/N0/run4 eevdf/N7/run1"
cd ~/stasis

echo "waiting for run-eevdf.sh to finish ($(date -Is))"
while pgrep -f run-eevdf.sh >/dev/null; do sleep 30; done
echo "sweep finished ($(date -Is))"

if pgrep -f 'dashboard/server.py' >/dev/null; then echo "dashboard is running: refusing"; exit 1; fi

sudo cp "$ROOT/plan.txt" "$ROOT/plan.full.txt"
restore() { sudo cp "$ROOT/plan.full.txt" "$ROOT/plan.txt"; echo "full plan restored ($(wc -l < "$ROOT/plan.txt") lines)"; }
trap restore EXIT
grep '^eevdf ' "$ROOT/plan.full.txt" | sudo tee "$ROOT/plan.txt" > /dev/null

for r in $RERUN; do
  echo "removing $r (status was $(cat "$ROOT/$r/status" 2>/dev/null || echo none))"
  sudo rm -rf "${ROOT:?}/$r"
done

# Every other EEVDF run is finished, so campaign.sh runs only these two. The
# --pff-hi value is unused by EEVDF runs and deliberately unparseable.
sudo STASISD_EXTRA=--pff-hi=NOT-CALIBRATED-eevdf-only eval/campaign.sh run "$ROOT" "$SIZE" "$PASSES"

echo "== analyze.py metrics + calibrate (all 45 EEVDF runs)"
sudo python3 eval/analyze.py metrics "$ROOT"
sudo python3 eval/analyze.py calibrate "$ROOT" --config eevdf | tee ~/pff_calibration.txt
{
  echo
  echo "$(date -Is): $RERUN were deleted and re-run with the dashboard off,"
  echo "so all 45 EEVDF runs used for PFF_HI were made under the README's rule."
} | sudo tee -a "$ROOT/NOTE-dashboard-during-runs.txt" >/dev/null
echo "RERUN DONE $(date -Is)"
