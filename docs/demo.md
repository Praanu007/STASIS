# Phase 6: live demo (3 minutes) and defence

Record a video of the whole thing first, in case the VM misbehaves on the day.

## Layout

Split screen on the Windows host:

- **Left:** a terminal with SSH into the VM. This shell is the "interactive
  user". Note its PID with `echo $$`.
- **Right:** the STASIS Control Room in the host's browser. Start it in a
  second SSH session:
  ```bash
  cd ~/stasis && sudo python3 dashboard/server.py --results /srv/results
  ```
  Then open `http://<vm-ip>:8686`. Use the **2 min** window.

The dashboard follows whatever run `run_mix.sh` is writing (it reads
`/run/stasis/current`). It shows memory stall, one lane per job (heat for
fault rate, frost for parked), the controller's decisions and the scheduler's
dispatch counters.

## Script

1. **EEVDF, the problem (60 s).**
   `sudo scripts/run_mix.sh 8 700 $PASSES /tmp/demo1 eevdf`
   The hero number climbs past the trigger line and the status turns
   **Thrashing**. Every lane glows, throughput drops to almost nothing, and
   `ls` in the left pane lags. Press Ctrl-C; the cleanup trap stops the jobs.
2. **STASIS (90 s).**
   `sudo DEMO_SHELL=<pid of left shell> STASISD_EXTRA=--pff-hi=<frozen> scripts/run_mix.sh 8 700 $PASSES /tmp/demo2 stasis`
   Point at four things on the dashboard:
   - the first **Halve** decision parks half the jobs (their lanes frost over),
   - the stall falls back under the trigger line and the status returns to
     **Calm**,
   - **Rotate** decisions once a running job has had 15 s of CPU, each
     resuming the job that has waited longest ("after waiting 38 s"), so
     every job keeps making progress,
   - each lane's **working set** line: measured from referenced bits, or the
     footprint until the job has been measured.

   `ls` stays responsive, and **Processes killed** stays at 0.
3. **Results (30 s).** Switch to the **Results** tab: the throughput curve
   with STASIS's plateau, and the pre-registered hypotheses with their
   pass/fail verdicts.

`DEMO_SHELL` adds the shell to the scheduler's protected set (`-p`), so it
sits on the priority queue with the latency probe.

If the VM won't cooperate, use **Replay** to play back a recorded run at 4×.
The **Simulator** tab explains the mechanism without a VM at all: it runs
the same control law on a model of the 4 GiB VM. Set **Hot memory** to 60%
and switch **Working sets** between Measured and Footprint to show what
measuring buys: measured, more jobs fit at once.

## Questions to rehearse

- **"Isn't this just Denning from 1968?"** The principle is, and so is the
  name: textbooks call it medium-term scheduling. The claim is an engineering
  one, and each part is tested: job-level load control on cgroup v2 beats
  today's page-level and kill-based defences without kills (H1, H2); turns
  in user CPU, FIFO resumption and a starvation bound make it fair (H3, H6,
  H7); measured working sets admit more than footprints when memory is
  partly cold (H8, H9); and a sched_ext scheduler adds an early signal and
  keeps interactive tasks responsive (H4, H5). `docs/related-work.md` has
  the comparison, with sources.
- **"How is this different from thrash-protect?"** thrash-protect 1.1.2
  triggers on swap traffic (weighted up by PSI) and stops processes with
  `SIGSTOP`, resuming mostly the last one stopped; its README says it is
  "not optimized to be 'fair'". STASIS admits a job only when its working
  set fits, resumes first-in-first-out with a bound on the wait, freezes
  whole jobs' cgroups instead of signalling, and pages parked jobs out ahead
  of need. It is a baseline in every comparison (H1, H3, H7b).
- **"Why park with the freezer and not in your BPF scheduler?"** sched_ext
  aborts a BPF scheduler that leaves a runnable task waiting longer than its
  timeout, at most 30 s. Parking lasts minutes. Frozen tasks aren't runnable,
  so the watchdog never sees them, and the controller works under plain
  EEVDF too.
- **"How do you know a job's working set?"** stasisd measures Denning's W(t,
  τ): it clears the job's referenced bits (`/proc/PID/clear_refs`), lets it
  run 1 s of its own CPU, and reads `Referenced` from `smaps_rollup`. On the
  rehearsal VM the measurements read 397–420 MiB, almost all 419–420, for a
  420 MiB hot set. A window counts only if the job didn't stall on memory
  and wasn't still allocating, and windows are paced (one per 10 s per job,
  none while the job stalls), because clearing the bits makes pages look
  idle to reclaim, and a window in which the job stalls is thrown away
  anyway. Until a job is measured, its footprint is used. DAMON would be
  better, but the Ubuntu 26.04 kernel doesn't enable it.
- **"Can a job starve?"** Not while rotation is on. A job that has waited
  120 s is swapped in even if no turn ends, one swap at a time, so each
  waits at most 120 s for the one before it. H6 checks no park exceeds
  180 s at N = 6 and 8, and the `stasis-norot` ablation shows what happens
  without rotation.
- **"Can STASIS cause an OOM kill?"** It never kills anything, but it can't
  stop the kernel: if swap fills, or one job alone needs more than RAM, the
  OOM killer can still act. That's why kills are measured for every
  configuration, STASIS included (H2).
- **"Can a parked job deadlock the system?"** Not at the kernel level.
  Freezing happens at signal-delivery points, so a frozen job holds no kernel
  locks. Locks shared in user space are out of scope, and the workloads share
  nothing.
- **"What if your BPF code is buggy?"** sched_ext's watchdog and SysRq-S
  return the system to EEVDF. Show the dmesg line from a deliberate fault
  test.
- **"Why doesn't one halving turn into a cascade?"** The jobs that survive a
  halving have to fault their pages back in, which looks like pressure for a
  while. If the active set's working sets fit, that pressure is a transition:
  halving more jobs can't bring their pages back faster. So stasisd treats
  it as warm-up while the set is still growing, still has pages in swap, or
  is still waiting for parked jobs to be paged out. Only pressure on a set
  that fits, is resident and has room means an estimate is wrong. The
  guide's original rule cascaded 7 → 3 → 1 within 4 s on the rehearsal VM
  (`docs/rehearsal/history/1-first-warm-up-guard`).
- **"Why does the probe stay responsive when the jobs run flat out?"**
  scx_stasis queues protected tasks first, and a waking one preempts a batch
  job (`SCX_KICK_PREEMPT`) instead of waiting up to a 20 ms slice. The
  rehearsal showed why that matters: without the kick, the probe's p99 was
  worse under STASIS than under EEVDF.
