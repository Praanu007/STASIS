# STASIS: Scheduling To Avert Swap-Induced Stalls

STASIS stops a Linux machine from thrashing by **pausing** memory-heavy batch
jobs fairly instead of killing them. This is Denning's working-set load
control, built from three parts:

| Part | What it does |
|---|---|
| **Jobs** | Each memory-heavy job runs as its own systemd service in `stasis.slice`, with its own cgroup for memory, CPU and freezing. |
| **stasisd** (`controller/stasisd.py`, Python stdlib only) | Listens for PSI triggers. On pressure it halves the active set. When things are calm it admits jobs by working-set estimate. It rotates parked jobs fairly and parks them with `cgroup.freeze`. |
| **scx_stasis** (`scx/`, sched_ext BPF) | A vtime scheduler based on `scx_simple`. It measures each process's **page-fault frequency** (major faults per ms of its own CPU time) and keeps protected interactive tasks on a priority queue. It never parks anything. |

## Repository

```
workloads/wss.c            synthetic working-set job (prints "pass wallclock")
scripts/setup_vm.sh        Phase 0 provisioning (run once in the guest, as root)
scripts/check_gate0.sh     Phase 0 exit criteria
scripts/calibrate.sh       pick PASSES so one job runs ~60 s alone
scripts/run_mix.sh         one run: N jobs + probe + sampler [+ config]
scripts/sweep.sh           N-sweep for one config (Phase 1, Phase 3 gate)
scripts/sample.py          1 Hz sampler -> samples.jsonl
scripts/check_faults.sh    Phase 3 fault-count correctness check
scripts/manifest.sh        per-run MANIFEST (kernel, commits, sysctls)
controller/stasisd.py      Phase 2 controller (+ Phase 4 --signal=bpf)
scx/scx_stasis.bpf.c       Phase 3 BPF scheduler
scx/scx_stasis.c           loader: -p TGID, exports /run/stasis/pff every 100 ms
scx/install_into_scx.sh    drops scx_stasis into a scx-c-examples checkout
eval/campaign.sh           Phase 5 randomized run matrix (resumable)
eval/analyze.py            metrics.csv, PFF calibration, figures F1-F5, score.md
docs/00-prereg.md          thresholds and hypotheses, frozen at Gate 1
docs/demo.md               Phase 6 demo script and defence questions
MANIFEST                   one line per run
```

## What has been verified so far

Checked in a cloud container with no sched_ext kernel:

- `wss` builds and runs.
- `scx_stasis.bpf.c` and the loader **compile** against scx-c-examples
  `370b1ce` with clang 18 and libbpf 1.8.
- stasisd's control law was exercised against a fake cgroup tree. Tested:
  calm-period admission, rotation of a starved parked job, PFF early warning
  followed by halving (worst fault rate parked first), and thaw-all on SIGTERM.
- `analyze.py` runs end to end on a synthetic results tree: metrics,
  calibration, all five figures and the score table.

**Not yet verified, and only possible on the VM:**

- Whether the BPF verifier accepts the program.
- Whether it attaches.
- Every real measurement.

These are Phase 0 and Phase 3 below.

## Runbook, phase by phase

Every phase ends in a gate. Don't start the next phase until the gate passes.
Phases 1–2 alone already make a reportable result.

### Phase 0: lab setup

1. Build the VM on the Windows host. Use Hyper-V (Gen 2, **Dynamic Memory
   off**, 4096 MB, 4 vCPU, 40 GB, "Microsoft UEFI Certificate Authority"
   template) or VirtualBox with fixed memory. Don't use WSL2. Install Ubuntu
   26.04 LTS Server.
2. Inside the guest:
   ```bash
   git clone <this repo> ~/stasis && cd ~/stasis
   sudo scripts/setup_vm.sh       # packages, 8 GB swap, zswap off, libbpf/bpftool, scx build
   sudo scripts/check_gate0.sh
   ```
   `setup_vm.sh` builds libbpf and bpftool from source when the distro's
   libbpf is older than 1.5. Ubuntu 24.04's 1.3 can't build current
   scx-c-examples.
3. Take a VM snapshot named **`clean`**.

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

- Check `probe_policy.txt` in any run: it must say `SCHED_OTHER`.
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

1. **Loads:** run `sudo /opt/stasis/scx_stasis`, then
   `cat /sys/kernel/sched_ext/root/ops` should print `stasis`. If the
   verifier rejects the program, the error names the line.
2. **Counts are correct:** with pressure running, run
   `sudo scripts/check_faults.sh`. BPF `maj_flt` and `/proc` majflt should
   agree to within a few faults. Put this in the report.
3. **Priority path:** run `sudo /opt/stasis/scx_stasis -p $(pgrep cyclictest)`.
   The `prio=` counter in its output should rise. Also check
   `sudo bpftool map dump name prio_tgid`.
4. **Survives load:** run `stress-ng --cpu 8 --vm 4 --timeout 600`, then
   `sudo dmesg | grep -i sched_ext` should show no ejection. While
   developing, lower `/sys/kernel/sched_ext/watchdog_timeout_ms` to 5000.
   Debug output goes to `/sys/kernel/tracing/trace_pipe`, and SysRq-S falls
   back to EEVDF.

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
# per batch: roll back to 'clean', then
sudo -E STASISD_EXTRA=--pff-hi=<frozen> eval/campaign.sh run /srv/results 700 $PASSES 70
python3 eval/analyze.py metrics /srv/results
python3 eval/analyze.py figures /srv/results
python3 eval/analyze.py score /srv/results docs/00-prereg.md   # pass/fail table
```

Decision rule: a hypothesis passes only if **every** STASIS run beats
**every** baseline run. Report the median and range for everything,
including the metrics STASIS loses on (N ≤ 4).

### Phase 6: write-up, demo and defence

The figures are F1–F5 in `results/figures/`. The demo script and rehearsal
questions are in `docs/demo.md`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| EEVDF still above 50% of peak at N=8 | Raise SIZE. Confirm zswap is off and Dynamic Memory is off. |
| Guest wedges during EEVDF runs | Expected at high N. Keep the 1200 s timeout, run baselines last in a batch, and roll back to the snapshot. |
| Scheduler ejected (`status` = `SCHED_EJECTED`) | Check PRIO_DSQ membership, and that nothing is held runnable. Read the dmesg dump in `sched_ext_dmesg.txt`. |
| Verifier rejects | NULL-check every lookup, initialize every variable, and don't use unbounded loops. |
| Jobs stay frozen after a crash | `for f in /sys/fs/cgroup/stasis.slice/*/cgroup.freeze; do echo 0 > $f; done` |
| Controller slow under thrash | Confirm mlockall succeeded. Run it outside stasis.slice with `nice -n -10`. |
| Probe latency perfect even under EEVDF thrashing | Check it isn't real-time (`chrt -p`) and isn't mlocked. |
| stasisd: `OSError: [Errno 22]` on the PSI trigger | Not running as real root. 1 s windows need privilege. |

Deviations from the build guide are listed in `docs/00-prereg.md`.
