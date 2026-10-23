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
- **Jain's index over the guide's window** (0.43 controller-only, 0.14 full
  STASIS). J over [onset, first finish] measures who was in the first active
  set, not fairness: the first job finishes before rotation reaches the
  parked ones. Longer jobs don't cure it (the simulator gives 0.55 at N = 8
  with four quanta per job), so the scored `jain` is now taken over the whole
  run (deviation 20): 0.80–0.87 for full STASIS on these runs.
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
  loops. They are paced by the clock now, and sessions 2 and 3 below ran
  them on the VM.

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

## Sessions 2 and 3: the revised design

After the independent review the controller changed (deviations 13–19 in
`docs/00-prereg.md`): measured working sets, first-in-first-out turns with a
starvation bound, new baselines and a preflight. Two more bounded sessions ran
it on the same VM, with the same caveats: emulated, one or two runs per cell,
N = 7, PASSES = 60. Every run is kept, laid out as a results root, so
`eval/analyze.py` and the dashboard read them directly
(`python3 dashboard/server.py --results docs/rehearsal/session3/B`).

### Session 2 (`session2/`): the first version

- `preflight.sh`: layers 1–3, measured working sets, sched_ext, MGLRU and
  systemd-oomd GO. thrash-protect NO-GO until installed: the VM's pip
  couldn't reach the network, so 1.1.2 was installed from a wheel copied in.
- Rungs 1 and 3 again, now with the preempt kick (deviation 11): scx_stasis
  attached and gave the protected cyclictest 16,531 priority dispatches under
  8 CPU hogs. Its latency averaged 1.41 ms against 1.50 ms under EEVDF, and
  the maxima were about 40 ms in both (41.0 and 39.6 ms): timer emulation.
  The scheduler also attached 8 s into the 60 s measurement. So this doesn't
  test the kick; the lab VM must (Phase 3, rung 3).
- Runs (baselines capped at 300 s):

| run | HOT | status | passes | makespan | major faults | full stall | longest park | probe p99 | kills |
|---|---|---|---|---|---|---|---|---|---|
| full STASIS | 100 | OK | 420 / 420 | 304 s | 337k | 5.3% | 140 s | 6.2 ms | 0 |
| thrash-protect 1.1.2 | 100 | TIMEOUT | 0 / 420 | – | 43k | 13.1% | – | 5.9 ms | 0 |
| MGLRU, `min_ttl_ms` = 1000 | 100 | TIMEOUT | 6 / 420 | – | 450k | 39.3% | – | 8.9 ms | 0 |
| full STASIS, measured working sets | 60 | OK | 420 / 420 | 384 s | 424k | 10.0% | 135 s | 10.5 ms | 0 |
| full STASIS, footprints | 60 | OK | 420 / 420 | 253 s | 238k | 6.2% | 125 s | 7.1 ms | 0 |

  Under thrash-protect, six of the seven jobs finished allocating and none
  completed a pass. In 85% of its job-seconds a job got no CPU time at all,
  which fits thrash-protect keeping most jobs stopped with `SIGSTOP` (under
  MGLRU: 0%). Under MGLRU the jobs ran and thrashed: every task was waiting
  on memory for 39% of the run.
- `tests/test_controller.sh`: 21 / 21 on kernel 7.0.
- What it found (deviations 18 and 19). The bound's victim rule preferred
  jobs that had had 10 s not stalled, so it swapped out three jobs resumed
  10–29 s earlier while three that had run since the start were never
  parked. And the sampler opened windows back to back under pressure: no job
  had a trusted window before 129 s, although every measurement read
  419–420 MiB for a 420 MiB hot set.

### Session 3 (`session3/`): the final version

Session 3's runs used the final controller except for two sampler details
changed afterwards: after a thaw the sampler compared a job's stall with its
reading from before the park instead of starting afresh, and it judged "not
stalling" from the last 0.5 s alone (now PSI's `some avg10` too; see the tests
below). The final controller's tests ran on the VM in a short third boot.

