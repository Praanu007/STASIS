#!/bin/bash
# Live-view check for the Control Room in WSL (run as root).
#   start [N]  start N fake jobs (default 3) in stasis.slice; each is held to
#              300 MB but keeps touching 500 MB, so it swaps
#   stop       stop them
set -u
case ${1:-start} in
start)
    for i in $(seq 1 "${2:-3}"); do
        systemctl stop "stasis-job-$i" 2>/dev/null
        systemctl reset-failed "stasis-job-$i" 2>/dev/null
        systemd-run --quiet --slice=stasis.slice --unit="stasis-job-$i" \
            -p MemoryMax=300M -p MemorySwapMax=1G \
            python3 -c 'b = bytearray(500 << 20)
while True:
    for j in range(0, len(b), 4096): b[j] ^= 1'
    done
    systemctl list-units 'stasis-job-*' --no-legend --plain | awk '{print $1, $4}'
    echo "Watch http://127.0.0.1:8686 (Live). Stop with: live-test.sh stop"
    ;;
run)
    # run MINUTES [N]: start, show a countdown, stop (also on Ctrl-C)
    mins=${2:-10}
    trap 'systemctl stop "stasis-job-*"; echo; echo "stopped"; exit 0' INT TERM
    bash "$0" start "${3:-3}"
    for ((s = mins * 60; s > 0; s--)); do
        printf '\r%02d:%02d left (Ctrl-C stops early) ' $((s / 60)) $((s % 60))
        sleep 1
    done
    systemctl stop 'stasis-job-*'
    echo; echo "stopped after $mins min"
    ;;
stop)
    systemctl stop 'stasis-job-*'
    echo "stopped"
    ;;
*)
    echo "usage: live-test.sh start [N] | run [MINUTES] [N] | stop" >&2; exit 2
    ;;
esac
