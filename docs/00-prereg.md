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
| HOT (share of each job's memory it keeps touching) | experiment A: 100 %; experiment B: 60 %; experiment C: 40 %, and 100 % for jobs 1–4 after 60 % of PASSES | B: 8 hot sets (3.4 GB) overcommit 4 GB, 6 (2.5 GB) don't; 8 footprints are 5.6 GB. C: 8 × 280 MB (2.2 GB) fit, 4 × 700 + 4 × 280 MB (3.9 GB) don't |
| N range | A: 1–8; B: 4, 6, 8; C: 8 | |
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
| Working-set estimate | measured: memory referenced (`smaps_rollup` `Referenced`) during τ = 1 s of the job's own user CPU after clearing its bits (`/proc/PID/clear_refs`); a window counts only if the job stayed unfrozen, stalled on memory < 2% of it (cgroup `memory.pressure`) and grew its footprint < 0.5%/s; estimate = 1.10 × the largest of its last 10 trusted windows, never above the footprint. A window opens at most every 10 s per job (`WS_PERIOD`), and only while the job isn't stalling on memory: its PSI `some avg10` is under 2% and it hasn't stalled since the sampler's last look (0.5 s). Footprint (`memory.current + memory.swap.current − swapcached`, at least `memory.peak`) until then, and always with `--ws=footprint` |
| Resumption | admission and rotation resume the parked job that has waited longest; halvings and rotations park the jobs with the most user CPU in their current turn |
| One swap at a time | a rotation or a rescue waits while a job resumed less than 120 s ago hasn't yet had MIN_TURN = min(10 s, 120/4, QUANTUM) not stalled on memory (its cgroup's PSI `some`) |
| Starvation bound | 120 s (`--max-wait`): a job parked that long is swapped in for the active job with the most user CPU in its turn, at the first step where no swap is still reloading; rescues ≥ 5 s apart |
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
5. Admission uses Denning's rule: admit the next parked job when the
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

13. Working sets are measured, not counted. `memory.current` says how much
    memory a job holds, not how much it needs; the footprint (everything it
    has touched) is an upper bound that over-parks any job with cold memory.
    stasisd now measures Denning's W(t, τ) per job: it clears the referenced
    bits of the job's processes (`/proc/PID/clear_refs`), lets the job run
    τ = 1 s of its own user CPU (Denning's window is in process time, not
    wall time), and reads `Referenced` from `/proc/PID/smaps_rollup`. Tried
    on a real kernel with a 400 MB job touching 100 %, 60 % and 25 % of its
    memory: 401, 241 and 101 MiB, at about 15–20 ms per window. A thrashing
    job references only what it keeps resident, so a window is trusted only
    if the job ran unfrozen, didn't stall on memory and wasn't still
    allocating; untrusted jobs keep the footprint. An estimate that a halving
    proves wrong (the set "fit" but thrashed anyway) is forgotten. DAMON was
    the reviewed alternative: Ubuntu 26.04's kernel (`7.0.0-38-generic`) is
    built with `# CONFIG_DAMON is not set`, so it isn't available on the lab
    VM; referenced bits need only `CONFIG_PROC_PAGE_MONITOR`, which it has.
    The cost is that clearing the bits briefly makes a job's pages look
    unused to reclaim. Deviation 19 limits it, and experiment A's `stasis-fp`
    runs measure what is left.
14. Fair resumption with a starvation bound. The guide parks the jobs with the
    highest fault rate and resumes the least-served one, with no limit on
    waiting. In the rehearsal, one job of the controller-only run stayed
    parked for 399 s in a single stretch (full STASIS: 93 s), and halvings
    parked jobs that had just been admitted and were still reloading (full
    STASIS at 62.0 s parked job-3, admitted at 49.7 s): the highest fault rate
    belongs to whoever is reloading, i.e. the job that has had the least of
    its turn. Now every job takes turns: halvings and rotations park the jobs
    with the most user CPU in their current turn, the job resumed is always
    the one that has waited longest, and a job that has waited 120 s is
    swapped in even if no turn has ended. `--no-rotation` (label
    `stasis-norot`) is the ablation.
