# STASIS pre-registration

Fill this in at the end of Phase 1 (Gate 1), then commit it and write the
commit hash below. **From that commit on, no threshold changes.** Anything
changed later is reported as a deviation, with the reason.

- Frozen at commit: `________` (fill in after `git commit`)
- Date: `____-__-__`

## Workload

| Parameter | Value | How it was chosen |
|---|---|---|
| SIZE (MB per job) | `700` | 4 jobs (2.8 GB) fit in 4 GB RAM; 8 don't |
| PASSES | `__` | `scripts/calibrate.sh 700` → one job alone runs ~60 s |
| N range | 1–8 | |
| Timeout per run | 1200 s | |
| Runs per cell | 5 | |

## Controller parameters

| Parameter | Value |
|---|---|
| PSI trigger | `full 100000 1000000` (100 ms full stall per 1 s window) |
| CALM / COOLDOWN / RESERVE | 5 s / 2 s / 10 % of MemTotal |
| QUANTUM | 15 s of the job's own user CPU time since it was last thawed (rotations at most every 15 s) |
| PFF_HI (faults per CPU-ms) | `____` from `eval/analyze.py calibrate` |
| PFF streak / grace | 3 consecutive 100 ms samples / 2 s after a job starts or is thawed |
| Working-set estimate | `memory.current + memory.swap.current − swapcached`, at least `memory.peak` |
| Growth window | every growth figure is measured over the last 5 s (`GROW_WINDOW`); a halving restarts the survivors' windows |
| PFF growth filter | the job's resident memory grew < 0.5% of its working set per second (`GROW_FRAC`) |
| Warm-up guard | pressure is warm-up when the active set's working sets fit and it is growing ≥ 0.1% of them per second (`SET_GROW_FRAC`), or ≥ 2% of them is swapped out (`RELOAD_FRAC`), or parked jobs are still being paged out to make its room |
| Proactive reclaim | page parked jobs out through `memory.reclaim` when the active set's missing pages exceed free memory beyond RESERVE (whole bytes, 32 MiB chunks, background thread) |
| Protected tasks | the probe and demo shell go on a priority queue, and a waking one preempts a batch job (`SCX_KICK_PREEMPT`) |
| Run start | each run waits (up to 3 min) for PSI `full avg10` to read 0 |
| Admission test | active set settled (growing < 0.1%/s), and Σ working sets of the active jobs + the candidate ≤ MemTotal − other − RESERVE; a candidate parked while still growing counts as the largest settled working set |

## Deviations from the build guide (decided before any Phase 5 run)

1. `wss` timestamps use `CLOCK_REALTIME` so that job logs, samples and the
   decision log share one clock (the fairness metric and time-to-first-park
   both compare them).
2. The PFF early warning only counts a job whose resident memory grew by less
   than 0.5% of its working set over the last second, and the 2 s grace period
   also applies after a thaw. A thawed job has to fault its pages back in from
   swap, which takes seconds and looks exactly like thrashing to PFF alone.
   The difference is that its resident set grows. Faulting that doesn't grow
   the resident set is Denning's definition of thrashing.
