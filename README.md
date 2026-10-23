# STASIS: Scheduling To Avert Swap-Induced Stalls

STASIS stops a Linux machine from thrashing by **pausing** memory-heavy batch
jobs fairly instead of killing them. This is Denning's working-set load
control, built from three parts:

| Part | What it does |
|---|---|
| **Jobs** | Each memory-heavy job runs as its own systemd service in `stasis.slice`, with its own cgroup for memory, CPU and freezing. |
| **stasisd** (`controller/stasisd.py`, Python stdlib only) | Listens for PSI triggers. On real thrashing it halves the active set. When things are calm it admits jobs whose working sets fit. It rotates parked jobs fairly and parks them with `cgroup.freeze`. |
| **scx_stasis** (`scx/`, sched_ext BPF) | A vtime scheduler based on `scx_simple`. It measures each process's **page-fault frequency** (major faults per ms of its own CPU time) and keeps protected interactive tasks on a priority queue. It never parks anything. |

The **STASIS Control Room** (`dashboard/`) shows all of it live in a browser:
memory stall, one lane per job (heat for page-fault rate, frost for parked),
the controller's decisions, the scheduler's dispatch counters and memory. It
also replays recorded runs, plots results against the pre-registered
hypotheses, and includes a simulator of the control law.

## Repository

```
workloads/wss.c            synthetic working-set job (prints "pass wallclock")
scripts/setup_vm.sh        Phase 0 provisioning (run once in the guest, as root)
scripts/check_gate0.sh     Phase 0 exit criteria
scripts/calibrate.sh       pick PASSES so one job runs ~60 s alone
scripts/run_mix.sh         one run: N jobs + probe + sampler [+ config]
scripts/sweep.sh           N-sweep for one config (Phase 1, Phase 3 gate)
scripts/sample.py          1 Hz sampler -> samples.jsonl (also used by the dashboard)
scripts/check_faults.sh    Phase 3 fault-count correctness check
scripts/manifest.sh        per-run MANIFEST (kernel, commits, sysctls)
controller/stasisd.py      Phase 2 controller (+ Phase 4 --signal=bpf)
scx/scx_stasis.bpf.c       Phase 3 BPF scheduler
scx/scx_stasis.c           loader: -p TGID, -w MS; exports /run/stasis/{pff,stats}
scx/install_into_scx.sh    drops scx_stasis into a scx-c-examples checkout
eval/campaign.sh           Phase 5 randomized run matrix (resumable)
eval/analyze.py            metrics.csv, PFF calibration, figures F1-F5, score.md
dashboard/server.py        Control Room server (stdlib only, read-only, mlocked)
dashboard/index.html       Control Room UI (Live, Replay, Results, Simulator)
tests/test_controller.sh   stasisd regression tests on fake cgroups (11 checks)
docs/00-prereg.md          thresholds, hypotheses and deviations, frozen at Gate 1
docs/demo.md               Phase 6 demo script and defence questions
docs/rehearsal/            the kernel 7.0 rehearsal: runs, logs, what changed (not results)
MANIFEST                   one line per run
```

## What has been verified

Everything below ran on a rehearsal VM built from the official **Ubuntu 26.04
cloud image, kernel 7.0.0-38-generic**, with 4 vCPUs and 4 GB of RAM (the
same shape as the lab VM). It was emulated in software, so it was much slower
than real hardware, above all its swap (about 3 MB/s in). The numbers are a
dress rehearsal, not results: `docs/rehearsal/README.md` has the details, the
caveats, and the logs.

| Check | Result |
|---|---|
| `setup_vm.sh`, unmodified | ✅ ran end to end in 3 min 41 s: swap, zswap, toolchain, pinned scx clone, both schedulers built and installed |
| Gate 0 (`check_gate0.sh`) | ✅ all checks pass; scx_simple attaches and Ctrl-C returns to EEVDF |
| Phase 1: EEVDF | ✅ N=2 finishes 120/120 passes in 19 s; N=7 collapses to 4/420 in 300 s |
| Phase 2: controller only, N=7 | ✅ 420/420 passes in 608 s, 0 kills (EEVDF: 4 passes in 300 s) |
| Phase 3 rung 1: verifier | ✅ the 7.0 verifier accepts scx_stasis: `sched_ext: BPF scheduler "stasis" enabled` |
| Rung 2: fault counts | ✅ BPF and `/proc` agree exactly (17,645 = 17,645 major faults) |
| Rung 3: priority path | ✅ 133 priority dispatches/s for the protected cyclictest under 8 CPU hogs |
| Rung 4: survives load | ✅ 90 s of stress-ng, no ejection |
| Phase 4: full STASIS, N=7 | ✅ 420/420 passes in 284 s, 0 kills, no ejection |
| Dashboard | ✅ live, replay and results views against these runs, no console errors |