15. Baselines, revised after an independent review. Added EEVDF + MGLRU
    thrashing protection (`min_ttl_ms = 1000`, the kernel documentation's
    value). thrash-protect is pinned to 1.1.2 (PyPI, 2026-04-12): its trigger
    is swap traffic (swap-in × swap-out per interval) weighted up by PSI, OOM
    prediction is on by default, and it parks system services with SIGSTOP. DAMON_RECLAIM was to be included if
    the kernel had it; it doesn't (deviation 13), so it is left out.
    `scx_simple` is no longer a baseline: it doesn't do load control, so it
    stays only in Gate 0. Every baseline's settings are in "Baselines" below.
16. Two experiments. A is the thrashing curve with every page hot. B makes
    60 % of each job's memory hot (`wss SIZE PASSES 60`), so footprints
    overstate working sets: it tests deviation 13 (`stasis` against
    `stasis-fp`, and against EEVDF where the hot sets overcommit memory).
17. More metrics: the longest single park (`max_wait_s`, for the starvation
    bound), the longest time any job went without finishing a pass
    (`max_gap_s`, comparable across every configuration whatever stopped
    the job), the share of job-time parked, PSI full stall, major faults,
    jobs finished within 600 s, and the makespan. Kills are reported as what
    the kernel's OOM killer and systemd-oomd did: STASIS sends no kills, but
    it can't promise the kernel won't.

18. One swap at a time. A rotation or a rescue trades a resident job for one
    that must fault its whole working set back in; overlapping swaps compete
    for the swap device. The simulator showed the cost: at 3 MB/s swap-in the
    first starvation bound (deviation 14) swapped the four parked jobs in 5 s
    apart as soon as they passed 120 s, swapped two of them out again 10 s
    later while they were still reloading, and repeated this round after
    round, with every active job reloading at once. At N = 8 controller-only
    STASIS completed 26 of 320 passes in 3000 s, and full STASIS 116. Now a
    rotation or a rescue waits until the job resumed before it has had
    MIN_TURN not stalled on memory, or 120 s have passed. In the simulator
    N = 8 then finishes in 975 s at 3 MB/s, in 276 s instead of 524 s at
    20 MB/s, and in 174 s instead of 228 s at the default 120 MB/s. Gating
    admission the same way never helped and cost up to 10%, so admission
    (which adds a job to a set that has room) is exempt.
    The rescue's victim is the active job with the most user CPU in its turn.
    A first version preferred jobs that had had MIN_TURN not stalled. On the
    rehearsal VM (N = 7) that swapped out three jobs resumed 10–29 s
    earlier, while three jobs that had run since the start were never
    parked: stalling on memory, they never counted as having had a turn.
19. The working-set sampler is paced and stays out of the way under pressure.
    The first version opened a new window for every running job as soon as
    its last one closed, under pressure too. Each window clears the job's
    referenced bits, which makes its pages look idle to reclaim, and a window
    in which the job stalls is discarded anyway: on the rehearsal VM at HOT
    60 (N = 7) no job had a trusted window before 129 s, although every
    measurement was right (419–420 MiB for a 420 MiB hot set). That run was
    also slower than the same run with footprints (384 s against 253 s, with
    78% more major faults), but one run each can't separate the sampler's
    cost from run-to-run variation: two runs of the final version took 231 s
    and 348 s. A window now opens at most every 10 s per job, and only while
    the job isn't stalling on memory (its PSI `some avg10` under 2%, and no
    stall since the sampler's last look).
