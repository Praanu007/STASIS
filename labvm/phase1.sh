#!/usr/bin/env bash
# phase1.sh - inside the VM: calibrate PASSES (three times, to see the
# spread), then plan experiment A. Runs nothing from the plan.
set -euo pipefail
cd ~/stasis

# The demo is not campaign data: analyze.py treats every ROOT/*/N*/run* as a
# run, so it must not sit under the results root.
if [[ -d /srv/results/demo ]]; then
  sudo mkdir -p /srv/demo && sudo mv /srv/results/demo/* /srv/demo/ && sudo rmdir /srv/results/demo
  echo "demo moved to /srv/demo"
fi

# A quiet machine: no leftover jobs, PSI full avg10 back at 0, caches dropped.
systemctl list-units --plain --no-legend 'stasis-job-*' | grep -q . && { echo "jobs still running"; exit 1; }
for _ in $(seq 120); do awk '/^full/{split($2,a,"=");exit !(a[2]+0==0)}' /proc/pressure/memory && break; sleep 1; done
echo "PSI: $(grep full /proc/pressure/memory)"
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null

echo "== calibrate.sh 700, three times"
for i in 1 2 3; do
  scripts/calibrate.sh 700
  sleep 3
done

echo "== plan experiment A"
sudo eval/campaign.sh plan /srv/results --experiment A
echo "experiment: $(cat /srv/results/experiment)"
echo "plan by label:"; awk '{print $1}' /srv/results/plan.txt | sort | uniq -c | sort -k2
echo "first lines:"; head -5 /srv/results/plan.txt