**Changed after the rehearsal, not yet run on a kernel.** Reading the final
runs' logs turned up three more problems. Their fixes are checked by
`tests/test_controller.sh` and the simulator, and `scx_stasis` still compiles
against the pinned scx-c-examples. The first real Phase 2–4 runs are their
first test on a kernel:

- **Sets that fit were halved while reloading** (deviation 10). Both runs
  halved again after the first halving, 7 times in all, each time with the
  set's working sets fitting and a job mid-reload.
- **The probe waited for a batch job's slice** (deviation 11). Its p99 was
  16.0 ms under full STASIS against 8.1 ms controller-only, because nothing
  preempted the batch jobs. `scx_stasis` now kicks the CPU.
- **Runs inherited the last run's PSI** (deviation 12). `run_mix.sh` now
  waits for it to settle.
- **Fractional byte counts** went to `memory.reclaim` whenever more than 10%
  of memory was free. The kernel rejects those, so nothing was paged out in
  that case.

The rehearsal found problems before these too. The control-law ones are
deviations 2–9 in `docs/00-prereg.md`, each with the rehearsal run that showed
it (`docs/rehearsal/history/`):

- **Admission blocked rotation.** In the guide's controller code, admission
  and rotation are an `if/elif` chain, so rotation stopped for good after 5 s
  of calm.
- **Halvings cascaded.** Jobs refaulting after a halving keep PSI high for a
  few seconds, so the guide's rule kept halving: 7 → 3 → 1 within 4 s, and 1
  pass out of 420 in 300 s.
- **Admission deadlocked, then over-admitted.** The guide's admission test
  counted frozen jobs' memory as unavailable, so nothing was admitted once
  anything was parked. Denning's test fixed that, but it admitted jobs
  before their working sets were known.
- **Rotation ran out the quantum during reload.** A wall-clock quantum, and
  then total CPU time, both expired while a thawed job was still faulting
  its pages back in. The quantum is now the job's user CPU time.
- **Parked jobs kept their memory.** Linux evicted running jobs' pages
  instead, so stasisd now pages parked jobs out through `memory.reclaim`.
- **Warm-up looked like thrashing.** A thawed job refaulting its pages looked
  like thrashing to the PFF early warning, and on slow swap a 1 s growth
  window read a flat second as thrashing.
- **No watchdog knob.** Kernel 7.0 has no `/sys/kernel/sched_ext/watchdog_timeout_ms`.
  The loader takes `-w MS` instead.
- **Priority path needs contention.** On an idle machine, protected tasks
  dispatch straight to idle CPUs, so rung 3 must run under CPU load.
- **Unfinished runs looked finished.** A run cut short by a wedged guest used
  to look finished. Runs now say `RUNNING` until they end, and sweeps re-run
  them.
- **Kill counts leaked between runs.** Kills were attributed with a 25-minute
  look-back, which counted the previous run's kills again. They're now taken
  from the run's own start time.

## Runbook, phase by phase

Every phase ends in a gate. Don't start the next phase until the gate passes.
Phases 1–2 alone already make a reportable result.

### Phase 0: lab setup

1. Build the VM on the Windows host. Use Hyper-V (Gen 2, **Dynamic Memory
   off**, 4096 MB, 4 vCPU, 40 GB, "Microsoft UEFI Certificate Authority"
   template) or VirtualBox with fixed memory. Don't use WSL2. Install Ubuntu
   26.04 LTS Server, which ships kernel 7.0 with sched_ext built in.
2. Copy the project in (from Windows PowerShell; OpenSSH is built in), then
   provision the guest:
   ```bash
   scp stasis.tar.gz <user>@<vm-ip>:~        # on the host
   tar xzf stasis.tar.gz && cd ~/stasis      # in the guest
   sudo scripts/setup_vm.sh                  # packages, 8 GB swap, zswap off, toolchain, scx build
   sudo scripts/check_gate0.sh
   sudo tests/test_controller.sh             # stasisd's control law, 11 checks, ~2 min
   ```
   The tarball includes the git history, so `git log` works inside it.
   On 26.04 the distro's libbpf (1.6) and bpftool (7.7) are new enough. On
   older releases, `setup_vm.sh` builds both from source: scx-c-examples needs
   libbpf ≥ 1.5 and bpftool ≥ 7.5.
3. Take a VM snapshot named **`clean`**. It's only for recovering a broken
   system (see Phase 5).

