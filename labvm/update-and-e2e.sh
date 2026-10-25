#!/usr/bin/env bash
# update-and-e2e.sh - inside the VM: move the repo to the deviation-23 branch,
# rebuild wss, and test the barrier end to end through run_mix.sh (small runs,
# in /srv/diag, not the campaign).
set -euo pipefail
cd ~/stasis
git fetch -q -f ~/stasis-dev23.bundle deviation/start-barrier:refs/remotes/b/dev23
git checkout -q -f -B deviation/start-barrier b/dev23       # drops the CR-stripped working copy...
git ls-files -z -- '*.sh' '*.py' '*.c' | xargs -0 sed -i 's/\r$//'   # ...and strips again
echo "repo now $(git rev-parse --short HEAD) on $(git branch --show-current)"
make -s -C workloads >/dev/null 2>&1 || cc -O2 -o workloads/wss workloads/wss.c
sudo install -m 755 workloads/wss /opt/stasis/wss
grep -q STASIS_GO workloads/wss.c && strings /opt/stasis/wss | grep -q STASIS_GO && echo "installed wss has the barrier"

pgrep -f 'dashboard/server.py' >/dev/null && { echo "dashboard running: refusing"; exit 1; }
for c in ${E2E_CONFIGS:-eevdf ctrl}; do
  out=/srv/diag/e2e/$c/N2/run1
  sudo rm -rf "$out"
  sudo STASIS_BARRIER=1 scripts/run_mix.sh 2 700 300 "$out" "$c" > /dev/null 2>&1 || true
  t0=$(cat $out/t0); tl=$(cat $out/t_launch 2>/dev/null || echo none)
  first_ctl=$( { sudo head -1 $out/decisions.jsonl 2>/dev/null || true; } | python3 -c 'import json,sys; l=sys.stdin.read(); print(json.loads(l)["t"] if l else "none")')
  echo "-- $c: status $(cat $out/status); $(cat $out/barrier.txt)"
  echo "   config: $(cat $out/config)"
  echo "   t_launch $tl < t0 $t0: $(awk -v a=$tl -v b=$t0 'BEGIN{print (a<b)?"yes":"NO"}')"
  [[ $first_ctl != none ]] && echo "   controller's first log at $first_ctl, after all filled: $(awk -v a=$tl -v c=$first_ctl -v b=$t0 'BEGIN{print (c>=a && c<=b+1)?"yes":"check"}')"
  echo "   passes: $(for f in $out/job-*.log; do tail -1 $f | cut -d' ' -f1; done | paste -sd' ')"
done
echo "go files left behind: $(ls /run/stasis/go-* 2>/dev/null | wc -l)"
echo "E2E DONE"
