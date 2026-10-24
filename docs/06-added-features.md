# The ten added features

Added 2026-10-24 in response to the novelty review
(`reports/STASIS novelty and related work.md`), and rebased onto arm 1 as it
stands (measured working sets, fair rotation with a starvation bound,
experiments A, B and C). This page is the index: what each feature is, where
it lives, what it would let the thesis claim, and what it costs the
pre-registration.

## The rule every feature follows

**Arm 1 is untouched.** Every feature is off by default, or is a new campaign
arm that only appends runs to experiment A, or is a metric computed over runs
that will be recorded anyway. `docs/00-prereg.md` is not edited; the new arms
are registered separately in `docs/01-prereg-arms.md`, as X1–X12. This is
checked, not asserted:

- `tests/test_controller.sh`, arm 1's own 22-check suite, passes with every
  change applied.
- `eval/campaign.sh plan` still writes arm 1's 155 runs for experiment A (182
  with `--sensitivity`). With every new option the plan holds 347 runs, of
  which exactly the same 182 are arm 1's.
- A run's recorded `config` line is unchanged unless it uses a real workload.

## Summary

| # | Feature | Where | How to enable | Arm | Runs on the lab VM? |
|---|---|---|---|---|---|
| 1 | earlyoom baseline | `scripts/run_mix.sh` | `--earlyoom` at plan time | 2 | yes, once installed |
| 2 | Control-law analysis | `eval/control_law.py`, `docs/05-control-law.md` | run the script | 9 | yes, reads existing runs |
| 3 | Real workloads | `scripts/workload.sh` | `--workloads` at plan time | 3 | yes, once the tools are installed |
| 4 | Fairness over parked time | `eval/analyze.py` `jain_frozen`, `scripts/sample.py` | always computed | 8 | yes |
| 5 | D-term (lead on PSI) | `controller/stasisd.py` | `--derivative-hi` | 4 | yes |
| 6 | DAMON estimate | `stasisd.py --ws=damon`, `scripts/damon_wss.sh` | `--controller-arms` | 5 | **no**, the stock kernel lacks DAMON |
| 7 | Controller cost (energy proxy) | stasisd's "self" log, `analyze.py` | always computed | 8 | yes |
| 8 | Predictive trigger | `stasisd.py --predict`, `eval/train_predictor.py` | `--controller-arms` | 6 | yes, once a model passes |
| 9 | CRIU actuator | `stasisd.py --actuator=criu` | not planned | 7 | **no**, see below |
| 10 | zram tier; Kubernetes | `scripts/zram_tier.sh`, `deploy/k8s/` | by hand | 10 | zram yes; k8s no |

The numbering above follows the review's ranking; arm numbers follow
registration order. Cite the arm number.

## What arm 1 already had

The review ranked "named baselines" first. By the time these features were
rebased, arm 1 already had most of it: EEVDF with MGLRU's `min_ttl_ms`,
systemd-oomd, and thrash-protect pinned at 1.1.2 in its own venv, all checked
by `scripts/preflight.sh`. Its sensitivity sweep already covers the rotation
quantum, the starvation bound and the PSI trigger. Arm 1 also redefined `jain`
as whole-run fairness of slowdowns and kept the build guide's version as
`jain_window`. So feature 1 here is only the earlyoom baseline, and the
fairness metric added (`jain_frozen`) is a third view, alongside those two.

## How the features fit arm 1's controller

Arm 1's controller was restructured since these features were first written:
working sets now come from a sampler through `estimate()`, and the control
loop calls its own `park()` and `thaw()`. The features were re-applied to that
structure rather than merged line by line:

- **DAMON is a third choice of arm 1's existing `--ws`** (`refs`, `footprint`,
  `damon`), capped at the footprint like the measured estimate, rather than a
  second, competing estimator flag.
- **The actuator is `actuate()`,** which arm 1's `park()` and `thaw()` now
  call. It is the freezer unless `--actuator=criu`. CRIU also bumps the counter
  arm 1's working-set sampler uses to discard a measurement window that spans a
  freeze.
- **The predictor's features use the controller's working-set estimate `W`,**
  and `train_predictor.py` trains on the estimate the controller logged, so
  training and inference see the same input.

## What was verified, and where

The machine this was written on has no sched_ext, so nothing ran under the lab
VM's real configuration. What could be checked was checked, in WSL2 Ubuntu
against the rehearsal runs in `docs/rehearsal/`:

| Check | Result |
|---|---|
| Arm 1's controller regression suite | 22/22 pass |
| Dashboard: type-check, tests, build | clean; 12/12; builds (in a scratch copy) |
| Every shell script parses, every Python file compiles | yes |
| No conflict markers anywhere | none |
| `--ws=damon` without DAMON sysfs | refuses to start, with the reason |
| Campaign plans: A, A + sensitivity, all arms | 155, 182, 347; arm 1's 182 unchanged inside |
| `--earlyoom` with experiment B or C | refused |
| Plan guards (missing threshold, missing or failed model) | 5/5: refusals leave any existing plan untouched |
| Both pre-registration files parse; IDs disjoint | 23 + 12 hypotheses, no collisions |
| `analyze.py metrics` on 12 rehearsal runs | runs; `jain_frozen` 0.55–0.91 where jobs were parked |
| `control_law.py` on 6 runs | dead band holds in all of experiment A, fails in both of C; period model fails |
| `train_predictor.py` → stasisd's `Predictor` | contract holds; sign check passes on consistent features |
| earlyoom kill counting, on a synthetic log | counts each process once, whether SIGTERM was enough or not |