| run | HOT | status | passes | makespan | major faults | full stall | longest park | probe p99 | kills |
|---|---|---|---|---|---|---|---|---|---|
| full STASIS, measured, run 1 | 60 | OK | 420 / 420 | 231 s | 259k | 6.3% | 130 s | 13.5 ms | 0 |
| full STASIS, measured, run 2 | 60 | OK | 420 / 420 | 348 s | 418k | 5.5% | 135 s | 6.4 ms | 0 |
| full STASIS, footprints, run 1 | 60 | OK | 420 / 420 | 202 s | 224k | 7.6% | 130 s | 6.6 ms | 0 |
| full STASIS, footprints, run 2 | 60 | OK | 420 / 420 | 397 s | 368k | 5.4% | 131 s | 7.2 ms | 0 |
| full STASIS | 100 | OK | 420 / 420 | 333 s | 292k | 3.3% | 130 s | 6.3 ms | 0 |

- Measured against footprints is **inconclusive here**: each configuration
  varied by a factor of 1.5–2 between its two runs, more than the two differ.
  Both made the same kinds of decisions (4–6 admissions, 2–4 rescues), and
  the measurements arrived only after 74–114 s, so in runs this short they
  changed few of them. Every measurement read 397–420 MiB for a 420 MiB hot
  set; 6 of the 7 jobs were measured in each run. Experiment B decides H8.
- No run rotated: with PASSES = 60 a job needs about 17 s of CPU in all, so
  turns rarely reach the 15 s quantum, and the starvation bound did the
  turn-taking. Its longest park was 125–140 s in every run of both
  sessions: the bound's 120 s, plus the wait for the swaps before it.
- `tests/test_controller.sh`: 21 of 22 in session 3. The failure was the
  new check that the sampler leaves a stalling job's referenced bits alone:
  under emulation the fake stall feeder sometimes didn't update between two
  of the sampler's polls 0.5 s apart, so the job looked unstalled for that
  moment. That was the fake's timing, not the kernel's, but a gate that one
  late reading can open is fragile: the sampler now also requires the job's
  PSI `some avg10` to be under 2%. With the feeder slowed to one update per 1.2 s, the old gate
  failed 3 runs of 3 and the new one passed 3 of 3. A short third boot then
  ran the final controller's tests on the VM: **22 / 22**
  (`session3/logs/verify4.log`).

### What sessions 2 and 3 show

- The revised controller runs on kernel 7.0: every STASIS run completed all
  420 passes, with no kills.
- The referenced-bit sampler measures real jobs' working sets accurately.
- At N = 7 the baselines tried here didn't recover: thrash-protect completed
  no pass and MGLRU 6 in 300 s, while every STASIS run finished all 420 in
  202–397 s. One run each, emulated: the campaign measures this properly.
- Whether measuring working sets pays is still open (H8).

## Pilot for experiment C (`pilot-c/`): the early warning's race

Every earlier run's first pressure event came from PSI, during the jobs'
initial allocations (deviation 22). Two pilots tried the onset experiment C
is built for: jobs whose working sets jump mid-run (`wss SIZE PASSES HOT
JUMP`), so the extra demand arrives as refaults. Full STASIS only, PFF_HI
0.029, the same emulated VM. `race.py RUN_DIR` prints what happened after the
first jump.

| Pilot | Workload | What happened |
|---|---|---|
| `1/` | 6 × 700 MB, 50 % hot, jobs 1–3 jump after pass 50 of 100 | The first halving (24 s, PSI, during allocation) parked jobs 4–6; jobs 1–3 jumped at 81–105 s while running nearly alone, fitted, and finished. No park for pressure after the jumps (only warm-up): the design jumped too early. |
| `2/` | 6 × 800 MB, 40 % hot, jobs 1–4 jump after pass 250 of 400 | Slow reloads kept full STASIS at 2 of 6 jobs for most of the run, and the first two jumps (451 s, 566 s) came then. At 778.6 s job 1, rotated back in after its jump, reloaded far more than its stale pre-jump estimate; PFF fired and stasisd parked job 2 although the two-job set fitted, and reset the estimates. By 809 s all six were admitted; job 3 jumped at 808.7 s, its PFF crossed PFF_HI at 810.5 s (1.04 faults per CPU-ms) and stasisd halved the set (`pressure-pff`). PSI `full avg10` was 0.0 then; the PSI trigger didn't fire again until 842.4 s. Stopped at about 874 s, once that was seen; this copy was taken while it ran, so `status` reads RUNNING. |

One race, won by 32 s, on emulated hardware with an uncalibrated threshold:
evidence that the mechanism can work, not a measurement. Experiment C on the
lab VM measures it.

