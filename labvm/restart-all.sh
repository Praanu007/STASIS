#!/usr/bin/env bash
# restart-all.sh - inside the VM: stop stasis-all and the run in flight,
# put back the N7 run5 result the old sleep check set aside by mistake,
# reinstall the new script and unit, and start again. The run in flight
# keeps status RUNNING, so campaign.sh runs it again.
set -uo pipefail
cd ~
sed -i 's/\r$//' stasis-all.sh stasis-all.service
chmod +x stasis-all.sh
sudo systemctl stop stasis-all.service
# [x]-style patterns so pkill can't match its own sudo parent
sudo pkill -f '[e]val/campaign.sh run'; sudo pkill -f '[s]cripts/run_mix.sh'
sudo pkill -f '[/]opt/stasis/sample.py'; sudo pkill -f '[c]yclictest'
sudo systemctl kill -s CONT 'stasis-job-*' 2>/dev/null
sudo systemctl stop stasis-probe 'stasis-job-*' 2>/dev/null
sudo rm -f /run/stasis/go-*
# The second N7 run5 was a real 1200 s TIMEOUT, not a sleep: restore it.
src=$(ls -d /srv/superseded/host-sleep/results/eevdf/N7/run5.1792941060 2>/dev/null)
if [[ -n $src ]]; then
  sudo rm -rf /srv/results/eevdf/N7/run5
  sudo mv "$src" /srv/results/eevdf/N7/run5
  echo "restored N7 run5 ($(cat /srv/results/eevdf/N7/run5/status))"
fi
for f in $(find /srv/results -name status); do
  [[ $(cat "$f") == RUNNING ]] && echo "will rerun: ${f%/status}"
done
sudo cp stasis-all.service /etc/systemd/system/stasis-all.service
sudo systemctl daemon-reload
sudo systemctl start stasis-all.service
systemctl is-active stasis-all.service