**Gate 0:** `check_gate0.sh` passes, meaning scx_simple loads, state reads
`enabled`, Ctrl-C returns to EEVDF, swap is 8 GB and zswap is off. If
`/sys/kernel/sched_ext` is missing, the kernel lacks
`CONFIG_SCHED_CLASS_EXT`. Fix that before anything else.

### Phase 1: workloads and the EEVDF thrashing curve

```bash
sudo scripts/calibrate.sh 700                       # -> PASSES for ~60 s alone
sudo scripts/sweep.sh eevdf "1 2 3 4 5 6 7 8" 5 700 $PASSES /srv/results
python3 eval/analyze.py figures /srv/results        # F1 (EEVDF only so far)
python3 eval/analyze.py calibrate /srv/results      # PFF distribution -> PFF_HI
```

- Check `probe_policy.txt` in any run: every thread must say `SCHED_OTHER`.
  Real-time tasks bypass sched_ext.
- Expected: throughput stays flat up to N≈4 (2.8 GB fits), then collapses.
  If N=8 is still above 50% of peak, raise SIZE.

**Gate 1:** F1 shows a visible collapse. Fill in `docs/00-prereg.md` (SIZE,
PASSES, PFF_HI, and review H1–H5), commit it, and write the hash into the file.
**From then on, no threshold changes.**

### Phase 2: stasisd, controller only

```bash
# unit check: freeze/thaw by hand
J=/sys/fs/cgroup/stasis.slice/stasis-job-1.service
echo 1 | sudo tee $J/cgroup.freeze; cat $J/cgroup.events   # frozen 1; job log stops
echo 0 | sudo tee $J/cgroup.freeze
# full run
sudo scripts/run_mix.sh 8 700 $PASSES /srv/results/ctrl/N8/run1 ctrl
```

**Gate 2:**
- Freezing by hand works.
- `kill -TERM` on stasisd mid-run thaws every job.
- N=8 under the controller finishes all jobs with zero kills, and its
  throughput is above EEVDF's N=8. That's H1–H3 in controller-only mode.

### Phase 3: scx_stasis, the BPF scheduler

Climb the verification ladder one rung at a time:

1. **Loads:** run `sudo /opt/stasis/scx_stasis -w 5000`, then
   `cat /sys/kernel/sched_ext/root/ops` should print `stasis`. If the
   verifier rejects the program, the error names the line. `-w 5000` lowers
   the watchdog to 5 s while developing; kernels since 6.12 have no sysfs
   knob for it.
2. **Counts are correct:** with memory pressure running (for example five
   `/opt/stasis/wss 700 100000 &`), run `sudo scripts/check_faults.sh`. It
   prints PASS when BPF and `/proc` agree within 1%. Put this in the report.
3. **Priority path:** under CPU contention (`stress-ng --cpu 8`), run
   `sudo /opt/stasis/scx_stasis -p $(pgrep cyclictest)`. The `prio=` counter
   should rise at about cyclictest's wake-up rate. On an idle machine it stays
   at 0, because protected tasks go straight to idle CPUs. Also check
   `sudo bpftool map dump name prio_tgid`. Then check that a protected task
   preempts (deviation 11). Under the same load, run cyclictest once under
   EEVDF and once protected:
   ```bash
   sudo cyclictest --policy=other -i 10000 -D 60 -q             # EEVDF: note Max
   sudo cyclictest --policy=other -i 10000 -D 60 -q & sleep 1
   sudo /opt/stasis/scx_stasis -p $(pgrep -n cyclictest)        # Ctrl-C after cyclictest prints
   ```
   Under scx_stasis, Max (µs) must stay well below the 20 ms slice. If it
   sits near 20,000, the preempt kick isn't working.
4. **Survives load:** run `stress-ng --cpu 8 --vm 4 --timeout 600`, then
   `sudo journalctl -k | grep -i sched_ext` should show no ejection. Debug
   output goes to `/sys/kernel/tracing/trace_pipe`, and SysRq-S falls back to
   EEVDF.

**Gate 3:** all four rungs pass. Repeat the N=1…8 sweep under scx_stasis
alone, with no controller:
`sudo scripts/sweep.sh scxonly "1 2 3 4 5 6 7 8" 5 700 $PASSES /srv/results`.
F1 then shows it next to EEVDF, and it must stay within EEVDF's run-to-run
noise. This shows the scheduler alone neither causes nor hides thrashing.

### Phase 4: full STASIS (controller + scheduler)

```bash
export STASISD_EXTRA=--pff-hi=<frozen value>
sudo -E scripts/run_mix.sh 8 700 $PASSES /srv/results/stasis/N8/run1 stasis
grep -E '"src"' /srv/results/stasis/N8/run1/decisions.jsonl   # psi vs pff events
```

