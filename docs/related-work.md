# Related work: what each defence against thrashing controls

Thrashing is a scheduling problem as much as a memory problem: the jobs that
are running together need more memory than there is. Defences differ in
**what they control**: pages, processes' lives, or which jobs run. The table
classifies the ones STASIS is compared with or builds on. Every row is taken
from the cited source; where a source is a program, from the pinned version.

| System | Controls | Signal | Action under pressure | Fairness between jobs | Kills? |
|---|---|---|---|---|---|
| Linux reclaim + MGLRU `min_ttl_ms` | pages | page age (accessed bits, generations) | keeps "the working set of N milliseconds" resident; "the OOM killer is triggered if this working set cannot be kept in memory" | none (per-page) | yes, by design when the TTL can't be met |
| OOM killer | process lifetime | allocation failure | kills the process with the highest `oom_score` | none | yes |
| systemd-oomd | cgroup lifetime | PSI per cgroup, swap use | "select a cgroup to terminate, and send SIGKILL to all processes in it" | none | yes |
| TMO / Senpai (Meta) | how much memory each container keeps | PSI | proactive reclaim with "mild memory pressure", offloading cold memory to compressed memory or SSD | not its goal | no |
| DAMON_RECLAIM | pages | DAMON access sampling | pages out regions not accessed for `min_age`, "under light memory pressure" | none | no |
| thrash-protect 1.1.2 | which processes run | swap-in × swap-out per interval above a threshold, weighted up by PSI (× (1 + `some avg10` / 5%) by default); OOM prediction | `SIGSTOP` on the "most nasty process" (cgroup freeze only for user-session scopes such as tmux); resumes one when swapping stops, mostly LIFO (`unfreeze_pop_ratio` 5) | "not optimized to be 'fair'" (README) | no |
| EEVDF (Linux's fair class) | CPU time | none about memory | none | fair CPU shares, so every thrashing job keeps faulting | no |
| sched_ext | CPU dispatch, in BPF | whatever the BPF program reads | can't hold runnable tasks for long: stalled runnable tasks abort the scheduler (≤ 30 s) | up to the scheduler | no |
| Denning's load control (1968) | which jobs are in memory | working sets W(t, τ) | run a job only when its working set fits beside the others | admission order | no |
| PFF (Chu & Opderbeck, 1972) | each job's memory allotment | page-fault frequency | grow a job's allotment when its faults are frequent, shrink it when rare | none | no |
| **STASIS** | which jobs run | PSI trigger + per-job PFF from sched_ext; measured working sets | parks whole jobs with `cgroup.freeze`, admits by working set, pages parked jobs out with `memory.reclaim` | FIFO turns in user CPU time, starvation bound, one swap at a time | no (the kernel still can; it is measured) |

## Where STASIS sits

**Closest prior work: thrash-protect.** It is the existing Linux program that
suspends processes when the machine thrashes, so it is the baseline STASIS
must beat on its own ground. The differences are the parts STASIS adds:

- **Admission by working set.** thrash-protect resumes a process when
  swapping stops. STASIS resumes a job only when its working set fits beside
  the active jobs' (Denning's test), using measured working sets.
- **Fairness with a bound.** thrash-protect's README says it is "not
  optimized to be 'fair'", and it resumes mostly last-in-first-out. STASIS
  resumes first-in-first-out, rotates turns measured in user CPU, and bounds
  the wait.
- **Whole jobs, no signals.** thrash-protect stops processes with `SIGSTOP`
  (freezing cgroups only for user-session scopes), which its README warns can
  confuse parent processes. STASIS freezes each job's cgroup.
- **Making room.** STASIS pages parked jobs out ahead of need, so running jobs
  don't evict each other while parked ones keep their memory.

**Complementary: page-level defences.** MGLRU's `min_ttl_ms`, TMO and
DAMON_RECLAIM decide which pages stay in memory. None of them decides which
jobs run, so when the running jobs' working sets genuinely exceed memory they
can only evict pages someone needs (and MGLRU then calls the OOM killer). STASIS
uses the same kernel mechanism as TMO, `memory.reclaim`, but only on jobs it
has parked. DAMON would be a better working-set sampler than referenced bits
(see `architecture.md`); the lab kernel doesn't have it.

**Kill-based defences** (the OOM killer, systemd-oomd) end the overload by
ending jobs. STASIS never kills; the evaluation counts the kernel's kills under
every configuration rather than assuming there are none.

## The claim, reframed

Load control by working sets is Denning's 1968 idea, and textbooks call it
medium-term scheduling. STASIS doesn't claim a new principle. Its claim is an
engineering one, with each part tested:

1. Job-level load control on cgroup v2 (freeze, `memory.reclaim`, per-cgroup
   PSI) beats page-level and kill-based defences on throughput under memory
   overcommit, without kills (H1, H2).
2. With turns in user CPU time, FIFO resumption and a starvation bound, it is
   fairer than thrash-protect and the page-level defences, and no job goes as
   long without progress (H3, H6, H7).
3. Measured working sets (referenced bits, sampled only while a job isn't
   stalling) admit more than footprints when part of each job's memory is cold
   (experiment B, H8, H9).
4. A sched_ext scheduler can supply an earlier signal (PFF) and protect
   interactive tasks while jobs are parked (H4, H5).

## Sources

- MGLRU: <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/multigen_lru.rst>
- systemd-oomd: <https://github.com/systemd/systemd/blob/main/man/systemd-oomd.service.xml>
- DAMON: <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/damon/usage.rst>, <https://github.com/torvalds/linux/blob/master/Documentation/mm/damon/design.rst>, DAMON_RECLAIM: <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/mm/damon/reclaim.rst>
- thrash-protect: README at <https://github.com/tobixen/thrash-protect>; 1.1.2 from PyPI (<https://pypi.org/pypi/thrash-protect/json>): defaults `use_psi = True`, `psi_threshold = 5.0` (PSI multiplies the swap-traffic trigger by 1 + some avg10 / 5), `oom_protection = True`, `unfreeze_pop_ratio = 5`, and cgroup freezing only for `.scope` cgroups under `user@NNN.service/`, read from its source
- sched_ext: <https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-ext.rst>
- cgroup v2: <https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cgroup-v2.rst>
- J. Weiner et al., "TMO: Transparent Memory Offloading in Datacenters", ASPLOS 2022, <https://doi.org/10.1145/3503222.3507731>
- P. J. Denning, "The working set model for program behavior", Communications of the ACM 11(5), 1968; "Thrashing: its causes and prevention", AFIPS Fall Joint Computer Conference, 1968.
- W. W. Chu and H. Opderbeck, "The page fault frequency replacement algorithm", AFIPS Fall Joint Computer Conference, 1972.
