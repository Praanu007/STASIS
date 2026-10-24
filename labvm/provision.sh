#!/usr/bin/env bash
# provision.sh - run inside the lab VM as user stasis. Clones the repo from
# the bundle copied in (the GitHub repo is private), fixes line endings in the
# working copy only, then runs the repo's own Phase 0 provisioning and Gate 0.
set -euo pipefail
cd ~
if [[ ! -d stasis ]]; then
  git clone -q -b features/novelty-ten ~/stasis.bundle stasis
  git -C stasis remote set-url origin https://github.com/Praanu007/STASIS.git
fi
cd ~/stasis
echo "repo at $(git rev-parse --short HEAD) on $(git branch --show-current)"

# Every .sh/.py file is committed with CRLF line endings, which bash and the
# kernel's #! loader reject on Linux. Strip them in this working copy only:
# nothing is committed, so manifest.sh will record the commit as -dirty.
git ls-files -z -- '*.sh' '*.py' | xargs -0 sed -i 's/\r$//'
echo "line endings fixed in $(git ls-files -- '*.sh' '*.py' | wc -l) files (working copy only)"

echo "== setup_vm.sh (Phase 0)"
sudo scripts/setup_vm.sh
echo "== check_gate0.sh"
sudo scripts/check_gate0.sh || echo "GATE0 EXIT $?"
echo "PROVISION DONE"
