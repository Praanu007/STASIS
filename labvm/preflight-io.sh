#!/usr/bin/env bash
# Does a 3 MB/s cap on stasis.slice actually take effect? Applies it, shows the
# kernel's view, then removes it.
set -uo pipefail
DEV=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")
echo "disk /dev/$DEV ($(cat /sys/block/$DEV/dev)); controllers before: $(cat /sys/fs/cgroup/stasis.slice/cgroup.controllers)"
sudo systemctl set-property --runtime stasis.slice IOAccounting=yes \
  "IOReadBandwidthMax=/dev/$DEV 3M" "IOWriteBandwidthMax=/dev/$DEV 3M"
echo "controllers after:  $(cat /sys/fs/cgroup/stasis.slice/cgroup.controllers)"
echo "io.max applied:     '$(cat /sys/fs/cgroup/stasis.slice/io.max 2>&1)'"
# Prove it throttles: a job-like unit in the slice writes 30 MB with O_DIRECT.
sudo systemd-run --quiet --wait --slice=stasis.slice -p StandardOutput=journal --unit=io-probe \
  dd if=/dev/zero of=/var/tmp/io-probe bs=1M count=30 oflag=direct 2>&1 | tail -1
sudo journalctl -u io-probe --no-pager -o cat | tail -1
sudo rm -f /var/tmp/io-probe
sudo systemctl set-property --runtime stasis.slice IOReadBandwidthMax= IOWriteBandwidthMax=
echo "io.max after reset: '$(cat /sys/fs/cgroup/stasis.slice/io.max 2>&1)'"
