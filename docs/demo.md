# Phase 6: live demo (3 minutes) and defence

Record a video of the whole thing first, in case the VM misbehaves on the day.

## Layout

Split screen (tmux: `tmux new -s demo \; split-window -h`).

- Left pane: a shell where you type `ls`. Note its PID with `echo $$`.
- Right pane: `watch -n1 cat /proc/pressure/memory`.
- A third window follows decisions: `tail -f /tmp/demo2/decisions.jsonl`.

## Script

1. **EEVDF, the problem (60 s).**
   `sudo /path/to/stasis/scripts/run_mix.sh 8 700 $PASSES /tmp/demo1 eevdf`
   Type `ls` in the left pane: it lags, and PSI `full` climbs. Ctrl-C the run.
   The cleanup trap stops the jobs.
2. **STASIS (90 s).**
   `sudo DEMO_SHELL=<pid of left shell> STASISD_EXTRA=... /path/to/stasis/scripts/run_mix.sh 8 700 $PASSES /tmp/demo2 stasis`
   The shell stays responsive. Freeze and thaw lines scroll in the decision log,
   and `journalctl -k | grep -i killed` prints nothing.
3. **Show F1 and F3 (30 s).** The thrashing curve with STASIS's plateau, and
   the fairness bars.

Note: `DEMO_SHELL` adds the shell to the scheduler's protected set (`-p`).

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