20. Fairness is measured over the whole run. The guide's Jain's index counts
    each job's passes between pressure onset and the first job finishing. That
    window ends before a load controller has rotated through its parked jobs,
    so it reads about (jobs resident) / N for any admission controller: in the
    rehearsal, full STASIS scored 0.14–0.41 at N = 7 (0.14 is 1/7: one job ran
    all 60 of its passes while six waited their turn), and in the simulator,
    with each job four rotation quanta long, it falls from 1.00 at N = 5 to
    0.55 at N = 8 while EEVDF scores 0.99 at N = 8 because every job is
    equally stuck. `jain` is now Jain's index over each job's progress rate
    (passes divided by the time from the run's start until the job finished
    or the run ended): the fairness of slowdowns, so a job killed or starved
    for the whole run pulls it down. On the same rehearsal runs this reads
    0.80–0.87 for full STASIS, 0.89 controller-only, 0.64 MGLRU and 0.57
    EEVDF. The guide's version is still reported as `jain_window`; H3 and
    H3c are scored on `jain`. Like any fairness index it rewards equal
    misery, which is why it is read beside throughput.
21. The sensitivity analysis is pre-registered, and sweeps the trigger too.
    The guide asks to show that the result doesn't hinge on one lucky
    setting, sweeping the rotation quantum (5, 15, 30 s) and the PSI trigger
    (50, 100, 200 ms of full stall per 1 s window) at N = 8. The campaign
    swept the quantum and the starvation bound (60, 120, 240 s, deviation 14)
    but not the trigger, and nothing scored either. `campaign.sh plan
    --sensitivity` now runs all three sweeps, 3 runs per setting (27 runs),
    H10 scores each non-default setting against the N = 8 baselines on
    throughput with the same strict rule as H1, and F8 plots them.
