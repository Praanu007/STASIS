#!/usr/bin/env bash
# run-eevdf.sh - inside the VM: experiment A's EEVDF runs only, then the
# PFF_HI calibration. Meant to be started detached (setsid nohup).
#
# campaign.sh runs its whole plan, so it is given an EEVDF-only plan for the
# duration, in the same shuffled order, and the full plan is restored after
# (finished runs are skipped later, so nothing runs twice). Running EEVDF
# first is the design, not a deviation: PFF_HI must come from these runs
# before any STASIS run, so the full shuffle can only cover the rest.
set -euo pipefail
ROOT=/srv/results PASSES=5874 SIZE=700
cd ~/stasis
echo "start $(date -Is)"
sudo cp "$ROOT/plan.txt" "$ROOT/plan.full.txt"
restore() { sudo cp "$ROOT/plan.full.txt" "$ROOT/plan.txt"; echo "full plan restored ($(wc -l < "$ROOT/plan.txt") lines)"; }
trap restore EXIT
grep '^eevdf ' "$ROOT/plan.full.txt" | sudo tee "$ROOT/plan.txt" > /dev/null
echo "EEVDF-only plan: $(wc -l < "$ROOT/plan.txt") runs"

# campaign.sh refuses to run without --pff-hi. No EEVDF run starts stasisd, so
# the flag is unused; it is set to a value stasisd cannot parse, so a STASIS
# run that slipped in would fail loudly instead of using a made-up threshold.
sudo STASISD_EXTRA=--pff-hi=NOT-CALIBRATED-eevdf-only eval/campaign.sh run "$ROOT" "$SIZE" "$PASSES"

echo "== analyze.py metrics + calibrate"
sudo python3 eval/analyze.py metrics "$ROOT"
sudo python3 eval/analyze.py calibrate "$ROOT" --config eevdf | tee ~/pff_calibration.txt
echo "EEVDF SWEEP DONE $(date -Is)"
