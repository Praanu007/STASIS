#!/usr/bin/env bash
# campaign.sh plan  RESULTS_ROOT [--sensitivity]   write RESULTS_ROOT/plan.txt
# campaign.sh run   RESULTS_ROOT SIZE_MB PASSES [MAX_RUNS]
#
# Phase 5 run matrix (about 140 runs of ~8 min, ~19 h of machine time):
#   eevdf          N=1..8   x5        stasis (full)  N=1..8   x5
#   ctrl           N=4,6,8  x5        oomd           N=4,6,8  x5
#   simple         N=4,6,8  x5        thrashprotect  N=4,6,8  x5
#   eevdf N=0 (unloaded probe reference) x5
# --sensitivity adds full STASIS at N=8 x3 for rotation quantum 5/15/30 s and
# trigger 50/100/200 ms per 1 s window.
#
# 'plan' shuffles the whole matrix once, so drift over time (thermals, host
# load) doesn't line up with one configuration. 'run' works through the plan
# in order, skipping finished runs, so it is resumable: roll back to the
# 'clean' snapshot, start a batch with MAX_RUNS, repeat.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cmd=$1 ROOT=$2
mkdir -p "$ROOT"

case $cmd in
plan)
  {
    # label config N run stasisd_args
    for k in 1 2 3 4 5; do
      for n in 1 2 3 4 5 6 7 8; do
        echo "eevdf eevdf $n $k -"
        echo "stasis stasis $n $k -"
      done
      for n in 4 6 8; do
        for c in ctrl oomd simple thrashprotect; do echo "$c $c $n $k -"; done
      done
      echo "eevdf eevdf 0 $k -"
    done
    if [[ ${3:-} == --sensitivity ]]; then
      for k in 1 2 3; do
        for q in 5 15 30; do echo "sens-q$q stasis 8 $k --quantum=$q"; done
        for t in 50 100 200; do echo "sens-t$t stasis 8 $k --trigger-ms=$t"; done
      done
    fi
  } | shuf > "$ROOT/plan.txt"
  echo "$(wc -l < "$ROOT/plan.txt") runs planned in $ROOT/plan.txt" ;;
run)
  SIZE=$3 PASSES=$4 MAX=${5:-1000000}
  [[ -f $ROOT/plan.txt ]] || { echo "run '$0 plan $ROOT' first" >&2; exit 1; }
  [[ ${STASISD_EXTRA:-} == *--pff-hi=* ]] || {
    echo "set STASISD_EXTRA=--pff-hi=<value frozen in docs/00-prereg.md>" >&2; exit 1; }
  done_now=0
  while read -r label config n k args; do
    out="$ROOT/$label/N$n/run$k"
    [[ -f $out/status ]] && continue
    (( done_now >= MAX )) && break
    [[ $args == - ]] && args=
    echo "== $(date +%T) $label N=$n run$k $args"
    sync; echo 3 > /proc/sys/vm/drop_caches
    "$HERE/../scripts/manifest.sh" "$out" "$label $args"
    STASISD_ARGS="$args" "$HERE/../scripts/run_mix.sh" "$n" "$SIZE" "$PASSES" "$out" "$config" || true
    done_now=$((done_now + 1))
  done < "$ROOT/plan.txt"
  left=$(while read -r label _ n k _; do [[ -f $ROOT/$label/N$n/run$k/status ]] || echo x; done < "$ROOT/plan.txt" | wc -l)
  echo "batch done: $done_now runs this batch, $left left" ;;
*) echo "usage: $0 plan|run ..." >&2; exit 2 ;;
esac