22. A fair race for the early warning: experiment C and H5c. In every
    rehearsal run the first pressure event came from PSI, and in experiments A
    and B it always will: every job allocates its memory at the start, and the
    first stall is reclaim during those allocations, before any page has been
    swapped out and touched again, so there are no major faults yet for PFF to
    count. H5 (time to first park) therefore measures that allocation
    transient, where both configurations react to the same PSI event. The
    early warning's claim is about another onset: thrashing that starts from
    refaults in some jobs while the machine as a whole isn't yet fully
    stalled. PSI `full` needs every runnable task stalled at once; PFF is per
    job. Experiment C creates that onset: 8 jobs, 40 % hot (2.2 GB of hot sets
    fit); after 60 % of their passes, jobs 1–4 make every page hot (`wss SIZE
    PASSES 40 JUMP`), so their extra 1.7 GB arrives as refaults of pages
    already in swap (3.9 GB of hot sets don't fit). `detect_s` is the time
    from the first jump to the first park for pressure, and H5c scores it,
    full STASIS (PSI or PFF) against controller-only (PSI only); `jump_src`
    records which signal the first acted-on pressure came from. Parking sooner
    is only worth it if it isn't parking for nothing, so H5d checks that full
    STASIS also keeps more throughput than controller-only, and H11 that it
    keeps more than EEVDF. H5 stays as registered. Two pilots on the emulated
    rehearsal VM (`docs/rehearsal/pilot-c/`) checked the mechanics: the jump,
    its log line, the metrics. They mostly couldn't reach the intended state:
    with swap-in at about 3 MB/s a job rotated in spends about 100 s
    reloading, so full STASIS was running only 2 of 6 jobs at the first two
    jumps (and at 778.6 s PFF parked a job although the two-job set fitted: a
    jumped job, rotated back in, reloaded far more than its stale estimate).
    At the third jump all six were running: job 3's PFF crossed PFF_HI 1.8 s
    after its jump and stasisd halved the set, while PSI `full` stayed at 0
    and its trigger didn't fire until 32 s later. That is one event, on
    emulated hardware, with an uncalibrated PFF_HI (0.029): it shows the race
    can be won, not that it is. Experiment C, on the lab VM and after a pilot
    there (Phase 5), measures it.

Deviations 2–7, 9–11, 13, 14, 18 and 19 change the control law or the
scheduler. All were found before any Phase 5 measurement: 3 by a fake-cgroup
test; 2, 4 and 5 by the dashboard's simulator, which runs the same law on a
model of the lab VM, then confirmed and refined on the rehearsal VM; 6, 7 and
9–11 on the rehearsal VM; 13–17 from an independent design review, using the
rehearsal's logs as evidence; 18 by the simulator and the rehearsal VM; 19 on
the rehearsal VM; 20 from the rehearsal's logs and the simulator; 21 from
the guide; 22 from the rehearsal's logs and a pilot on the rehearsal VM. Each controller change is in `controller/stasisd.py` and the
simulator, and `tests/test_controller.sh` checks it.

## Baselines

| Label | What runs beside the jobs | Settings |
|---|---|---|
| `eevdf` | nothing: stock Linux 7.0 | |
| `mglru` | EEVDF + MGLRU thrashing protection | `/sys/kernel/mm/lru_gen/min_ttl_ms = 1000`, restored after the run |
| `oomd` | EEVDF + systemd-oomd | `ManagedOOMMemoryPressure=kill` on `stasis.slice`, systemd defaults otherwise |
| `thrashprotect` | thrash-protect 1.1.2 | its defaults (swap-traffic trigger weighted by PSI, OOM prediction on); whitelist: sshd, shells, sudo, tmux, screen, systemd, journald, cyclictest, python3 |
| `ctrl` | stasisd, memstat signal, under EEVDF | layers 1–3 only |
| `stasis-norot`, `stasis-fp` | full STASIS with `--no-rotation`, `--ws=footprint` | ablations |
| not run | DAMON_RECLAIM | not built into the lab kernel (`CONFIG_DAMON` not set) |
| not a baseline | `scx_simple` | Gate 0 only: it doesn't control load |

## Metrics

See the docstring of `eval/analyze.py`: throughput, kills, Jain's J (over the
whole run, and the guide's onset-to-first-finish window), probe
p99, time-to-first-park, swap traffic, longest park, longest time without
progress, share of time parked, PSI full stall, major faults, jobs finished
within 600 s, and makespan.

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
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]},
    {"id": "H1c", "claim": "Controller-only beats EEVDF on throughput",
     "metric": "throughput", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["eevdf"]},
    {"id": "H2",  "claim": "No workload process is killed under full STASIS (it sends no kills; this checks that the OOM killer and oomd didn't either)",
     "metric": "kills", "equals": 0, "stasis": "stasis", "N": [4, 6, 8]},
    {"id": "H2c", "claim": "No workload process is killed under controller-only STASIS",
     "metric": "kills", "equals": 0, "stasis": "ctrl", "N": [4, 6, 8]},
    {"id": "H3",  "claim": "Full STASIS is fairer (Jain) than thrash-protect, oomd and MGLRU",
     "metric": "jain", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["thrashprotect", "oomd", "mglru"]},
    {"id": "H3c", "claim": "Controller-only is fairer than thrash-protect",
     "metric": "jain", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["thrashprotect"]},
    {"id": "H4",  "claim": "The protected probe's p99 latency is lower under full STASIS",
     "metric": "p99_us", "better": "lower", "stasis": "stasis", "N": [8],
     "baselines": ["eevdf", "ctrl", "mglru", "oomd", "thrashprotect"]},
    {"id": "H5",  "claim": "Scheduler PFF parks jobs earlier than the controller alone",
     "metric": "ttfp_s", "better": "lower", "stasis": "stasis", "N": [6, 8],
     "baselines": ["ctrl"]},
    {"id": "H6",  "claim": "The starvation bound holds: no job stays parked more than 180 s (120 s, then one swap at a time)",
     "metric": "max_wait_s", "at_most": 180, "stasis": "stasis", "N": [6, 8]},
    {"id": "H6c", "claim": "The starvation bound holds under controller-only STASIS",
     "metric": "max_wait_s", "at_most": 180, "stasis": "ctrl", "N": [6, 8]},
    {"id": "H7",  "claim": "Fair rotation shortens the longest time a job goes without progress",
     "metric": "max_gap_s", "better": "lower", "stasis": "stasis", "N": [8],
     "baselines": ["stasis-norot"]},
    {"id": "H7b", "claim": "No job goes as long without progress under STASIS as under thrash-protect or MGLRU",
     "metric": "max_gap_s", "better": "lower", "stasis": "stasis", "N": [8],
     "baselines": ["thrashprotect", "mglru"]},
    {"id": "H8",  "experiment": "B", "claim": "Measured working sets beat footprints when part of each job's memory is cold",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [6, 8],
     "baselines": ["stasis-fp"]},
    {"id": "H9",  "experiment": "B", "claim": "With measured working sets, STASIS beats EEVDF when the hot sets overcommit memory",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["eevdf"]},
    {"id": "H5c", "experiment": "C", "claim": "When working sets jump mid-run, the scheduler's PFF warning parks sooner than PSI alone",
     "metric": "detect_s", "better": "lower", "stasis": "stasis", "N": [8],
     "baselines": ["ctrl"]},
    {"id": "H5d", "experiment": "C", "claim": "The early warning pays off: through a working-set jump, full STASIS keeps higher throughput than controller-only",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["ctrl"]},
    {"id": "H11", "experiment": "C", "claim": "Through a working-set jump, full STASIS keeps higher throughput than EEVDF",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["eevdf"]},
    {"id": "H10-q5", "claim": "Full STASIS still beats every baseline at N = 8 with a 5 s rotation quantum (--quantum=5)",
     "metric": "throughput", "better": "higher", "stasis": "sens-q5", "N": [8],
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]},
    {"id": "H10-q30", "claim": "Full STASIS still beats every baseline at N = 8 with a 30 s rotation quantum (--quantum=30)",
     "metric": "throughput", "better": "higher", "stasis": "sens-q30", "N": [8],
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]},
    {"id": "H10-w60", "claim": "Full STASIS still beats every baseline at N = 8 with a 60 s starvation bound (--max-wait=60)",
     "metric": "throughput", "better": "higher", "stasis": "sens-w60", "N": [8],
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]},
    {"id": "H10-w240", "claim": "Full STASIS still beats every baseline at N = 8 with a 240 s starvation bound (--max-wait=240)",
     "metric": "throughput", "better": "higher", "stasis": "sens-w240", "N": [8],
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]},
    {"id": "H10-t50", "claim": "Full STASIS still beats every baseline at N = 8 with a 50 ms PSI trigger (--trigger-ms=50)",
     "metric": "throughput", "better": "higher", "stasis": "sens-t50", "N": [8],
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]},
    {"id": "H10-t200", "claim": "Full STASIS still beats every baseline at N = 8 with a 200 ms PSI trigger (--trigger-ms=200)",
     "metric": "throughput", "better": "higher", "stasis": "sens-t200", "N": [8],
     "baselines": ["eevdf", "mglru", "oomd", "thrashprotect"]}
  ]
}
```

H6's 180 s: a halving at N = 8 parks 4 jobs at once, so up to 4 pass the
bound together. The bound swaps them in one at a time, each once the one
before has reloaded and had 10 s not stalled on memory: 120 + 3 × (10 s +
one reload). On the lab VM's swap a 700 MB reload takes a few seconds; on
the emulated rehearsal VM, three jobs passing the bound together waited up
to 140 s. If more jobs are parked at once, or reloads are slow, the wait can
exceed 180 s: the hard guarantee is only that each swap waits at most 120 s
for the one before it.

Expected losses, reported honestly: at N ≤ 4 everything fits, so the
controller can only add overhead. In experiment A, `stasis-fp` against
`stasis` measures what sampling working sets costs when it can't help. In
experiment B at N = 6 the hot sets fit: STASIS should only match EEVDF there,
and `stasis-fp` should lose (it parks jobs it doesn't need to). Each run's
`experiment` file decides which hypotheses `analyze.py score` checks.
