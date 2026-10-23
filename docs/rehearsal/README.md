# Rehearsal on kernel 7.0: dress rehearsal, not results

Phases 0–4 were rehearsed end to end on a throwaway VM before the real
campaign, to find bugs while they were still cheap. **None of these numbers
are experiment results.** The VM ran under software emulation, every cell has
one run, N = 7 isn't one of the pre-registered N, and PASSES was cut to 60 so
runs would finish. What the rehearsal establishes is that the pipeline works
on a real kernel 7.0 and what had to change before Phase 5.

## Setup

| | |
|---|---|
| Guest | Ubuntu 26.04 cloud image, kernel `7.0.0-38-generic`, MGLRU on, zswap off |
| Machine | QEMU, 4 vCPUs, 4 GB RAM, **TCG software emulation (no KVM)**, 8 GB swapfile on a virtio disk |
| Swap speed | about 12 MB/s out and 3 MB/s in, as measured; an SSD-backed VM is one to two orders of magnitude faster |
| Workload | `wss` 700 MB, PASSES = 60 (one job alone: about 17 s of CPU) |
| Timeouts | EEVDF 300 s; controller-only and full STASIS 900 s |
| PFF_HI | 0.029, from `calibrate.txt` |

## Verified on the kernel

- `scripts/setup_vm.sh` ran unmodified (3 min 41 s): libbpf and bpftool from
  source, scx-c-examples at the pinned commit, `scx_simple` and `scx_stasis`.
- Gate 0's checks pass.
- `scx_stasis` passes the verifier and attaches. Rung 2: the fault counts
  match exactly (17645 = 17645). Rung 3: 133 priority dispatches per second
  under contention. Rung 4: survives a stress run.
- The final control law (deviations 1–9 in `docs/00-prereg.md`) completed
  every pass in both configurations, with no kills:

| config | N | status | passes | time | throughput (passes/s) | kills | probe p99 | swap traffic |
|---|---|---|---|---|---|---|---|---|
| EEVDF | 2 | OK | 120 / 120 | 19 s | 6.96 | 0 | 5.5 ms | ~0 |
| EEVDF | 7 | TIMEOUT | 4 / 420 | 300 s (cap) | 0.013 | 0 | 11.0 ms | 4.2 GiB |
| controller only | 7 | OK | 420 / 420 | 608 s | 0.70 | 0 | 8.1 ms | 5.1 GiB |
| full STASIS | 7 | OK | 420 / 420 | 284 s | 1.50 | 0 | 16.0 ms | 1.4 GiB |

  The controller-only run halved at 23.5 s, 27.2 s, 96.3 s and 168.6 s,
  admitted 8 times, rotated 6 times, and held 41 pressure events as warm-up.
  Full STASIS halved at 23.6 s, 28.6 s, 62.0 s, 116.7 s and 183.5 s, admitted
  10 times, and held 389 pressure events as warm-up. 358 of its 396 pressure
  events came from the PFF early warning; 355 of those were held as warm-up.

- The dashboard's live, replay and results views worked against these runs.

`results/` holds the four runs exactly as `run_mix.sh` wrote them, plus
`metrics.csv` from `eval/analyze.py metrics`. To replay them:

```bash
python3 dashboard/server.py --results docs/rehearsal/results
```

`analyze.py score` runs on them too. Every hypothesis reads MISSING, because
the hypotheses are scored at N = 4, 6 and 8.

## Not representative

- **Throughput ratios.** Emulated swap makes thrashing far worse than on real
  hardware, and it makes reloading slow for STASIS too.
- **Jain's index** (0.43 controller-only, 0.14 full STASIS). With PASSES = 60
  a job needs about one quantum of CPU in total, so the first job finishes
  before rotation can give anyone else a turn. J over [onset, first finish]
  then measures who was in the first active set. The pre-registered PASSES
  (about 60 s alone) gives each job about four quanta.
- **Probe latency.** Even with nothing to do (EEVDF, N = 2), p99 was 5.5 ms:
  timer emulation dominates. The ranking still exposed a real design flaw
  (deviation 11).
- **Full STASIS's time-to-first-park** (24.7 s in `metrics.csv`) is wrong.
  That run started 22 s after the previous one, with PSI's `full avg10`
  still at 0.10, so its onset was its first sample. Measured from PSI's
  cumulative stall counter, stalls began at 23.4 s and the first park
  followed 1.0 s later (controller-only: 4.5 s). PSI fired first in both
  runs, so the rehearsal doesn't show H5.
- One run per cell.

## Found here, fixed afterwards

These changes came from reading the final runs' logs. They are tested with
fake cgroups and the simulator, but **not yet run on a kernel**. The first
real runs of Phase 2–4 will be their first test.

- **Deviation 10: transitions are not thrashing.** Both runs halved sets
  that fit: 7 times in all after the first halving, each time parking a job
  that was mid-reload. The new rule holds pressure as warm-up while the set
  fits and is reloading or still making room, and restarts the survivors'
  growth windows at a halving. By the samples, all 7 would have been held.
- **Deviation 11: protected tasks preempt.** The probe's p99 was worst under
  full STASIS (16.0 ms against 8.1 ms and 11.0 ms). Nothing preempted the
  batch jobs, so a waking probe waited for a 20 ms slice to end.
- **Deviation 12: quiet start.** `run_mix.sh` waits for PSI to settle before
  each run.
- **Whole bytes for `memory.reclaim`** (deviation 7). The first version wrote
  `20971520.0` whenever more than RESERVE of memory was free, and the kernel
  rejects that.
- **`tests/test_controller.sh`.** On the VM, 4 of its then 9 tests failed:
  the fake workloads were paced by loop counts, and emulation slowed the
  loops. They are paced by the clock now and pass under a 7× CPU slowdown.
  They haven't been re-run on the VM.

## History: how the law got here

One controller-only N = 7 run per version, each kept as its decision log
(`history/`). Each run's outcome is the evidence for the deviation it led to.

| run | outcome | what it showed | led to |
|---|---|---|---|
| `1-first-warm-up-guard` | TIMEOUT, 1 / 420 passes in 300 s | a 1%/s growth threshold is above this swap's refault rate: halved 7 → 3 at 16.6 s and to 1 at 20.3 s | deviation 4 |
| `2-wall-clock-quantum` | TIMEOUT, 78 / 420 in 600 s | jobs rotated out before they warmed up | deviation 6 |
| `3-denning-admission` | stopped at 347 s, 3 passes | two half-allocated jobs admitted in a row: 3 → 5 → 2 active | deviation 5 |
| `4-settled-admission` | stopped at 633 s, 11 passes | parked jobs kept their memory while running jobs evicted each other (`samples.jsonl` kept) | deviation 7 |
| `5-total-cpu-quantum` | stopped at 665 s, 6 passes | `usage_usec` counts fault handling, so jobs left mid-reload; a flat 1 s window halved 3 → 1 | deviations 6 and 9 |
| `results/` (final) | OK, 420 / 420 both | transitional halvings, probe latency, stale PSI at the start | deviations 10–12 |
