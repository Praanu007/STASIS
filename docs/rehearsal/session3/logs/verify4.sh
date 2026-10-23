#!/usr/bin/env bash
# Third bounded session: the final controller's tests on kernel 7.0, then power off.
set -u
V=/root/verify4
mkdir -p $V
exec > >(tee -a $V/verify.log) 2>&1
cd /root/stasis
echo "== $(date +%T) install"
install -m 755 controller/stasisd.py /opt/stasis/stasisd.py
md5sum /opt/stasis/stasisd.py controller/stasisd.py tests/test_controller.sh
echo "== $(date +%T) controller tests on this kernel"
tests/test_controller.sh
echo "== $(date +%T) done"
sync
poweroff
