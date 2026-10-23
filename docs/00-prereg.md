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
| QUANTUM | 15 s of the job's own CPU time since it was last thawed (rotations at most every 15 s) |
| PFF_HI (faults per CPU-ms) | `____` from `eval/analyze.py calibrate` |
| PFF streak / grace | 3 consecutive 100 ms samples / 2 s after a job starts or is thawed |
| Working-set estimate | `memory.current + memory.swap.current − swapcached`, at least `memory.peak` |
| PFF growth filter | the job's resident memory grew < 0.5% of its working set in the last second (`GROW_FRAC`) |
| Warm-up guard | pressure is warm-up when the active set's working sets fit and its resident memory grows ≥ 0.1% of them per second (`SET_GROW_FRAC`) |
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
   rule cascades. On the rehearsal VM (Ubuntu 26.04, kernel 7.0) the guide's
   law halved 7 → 3 at 16.6 s, rotated 2.3 s later, halved again to 1 job at
   20.3 s, then kept rotating that single job out before it could warm up:
   **1 pass completed across 7 jobs in 300 s**, with 181 pressure events. A
   first version of the guard used a 1%/s growth threshold, tuned on the
   simulator; it failed on the VM, whose swap device refaults at about
   0.6%/s. The threshold is now 0.1%/s: a slow device makes warm-up slow,
   not absent. The rotation clock also restarts when jobs are parked, so a
   job isn't rotated in seconds after a halving.
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
   half-allocated jobs in a row and had to halve again (3 → 5 → 2 active).
6. The rotation quantum is counted in the outgoing job's own CPU time since
   it was last thawed, not in wall-clock time. A thawed job spends its first
   seconds faulting its pages back in, using almost no CPU. On the rehearsal
   VM that took about a minute per job, so a 15 s wall-clock quantum rotated
   jobs out before they did any work: with that version, 7 jobs completed 78
   of 420 passes in 600 s. Counting CPU time gives every job 15 s of real
   work per turn however slow the swap device is.
7. Pressure onset for Jain's index is the earlier of the first PSI
   `full avg10 > 0` sample and the controller's first pressure event, so that
   every configuration uses the same definition.

Deviations 2–6 change the control law. All were found before any Phase 5
measurement: 3 by a fake-cgroup test; 2, 4 and 5 by the dashboard's simulator,
which runs the same law on a model of the lab VM, then confirmed and refined on
the rehearsal VM; 6 on the rehearsal VM. Each fix is in both
`controller/stasisd.py` and the simulator, and `tests/test_controller.sh`
checks them.

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
