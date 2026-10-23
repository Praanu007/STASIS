# STASIS architecture

STASIS is medium-term scheduling for Linux: when the jobs it manages need more
memory than the machine has, it decides how many of them run at once and
**parks** the rest, so memory overcommit costs waiting instead of thrashing or
killed processes. This is Denning's working-set load control (1968), rebuilt
on cgroup v2. Every job runs as its own systemd service in `stasis.slice`, with
its own cgroup.

## Four layers

| Layer | Job | Mechanism | Code |
|---|---|---|---|
| 1. Sensing | notice thrashing | PSI memory trigger (`full 100000 1000000`: 100 ms of full stall in a 1 s window); per-job page-fault frequency (PFF, major faults per ms of the job's own CPU) from layer 4, or `workingset_refault_anon` without it | `stasisd` |
| 2. Demand | estimate what each job needs | Denning's W(t, τ) measured from referenced bits; the footprint as an upper bound until a job has a trusted sample | `stasisd`, `WorkingSets` |
| 3. Load control | decide who runs | halve on thrashing, admit when calm and the working sets fit, rotate fairly, bound the wait; park with `cgroup.freeze`; page parked jobs out with `memory.reclaim` | `stasisd` main loop, `Reclaimer` |
| 4. Scheduling | keep the machine responsive | a sched_ext scheduler: exports per-process PFF every 100 ms, and runs protected tasks (the probe, a shell) from a priority queue that preempts batch work | `scx/scx_stasis.bpf.c` |

Layers 1–3 run without layer 4 (the `ctrl` configuration, under stock EEVDF).
Layer 4 never parks anything: it only measures and protects.

## Why parking uses the freezer, not the scheduler

A sched_ext scheduler could "park" a job by never dispatching its tasks, but
the kernel won't allow that for long. sched_ext aborts a BPF scheduler, and
reverts every task to the fair class, when a runnable task stalls; the
scheduler's `timeout_ms` sets how long that may be, at most
`SCX_WATCHDOG_MAX_TIMEOUT`, 30 s. Parking lasts minutes. A frozen cgroup's
tasks aren't runnable at all ("all belonging processes will be stopped and
will not run until the cgroup will be explicitly unfrozen", cgroup-v2.rst), so
parking can't trip the watchdog. The freezer also:

- works without sched_ext, so the controller can be evaluated alone;
- acts on a whole job (all its processes) at once and is reported in
  `cgroup.events`;
- doesn't use signals, so no parent shell sees its child stop, unlike
  `SIGSTOP`, which thrash-protect uses for system services.

## The control law

```
pressure (PSI trigger, or PFF > PFF_HI for 3 samples while not growing)
   └─ warm-up? (the active set's working sets fit and it is still growing,
   │            reloading, or waiting for parked jobs to be paged out)
   │     yes → halve nothing
   │     no  → park half the active set: the jobs with the most user CPU in
   │           their current turn (cooldown 2 s)
calm for 5 s, active set settled
   └─ admit the job that has waited longest, if its working set fits too
a job has had QUANTUM (15 s) of user CPU in its turn, and jobs are waiting
   └─ rotate: park it, resume the job that has waited longest (one swap at a time)
the longest-waiting job has waited MAX_WAIT (120 s)
   └─ the bound: swap it in for the job with the most of its turn (one swap at a time)
```

**Making room.** Linux's reclaim doesn't prefer a frozen job's pages, so
without help the running jobs evict each other while parked jobs keep their
memory. When the active set's missing pages exceed free memory, a background
thread writes whole byte counts to the parked jobs' `memory.reclaim`, largest
first, in 32 MiB chunks.

**Warm-up isn't thrashing.** A job that was just thawed, or that survived a
halving, faults its pages back in. Its PFF is high and PSI may fire, but if the
active set's working sets fit, halving again can't bring its pages back any
faster. Pressure only halves the set when the set doesn't fit, or when it fits,
is resident, has room and still thrashes, which means an estimate was wrong:
then the active jobs' measured working sets are forgotten.

**When the early warning can lead.** PSI and PFF see different things. PSI
`full` counts time in which every runnable task is stalled on memory; PFF
counts one job's major faults per millisecond of its own CPU. When jobs start
together and allocate more than fits, the first stall is reclaim during those
allocations, before any page has been swapped out and touched again: there are
no major faults yet, so PSI always fires first (every rehearsal run). PFF can
only lead when thrashing starts from refaults in some jobs while others keep
the CPUs busy, so the machine as a whole isn't fully stalled yet. Experiment C
(`docs/00-prereg.md`, deviation 22) creates exactly that onset, by making half
the jobs' working sets jump mid-run.

## Fairness and the starvation bound

Every job takes turns. A turn starts when a job is resumed and is measured in
the job's own **user** CPU time, because faulting pages back in is kernel time
and a slow swap device shouldn't use up a turn before any work is done.
Halvings and rotations park the jobs that have had the most of their turn; the
job resumed is always the one that has waited longest (FIFO).

**One swap at a time.** A swap (a rotation, or a rescue by the bound) trades a
resident job for one that must fault its whole working set back in. Swaps that
overlap compete for the swap device, so each takes longer; on a slow device the
swaps can take longer than the turns they buy, until every active job is
reloading and none computes. The simulator livelocks exactly this way at
3 MB/s swap-in. So a swap waits until the job resumed before it has reloaded:
until it has had MIN_TURN = min(10 s, MAX_WAIT / 4, QUANTUM) **not stalled on
memory** (its cgroup's PSI), or MAX_WAIT has passed. Admission is exempt: it
adds a job to a set that has room, so nobody else loses pages.

**The bound.** If no turn ends (a job that sleeps, or one starved of CPU), the
longest-waiting job is swapped in once it has waited MAX_WAIT, for the active
job with the most user CPU in its turn. What this guarantees:

- every parked job is eventually resumed while rotation is on: each swap waits
  at most MAX_WAIT for the one before it, so with P jobs parked none waits more
  than about P × MAX_WAIT, even if no turn ever ends;
- with ordinary reload times, a job waits MAX_WAIT plus MIN_TURN plus one
  reload for each job ahead of it in the queue (H6 tests this at N = 6 and 8).

`--no-rotation` turns rotation and the bound off. That is the fairness
ablation: parked jobs then wait until memory frees up.

## Working-set estimation

Denning's working set W(t, τ) is the memory a process referenced during the
last τ of its own execution time. stasisd measures it per job:

1. clear the referenced bits of the job's processes: `echo 1 >
   /proc/PID/clear_refs` resets "the PG_Referenced and ACCESSED/YOUNG bits"
   (proc.rst);
2. let the job run τ = 1 s of its own user CPU;
3. read `Referenced` from `/proc/PID/smaps_rollup`: the memory "currently
   marked as referenced or accessed", summed over all mappings.

A window is **trusted** only if the job ran it unfrozen, stalled on memory
less than 2% of it, and grew its footprint less than 0.5%/s: a thrashing job
references only what it manages to keep resident, which understates its
demand. The estimate is 1.10 × the largest of its last 10 trusted samples,
never more than its footprint (resident + swapped out − swap cache: everything
it has touched). Until a job has a trusted sample, the footprint is used. That
is an upper bound, so the controller errs towards parking.

**What it costs.** Clearing referenced bits makes the job's pages look idle
to reclaim (MGLRU ages pages by their accessed bits). The first version opened
a new window as soon as the last closed, under pressure too, where a window is
thrown away anyway: on the rehearsal VM at 60% hot memory no job had a trusted
window before 129 s. The measurements themselves were right: every job read
419–420 MiB, its hot set (60% of 700 MiB). That run was also slower than the
same run with footprints (384 s against 253 s, with 78% more major faults),
but one run each can't separate the sampler's cost from run-to-run variation:
two runs of the final version in the same configuration took 231 s and 348 s.
Now a window opens at most every 10 s per job (`WS_PERIOD`), and only while
the job isn't stalling on memory (its PSI `some avg10` under 2%, and no stall
since the sampler last looked, 0.5 s before), so under pressure the sampler
leaves the job's bits alone. Experiment B, five
runs per cell on the lab VM, measures what is left of the cost against what
the measurements buy.

**What it is not.** It is a sample of the accessed bits over 1 s of CPU, not
a full reuse-distance measurement: a job whose reuse distance is longer than τ
is underestimated (the margin and the 10-sample maximum soften this), and an
estimate can be wrong in either direction until the job is measured again. It
is a measurement, but a coarse one, and the evaluation compares it with
footprints (`stasis-fp`) in both experiments.

**Why not DAMON.** DAMON is the kernel's own access monitor (region sampling
with adaptive regions), and DAMON_RECLAIM uses it to page out memory that
"didn't accessed for specific time duration" under light pressure. It would be
the better sampler. Ubuntu 26.04's kernel, `7.0.0-38-generic`, is built with
`# CONFIG_DAMON is not set`; `scripts/preflight.sh` reports this. Referenced
bits need only `CONFIG_PROC_PAGE_MONITOR`, which it has. The sampler is one
class (`WorkingSets`), so a DAMON-backed one could replace it.

## Safety

- stasisd manages only cgroups named `stasis-job-*` directly under
  `stasis.slice`, and refuses any that contain its own process.
- It never sends a signal and never kills. On SIGTERM, SIGINT or any crash it
  thaws every job; `run_mix.sh` also thaws them if stasisd dies.
- It locks itself in memory (`mlockall`; threads get 256 KiB stacks), so it
  keeps running while the machine thrashes.
- **No promise about OOM.** Parked jobs' memory goes to swap. If swap runs
  out, or one job's working set alone exceeds RAM, the kernel's OOM killer (or
  systemd-oomd, where it is enabled) can still kill. The evaluation counts
  every kill, for every configuration (H2, H2c).
- `scx_stasis` can be ejected like any sched_ext scheduler (watchdog, SysRq-S,
  or an error); the kernel then falls back to EEVDF, and the run records it as
  `SCHED_EJECTED`.

## Limitations

- One job whose working set exceeds RAM thrashes on its own: load control
  can't help it.
- Only batch jobs in `stasis.slice` are managed. Interactive programs aren't
  parked, only protected by layer 4.
- Parking trades latency for throughput: a parked job makes no progress. The
  bound limits the wait; it doesn't remove it.
- Clearing referenced bits still costs a little even when it's paced: a
  window makes a running job's pages look idle for up to a second, every
  10 s.
- PSI "some" for a multi-threaded job counts time when any of its threads is
  stalled, so the not-stalled time used by MIN_TURN is conservative for such
  jobs.

## Sources

- cgroup v2 (`cgroup.freeze`, `memory.reclaim`, `memory.peak`, `memory.pressure`): <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cgroup-v2.rst>
- `/proc/PID/clear_refs`, `smaps_rollup`: <https://github.com/torvalds/linux/blob/master/Documentation/filesystems/proc.rst>
- sched_ext (stalled runnable tasks abort the BPF scheduler): <https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-ext.rst>; `SCX_WATCHDOG_MAX_TIMEOUT = 30000` in the kernel's BTF (`vmlinux.h` of 6.18, scx repository)
- MGLRU (`min_ttl_ms`, accessed-bit aging): <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/multigen_lru.rst>
- DAMON and DAMON_RECLAIM: <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/damon/usage.rst>, <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/damon/reclaim.rst>
- P. J. Denning, "The working set model for program behavior", CACM 11(5), 1968; "Thrashing: its causes and prevention", AFIPS FJCC 1968.
