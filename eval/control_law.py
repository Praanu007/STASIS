#!/usr/bin/env python3
"""control_law.py - STASIS's control law as a switched system, and a test of
its predictions against runs that are already recorded.

  control_law.py analyse RUN...        per-run cycle structure and estimator error
  control_law.py predict RUN...        predicted vs measured limit-cycle period
  control_law.py deadband RUN...       does the no-limit-cycle condition hold?

This is the analysis half of the control-theory arm (arm 9, 2026-10-24). It
changes no thresholds and runs no experiments: it reads decisions.jsonl and
samples.jsonl from runs that already exist, so it costs no machine time and
cannot disturb the pre-registration. The companion write-up is
docs/05-control-law.md.

The model
---------
Treat the active-set size A(k) as the state of a discrete-time switched system
with two modes and a one-sample measurement delay (PSI's window):

    pressure mode   A(k+1) = max(1, floor(A(k)/2))     (multiplicative decrease)
    calm mode       A(k+1) = A(k) + 1                  (additive increase, gated)

The gate on the increase is Denning's admission test: a parked job is admitted
only when the working sets of the active jobs plus its own fit in
    MemTotal - other - RESERVE*MemTotal.
This is MIMD, not AIMD: both directions are switched, the decrease is
multiplicative, and the actuator saturates at A=1. A switched system of this
shape does not converge to a point; the useful question is not "is it stable"
but "does it admit a limit cycle, and if so what is its period".

The dead-band condition
-----------------------
A limit cycle exists when admitting a job immediately recreates the pressure
that parked it. Admission uses an *estimate* W^ of a job's working set. Write
the estimator error as e = W_actual - W^. Admission leaves a margin of
RESERVE*MemTotal. So the loop has a dead band - admission cannot immediately
re-trigger pressure - exactly when

    e_max  <  RESERVE * MemTotal                                      (DB)

Both sides are measurable from a recorded run: RESERVE*MemTotal is known, and
e is the gap between the working set the controller believed at the moment it
admitted a job and the resident memory that job actually reached afterwards.
`deadband` reports both and says which way (DB) falls.

If (DB) fails, the predicted period of the resulting cycle is

    T  =  T_reload + CALM + T_rise

where T_reload is the time the survivors take to fault their pages back in
(missing bytes / swap-in rate, both recorded), CALM is the calm period the
admission rule waits out, and T_rise is the time from admission to the next
pressure event. `predict` computes T from the first two terms and compares it
against the halve-to-halve intervals actually observed.

Honesty about what this is
--------------------------
This is a model of the implemented law, derived to be tested, not a proof. The
likely outcome is that it shows the law *can* oscillate under some
parameterisations; the stability claim available is bounded - stability within
an operating region, in the sense standard in control of computing systems,
not global stability. Where the measured period disagrees with T, the model is
wrong and the thesis should say so: a prediction that survives contact with
four recorded runs is worth more than an unfalsifiable one.
"""
import json, os, statistics, sys

RESERVE = 0.10          # must match controller/stasisd.py
CALM = 5.0              # must match controller/stasisd.py
PAGE = 4096


def jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


def load(run):
    return jsonl(f"{run}/decisions.jsonl"), jsonl(f"{run}/samples.jsonl")


def mem_total(samples):
    for s in samples:
        t = s.get("mem", {}).get("total")
        if t:
            return t
    return None


def at(samples, t):
    """The sample nearest in time to t."""
    return min(samples, key=lambda s: abs(s["t"] - t)) if samples else None


def halvings(dec):
    """Times at which a halving parked at least one job."""
    ts = []
    for e in dec:
        if e.get("frozen") is True and str(e.get("why", "")).startswith("pressure"):
            if not ts or e["t"] - ts[-1] > 0.5:     # one halving parks several jobs
                ts.append(e["t"])
    return ts


def admits(dec):
    return [e["t"] for e in dec if e.get("frozen") is False and e.get("why") == "admit"]


def swap_in_rate(samples):
    """Bytes per second faulted back in, median over samples where it is > 0."""
    rates = []
    for a, b in zip(samples, samples[1:]):
        dt = b["t"] - a["t"]
        if dt > 0:
            d = (b["pswpin"] - a["pswpin"]) * PAGE / dt
            if d > 0:
                rates.append(d)
    return statistics.median(rates) if rates else None


def missing_at(samples, t):
    """Bytes the still-running jobs must fault back in, at time t: for every
    job not frozen in the nearest sample, max(0, peak - resident). Used when a
    run's decision log predates the controller logging 'missing' itself (the
    kernel 7.0 rehearsal runs do)."""
    s = at(samples, t)
    if not s:
        return None
    return sum(max(0, v.get("peak", 0) - v.get("cur", 0))
               for v in s.get("jobs", {}).values() if not v.get("frozen"))


