# STASIS pre-registration, arms 2–10

Registered 2026-10-24, before any Phase 5 run of any arm.

**docs/00-prereg.md is arm 1 and is not edited by this document.** Arm 1 is
that file as it stands, with its experiments A, B and C, its numbered
deviations and its hypotheses H1–H11 (including H10's sensitivity variants).
Every arm here only appends runs and hypotheses, and each arm-1 hypothesis is
scored from its own file exactly as before:

```
eval/analyze.py score ROOT docs/00-prereg.md      # arm 1: H1-H11
eval/analyze.py score ROOT docs/01-prereg-arms.md # arms 2-10: X1-X12
```

**These hypotheses are numbered X1–X12, not H-numbers.** Arm 1 numbers its
hypotheses H1, H2, ... in order and adds the next one as the design grows, so
any H-number taken here would collide the next time arm 1 registers one. An
earlier draft of this file used H6–H12 and collided with arm 1 on H6, H8, H9
and H11; the X prefix is a separate namespace that cannot.

**Every hypothesis here belongs to experiment A** (the thrashing curve, every
page hot). They carry no `experiment` key, which `analyze.py` reads as A, so
they are scored only for a results root planned as experiment A. The arms
that add runs (2–6) are planned only into experiment A: `campaign.sh` refuses
`--earlyoom`, `--workloads` and `--controller-arms` with B or C.

The same strict decision rule as arm 1 applies: a hypothesis passes only if
**every** run of the system under test beats **every** run of each baseline on
that metric. Report the median and range for everything, including where
STASIS is expected to lose.

## Disclosure: what had been seen before this was registered

Some of these arms were developed against data, and that has to be stated so
the reader can discount them accordingly. All of it is rehearsal data, on
emulated hardware, mostly one run per cell; none of it is campaign data and
none of it is scored.

**Fairness over parked time (arm 8).** `jain_frozen` was computed on every
rehearsal run that has samples:

| Rehearsal run | jain (whole run) | jain_window | jain_frozen |
|---|---|---|---|
| results/ctrl N=7 | 0.885 | 0.428 | 0.907 |
| results/stasis N=7 | 0.830 | 0.143 | 0.771 |
| session2/A/stasis N=7 | 0.869 | 0.415 | 0.552 |
| session3/A/stasis N=7 | 0.799 | 0.162 | 0.640 |
| session3/B/stasis N=7, two runs | 0.871, 0.856 | 0.202, 0.364 | 0.566, 0.811 |
| session3/B/stasis-fp N=7, two runs | 0.858, 0.884 | 0.148, 0.294 | 0.570, 0.710 |

X11's direction was chosen after seeing the first two rows. **No thrash-protect
run could be measured**: the one rehearsal thrash-protect run
(session2/A/thrashprotect) was recorded before `sample.py` recorded stopped
tasks, so its parked time is unknown and `jain_frozen` reads empty. X11 and X12
will be the first measurement of thrash-protect on this metric.

**The control law (arm 9).** The dead-band condition and the period model were
tested against rehearsal runs while `eval/control_law.py` was written, first
against two runs and then, while verifying this registration against the
current arm 1, against four more that log the controller's own estimates:

| Rehearsal run | experiment | e_max | RESERVE × MemTotal | dead band | period: predicted / measured |
|---|---|---|---|---|---|
| results/ctrl N=7 | A | +160.6 MB (peak) | 409.9 MB | holds | 8.1 / 69.1 s (−88%) |
| results/stasis N=7 | A | +211.3 MB (peak) | 409.9 MB | holds | 35.9 / 44.0 s (−19%) |
| session2/A/stasis N=7 | A | −16.4 MB (logged) | 409.9 MB | holds | too few cycles |
| session3/A/stasis N=7 | A | +82.9 MB (logged) | 409.9 MB | holds | 29.1 / 81.9 s (−65%) |
| pilot-c/3/ctrl N=8 (1.5 GB VM) | C | +190.7 MB (logged) | 152.8 MB | **fails** | 20.3 / 19.1 s (+6%) |
| pilot-c/3/stasis N=8 (1.5 GB VM) | C | +176.3 MB (logged) | 152.8 MB | **fails** | 36.4 / 117.5 s (−69%) |

The dead band held in every experiment-A run and failed in both experiment-C
runs. That is what the model predicts for experiment C: there the working
sets jump by design, so the estimate made before the jump is wrong by
construction. The arm-1 pilot notes for those runs report full STASIS slower
than controller-only and the early warning parking on reloads, which is
consistent with an oscillation, though it rests on one or two runs. The period
model does **not** hold up: it ranges from −88% to +6% across runs.

**The predictive trigger (arm 6).** `eval/train_predictor.py` was run on the
rehearsal runs. Trained on the first two runs alone, the model had the wrong
sign on `missing_frac`, and the trainer refused it. That turned out to be
caused by the trainer computing its features from `memory.peak` while the
controller computes them from its own (measured) working set. With the
trainer fixed to use the controller's logged estimates and to skip runs that
have none, a model trained on three experiment-A/B stasis runs passed the sign
check (precision 0.42, recall 0.86, **one** held-out run: not reportable).

Arms 2–5, 7 and 10 were registered without having seen any data for them.

## Thresholds to freeze before an arm runs

Like `PFF_HI` in arm 1, these must be filled in and committed **before** the
arm's first run, and the commit hash recorded. `eval/campaign.sh plan
--controller-arms` refuses to plan without them and writes the values into
`plan.txt`, so the value a run used is always recoverable.

| Parameter | Arm | Value | How it is chosen |
|---|---|---|---|
| `STASIS_DERIV_HI` (pp/s of PSI full avg10) | 4 | `____` | from arm-1 experiment-A runs: the PSI slope in the 5 s before PSI's own trigger fires, p50 |
| `STASIS_MODEL` (sha256 of the model file) | 6 | `____` | `eval/train_predictor.py` on arm-1 experiment-A full-STASIS runs only, which log the controller's estimates; must pass the sign check with ≥3 held-out runs |
| `--derivative-window` | 4 | `3.0` s | stasisd default; not tuned |
| DAMON sampling interval | 5 | `1` s | `scripts/damon_wss.sh` default; not tuned |
| zram tier size | 10 | `2048` MB | half of RAM; `scripts/zram_tier.sh` default |

## The arms

Run counts at the default K = 5 runs per cell.

| Arm | What changes | Runs | Labels | Can it run on the lab VM? |
|---|---|---|---|---|
| 2 | earlyoom as a further baseline | N=4,6,8: 15 | `earlyoom` | yes, once earlyoom is installed |
| 3 | real workloads replace wss | N=4,6,8 × 2 workloads × 4 configs: 120 | `rocksdb-*`, `memcached-*` | yes, once db_bench, memcached and memtier_benchmark are installed |
| 4 | D-term: pressure on rising PSI | N=6,8: 10 | `dterm` | yes |
| 5 | DAMON working-set estimate (`--ws=damon`) | N=6,8: 10 | `damon` | **no**: Ubuntu 26.04's stock kernel has no DAMON, and stasisd refuses `--ws=damon` without it. Needs a DAMON-enabled kernel, recorded in the MANIFEST |
| 6 | predictive (logistic) trigger | N=6,8: 10 | `predict` | yes, once a model passes the sign check |
| 7 | CRIU checkpoint instead of freeze | not planned | `criu` | **no**: see below |
| 8 | fairness over parked time | no new runs: a metric over arm-1 runs | — | yes |
| 9 | control-law analysis | no new runs: tested against arm-1 runs | — | yes |
| 10 | zram tier; Kubernetes deployment | zram: exploratory; k8s: not evaluated | — | zram yes; k8s no |

**Arm 7 cannot run yet.** Jobs run as systemd transient units. CRIU frees a
job's memory by killing it after the dump, and when a unit's processes die,
systemd ends the unit and removes its cgroup, so `run_mix.sh` would count the
job as finished. The arm needs the job units started with `RemainAfterExit`
and CRIU's `--manage-cgroups` restore validated on the lab VM before any run is
planned. Until then X10 stays registered and scores MISSING.

## Hypotheses

```json
{
  "hypotheses": [
    {"id": "X1",  "claim": "Full STASIS has higher throughput than earlyoom under overload (arm 2)",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [6, 8],
     "baselines": ["earlyoom"]},

    {"id": "X2",  "claim": "Under RocksDB, full STASIS has higher throughput than EEVDF and thrash-protect (arm 3)",
     "metric": "throughput", "better": "higher", "stasis": "rocksdb-stasis", "N": [6, 8],
     "baselines": ["rocksdb-eevdf", "rocksdb-thrashprotect"]},
    {"id": "X3",  "claim": "Under memcached, full STASIS has higher throughput than EEVDF and thrash-protect (arm 3)",
     "metric": "throughput", "better": "higher", "stasis": "memcached-stasis", "N": [6, 8],
     "baselines": ["memcached-eevdf", "memcached-thrashprotect"]},
    {"id": "X4",  "claim": "Under RocksDB, full STASIS kills nothing (arm 3)",
     "metric": "kills", "equals": 0, "stasis": "rocksdb-stasis", "N": [4, 6, 8]},
    {"id": "X5",  "claim": "Under memcached, full STASIS kills nothing (arm 3)",
     "metric": "kills", "equals": 0, "stasis": "memcached-stasis", "N": [4, 6, 8]},

    {"id": "X6",  "claim": "The D-term parks jobs earlier than PSI alone (arm 4)",
     "metric": "ttfp_s", "better": "lower", "stasis": "dterm", "N": [6, 8],
     "baselines": ["stasis"]},

    {"id": "X7",  "claim": "DAMON-estimated admission has higher throughput than the measured (refs) estimate (arm 5)",
     "metric": "throughput", "better": "higher", "stasis": "damon", "N": [6, 8],
     "baselines": ["stasis"]},
    {"id": "X8",  "claim": "DAMON-estimated admission kills nothing: an under-estimate must not over-commit (arm 5)",
     "metric": "kills", "equals": 0, "stasis": "damon", "N": [6, 8]},

    {"id": "X9",  "claim": "The predictive trigger parks jobs earlier than the D-term (arm 6)",
     "metric": "ttfp_s", "better": "lower", "stasis": "predict", "N": [6, 8],
     "baselines": ["dterm"]},

    {"id": "X10", "claim": "Freezing in place has higher throughput than CRIU checkpointing (arm 7)",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [6, 8],
     "baselines": ["criu"]},

    {"id": "X11", "claim": "Full STASIS shares parked time more fairly than thrash-protect (arm 8)",
     "metric": "jain_frozen", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["thrashprotect"]},
    {"id": "X12", "claim": "Controller-only shares parked time more fairly than thrash-protect (arm 8)",
     "metric": "jain_frozen", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["thrashprotect"]}
  ]
}
```

## Expected results, stated in advance

Writing these down now is what lets a negative result count.

- **X9 is expected to fail.** The comparable published work predicts memory
  fragmentation rather than pressure, and Meta's TMO argues that acting
  continuously on a subliminal signal beats forecasting. If a four-feature
  logistic model does not beat a lead term on the same signal, that is the
  finding. The rehearsal model passed the sign check on one held-out run, which
  says it is not backwards, not that it is good. Arm 6 runs only if a model
  trained on arm-1 data passes the sign check with ≥3 held-out runs; if none
  does, that is reported rather than worked around.
- **X10 is registered in the direction the evidence points: freeze beats
  CRIU.** HFSP measured suspend/resume traffic at disk raw speed, and CRIU's cost
  is dominated by writing and reading the memory image. It scores MISSING until
  arm 7 can run.
- **X7 may pass on throughput and fail X8.** DAMON reports what was accessed,
  which can be below the measured estimate's footprint cap. Admitting on a
  smaller estimate admits more jobs, which helps throughput until it
  over-commits. X7 and X8 are registered together so that a throughput win
  bought with kills cannot be reported as a win. Both score MISSING unless arm 5
  is run on a DAMON-enabled kernel.
- **Arm 3 may show STASIS losing at N=4,** as arm 1 is expected to: where
  everything fits, the controller only adds overhead. Report it.

## Arm 9: the control-law predictions

Registered as quantitative predictions, tested on the experiment-A campaign
runs. `eval/control_law.py` models the controller as a switched system.

1. **Dead band.** Admission cannot by itself drive a limit cycle when the
   worst estimator error is smaller than the reserve,
   `e_max < RESERVE × MemTotal`, with e measured against the estimate the
   controller logged. Prediction: this holds for every full-STASIS and
   controller-only run of experiment A at N=6 and N=8, with the default
   `--ws=refs`. This restates an earlier draft that named the footprint
   estimator, because arm 1's default estimator is now the measured one; the
   restatement was written after seeing two experiment-A runs in which it held
   (table above).
2. **Period.** Where halvings recur, the halve-to-halve interval is at least
   `T_reload + CALM`. The earlier draft predicted this lower bound would come
   within 25% of the measured median interval for full STASIS. **On the
   rehearsal runs it missed by 65% and 69%,** so this prediction is expected to
   fail. It is kept as registered rather than weakened after the fact, and its
   failure will be reported as a limit of the model: the unmodelled rise time
   from admission to the next pressure dominates the cycle.

## Arm 10: exploratory, not confirmatory

The zram tier has no confirmatory hypothesis. It is measurement only: run the
experiment-A configs at N=6,8 with `scripts/zram_tier.sh on`, and report
whether a fast second tier changes STASIS's behaviour, and whether
thrash-protect misfires the way its own PR #42 reports (a zram laptop frozen
"37 + 4 times in eleven hours without real pressure"). The Kubernetes
deployment (`deploy/k8s/`) is engineering and is not evaluated, because it
cannot be on one VM.
