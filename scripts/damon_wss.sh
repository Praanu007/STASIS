#!/usr/bin/env bash
# damon_wss.sh [INTERVAL_S]   - working-set sizes from DAMON, for arm 5
#
# Writes "<bytes> <unix_time>" to /run/stasis/damon/<job> for each job in
# stasis.slice, which stasisd --ws=damon reads. A reading older than five
# seconds is ignored by the controller, which falls back to that job's
# footprint, so a stall here degrades the estimate rather than breaking it.
#
# Needs a DAMON-enabled kernel. Ubuntu 26.04's stock kernel is built without
# DAMON, and stasisd --ws=damon refuses to start without it, so on the stock
# lab VM arm 5 does not run at all rather than silently becoming a footprint
# run.
#
# Why this is a separate process. stasisd is mlockall'd and must stay on its
# 100 ms loop; DAMON's sysfs handshake involves writes, a commit and a read
# back, which is not work for the control loop. The file is swapped in
# atomically with rename() so the controller never reads a half-written value.
#
# What DAMON gives you that memory.current does not: it reports the memory
# actually *accessed* in its monitoring window, which is a working set in
# Denning's sense. resident+swapped is an upper bound on it. For a job that
# touches all of its allocation (wss does, by construction) the two agree; for
# a real workload they can differ a lot, and that difference is what arm 5 is
# for.
#
# Known accuracy caveat, which belongs in the thesis: DAMON reads page
# Accessed bits without forcing a TLB flush, so a working set small enough to
# sit in the TLB can be reported as idle. A DAMON selftest patch from January
# 2026 raised its own test size to 160 MiB for exactly this reason. At
# SIZE=700 MB this is unlikely to bite, but it is a real limit of the
# estimator and must not be presented as exact.
set -euo pipefail
INTERVAL=${1:-1}
CG=${STASIS_CG:-/sys/fs/cgroup/stasis.slice}
OUT=${STASIS_DAMON:-/run/stasis/damon}
SYSFS=/sys/kernel/mm/damon/admin/kdamonds

[[ -d $SYSFS ]] || { echo "DAMON sysfs not present: needs CONFIG_DAMON_SYSFS" >&2; exit 1; }
mkdir -p "$OUT"

# One kdamond per job would be cleanest but DAMON's sysfs allows a limited
# number; instead monitor each job's address space in turn through damo when it
# is available, which is the supported interface and handles the handshake.
command -v damo >/dev/null 2>&1 ||
  { echo "needs 'damo' (pip install damo) to drive DAMON; see its docs" >&2; exit 1; }

cleanup() { rm -f "$OUT"/*.tmp 2>/dev/null || true; }
trap cleanup EXIT

while true; do
  for d in "$CG"/stasis-job-*; do
    [[ -d $d ]] || continue
    j=$(basename "$d")
    pid=$(head -1 "$d/cgroup.procs" 2>/dev/null || true)
    [[ -n ${pid:-} ]] || continue
    # One short monitoring pass over this pid's address space. The working set
    # is the sum of (region size x nonzero access rate) regions DAMON reports.
    bytes=$(timeout 5 damo report heatmap --input_pid "$pid" --output_type json 2>/dev/null \
            | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(0); raise SystemExit
tot = 0
for r in d.get("regions", []):
    if r.get("nr_accesses", 0) > 0:
        tot += max(0, int(r.get("end", 0)) - int(r.get("start", 0)))
print(tot)
' 2>/dev/null || echo 0)
    [[ ${bytes:-0} -gt 0 ]] || continue
    printf '%s %s\n' "$bytes" "$(date +%s.%N)" > "$OUT/$j.tmp"
    mv -f "$OUT/$j.tmp" "$OUT/$j"            # atomic: the controller never sees a partial line
  done
  sleep "$INTERVAL"
done