def logged_estimate(dec, j, t):
    """The working-set estimate the controller logged for job j at or before
    t ({"event": "ws", "jobs": {j: {"est": ...}}}, every 5 s), or None."""
    best = None
    for e in dec:
        if e.get("event") == "ws" and e["t"] <= t:
            v = e.get("jobs", {}).get(j)
            if v and v.get("est") is not None:
                best = v["est"]
    return best


def estimator_error(dec, samples):
    """For each admission, the gap between the working set the controller
    believed and the resident memory that job actually reached afterwards
    (its peak over the 30 s that follow).

    "Believed" is the estimate the controller itself logged, which is the
    measured working set under --ws=refs. Runs from before stasisd logged its
    estimates fall back to the job's memory.peak at admission, which was the
    estimator then. Each result is (job, t, believed, reached, error, source),
    with source "logged" or "peak"."""
    errs = []
    for e in dec:
        if e.get("frozen") is not False or e.get("why") != "admit":
            continue
        j, t = e.get("job"), e["t"]
        s0 = at(samples, t)
        if not s0 or j not in s0.get("jobs", {}):
            continue
        est = logged_estimate(dec, j, t)
        src = "logged" if est is not None else "peak"
        believed = est if est is not None else (s0["jobs"][j].get("peak") or s0["jobs"][j].get("cur", 0))
        after = [s["jobs"][j]["cur"] for s in samples
                 if t < s["t"] <= t + 30 and j in s.get("jobs", {})]
        if after:
            errs.append((j, t, believed, max(after), max(after) - believed, src))
    return errs


def cmd_analyse(runs):
    for run in runs:
        dec, samples = load(run)
        if not dec and not samples:
            print(f"{run}: no decisions.jsonl or samples.jsonl"); continue
        h, ad = halvings(dec), admits(dec)
        gaps = [b - a for a, b in zip(h, h[1:])]
        print(f"\n=== {run}")
        print(f"  halvings {len(h)}   admissions {len(ad)}   samples {len(samples)}")
        if gaps:
            print(f"  halve-to-halve: median {statistics.median(gaps):.1f}s  "
                  f"min {min(gaps):.1f}  max {max(gaps):.1f}")
        else:
            print("  halve-to-halve: fewer than two halvings; no cycle to measure")
        r = swap_in_rate(samples)
        print(f"  swap-in rate: {r/1e6:.1f} MB/s" if r else "  swap-in rate: none observed")
        errs = estimator_error(dec, samples)
        if errs:
            e = [x[4] for x in errs]
            print(f"  estimator error over {len(e)} admissions: "
                  f"median {statistics.median(e)/1e6:+.1f} MB  max {max(e)/1e6:+.1f} MB")


def cmd_deadband(runs):
    print(f"{'run':<44} {'e_max (MB)':>11} {'RESERVE*Total (MB)':>19}  verdict")
    for run in runs:
        dec, samples = load(run)
        total = mem_total(samples)
        errs = estimator_error(dec, samples)
        if not total or not errs:
            print(f"{run:<44} {'-':>11} {'-':>19}  insufficient data")
            continue
        e_max = max(x[4] for x in errs)
        margin = RESERVE * total
        ok = e_max < margin
        srcs = sorted({x[5] for x in errs})
        print(f"{run:<44} {e_max/1e6:>11.1f} {margin/1e6:>19.1f}  "
              f"{'dead band HOLDS' if ok else 'dead band FAILS -> limit cycle expected'}"
              f"  (estimate: {'/'.join(srcs)})")


def cmd_predict(runs):
    print(f"{'run':<44} {'T_pred (s)':>11} {'T_meas (s)':>11}  note")
    for run in runs:
        dec, samples = load(run)
        h = halvings(dec)
        gaps = [b - a for a, b in zip(h, h[1:])]
        rate = swap_in_rate(samples)
        # T_reload: the memory that had to come back after a halving, over the
        # rate it came back at. The controller logs 'missing' at pressure
        # events; older logs lack it, so reconstruct it from the samples at
        # each halving instead, and say which source was used.
        miss = [e["missing"] for e in dec
                if e.get("event") == "pressure" and e.get("missing")]
        src = "logged"
        if not miss:
            miss = [m for m in (missing_at(samples, t) for t in h) if m]
            src = "from samples"
        if not (gaps and rate and miss):
            print(f"{run:<44} {'-':>11} {'-':>11}  not enough cycles to test")
            continue
        t_reload = statistics.median(miss) / rate
        t_pred = t_reload + CALM
        t_meas = statistics.median(gaps)
        rel = (t_pred - t_meas) / t_meas * 100
        print(f"{run:<44} {t_pred:>11.1f} {t_meas:>11.1f}  "
              f"{rel:+.0f}% vs measured; missing bytes {src}. "
              f"T_rise is not modelled, so T_pred is a lower bound")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, runs = sys.argv[1], sys.argv[2:]
    if cmd == "analyse":
        cmd_analyse(runs)
    elif cmd == "deadband":
        cmd_deadband(runs)
    elif cmd == "predict":
        cmd_predict(runs)
    else:
        sys.exit(__doc__)
