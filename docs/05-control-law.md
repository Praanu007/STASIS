# The control law as a switched system

This is the analysis behind arm 9 (registered in `docs/01-prereg-arms.md`).
It changes no thresholds and runs no experiments. It models the controller as
implemented, derives conditions that can be checked, and checks them against
recorded runs with `eval/control_law.py`.

## Why this is worth a chapter

No formal stability or convergence analysis of a thrashing controller was
found in the literature survey (`reports/STASIS novelty and related work.md`),
though the 1970s multiprogramming-level-control literature was not fully
searched, so the claim must be phrased as "not found", not "does not exist".
The machinery for analysing computing systems as feedback loops is
established: the SASO properties (stability, accuracy, settling time,
overshoot), and the observation that such systems are stable only within an
operating region, never globally. Diao et al. name three open problems for
controlling self-managing systems: resource models, sensor delay, and actuator
lead time. STASIS has all three. PSI's window is a sensor delay, reloading a
resumed job's pages is an actuator lead time, and the working-set estimate is
the resource model.

## The model

The state is the active-set size A(k). The controller switches between modes,
with a one-sample measurement delay:

| Mode | Condition | Update |
|---|---|---|
| pressure | PSI trigger fires, not warm-up, cooldown elapsed | A(k+1) = max(1, ⌊A(k)/2⌋) |
| calm | CALM s without pressure, set settled, candidate fits | A(k+1) = A(k) + 1 |
| rotation / bound | parked jobs exist; a turn has ended or a job has waited MAX_WAIT | A unchanged; membership swaps |

**This is MIMD, not AIMD.** Both directions are switched rather than
continuous, the decrease is multiplicative, and the actuator saturates at A=1.
A system of this shape does not converge to a fixed point. The useful question
is not whether it is stable but whether it admits a limit cycle, and with what
period. The honest expectation is that it *can* oscillate; a condition that
rules oscillation out, tested against data, is a stronger result than a
stability claim the model cannot support.

## Result 1: the dead band

Admission tests an *estimate* of a job's working set against a margin of
RESERVE × MemTotal. If the estimate is wrong by e, admitting the job can
recreate exactly the pressure that parked it: a limit cycle. So the loop has a
dead band, in which admission cannot by itself re-trigger pressure, when

> e_max < RESERVE × MemTotal

Both sides are measured from a run. The estimate is the one the controller
logged for the job at the moment it admitted it (`{"event": "ws"}`, every 5 s;
the measured working set under the default `--ws=refs`). The error is the gap
to the resident memory the job actually reaches in the 30 s afterwards. Runs
recorded before stasisd logged its estimates fall back to the job's
`memory.peak`, which was the estimator then.

| Rehearsal run | experiment | estimate | e_max | RESERVE × MemTotal | dead band |
|---|---|---|---|---|---|
| results/ctrl N=7 | A | peak | +160.6 MB | 409.9 MB | holds |
| results/stasis N=7 | A | peak | +211.3 MB | 409.9 MB | holds |
| session2/A/stasis N=7 | A | logged | −16.4 MB | 409.9 MB | holds |
| session3/A/stasis N=7 | A | logged | +82.9 MB | 409.9 MB | holds |
| pilot-c/3/ctrl N=8 (1.5 GB VM) | C | logged | +190.7 MB | 152.8 MB | **fails** |
| pilot-c/3/stasis N=8 (1.5 GB VM) | C | logged | +176.3 MB | 152.8 MB | **fails** |

**The dead band holds in every experiment-A run and fails in both
experiment-C runs.** That split is what the model predicts. In experiment C,
four jobs' working sets jump by design after 60% of their passes, so the
estimate made before the jump is wrong by construction and the error exceeds
the reserve, which is a smaller margin on the 1.5 GB VM. The model then
expects oscillation. Arm 1's own pilot notes for those runs report full STASIS
finishing slower than controller-only (328–409 s against 174–228 s) and the
early warning parking on reloads: consistent with an oscillation, from one or
two runs on emulated hardware.

The measured estimate is also tighter than the footprint was: the two
experiment-A runs that log it show e_max of −16.4 and +82.9 MB, against +160.6
and +211.3 MB for the older runs judged by peak.

## Result 2: the period, which does not hold up

Where halvings recur, the interval between them is bounded below by the time
the survivors take to fault their pages back in plus the calm period
admission waits out:

> T ≥ T_reload + CALM, with T_reload = missing bytes / swap-in rate

The rise time from admission to the next pressure event is not modelled, so
this is a lower bound.

| Rehearsal run | missing bytes | T predicted | T measured (median) | error |
|---|---|---|---|---|
| results/stasis N=7 | from samples | 35.9 s | 44.0 s | −19% |
| results/ctrl N=7 | from samples | 8.1 s | 69.1 s | −88% |
| session3/A/stasis N=7 | logged | 29.1 s | 81.9 s | −65% |
| pilot-c/3/ctrl N=8 | logged | 20.3 s | 19.1 s | +6% |
| pilot-c/3/stasis N=8 | logged | 36.4 s | 117.5 s | −69% |

The bound is never badly violated (the one positive error is 6%), so as a
lower bound it survives. **As a prediction of the period it fails:** the
error ranges from −88% to +6%. Full STASIS's first run looked encouraging
(−19%), and the next two missed by two thirds. The unmodelled rise time
dominates the cycle and varies from run to run. An earlier draft registered
"within 25% for full STASIS" on the strength of that first run; it is kept as
registered and expected to fail, and that failure is the honest finding here.

Where a run's decision log predates the controller logging `missing` itself,
missing bytes are reconstructed from the samples (each running job's peak
minus resident at the moment of halving), and `control_law.py` says which
source it used.

## What these results are and are not

They come from six rehearsal runs, one or two per cell, on VMs whose swap ran
at a few MB/s. They show that the model is testable and that it is partly
wrong, which is what a falsifiable model should show. They are not results.
The predictions registered in `docs/01-prereg-arms.md` are tested on the
campaign runs, which the model has not seen.

## Reproducing

```bash
R=docs/rehearsal
RUNS="$R/results/ctrl/N7/run1 $R/results/stasis/N7/run1 \
      $R/session2/A/stasis/N7/run1 $R/session3/A/stasis/N7/run1 \
      $R/pilot-c/3/ctrl/N8/run1 $R/pilot-c/3/stasis/N8/run1"
python3 eval/control_law.py analyse  $RUNS
python3 eval/control_law.py deadband $RUNS
python3 eval/control_law.py predict  $RUNS
```

## Further work, deliberately not done

Two things suggest themselves, and neither is done here, because each would
change arm 1's controller and so invalidate its pre-registration:

- Model the rise time, which is what the period prediction lacks. That needs
  a model of how fast an admitted job's demand grows, which is
  workload-specific.
- Reformulate the law as AIMD and ask the AIMD convergence question
  explicitly. That would be reviewer-legible and, as far as the survey found,
  unclaimed for thrashing. It is proposed, not implemented.