3. Admission and rotation are independent. In the build guide's controller
   code they are an `if/elif` chain, so once the system had been calm for 5 s
   the admission branch ran on every loop (doing nothing when the parked job
   didn't fit) and rotation never ran again. A fake-cgroup test with advancing
   CPU time showed the guide's version rotating 0 times after the calm point
   and the fixed version continuing round-robin.
4. Warm-up guard. A pressure event is treated as warm-up, halving nothing and
   not restarting the calm period, while the active set's working sets fit
   and its resident memory is still growing. After a halving, the surviving
   jobs fault their evicted pages back in, which keeps PSI's full stall high
   for as long as that takes. The guide's 2 s cooldown is far shorter, so its
   rule cascades. A first version of the guard used a 1%/s growth threshold,
   tuned on the simulator. On the rehearsal VM (Ubuntu 26.04, kernel 7.0),
   whose swap refaults at only about 0.6%/s, that made it behave like the
   guide's law: it halved 7 → 3 at 16.6 s, rotated 2.3 s later, halved again
   to 1 job at 20.3 s, then kept rotating that single job out before it
   could warm up: **1 pass completed across 7 jobs in 300 s**, with 181
   pressure events (`docs/rehearsal/history/1-first-warm-up-guard`). The
   threshold is now 0.1%/s: a slow device makes warm-up slow, not absent.
   The rotation clock also restarts when jobs are parked, so a job isn't
   rotated in seconds after a halving.
5. Admission uses Denning's rule: admit the least-served parked job when the
   working sets of the active jobs plus its own fit in the memory jobs can
   use, counting parked jobs' memory as reclaimable. The guide's test compared
   the job's missing pages with `MemAvailable`, which counts frozen jobs'
   anonymous pages as unavailable, so once anything was parked nothing was
   ever admitted again. The working set is estimated as everything the job
   has touched, `memory.current + memory.swap.current − swapcached` (swap
   cache is counted in both). On the VM this read 701–704 MB for every 700 MB
   job. `memory.peak` read only 494–658 MB for jobs that started under
   pressure, which would have over-admitted. A working set is only known once
   it has settled, so admission also waits until the active set's resident
   memory has stopped growing, and a job parked while it was still growing
   (for example, mid-way through allocating its memory) is assumed to be as
   large as the largest settled job. Without this, the VM run admitted two
   half-allocated jobs in a row and had to halve again (3 → 5 → 2 active;
   `docs/rehearsal/history/3-denning-admission`).
6. The rotation quantum is counted in the outgoing job's own user CPU time
   since it was last thawed, not in wall-clock time. A thawed job spends its
   first seconds faulting its pages back in. On the rehearsal VM that took
   about a minute per job, so a 15 s wall-clock quantum rotated jobs out
   before they did any work: with that version, 7 jobs completed 78 of 420
   passes in 600 s (`history/2-wall-clock-quantum`). Total CPU time
   (`usage_usec`) wasn't enough either, because handling those faults is
   kernel CPU time: each job was rotated out about 26 s after its thaw,
   still faulting, so the active set never settled, nothing was admitted,
   and one job ran at a time: 6 of 420 passes in 665 s, with 23 rotations
   and no admissions (`history/5-total-cpu-quantum`). User CPU time
   (`user_usec`) gives every job 15 s of real work per turn however slow
   the swap device is.
7. Proactive reclaim of parked jobs. When the active jobs' working sets need
   more memory than is free, stasisd writes to the parked jobs' cgroup v2
   `memory.reclaim` from a background thread, largest parked job first, so
   pages are swapped out of jobs nobody is running. On the rehearsal VM,
   Linux's own reclaim didn't prefer frozen pages: over 24 s the four parked
   jobs gave up only about 280 MB (one of them kept 500 MB the whole time)
   while a running job was squeezed from 577 to 511 MB
   (`history/4-settled-admission`). The running jobs were
   evicting each other, so the active set never warmed up and was halved
   again. This implements the proposal's "batch memory movement" with the
   kernel's own interface. `--no-reclaim` turns it off. Sizes are written as
   whole bytes: the first version wrote `20971520.0` whenever more than
   RESERVE of memory was free, which `memory.reclaim` rejects.
8. Pressure onset for Jain's index is the earlier of the first PSI
   `full avg10 > 0` sample and the controller's first pressure event, so that
   every configuration uses the same definition.
9. Growth is measured over 5 s, not 1 s. On the rehearsal VM's swap a single
   flat second read as "not growing": with a 1 s window the set was halved
   3 → 1 at 50.9 s while the survivors were still faulting their pages in
   (`history/5-total-cpu-quantum`).
   Deviations 6 and 9 together are the law that completed every pass on the
   rehearsal VM (see `docs/rehearsal/`).
10. Transitions are not thrashing. Both completed rehearsal runs still halved
    sets whose working sets fit: the controller-only run 3 times after its
    first halving, full STASIS 4 times. Every one of those pressure events
    had `fits: true` and growth below 0.1%/s. In 5 of the 7, 10–59% of the
    active set was still swapped out: a job admitted or rotated in was
    reloading at the device's ~3 MB/s, which is only 0.3–0.5% of its working
    set per second. The other 2 came 3.5–5 s after a halving, with the growth
    window spanning it. Each one parked a job mid-reload, which then had to
    reload again later. If the working sets fit, halving more jobs can't
    bring the survivors' pages back any faster, so pressure is now also
    warm-up while ≥ 2% of the active set is swapped out (reloading) or parked
    jobs are still being paged out to make its room, and a halving restarts
    the survivors' growth windows. Pressure while the set fits, is resident,
    has room and isn't growing still halves it: that means an estimate is
    wrong. Checked by fake-cgroup tests and the simulator (which gives the
    same results as before at three swap speeds, because its idealized
    reclaim never produces these halvings); **not yet run on the VM**.
11. Protected tasks preempt. The rehearsal's probe p99 was 16.0 ms under full
    STASIS against 8.1 ms (controller only) and 11.0 ms (EEVDF). Queueing
    protected tasks first isn't enough: with the active jobs resident and
    CPU-bound, which is what STASIS achieves, every CPU is usually running a
    batch job with up to `SCX_SLICE_DFL` (20 ms) left. `scx_stasis` now kicks
    the task's CPU with `SCX_KICK_PREEMPT` when it enqueues a protected task,
    and that CPU's dispatch takes the priority queue first. Compiled against
    the pinned scx-c-examples; **not yet run on a kernel**. Gate 3's
    contention rung checks it.
12. Each run starts from a quiet machine: `run_mix.sh` waits up to 3 min for
    PSI `full avg10` to decay to 0, and records the wait in `quiesce_s`. On
    the rehearsal VM the full-STASIS run started 22 s after the previous run
    ended, with `full avg10` still at 0.10. Its onset was therefore its first
    sample, and its time-to-first-park read 24.7 s. Measured from PSI's
    cumulative stall counter, stalls began at 23.4 s and the first park
    followed 1.0 s later.

Deviations 2–7 and 9–11 change the control law or the scheduler. All were
found before any Phase 5 measurement: 3 by a fake-cgroup test; 2, 4 and 5 by
the dashboard's simulator, which runs the same law on a model of the lab VM,
then confirmed and refined on the rehearsal VM; 6, 7 and 9–11 on the
rehearsal VM. Each controller fix is in both `controller/stasisd.py` and the
simulator, and `tests/test_controller.sh` checks them.

## Metrics

See the docstring of `eval/analyze.py`: throughput, kills, Jain's J, probe
p99, time-to-first-park, and swap traffic.

## Hypotheses

Decision rule (strict): a hypothesis passes only if **every** STASIS run beats
**every** baseline run on that metric at that N. With 5 runs a side, luck can't
carry it. `kills` uses an exact match instead.

These are a proposed set: edit them before you freeze this file.

```json
{
  "hypotheses": [
    {"id": "H1",  "claim": "Full STASIS has higher throughput than every baseline under overload",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [6, 8],
     "baselines": ["eevdf", "oomd", "simple", "thrashprotect"]},
    {"id": "H1c", "claim": "Controller-only beats EEVDF on throughput",
     "metric": "throughput", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["eevdf"]},
    {"id": "H2",  "claim": "Full STASIS kills nothing",
     "metric": "kills", "equals": 0, "stasis": "stasis", "N": [4, 6, 8]},
    {"id": "H2c", "claim": "Controller-only kills nothing",
     "metric": "kills", "equals": 0, "stasis": "ctrl", "N": [4, 6, 8]},
    {"id": "H3",  "claim": "Full STASIS is fairer (Jain) than thrash-protect and oomd",
     "metric": "jain", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["thrashprotect", "oomd"]},
    {"id": "H3c", "claim": "Controller-only is fairer than thrash-protect",
     "metric": "jain", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["thrashprotect"]},
    {"id": "H4",  "claim": "The protected probe's p99 latency is lower under full STASIS",
     "metric": "p99_us", "better": "lower", "stasis": "stasis", "N": [8],
     "baselines": ["eevdf", "ctrl", "oomd", "thrashprotect"]},
    {"id": "H5",  "claim": "Scheduler PFF parks jobs earlier than the controller alone",
     "metric": "ttfp_s", "better": "lower", "stasis": "stasis", "N": [6, 8],
     "baselines": ["ctrl"]}
  ]
}
```

Expected losses, reported honestly: at N ≤ 4 everything fits, so the
controller can only add overhead.
