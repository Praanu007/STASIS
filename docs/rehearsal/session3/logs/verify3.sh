#!/usr/bin/env bash
# Second bounded session on the kernel 7.0 VM: the paced, stall-gated
# working-set sampler and the corrected starvation bound. HOT 60 pairs twice
# (measured vs footprint, interleaved), full STASIS at HOT 100, then the
# controller tests. Logs to /root/verify3, runs to /srv/v3, then powers off.
set -u
R=/root/stasis V=/root/verify3 OUT=/srv/v3
mkdir -p $V $OUT
exec > >(tee -a $V/verify.log) 2>&1
step() { echo; echo "== $(date +%T) $*"; }
cd $R
step "install"
install -m 755 controller/stasisd.py /opt/stasis/stasisd.py
md5sum /opt/stasis/stasisd.py tests/test_controller.sh
PFF=$(sed -n 's/^suggested PFF_HI ~ \([0-9.]*\).*/\1/p' /root/calibrate.txt)
export STASISD_EXTRA="--pff-hi=${PFF:-0.029}"
run() {   # run LABEL N HOT TIMEOUT CONFIG [STASISD_ARGS]
  step "run $1: N=$2 hot=$3% $5 ${6:-}"
  rm -rf "$OUT/$1"
  STASIS_HOT=$3 STASIS_TIMEOUT=$4 STASISD_ARGS="${6:-}" scripts/run_mix.sh "$2" 700 60 "$OUT/$1" "$5" < /dev/null
  echo "status $(cat $OUT/$1/status); passes $(cat $OUT/$1/job-*.log 2>/dev/null | awk '$1 > 0' | wc -l)"
}
run stasis-hot60-r1   7 60  900 stasis
run stasisfp-hot60-r1 7 60  900 stasis --ws=footprint
run stasis-hot60-r2   7 60  900 stasis
run stasisfp-hot60-r2 7 60  900 stasis --ws=footprint
run stasis-hot100     7 100 900 stasis
step "controller tests on this kernel"
tests/test_controller.sh
step "done"
sync
poweroff
