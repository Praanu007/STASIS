#!/usr/bin/env bash
# campaign.sh plan  RESULTS_ROOT [--experiment A|B|C] [--runs K] [--sensitivity]
#                   [--earlyoom] [--workloads] [--controller-arms]
# campaign.sh run   RESULTS_ROOT SIZE_MB PASSES [MAX_RUNS]
#
# Phase 5 run matrix. Each experiment gets its own RESULTS_ROOT (for example
# /srv/results and /srv/results-skew); 'plan' records the experiment in
# RESULTS_ROOT/experiment, which 'analyze.py score' uses to pick hypotheses.
#
# Experiment A, the thrashing curve (every page hot, HOT = 100):
#   eevdf          N=1..8          stasis (full)   N=1..8
#   ctrl           N=4,6,8         mglru           N=4,6,8
#   oomd           N=4,6,8         thrashprotect   N=4,6,8
#   stasis-norot   N=8  (stasis --no-rotation: the fairness ablation)
#   stasis-fp      N=8  (stasis --ws=footprint: with every page hot, measuring
#                        can't change a decision, so this isolates its cost)
#   eevdf N=0 (unloaded probe reference)
#   --sensitivity adds full STASIS at N=8 x3 for quantum 5/15/30 s,
#   starvation bound 60/120/240 s and PSI trigger 50/100/200 ms of full
#   stall per 1 s window (H10 in docs/00-prereg.md).
# Experiment B, measured working sets (60% of each job's memory hot, HOT = 60):
#   eevdf, stasis, stasis-fp (stasis --ws=footprint)   N=4,6,8
# Experiment C, a working-set jump (H5c): 8 jobs, 40% hot (their hot sets
# fit); after 60% of PASSES, jobs 1-4 make every page hot, so the extra demand
# arrives as refaults of swapped-out pages, the onset PFF is meant to catch:
#   eevdf, ctrl, stasis   N=8
# K runs per cell (default 5): A is 155 runs (182 with --sensitivity), B is
# 45, C is 15.
#
# Arms 2-7 (registered 2026-10-24 in docs/01-prereg-arms.md) extend
# experiment A only, and only ever append runs: the matrix above is unchanged.
#   --earlyoom         earlyoom baseline, N=4,6,8                     (arm 2)
#   --workloads        eevdf/stasis/ctrl/thrashprotect at N=4,6,8 under
#                      rocksdb and memcached instead of wss           (arm 3)
#   --controller-arms  full STASIS at N=6,8 with one controller option each:
#                      dterm (arm 4), damon (arm 5), predict (arm 6). dterm
#                      needs STASIS_DERIV_HI and predict needs STASIS_MODEL
#                      at PLAN time: like PFF_HI a threshold is frozen before
#                      the runs, so the value is written into plan.txt and
#                      planning refuses without it. damon needs a
#                      DAMON-enabled kernel, which stasisd checks at start.
#                      Arm 7 (criu) is not planned: it is not yet runnable
#                      under run_mix.sh (see controller/stasisd.py).
# A plan line is "label config N run hot args [workload]". args is a single
# token ('-' for none); workload defaults to wss when the field is absent, so
# plans written before 2026-10-24 still run unchanged.
#
# 'plan' shuffles the whole matrix once, so drift over time (thermals, host
# load) doesn't line up with one configuration. 'run' works through the plan
# in order, skipping finished runs, so it is resumable: reboot, start a batch
# with MAX_RUNS, repeat.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cmd=$1 ROOT=$2
mkdir -p "$ROOT"

