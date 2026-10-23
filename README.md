# STASIS: Scheduling To Avert Swap-Induced Stalls

STASIS stops a Linux machine from thrashing by **parking** memory-heavy batch
jobs, fairly and without killing them. When the jobs need more memory than
the machine has, it decides how many run at once (Denning's working-set load
control, 1968), freezes the rest with the cgroup v2 freezer, and rotates them
so every job keeps making progress.

| Layer | What it does | Where |
|---|---|---|
| 1. Sensing | PSI memory trigger, plus each job's page-fault frequency (PFF) from layer 4 | `controller/stasisd.py` |
| 2. Demand | measures each job's working set from its referenced bits; its footprint until then | `stasisd` (`WorkingSets`) |
| 3. Load control | halves the active set on real thrashing, admits jobs whose working sets fit, rotates turns fairly with a starvation bound, parks with `cgroup.freeze`, pages parked jobs out with `memory.reclaim` | `stasisd` |
| 4. Scheduling | a sched_ext scheduler: exports per-process PFF, and keeps protected interactive tasks on a priority queue that preempts batch work. It never parks anything. | `scx/scx_stasis.bpf.c` |

Every job runs as its own systemd service in `stasis.slice`. The **STASIS
Control Room** (`dashboard/`) shows it all live in a browser, replays recorded
runs, scores results against the pre-registered hypotheses, and simulates the
control law.

Read next:

- [`docs/architecture.md`](docs/architecture.md): how it works, and why
  (freezer rather than scheduler, the starvation bound, how working sets are
  measured and what that costs, safety).
- [`docs/related-work.md`](docs/related-work.md): what each existing defence
  (MGLRU, systemd-oomd, thrash-protect, TMO, DAMON) controls, with sources,
  and the claim STASIS makes.
- [`docs/00-prereg.md`](docs/00-prereg.md): thresholds, hypotheses, baselines
  and every deviation from the build guide, with the evidence for each.
- [`docs/rehearsal/`](docs/rehearsal/): what ran on a real kernel 7.0.

## The claim

The principle is Denning's. STASIS claims that, rebuilt on today's Linux, it
beats today's defences, and each part is a pre-registered hypothesis:

1. Job-level load control beats page-level and kill-based defences (EEVDF,
   MGLRU `min_ttl_ms`, systemd-oomd, thrash-protect) on throughput under
   memory overcommit, with no kills (H1, H2).
2. Turns measured in user CPU, first-in-first-out resumption and a starvation
   bound make it fairer, and no job goes as long without progress (H3, H6,
   H7).
3. Measured working sets admit more jobs than footprints when part of each
   job's memory is cold (H8, H9).
4. The scheduler layer gives an earlier signal and keeps interactive tasks
   responsive (H4, H5).

STASIS never kills, but it doesn't promise the kernel won't: kills are
counted under every configuration.

## Repository

```
workloads/wss.c            synthetic job: wss SIZE_MB PASSES [HOT_PCT] (prints "pass wallclock")
scripts/setup_vm.sh        Phase 0 provisioning (run once in the guest, as root)
scripts/preflight.sh       go/no-go for each layer and baseline on this kernel
scripts/check_gate0.sh     Phase 0 exit criteria (runs preflight first)
scripts/calibrate.sh       pick PASSES so one job runs ~60 s alone
scripts/run_mix.sh         one run: N jobs + probe + sampler [+ config]
scripts/sweep.sh           N-sweep for one config (Phase 1, Phase 3 gate)
scripts/sample.py          1 Hz sampler -> samples.jsonl (also used by the dashboard)
scripts/check_faults.sh    Phase 3 fault-count correctness check
scripts/manifest.sh        per-run MANIFEST (kernel, commits, sysctls, baseline versions)
controller/stasisd.py      layers 1-3 (Python stdlib only)
scx/scx_stasis.bpf.c       layer 4, the BPF scheduler
scx/scx_stasis.c           loader: -p TGID, -w MS; exports /run/stasis/{pff,stats}
scx/install_into_scx.sh    drops scx_stasis into a scx-c-examples checkout
eval/campaign.sh           Phase 5 randomized run matrix, experiments A and B (resumable)
eval/analyze.py            metrics.csv, PFF calibration, figures F1-F7, score.md
dashboard/server.py        Control Room server (stdlib only, read-only, mlocked)
dashboard/src/             Control Room UI in React + TypeScript (Live, Replay, Results, Simulator)
dashboard/index.html       the UI built into one self-contained file; what the server sends
dashboard/test/            the simulator pinned to its known results, and the formatters
tests/test_controller.sh   stasisd regression tests on fake cgroups (22 checks)
docs/                      architecture, related work, pre-registration, demo, rehearsal
MANIFEST                   one line per run
```

## What has been verified