**Not verified anywhere:** that earlyoom, RocksDB, memcached, DAMON, CRIU and
zram behave as the scripts assume on kernel 7.0. Each needs a first run on the
lab VM before its arm's campaign. Their first failure is expected to be a flag
or a path, not the design.

## The features the evidence is against

Three were added because they were asked for, against the review's advice.
They are implemented so their outcome can be measured rather than argued, and
each is registered with its expected result, so that a negative counts.

**Predictive trigger (8, arm 6).** The closest published work uses eBPF and
LightGBM to predict memory *fragmentation*, not pressure. Meta's TMO acts
continuously on a subliminal pressure signal precisely to avoid forecasting.
The D-term (feature 5) supports the same claim, acting before onset, at a
fraction of the complexity, so X9 tests whether the model beats it. The
trainer refuses a model whose weights point the wrong way physically, and
stasisd refuses to load one, so a bad model cannot reach a run.

**CRIU (9, arm 7), which cannot run yet.** HFSP measured suspend/resume
traffic at disk raw speed, and CRIU's cost is dominated by writing and reading
the memory image, so X10 is registered expecting freezing to win. Two further
problems were found while rebasing:

- **It breaks arm 1's safety invariants.** Arm 1 guarantees that stasisd never
  sends a signal and never kills; CRIU frees memory by killing the
  checkpointed job. The controller's docstring now says so: this one arm gives
  up both, which is part of what it measures.
- **The run harness can't host it.** Jobs are systemd transient units. When
  CRIU kills a job's processes, systemd ends the unit and removes its cgroup,
  and `run_mix.sh` counts the job as finished. Runs would produce garbage that
  looks valid. So arm 7 is not planned. It needs `RemainAfterExit` on the job
  units and CRIU's `--manage-cgroups` restore validated on the lab VM first.

**CXL and tiered memory (10, arm 10).** These can't be evaluated honestly on one
4 GB VM against TPP, HybridTier, xTier or TierBPF, and nothing here pretends
otherwise. A zram tier is the honest stand-in. It tests the one question
tiering raises for load control: does a fast second tier make thrashing
control obsolete? It also lets the thesis measure a real, citable baseline
failure: thrash-protect's own PR #42 reports a zram laptop frozen "37 + 4
times in eleven hours without real pressure". The Kubernetes deployment is
engineering with near-zero novelty, kept so the thesis can show the controller
is deployable where overcommit is managed.

## Findings made while building this

- **Ubuntu 26.04's stock kernel has no DAMON** (arm 1's controller docstring
  says so). So arm 5 cannot run on the lab VM as built. `--ws=damon` refuses to
  start rather than quietly falling back to the footprint for every job, which
  would have produced a footprint run labelled DAMON.
- **Fairness over parked time needed a fix to measure thrash-protect.**
  thrash-protect parks system services with SIGSTOP, which `cgroup.freeze`
  never records (arm 1's `run_mix.sh` notes the same). `sample.py` now records
  stopped tasks too, and `jain_frozen` counts both. The one rehearsal
  thrash-protect run predates this, so it has no value yet.
- **The predictor's backwards model came from a training/inference mismatch.**
  The trainer computed features from `memory.peak` while the controller uses
  its measured working set. With the trainer reading the controller's logged
  estimates and skipping runs that have none (`--allow-fallback` overrides),
  the rehearsal model passes its sign check. It rests on one held-out run, so
  this says it is not backwards, not that it is good.
- **earlyoom sends SIGTERM before SIGKILL,** and wss has no SIGTERM handler, so
  counting only SIGKILL would have under-counted its kills. Kills are now
  counted per process.
- **Security review, two rounds.** The first found that `workload.sh` used a
  guessable `/var/tmp` path while its jobs run as root, so a planted directory
  could have redirected root's writes; it now uses `mktemp -d`. The second
  found no vulnerability, and two issues below its reporting bar that were
  fixed anyway:
  - The Kubernetes DaemonSet was Burstable, so stasisd sat in the tier it
    manages and could freeze its own pod. Its self-protection check reads the
    pod-level `cgroup.procs`, which is empty under cgroup v2, so it cannot
    catch this. The pod is now Guaranteed (requests equal limits), which puts
    it outside that tier.
  - `workload.sh compile` runs `make` as root, so a source tree another user
    can write to would give them root. It now refuses any tree with an entry
    not owned by root or writable by group or others.

## Before running any new arm

1. Run arm 1's experiment A first. Arms 4 and 6 take their thresholds from it.
2. Fill `STASIS_DERIV_HI` and the model hash into `docs/01-prereg-arms.md`, and
   commit that before planning `--controller-arms`.
3. Install and pin each arm's tools (earlyoom, db_bench, memcached and
   memtier_benchmark, damo, criu) and record the versions in the MANIFEST.
   `run_mix.sh` marks a run whose tools are missing as UNAVAILABLE, never as a
   result.
4. Leave `scripts/zram_tier.sh` **off** for every arm-1 run. Each run's MANIFEST
   has a `zram tier:` line, which must read `off`.