case $cmd in
plan)
  shift 2
  EXP=A K=5 SENS= EOOM= WLS= CARMS=
  while (( $# )); do
    case $1 in
      --experiment) EXP=$2; shift ;;
      --runs) K=$2; shift ;;
      --sensitivity) SENS=1 ;;
      --earlyoom) EOOM=1 ;;
      --workloads) WLS=1 ;;
      --controller-arms) CARMS=1 ;;
      *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
  done
  [[ $EXP == A || $EXP == B || $EXP == C ]] || { echo "experiment must be A, B or C" >&2; exit 2; }
  if [[ -n $EOOM$WLS$CARMS && $EXP != A ]]; then
    echo "--earlyoom, --workloads and --controller-arms extend experiment A only" >&2; exit 2
  fi
  # Validate everything before writing anything. The plan is built in a
  # subshell (the { } | shuf below), where a failed check would only end the
  # subshell and leave a truncated plan behind for 'run' to execute.
  if [[ -n $CARMS ]]; then
    : "${STASIS_DERIV_HI:?--controller-arms needs STASIS_DERIV_HI, frozen in docs/01-prereg-arms.md}"
    : "${STASIS_MODEL:?--controller-arms needs STASIS_MODEL, a model that passed train_predictor.py}"
    [[ -f $STASIS_MODEL ]] || { echo "STASIS_MODEL $STASIS_MODEL not found" >&2; exit 1; }
    [[ $STASIS_MODEL != *[[:space:]]* ]] || { echo "STASIS_MODEL path must not contain spaces" >&2; exit 1; }
    python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("sign_check",{}).get("passed") else 1)' \
      "$STASIS_MODEL" || { echo "STASIS_MODEL failed train_predictor.py's sign check; retrain it" >&2; exit 1; }
  fi
  echo "$EXP" > "$ROOT/experiment"
  # Built in a temporary file and renamed, so a failure part-way can never
  # leave a partial plan where 'run' would find it.
  tmp="$ROOT/plan.txt.tmp"
  {
    # label config N run hot stasisd_args
    for k in $(seq 1 "$K"); do
      if [[ $EXP == A ]]; then
        for n in 1 2 3 4 5 6 7 8; do
          echo "eevdf eevdf $n $k 100 -"
          echo "stasis stasis $n $k 100 -"
        done
        for n in 4 6 8; do
          for c in ctrl mglru oomd thrashprotect; do echo "$c $c $n $k 100 -"; done
        done
        echo "stasis-norot stasis 8 $k 100 --no-rotation"
        echo "stasis-fp stasis 8 $k 100 --ws=footprint"
        echo "eevdf eevdf 0 $k 100 -"
      elif [[ $EXP == B ]]; then
        for n in 4 6 8; do
          echo "eevdf eevdf $n $k 60 -"
          echo "stasis stasis $n $k 60 -"
          echo "stasis-fp stasis $n $k 60 --ws=footprint"
        done
      else
        for c in eevdf ctrl stasis; do echo "$c $c 8 $k 40 -"; done
      fi
    done
    if [[ $EXP == A && -n $SENS ]]; then
      for k in 1 2 3; do
        for q in 5 15 30; do echo "sens-q$q stasis 8 $k 100 --quantum=$q"; done
        for w in 60 120 240; do echo "sens-w$w stasis 8 $k 100 --max-wait=$w"; done
        for t in 50 100 200; do echo "sens-t$t stasis 8 $k 100 --trigger-ms=$t"; done
      done
    fi
    # Arms 2-6, experiment A only (checked above); every page hot, as in A.
    if [[ -n $EOOM ]]; then
      for k in $(seq 1 "$K"); do
        for n in 4 6 8; do echo "earlyoom earlyoom $n $k 100 -"; done
      done
    fi
    if [[ -n $WLS ]]; then
      for k in $(seq 1 "$K"); do
        for w in rocksdb memcached; do
          for n in 4 6 8; do
            for c in eevdf stasis ctrl thrashprotect; do echo "$w-$c $c $n $k 100 - $w"; done
          done
        done
      done
    fi
    if [[ -n $CARMS ]]; then
      for k in $(seq 1 "$K"); do
        for n in 6 8; do
          echo "dterm stasis $n $k 100 --derivative-hi=$STASIS_DERIV_HI"
          echo "damon stasis $n $k 100 --ws=damon"
          echo "predict stasis $n $k 100 --predict=$STASIS_MODEL"
        done
      done
    fi
  } | shuf > "$tmp"
  mv -f "$tmp" "$ROOT/plan.txt"
  echo "experiment $EXP: $(wc -l < "$ROOT/plan.txt") runs planned in $ROOT/plan.txt" ;;
run)
  SIZE=$3 PASSES=$4 MAX=${5:-1000000}
  [[ -f $ROOT/plan.txt ]] || { echo "run '$0 plan $ROOT' first" >&2; exit 1; }
  JUMP=
  [[ $(cat "$ROOT/experiment" 2>/dev/null) == C ]] && JUMP="$((PASSES * 6 / 10)):4"   # jobs 1-4 jump
  [[ ${STASISD_EXTRA:-} == *--pff-hi=* ]] || {
    echo "set STASISD_EXTRA=--pff-hi=<value frozen in docs/00-prereg.md>" >&2; exit 1; }
  done_now=0
  while read -r label config n k hot args wl; do
    out="$ROOT/$label/N$n/run$k"
    if [[ -f $out/status ]]; then
      [[ $(cat "$out/status") != RUNNING ]] && continue
      echo "rerun unfinished $out"; rm -rf "$out"
    fi
    (( done_now >= MAX )) && break
    [[ $args == - ]] && args=
    wl=${wl:-wss}
    echo "== $(date +%T) $label N=$n run$k hot=$hot $args workload=$wl"
    sync; echo 3 > /proc/sys/vm/drop_caches
    # </dev/null: nothing inside a run may read (and swallow) lines of plan.txt
    "$HERE/../scripts/manifest.sh" "$out" "$label $args $wl" < /dev/null
    STASIS_HOT=$hot STASIS_JUMP=$JUMP STASISD_ARGS="$args" STASIS_WORKLOAD="$wl" \
      "$HERE/../scripts/run_mix.sh" "$n" "$SIZE" "$PASSES" "$out" "$config" < /dev/null || true
    done_now=$((done_now + 1))
  done < "$ROOT/plan.txt"
  left=$(while read -r label _ n k _ _; do
           f=$ROOT/$label/N$n/run$k/status
           [[ -f $f && $(cat "$f") != RUNNING ]] || echo x
         done < "$ROOT/plan.txt" | wc -l)
  echo "batch done: $done_now runs this batch, $left left" ;;
*) echo "usage: $0 plan|run ..." >&2; exit 2 ;;
esac
