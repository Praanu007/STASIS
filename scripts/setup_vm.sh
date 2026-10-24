#!/usr/bin/env bash
# setup_vm.sh - Phase 0 provisioning for the Ubuntu guest. Run as root, once.
# Idempotent: safe to re-run after a snapshot rollback.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
SCX_DIR=${SCX_DIR:-/root/scx}
SCX_COMMIT=${SCX_COMMIT:-370b1cea95c6bb668d5b9261f069d433142b9986}   # pinned scx-c-examples
TP_VERSION=${TP_VERSION:-1.1.2}                                        # pinned thrash-protect (PyPI)
SRC=/root/src

echo "== packages"
apt-get update
apt-get install -y build-essential clang llvm libbpf-dev libelf-dev \
  zlib1g-dev libzstd-dev pkg-config git python3 python3-matplotlib \
  python3-numpy python3-venv stress-ng rt-tests systemd-oomd \
  linux-tools-common "linux-tools-$(uname -r)" || \
apt-get install -y build-essential clang llvm libbpf-dev libelf-dev \
  zlib1g-dev libzstd-dev pkg-config git python3 python3-matplotlib \
  python3-numpy python3-venv stress-ng rt-tests systemd-oomd linux-tools-common

echo "== 8 GB swap file (replaces Ubuntu's /swap.img)"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  swapoff -a
  sed -i '/swap.img/d' /etc/fstab
  [[ -f /swapfile ]] || { fallocate -l 8G /swapfile; chmod 600 /swapfile; mkswap /swapfile; }
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== zswap must be off"
if [[ $(cat /sys/module/zswap/parameters/enabled 2>/dev/null) == Y ]]; then
  echo N > /sys/module/zswap/parameters/enabled
  if ! grep -q 'zswap.enabled=0' /etc/default/grub; then
    sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="/&zswap.enabled=0 /' /etc/default/grub
    update-grub
  fi
fi

echo "== libbpf + bpftool new enough for scx-c-examples"
# scx-c-examples needs libbpf >= 1.5 and bpftool >= 7.5 (skeletons with
# struct_ops members). Ubuntu 26.04 ships both, with bpftool as its own
# package. Ubuntu 24.04 ships libbpf 1.3 / bpftool 7.4: build both from source.
apt-get install -y bpftool 2>/dev/null || true
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}
bpftool_version() { bpftool version 2>/dev/null | awk 'NR == 1 { sub(/^v/, "", $2); print $2 }'; }
if ! pkg-config --atleast-version=1.5 libbpf 2>/dev/null ||
   ! dpkg --compare-versions "$(bpftool_version)" ge 7.5 2>/dev/null; then
  mkdir -p "$SRC"
  [[ -d $SRC/bpftool ]] || git clone --depth 1 --recurse-submodules --shallow-submodules \
    https://github.com/libbpf/bpftool "$SRC/bpftool"
  make -C "$SRC/bpftool/libbpf/src" -j"$(nproc)"
  make -C "$SRC/bpftool/libbpf/src" install PREFIX=/usr/local LIBDIR=/usr/local/lib
  make -C "$SRC/bpftool/src" -j"$(nproc)"
  install -m 755 "$SRC/bpftool/src/bpftool" /usr/local/bin/bpftool
  ldconfig
  hash -r
fi
echo "libbpf $(pkg-config --modversion libbpf), bpftool $(bpftool_version) at $(command -v bpftool)"

echo "== scx-c-examples at $SCX_COMMIT + scx_stasis"
[[ -d $SCX_DIR ]] || git clone https://github.com/sched-ext/scx-c-examples "$SCX_DIR"
git -C "$SCX_DIR" checkout -q "$SCX_COMMIT"
"$REPO/scx/install_into_scx.sh" "$SCX_DIR"
make -C "$SCX_DIR" BPFTOOL="$(command -v bpftool)" scx_simple scx_stasis

echo "== install to /opt/stasis"
make -C "$REPO/workloads"
install -d /opt/stasis /var/log/stasis /run/stasis
install -m 755 "$REPO/workloads/wss" "$SCX_DIR/build/scheds/c/scx_simple" \
  "$SCX_DIR/build/scheds/c/scx_stasis" /opt/stasis/
install -m 755 "$REPO/scripts/sample.py" "$REPO/controller/stasisd.py" /opt/stasis/
# run_mix.sh runs arm 3's jobs from /opt/stasis (added 2026-10-24); arm 1 never calls it
install -m 755 "$REPO/scripts/workload.sh" /opt/stasis/

echo "== baselines: systemd-oomd running, thrash-protect $TP_VERSION in its own venv"
systemctl enable --now systemd-oomd
if [[ $(/opt/thrash-protect/bin/pip show thrash-protect 2>/dev/null | sed -n 's/^Version: //p') != "$TP_VERSION" ]]; then
  python3 -m venv /opt/thrash-protect
  /opt/thrash-protect/bin/pip install --quiet "thrash-protect==$TP_VERSION"
fi
/opt/thrash-protect/bin/thrash-protect --help > /dev/null

echo "== stasis.slice (persistent so oomd properties can be set on it)"
cat > /etc/systemd/system/stasis.slice <<'EOF'
[Unit]
Description=STASIS batch jobs
[Slice]
MemoryAccounting=yes
CPUAccounting=yes
EOF
systemctl daemon-reload

echo "== done. Now run: sudo $REPO/scripts/check_gate0.sh"
