#!/usr/bin/env bash
# Bounded verification of the revised STASIS on the kernel 7.0 rehearsal VM.
# Runs once, logs to /root/verify, writes runs to /srv/v2, then powers off.
set -u
R=/root/stasis V=/root/verify OUT=/srv/v2
mkdir -p $V $OUT
exec > >(tee -a $V/verify.log) 2>&1
step() { echo; echo "== $(date +%T) $*"; }
set -a; . /etc/environment; set +a          # the egress proxy for apt and pip

step "install the revised build"
cd $R
make -C workloads > /dev/null && install -m 755 workloads/wss /opt/stasis/
install -m 755 controller/stasisd.py scripts/sample.py /opt/stasis/
scx/install_into_scx.sh /root/scx > /dev/null
make -C /root/scx BPFTOOL="$(command -v bpftool)" scx_stasis 2>&1 | tail -1
install -m 755 /root/scx/build/scheds/c/scx_stasis /opt/stasis/
apt-get install -y -q systemd-oomd python3-venv 2>&1 | tail -1
systemctl enable --now systemd-oomd
[[ -x /opt/thrash-protect/bin/thrash-protect ]] || {
  python3 -m venv /opt/thrash-protect
  /opt/thrash-protect/bin/pip install --quiet thrash-protect==1.1.2
}
/opt/thrash-protect/bin/thrash-protect --version

step "preflight"
scripts/preflight.sh; echo "preflight exit $?"

step "rung 1 + 3: scx_stasis loads; probe latency under 8 CPU hogs, EEVDF vs scx_stasis"
stress-ng --cpu 8 --timeout 140 > /dev/null 2>&1 & SS=$!
sleep 3
cyclictest --policy=other -i 10000 -D 60 -q > $V/lat_eevdf.txt 2>&1
cyclictest --policy=other -i 10000 -D 60 -q > $V/lat_scx.txt 2>&1 & CP=$!
sleep 1
/opt/stasis/scx_stasis -p "$CP" > $V/scx_rung3.log 2>&1 & SP=$!
sleep 3; echo "sched_ext: $(cat /sys/kernel/sched_ext/state) ops=$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null)"
wait $CP
kill -INT $SP; wait $SP; kill $SS 2>/dev/null; wait $SS 2>/dev/null
echo "EEVDF:      $(grep -o 'Min:.*' $V/lat_eevdf.txt)"
echo "scx_stasis: $(grep -o 'Min:.*' $V/lat_scx.txt)"
tail -3 $V/scx_rung3.log
journalctl -k --since "-5 min" | grep -i sched_ext | tail -5

PFF=$(sed -n 's/^suggested PFF_HI ~ \([0-9.]*\).*/\1/p' /root/calibrate.txt)
export STASISD_EXTRA="--pff-hi=${PFF:-0.029}"
run() {   # run LABEL N HOT TIMEOUT CONFIG [STASISD_ARGS]
  step "run $1: N=$2 hot=$3% $5 ${6:-}"
  rm -rf "$OUT/$1"
  STASIS_HOT=$3 STASIS_TIMEOUT=$4 STASISD_ARGS="${6:-}" scripts/run_mix.sh "$2" 700 60 "$OUT/$1" "$5" < /dev/null
  echo "status $(cat $OUT/$1/status); passes $(cat $OUT/$1/job-*.log 2>/dev/null | awk '$1 > 0' | wc -l)"
}
run stasis-hot60   7 60  900 stasis
run stasisfp-hot60 7 60  900 stasis --ws=footprint
run thrashprotect  7 100 300 thrashprotect
run mglru          7 100 300 mglru

step "controller tests on this kernel (slow machine)"
tests/test_controller.sh

run stasis-hot100  7 100 900 stasis

step "done"
sync
poweroff