**Gate 4:** 3 consecutive N=8 runs finish with zero kills and no watchdog
ejections, and the decision log shows both `psi` and `pff` events. If PFF
never fires first, report that honestly; it's evidence for H5 either way.

### Phase 5: evaluation campaign

```bash
git clone https://github.com/<pinned>/thrash-protect && export THRASH_PROTECT=...
eval/campaign.sh plan /srv/results --sensitivity
# per batch: reboot the VM (fresh kernel state), then
sudo -E STASISD_EXTRA=--pff-hi=<frozen> eval/campaign.sh run /srv/results 700 $PASSES 70
# after each batch, copy the results to the host (PowerShell):
#   scp -r <user>@<vm-ip>:/srv/results .\results
python3 eval/analyze.py metrics /srv/results
python3 eval/analyze.py figures /srv/results
python3 eval/analyze.py score /srv/results docs/00-prereg.md   # pass/fail table
```

- **Reboot between batches, don't restore the snapshot.** Restoring `clean`
  also wipes `/srv/results`. Restore only to recover a broken system, and copy
  the results off first.
- A run reads `RUNNING` until it ends. If the guest wedges mid-run, power-cycle
  it and start the batch again: unfinished runs are deleted and re-run, and
  finished ones are skipped.

Decision rule: a hypothesis passes only if **every** STASIS run beats
**every** baseline run. Report the median and range for everything,
including the metrics STASIS loses on (N ≤ 4).

### Phase 6: write-up, demo and defence

The figures are F1–F5 in `results/figures/`. The demo script and rehearsal
questions are in `docs/demo.md`.

## The Control Room

```bash
cd ~/stasis && sudo python3 dashboard/server.py --results /srv/results
```

It prints the address to open from the host's browser (port 8686). Its four
views are:

- **Live** follows the run `run_mix.sh` is writing, through
  `/run/stasis/current`. With no run it shows the whole machine.
- **Replay** plays back any finished run at 1×, 4× or 16×, with its metrics.
- **Results** shows the throughput-vs-N curve, the pre-registered hypotheses
  with pass/fail verdicts, and every run's metrics. Each chart has a table view.
- **Simulator** runs stasisd's control law on a model of the 4 GB VM. Switch
  the policy to watch EEVDF thrash and STASIS recover. It is a model, and the
  page says so.

The server is read-only and has no authentication; run it only on the lab
VM's private network. It locks itself into RAM so it keeps answering while
the machine thrashes. It's light, but leave it off during campaign batches so
it can't perturb measurements; use Replay afterwards.

## Troubleshooting

| Symptom | Fix |
|---|---|
| EEVDF still above 50% of peak at N=8 | Raise SIZE. Confirm zswap is off and Dynamic Memory is off. |
| Guest wedges during EEVDF runs | Expected at high N. Keep the 1200 s timeout, run baselines last in a batch, power-cycle and re-run the batch. |
| Scheduler ejected (`status` = `SCHED_EJECTED`) | Check PRIO_DSQ membership, and that nothing is held runnable. Read the dmesg lines in `sched_ext_dmesg.txt`. |
| Controller died (`status` = `CTRL_DIED`) | Read `stasisd.log` in the run directory; the run harness thawed the jobs. |
| Verifier rejects | NULL-check every lookup, initialize every variable, and don't use unbounded loops. |
| Jobs stay frozen after a crash | `for f in /sys/fs/cgroup/stasis.slice/*/cgroup.freeze; do echo 0 > $f; done` |
| Controller slow under thrash | Confirm mlockall succeeded. Run it outside stasis.slice with `nice -n -10`. |
| Probe latency perfect even under EEVDF thrashing | Check it isn't real-time (`chrt -a -p`) and isn't mlocked. |
| Probe Max near 20 ms under scx_stasis | Protected tasks aren't preempting batch jobs. Check `prio=` rises in `scx.log` and the loader is the rebuilt one (`setup_vm.sh` again). |
| A run waits before starting | It is waiting for PSI from the previous run to settle (up to 3 min; `quiesce_s` records how long). |
| stasisd: `OSError: [Errno 22]` on the PSI trigger | Not running as real root. 1 s windows need privilege. |
| `watchdog_timeout_ms: Permission denied` | That file doesn't exist on kernel 7.0. Use `scx_stasis -w 5000`. |
| Dashboard shows "Live needs the STASIS server" | Open it through the server's address, not as a file. |

Deviations from the build guide are listed in `docs/00-prereg.md`.
