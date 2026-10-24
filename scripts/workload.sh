#!/usr/bin/env bash
# workload.sh KIND SIZE_MB PASSES
#
# Real memory-hungry batch jobs for arm 3 (pre-registered 2026-10-24), used in
# place of workloads/wss.c when $STASIS_WORKLOAD is set. wss remains arm 1's
# workload and is untouched.
#
# Every kind prints one "<pass> <wallclock>" line per completed unit of work on
# stdout, exactly like wss, so analyze.py's throughput, Jain and progress
# definitions carry over without change. A "pass" is one full iteration over
# roughly SIZE_MB of live data.
#
#   rocksdb    db_bench readrandom over a database sized to SIZE_MB, kept in
#              the block cache so the working set is resident and swappable.
#              Hermes (arXiv 2109.02922) uses RocksDB under graded memory
#              pressure for the same reason.
#   memcached  memtier_benchmark against a private memcached holding SIZE_MB.
#              Caladan's memcached-vs-batch shape is the template.
#   compile    repeated `make` of a source tree. Honest about its status: a
#              kernel build is self-evidently a real memory-hungry batch job,
#              but it is NOT a citable standard memory-pressure benchmark, and
#              the thesis should not present it as one.
#
# These need db_bench / memcached + memtier_benchmark / a source tree present.
# setup_vm.sh does not install them, because arm 1 does not need them: install
# them by hand on the lab VM before planning with --workloads, and record the
# versions in the MANIFEST.
set -euo pipefail
KIND=$1 SIZE=$2 PASSES=$3
# mktemp -d, never a guessable name. These jobs run as root (systemd-run sets
# no User=), and /var/tmp is world-writable: with a predictable path such as
# /var/tmp/stasis-workload-$$, another local account could pre-create the
# directory, plant symlinks in it (fill.log -> /etc/shadow), and have root
# overwrite their targets. mktemp makes a fresh 0700 directory with a random
# name and fails rather than reuse one that already exists.
WORK=$(mktemp -d "${STASIS_WORKDIR:-/var/tmp}/stasis-workload.XXXXXXXX")
trap 'rm -rf "$WORK"' EXIT

now() { date +%s.%N; }
emit() { printf '%d %.3f\n' "$1" "$(now)"; }

need() {
  command -v "$1" >/dev/null 2>&1 ||
    { echo "workload $KIND needs $1 on PATH; install it on the lab VM" >&2; exit 127; }
}

case $KIND in
rocksdb)
  need db_bench
  # One pass = one readrandom sweep whose block cache holds SIZE_MB. num is set
  # so the value bytes alone reach SIZE_MB: 1 KiB values -> SIZE * 1024 keys.
  keys=$(( SIZE * 1024 ))
  db_bench --benchmarks=fillrandom --db="$WORK/db" --num="$keys" \
           --value_size=1024 --compression_type=none > "$WORK/fill.log" 2>&1
  for p in $(seq 1 "$PASSES"); do
    db_bench --benchmarks=readrandom --use_existing_db=1 --db="$WORK/db" \
             --num="$keys" --reads="$keys" --value_size=1024 \
             --cache_size=$(( SIZE * 1024 * 1024 )) --compression_type=none \
             > "$WORK/read.log" 2>&1
    emit "$p"
  done ;;

memcached)
  need memcached; need memtier_benchmark
  port=$(( 20000 + (RANDOM % 10000) ))
  memcached -u nobody -m "$SIZE" -p "$port" -l 127.0.0.1 &
  mc=$!
  trap 'kill $mc 2>/dev/null || true; rm -rf "$WORK"' EXIT
  for _ in $(seq 50); do
    (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null && break
    sleep 0.1
  done
  # One pass = one fixed batch of requests over a key space whose values total
  # about SIZE_MB, so the resident set is the thing under pressure.
  keys=$(( SIZE * 1024 ))
  for p in $(seq 1 "$PASSES"); do
    memtier_benchmark -P memcache_text -s 127.0.0.1 -p "$port" \
      --protocol=memcache_text --clients=4 --threads=1 --requests=$(( keys / 4 )) \
      --data-size=1024 --key-maximum="$keys" --ratio=1:4 --hide-histogram \
      > "$WORK/mt.log" 2>&1
    emit "$p"
  done ;;

compile)
  SRC=${STASIS_COMPILE_SRC:?set STASIS_COMPILE_SRC to a source tree to build}
  need make
  # make runs the tree's Makefiles, and this job runs as root, so whoever can
  # write to the tree can run code as root. Refuse a tree anyone but root
  # could have changed: one entry not owned by root, or group- or
  # world-writable, is enough.
  bad=$(find "$SRC" \( ! -user root -o -perm -g+w -o -perm -o+w \) -print -quit 2>/dev/null || true)
  [[ -z $bad ]] || { echo "refusing to build $SRC as root: $bad is not root-only" >&2; exit 126; }
  for p in $(seq 1 "$PASSES"); do
    make -C "$SRC" -j"$(nproc)" clean  > "$WORK/make.log" 2>&1 || true
    make -C "$SRC" -j"$(nproc)"       >> "$WORK/make.log" 2>&1 || true
    emit "$p"
  done ;;

*) echo "unknown workload kind $KIND" >&2; exit 2 ;;
esac
