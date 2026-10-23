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
   Point at three things on the dashboard:
   - the first **Halve** decision parks half the jobs (their lanes frost over),
   - the stall falls back under the trigger line and the status returns to
     **Calm**,
   - **Rotate** decisions every 15 s, so every job keeps making progress.

   `ls` stays responsive, and **Processes killed** stays at 0.
3. **Results (30 s).** Switch to the **Results** tab: the throughput curve
   with STASIS's plateau, and the pre-registered hypotheses with their
   pass/fail verdicts.

`DEMO_SHELL` adds the shell to the scheduler's protected set (`-p`), so it
sits on the priority queue with the latency probe.

If the VM won't cooperate, use **Replay** to play back a recorded run at 4×.
The **Simulator** tab explains the mechanism without a VM at all: it runs
the same control law on a model of the 4 GiB VM.

## Questions to rehearse

- **"Isn't this just Denning from 1968?"** Yes, the idea is. The contribution
  is fault frequency measured inside a pluggable CPU scheduler, plus fair
  rotation, plus a measured comparison against today's defences. The ablation
  (F5) shows what the scheduler part adds.
- **"How is this different from thrash-protect?"** thrash-protect has no
  fairness (its README says so), no admission control and no scheduler
  involvement. Point to F3 and F5.
- **"Can a parked job deadlock the system?"** Not at the kernel level.
  Freezing happens at signal-delivery points, so a frozen job holds no kernel
  locks. Locks shared in user space are out of scope, and the workloads share
  nothing.
- **"What if your BPF code is buggy?"** sched_ext's watchdog and SysRq-S
  return the system to EEVDF. Show the dmesg line from a deliberate fault
  test.
- **"Why doesn't one halving turn into a cascade?"** The jobs that survive a
  halving have to fault their pages back in, which looks like pressure for a
  few seconds. stasisd only halves when the active set's resident memory has
  stopped growing: faulting that grows the resident set is warm-up, faulting
  that doesn't is thrashing. The simulator showed the guide's original rule
  cascading 8 → 4 → 2 → 1.