On a rehearsal VM built from the official **Ubuntu 26.04 cloud image, kernel
7.0.0-38-generic**, with 4 vCPUs and 4 GB of RAM, emulated in software (no
KVM): much slower than real hardware, above all its swap (about 3 MB/s in).
These are dress rehearsals with one or two runs per cell, not results.
`docs/rehearsal/README.md` has the details and the logs.

**First design** (session 1):

| Check | Result |
|---|---|
| `setup_vm.sh`, unmodified | ✅ end to end in 3 min 41 s: swap, zswap off, toolchain, pinned scx clone, both schedulers built |
| Gate 0 | ✅ all checks pass; scx_simple attaches and Ctrl-C returns to EEVDF |
| scx_stasis rungs 1, 2, 4 | ✅ the 7.0 verifier accepts it; BPF and `/proc` fault counts agree exactly (17,645 = 17,645); survives 90 s of stress-ng |
| Rung 3, priority path | ✅ 133 priority dispatches/s for the protected cyclictest under 8 CPU hogs |
| N = 7: EEVDF / controller only / full STASIS | 4 / 420 passes in 300 s; 420 / 420 in 608 s; 420 / 420 in 284 s; no kills in any |

**Revised design** (sessions 2 and 3, after the independent review):

| Check | Result |
|---|---|
| `preflight.sh` | ✅ controller, measured working sets, sched_ext, MGLRU and oomd: GO. thrash-protect: NO-GO until installed (the VM's pip had no network; installed from a wheel) |
| Working-set sampler on real jobs | ✅ at HOT 60 the measurements read 397–420 MiB, almost all 419–420, for a 420 MiB hot set |
| Measured against footprint working sets, HOT 60, N = 7 | inconclusive on this VM: measured 231 s and 348 s, footprints 202 s and 397 s (two runs each). One configuration varies more than the two differ; experiment B, with five runs per cell, decides H8 |
| Baselines run, N = 7, 300 s | thrash-protect 1.1.2: 0 passes; MGLRU `min_ttl_ms=1000`: 6 passes (full STASIS: 420 / 420 in 304 s) |
| Probe p99 under full STASIS, N = 7 | median 6.8 ms over 8 runs with the preempt kick (deviation 11; range 6.2–13.5 ms), against 16.0 ms in session 1's run without it. Emulated timers are noisy, so the lab VM's rung 3 is the real check |
| `tests/test_controller.sh` on kernel 7.0 | ✅ 22 / 22 with the final controller (session 3 found a timing weakness in one new check; fixed, then rerun) |

Testing the revised design found problems of its own. Each is a deviation in
`docs/00-prereg.md` with the evidence for it, and each fix is covered by a
test:

- **The starvation bound could livelock on slow swap** (deviation 18, found in
  the simulator). It swapped parked jobs in back to back, so every active job
  was reloading at once. Swaps now happen one at a time. On the VM, its
  victim rule also turned out to swap out freshly resumed jobs; the victim is
  now the job that has had the most of its turn.
- **The working-set sampler worked when it couldn't help** (deviation 19).
  It opened windows back to back, under pressure too, clearing referenced
  bits (which makes pages look idle to reclaim) for windows that were then
  discarded: at HOT 60 no job had a trusted window before 129 s. Windows are
  now paced, one per 10 s per job, and skipped while a job is stalling.
- Earlier ones (deviations 2–12): admission blocked rotation; halvings
  cascaded; admission deadlocked, then over-admitted; the quantum ran out
  during reload; parked jobs kept their memory; warm-up looked like
  thrashing; sets that fit were halved while reloading; the probe waited for
  a batch job's slice; runs inherited the last run's PSI; fractional byte
  counts went to `memory.reclaim`.

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
   sudo scripts/setup_vm.sh                  # packages, 8 GB swap, zswap off, toolchain, scx build, baselines
   sudo scripts/preflight.sh                 # go/no-go per layer and baseline
   sudo scripts/check_gate0.sh
   sudo tests/test_controller.sh             # stasisd's control law, 22 checks, ~4 min
   ```
   `setup_vm.sh` also enables systemd-oomd and installs thrash-protect 1.1.2
   into `/opt/thrash-protect` with pip. Without internet in the guest,
   download the wheel elsewhere (`pip download thrash-protect==1.1.2`), copy
   it in, and run `/opt/thrash-protect/bin/pip install --no-index <wheel>`.
   On 26.04 the distro's libbpf (1.6) and bpftool (7.7) are new enough. On
   older releases, `setup_vm.sh` builds both from source: scx-c-examples needs
   libbpf ≥ 1.5 and bpftool ≥ 7.5.
3. Take a VM snapshot named **`clean`**. It's only for recovering a broken
   system (see Phase 5).

**Gate 0:** `preflight.sh` says GO for layers 1–3 and layer 4, and
`check_gate0.sh` passes: scx_simple loads, state reads `enabled`, Ctrl-C
returns to EEVDF, swap is 8 GB and zswap is off. If `/sys/kernel/sched_ext`
is missing, the kernel lacks `CONFIG_SCHED_CLASS_EXT`; the controller still
runs under EEVDF (`ctrl`), but fix that before Phase 3. A baseline that says
NO-GO is reported as unavailable by `run_mix.sh` rather than run wrongly.

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

**Gate 1:** F1 shows a visible collapse. Fill in `docs/00-prereg.md` (PASSES,
PFF_HI, and review the hypotheses), commit it, and write the hash into the
file. **From then on, no threshold changes.**

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
  throughput is above EEVDF's N=8.
- `decisions.jsonl` has `ws` events every 5 s. Once jobs run without
  stalling, their measured working sets (`m`) appear there.

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
   sits near 20,000, the preempt kick isn't working. (On the emulated
   rehearsal VM both runs showed about 40 ms maxima from timer emulation, so
   this check needs the real lab VM.)
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

Three experiments, each in its own results root:

| | Workload | Configurations | Runs (K = 5) |
|---|---|---|---|
| **A**, the thrashing curve | every page hot (HOT 100) | EEVDF and full STASIS at N = 1–8; controller-only, MGLRU, systemd-oomd and thrash-protect at N = 4, 6, 8; ablations `stasis-norot` and `stasis-fp` at N = 8; EEVDF at N = 0 (probe reference) | 155 (182 with `--sensitivity`: quantum 5/15/30 s, bound 60/120/240 s and PSI trigger 50/100/200 ms per 1 s, at N = 8) |
| **B**, measured working sets | 60% of each job's memory hot (HOT 60) | EEVDF, full STASIS and `stasis-fp` at N = 4, 6, 8 | 45 |
| **C**, a working-set jump (H5c, the early warning) | 40% hot; after 60% of PASSES, jobs 1–4 make every page hot, so thrashing starts from refaults in half the jobs | EEVDF, controller-only and full STASIS at N = 8 | 15 |

```bash
eval/campaign.sh plan /srv/results --experiment A --sensitivity
eval/campaign.sh plan /srv/results-b --experiment B
eval/campaign.sh plan /srv/results-c --experiment C
# per batch: reboot the VM (fresh kernel state), then
sudo -E STASISD_EXTRA=--pff-hi=<frozen> eval/campaign.sh run /srv/results 700 $PASSES 40
sudo -E STASISD_EXTRA=--pff-hi=<frozen> eval/campaign.sh run /srv/results-b 700 $PASSES 40
sudo -E STASISD_EXTRA=--pff-hi=<frozen> eval/campaign.sh run /srv/results-c 700 $PASSES 40
# after each batch, copy the results to the host (PowerShell):
#   scp -r <user>@<vm-ip>:/srv/results* .\
for r in /srv/results /srv/results-b /srv/results-c; do
  python3 eval/analyze.py metrics $r
  python3 eval/analyze.py figures $r
  python3 eval/analyze.py score $r docs/00-prereg.md     # pass/fail table
done
```

- **Pilot experiment C first.** Run one full-STASIS and one controller-only
  run, then check that the jumps came while every job was running (the
  `jump` lines in `job-1.log`…`job-4.log` against the thaws in
  `decisions.jsonl`) and that the controller parked jobs for pressure
  afterwards (`detect_s` in `analyze.py metrics`):
  ```bash
  for c in stasis ctrl; do
    sudo -E STASISD_EXTRA=--pff-hi=<frozen> STASIS_HOT=40 STASIS_JUMP=$((PASSES * 6 / 10)):4 \
      scripts/run_mix.sh 8 700 $PASSES /srv/pilot-c/$c/N8/run1 $c
  done
  python3 eval/analyze.py metrics /srv/pilot-c
  ```
  The rehearsal VM couldn't: at 3 MB/s of swap-in, full STASIS was still
  reloading jobs when the jumps came (deviation 22).
- **Reboot between batches, don't restore the snapshot.** Restoring `clean`
  also wipes `/srv/results`. Restore only to recover a broken system, and copy
  the results off first.
- A run reads `RUNNING` until it ends. If the guest wedges mid-run, power-cycle
  it and start the batch again: unfinished runs are deleted and re-run, and
  finished ones are skipped. Runs of a baseline the machine can't provide
  read `UNAVAILABLE` and are left out of the analysis.
- EEVDF and the baselines often run to the 1200 s timeout at N ≥ 6, so the
  campaign takes a day or more of machine time. Batches make it resumable.

Decision rule: a hypothesis passes only if **every** STASIS run beats
**every** baseline run. Report the median and range for everything,
including the metrics STASIS loses on (N ≤ 4, and `stasis-fp` in A).

### Phase 6: write-up, demo and defence

The figures are in `results/figures/`: F1 the throughput curve, F2 one N=8
STASIS run's timeline, F3 fairness, F4 probe latency, F5 the ablations, F6
starvation (the longest time any job went without progress, against the
bound), F7 measured against footprint working sets. The demo script and
defence questions are in `docs/demo.md`.

## The Control Room

```bash
cd ~/stasis && sudo python3 dashboard/server.py --results /srv/results
```

It prints the address to open from the host's browser (port 8686). Its four
views are:

- **Live** follows the run `run_mix.sh` is writing, through
  `/run/stasis/current`. With no run it shows the whole machine.
- **Replay** plays back any finished run at 1×, 4× or 16×, with its metrics
  and every decision explained (halve, admit, rotate, bound, re-measure).
- **Results** shows the throughput curve (the thrashing curve, and STASIS
  against the other defences), the pre-registered hypotheses with pass/fail
  verdicts, and every run's metrics. Each chart has a table view.
- **Simulator** runs stasisd's control law on a model of the 4 GB VM. Switch
  the policy to watch EEVDF thrash and STASIS recover; set **Hot memory** to
  60% and compare **Working sets** measured against footprint. It is a
  model, and the page says so.

The server is read-only and has no authentication; run it only on the lab
VM's private network. It locks itself into RAM so it keeps answering while
the machine thrashes. It's light, but leave it off during campaign batches so
it can't perturb measurements; use Replay afterwards.

The UI is written in React and TypeScript under `dashboard/src/`.
`dashboard/index.html` is built from it and committed: one file with the
stylesheet and script inlined (only the fonts come from Google Fonts, with
fallbacks), so the VM needs no Node.js and the server sends the file as is. To
change the UI, on any machine with Node.js 18 or newer:

```bash
cd dashboard
npm ci            # React, TypeScript and esbuild, as pinned in package-lock.json
npm run check     # type-check, tests, then build index.html
```

Commit the rebuilt `index.html` with the sources. While working on the UI,
`node build.mjs --dev` writes React's development build to `index.html`
instead, which reports React's warnings in the browser console; run
`npm run build` again before committing.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `preflight.sh`: layers 1–3 NO-GO | Read the `[FAIL]` lines: cgroup v2, the memory and cpu controllers, PSI triggers and swap are all required. |
| `preflight.sh`: measured working sets NO-GO | No `clear_refs`/`smaps_rollup`: stasisd falls back to footprints by itself (`--ws=footprint`). |
| A baseline run reads `UNAVAILABLE` | Its prerequisite is missing: sched_ext, MGLRU `min_ttl_ms`, systemd-oomd, or `/opt/thrash-protect`. `preflight.sh` says which. |
| EEVDF still above 50% of peak at N=8 | Raise SIZE. Confirm zswap is off and Dynamic Memory is off. |
| Guest wedges during EEVDF runs | Expected at high N. Keep the 1200 s timeout, run baselines last in a batch, power-cycle and re-run the batch. |
| Scheduler ejected (`status` = `SCHED_EJECTED`) | Check PRIO_DSQ membership, and that nothing is held runnable. Read the dmesg lines in `sched_ext_dmesg.txt`. |
| Controller died (`status` = `CTRL_DIED`) | Read `stasisd.log` in the run directory; the run harness thawed the jobs. |
| Verifier rejects | NULL-check every lookup, initialize every variable, and don't use unbounded loops. |
| Jobs stay frozen after a crash | `for f in /sys/fs/cgroup/stasis.slice/*/cgroup.freeze; do echo 0 > $f; done` |
| Controller slow under thrash | Confirm mlockall succeeded. Run it outside stasis.slice with `nice -n -10`. |
| `ws` events never show a measured `m` | The jobs never ran unstalled: the sampler only trusts (and only opens) windows while a job isn't stalling on memory. Expected while the set thrashes. |
| Probe latency perfect even under EEVDF thrashing | Check it isn't real-time (`chrt -a -p`) and isn't mlocked. |
| Probe Max near 20 ms under scx_stasis | Protected tasks aren't preempting batch jobs. Check `prio=` rises in `scx.log` and the loader is the rebuilt one (`setup_vm.sh` again). |
| A run waits before starting | It is waiting for PSI from the previous run to settle (up to 3 min; `quiesce_s` records how long). |
| stasisd: `OSError: [Errno 22]` on the PSI trigger | Not running as real root. 1 s windows need privilege. |
| `watchdog_timeout_ms: Permission denied` | That file doesn't exist on kernel 7.0. Use `scx_stasis -w 5000`. |
| Dashboard shows "Live needs the STASIS server" | Open it through the server's address, not as a file. |

Deviations from the build guide are listed in `docs/00-prereg.md`.
