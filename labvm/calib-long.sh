#!/usr/bin/env bash
# calib-long.sh [RUNS] - calibrate.sh's formula on a long enough sample.
# calibrate.sh times 3 passes, about 60 ms on this VM, which ms timestamps and
# noise swamp. Same arithmetic: per_pass = (last t - first t) / last pass;
# PASSES = 60 / per_pass. One warm-up run is discarded first.
set -euo pipefail
SIZE=700 N=600 TARGET=60 RUNS=${1:-5}
out=$(mktemp)
for i in $(seq 0 "$RUNS"); do
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  log=$(mktemp)
  /opt/stasis/wss "$SIZE" "$N" > "$log"
  awk -v t="$TARGET" -v i="$i" 'NR==1 {t0=$2} END {pp=($2-t0)/$1; n=int(t/pp+0.5);
       if (i == 0) printf "warm-up (discarded): %.5f s/pass -> PASSES=%d\n", pp, n;
       else        printf "run %d: %.2f s for %d passes -> %.5f s/pass -> PASSES=%d\n", i, $2-t0, $1, pp, n}' "$log" | tee -a "$out"
  rm -f "$log"
  sleep 3
done
grep '^run' "$out" | awk '{p=$NF; sub("PASSES=","",p); v[NR]=p; s+=p} END {
  n=NR; asort(v); m=s/n; for(i=1;i<=n;i++) ss+=(v[i]-m)^2; sd=sqrt(ss/(n-1));
  med=(n%2)?v[(n+1)/2]:(v[n/2]+v[n/2+1])/2;
  printf "\nPASSES over %d runs: min %d, median %d, max %d, mean %.0f, sd %.0f\n", n, v[1], med, v[n], m, sd;
  printf "spread (max-min)/median: %.1f%%   coefficient of variation: %.1f%%\n", 100*(v[n]-v[1])/med, 100*sd/m }'
rm -f "$out"
