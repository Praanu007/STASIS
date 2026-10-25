#!/usr/bin/env bash
# install-all.sh - inside the VM: install and start stasis-all.service.
# It idles until redo-eevdf.sh is done and PFF_HI is in /etc/stasis-all.env.
set -euo pipefail
cd ~
sed -i 's/\r$//' stasis-all.sh stasis-all.service
chmod +x stasis-all.sh
sudo cp stasis-all.service /etc/systemd/system/stasis-all.service
sudo systemctl daemon-reload
sudo systemctl enable --now stasis-all.service
sleep 2
systemctl is-active stasis-all.service
