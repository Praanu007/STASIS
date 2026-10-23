#!/usr/bin/env bash
# install_into_scx.sh SCX_DIR - drop scx_stasis into a scx-c-examples checkout
# and register it in both Makefiles (wherever scx_simple appears). Idempotent.
set -euo pipefail
SCX=${1:?usage: $0 path/to/scx-c-examples}
HERE=$(cd "$(dirname "$0")" && pwd)
cp "$HERE"/scx_stasis.bpf.c "$HERE"/scx_stasis.c "$HERE"/scx_stasis.h "$SCX/scheds/c/"
for mk in "$SCX/Makefile" "$SCX/scheds/c/Makefile"; do
  grep -q scx_stasis "$mk" || sed -i \
    -e 's/^\(C_SCHEDS := .*\)$/\1 scx_stasis/' \
    -e 's/^\(\trm -f .* scx_prev\)$/\1 scx_stasis/' "$mk"
done
grep -n scx_stasis "$SCX/Makefile" "$SCX/scheds/c/Makefile"
