#!/usr/bin/env bash
# stop-dashboard.sh - inside the VM: stop the Control Room for the rest of the
# campaign, and record which campaign runs it was running during. The README
# says to keep it off during batches so it can't perturb measurements; it was
# on from the demo into the first EEVDF runs.
set -uo pipefail
now=$(date -Is)
pids=$(pgrep -f 'dashboard/server.py' || true)
if [[ -n $pids ]]; then
  sudo kill $pids; sleep 2
  pgrep -f 'dashboard/server.py' >/dev/null && sudo kill -9 $(pgrep -f 'dashboard/server.py')
  echo "dashboard stopped at $now (was pid $pids)"
else
  echo "dashboard was not running at $now"
fi
started=$(stat -c %y ~/dashboard.log 2>/dev/null | cut -d. -f1)

note=/srv/results/NOTE-dashboard-during-runs.txt
{
  echo "The Control Room (dashboard/server.py) was running in this VM from"
  echo "$started (started for a demo run) until $now, when it was stopped for"
  echo "the rest of the campaign. Up to two browsers were streaming from it."
  echo "Every campaign run that started before it was stopped is listed below."
  echo "Its load is small, but these runs were not made under the README's"
  echo "rule (dashboard off during batches). Re-run them, or report this."
  echo
  echo "run                    status    started (UTC)"
  for d in /srv/results/*/N*/run*; do
    [[ -f $d/t0 ]] || continue
    t0=$(date -u -d "@$(cut -d. -f1 "$d/t0")" +%FT%T)
    printf '%-22s %-9s %s\n' "${d#/srv/results/}" "$(cat "$d/status" 2>/dev/null)" "$t0"
  done | sort -k3
} | sudo tee "$note"
echo
echo "sweep still running: $(pgrep -f run-eevdf.sh >/dev/null && echo yes || echo NO)"
echo "current run: $(readlink /run/stasis/current 2>/dev/null)"
