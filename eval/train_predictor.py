#!/usr/bin/env python3
"""train_predictor.py ROOT OUT.json [--horizon S] [--threshold P] [--allow-fallback]

Fits the logistic model that stasisd --predict reads (arm 6, 2026-10-24), from
runs that are already recorded. Stdlib only, plain batch gradient descent: the
point of arm 6 is to test whether *any* forecast beats a lead term, and a
four-weight logistic model is the honest minimum for that, not a strawman.

Label. A sample is positive when a PSI-sourced pressure event follows within
--horizon seconds (default 5). The model is therefore trained to fire *before*
PSI does; a model that only learns to fire at the same moment adds nothing.

Features, as controller/stasisd.py's predict_features() computes them:
    psi_slope     PSI full avg10 rise, percentage points per second
    pff_mean      mean per-job fault rate (here major faults per CPU-ms)
    missing_frac  non-resident share of the running jobs' working sets
    fit_margin    (MemTotal - reserve - sum of working sets) / MemTotal

"Working set" must mean what the controller meant, or the model is trained
on one input and run on another. The controller's estimate is its measured
working set (--ws=refs, the default), not memory.peak, and it logs that
estimate every 5 s as {"event": "ws", "jobs": {job: {"est": ...}}}. So each
sample uses the latest logged estimate at or before it, and by default only
runs that have such a log are used at all: a run without stasisd (EEVDF, a
baseline) or recorded before it logged estimates has no estimate to use.
--allow-fallback includes those runs with max(peak, resident) in place of the
estimate, and the model file records how many samples used each source.

Validation. Runs are split by run, never by sample: samples within one run are
strongly autocorrelated, and a random split would leak the future into the
training set and report an accuracy the model cannot have. With few runs the
held-out estimate is noisy, and the output says how many runs it rests on.

Read docs/06-added-features.md before using this arm. The nearest published
work predicts memory fragmentation, not pressure, and Meta's TMO argues that
acting on a continuous subliminal signal beats forecasting.
"""
import bisect, glob, json, math, os, random, sys

RESERVE = 0.10
FEATURES = ["psi_slope", "pff_mean", "missing_frac", "fit_margin"]


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


def run_dirs(root):
    for d in sorted(glob.glob(f"{root}/*/N*/run*")):
        try:
            if open(f"{d}/status").read().strip() != "RUNNING":
                yield d
        except FileNotFoundError:
            pass


SOURCE = {"logged_est": 0, "peak_fallback": 0}   # where each working set came from

def examples(run, horizon):
    """(features, label) per sample of one run."""
    S = jsonl(f"{run}/samples.jsonl")
    D = jsonl(f"{run}/decisions.jsonl")
    psi_t = sorted(e["t"] for e in D if e.get("event") == "pressure" and e.get("src") == "psi")
    ws_ev = sorted((e["t"], e.get("jobs", {})) for e in D if e.get("event") == "ws")
    ws_t = [t for t, _ in ws_ev]

    def W_of(t, j, v):
        """The controller's estimate for job j at time t, as it logged it."""
        i = bisect.bisect_right(ws_t, t) - 1
        if i >= 0:
            e = ws_ev[i][1].get(j)
            if e and e.get("est") is not None:
                SOURCE["logged_est"] += 1
                return e["est"]
        SOURCE["peak_fallback"] += 1
        return max(v.get("peak", 0), v.get("cur", 0))

    out = []
    for a, b in zip(S, S[1:]):
        dt = b["t"] - a["t"]
        if dt <= 0:
            continue
        total = b.get("mem", {}).get("total")
        if not total:
            continue
        act = {j: v for j, v in b["jobs"].items() if not v.get("frozen")}
        if not act:
            continue
        slope = (b["psi"]["full"]["avg10"] - a["psi"]["full"]["avg10"]) / dt
        pff = []
        for j, v in act.items():
            u = a["jobs"].get(j)
            if u and "pgmajfault" in v and "pgmajfault" in u:
                dms = (v["cpu_us"] - u["cpu_us"]) / 1000
                if dms > 0:
                    pff.append((v["pgmajfault"] - u["pgmajfault"]) / dms)
        W = {j: W_of(b["t"], j, v) for j, v in act.items()}
        sets = sum(W.values()) or 1
        miss = sum(max(0, W[j] - v.get("cur", 0)) for j, v in act.items())
        x = {"psi_slope": slope,
             "pff_mean": sum(pff) / len(pff) if pff else 0.0,
             "missing_frac": miss / sets,
             "fit_margin": (total - RESERVE * total - sets) / total}
        y = 1 if any(b["t"] < t <= b["t"] + horizon for t in psi_t) else 0
        out.append((x, y))
    return out


def standardise(rows):
    mu = {k: sum(x[k] for x, _ in rows) / len(rows) for k in FEATURES}
    sd = {k: math.sqrt(sum((x[k] - mu[k]) ** 2 for x, _ in rows) / len(rows)) or 1.0
          for k in FEATURES}
    return mu, sd


def fit(rows, mu, sd, epochs=400, lr=0.1):
    """Batch gradient descent on standardised features, class-weighted so a
    rare positive class is not simply ignored."""
    w = {k: 0.0 for k in FEATURES}
    b = 0.0
    pos = sum(y for _, y in rows) or 1
    neg = (len(rows) - pos) or 1
    cw = {1: len(rows) / (2 * pos), 0: len(rows) / (2 * neg)}
    for _ in range(epochs):
        gw = {k: 0.0 for k in FEATURES}
        gb = 0.0
        for x, y in rows:
            z = b + sum(w[k] * (x[k] - mu[k]) / sd[k] for k in FEATURES)
            p = 1 / (1 + math.exp(-max(-60, min(60, z))))
            g = (p - y) * cw[y]
            for k in FEATURES:
                gw[k] += g * (x[k] - mu[k]) / sd[k]
            gb += g
        n = len(rows)
        for k in FEATURES:
            w[k] -= lr * gw[k] / n
        b -= lr * gb / n
    return w, b


def unstandardise(w, b, mu, sd):
    """Fold the scaling into the weights so stasisd can use raw features."""
    raw = {k: w[k] / sd[k] for k in FEATURES}
    return raw, b - sum(w[k] * mu[k] / sd[k] for k in FEATURES)


def evaluate(rows, w, b, thr):
    tp = fp = fn = tn = 0
    for x, y in rows:
        z = b + sum(w[k] * x[k] for k in FEATURES)
        p = 1 / (1 + math.exp(-max(-60, min(60, z))))
        hit = p >= thr
        tp += hit and y; fp += hit and not y; fn += (not hit) and y; tn += (not hit) and not y
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec}


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    root, out = args[0], args[1]
    horizon = float(args[args.index("--horizon") + 1]) if "--horizon" in args else 5.0
    thr = float(args[args.index("--threshold") + 1]) if "--threshold" in args else 0.5
    allow_fallback = "--allow-fallback" in args

    runs = list(run_dirs(root))
    if not allow_fallback:
        logged = [r for r in runs
                  if any(e.get("event") == "ws" for e in jsonl(f"{r}/decisions.jsonl"))]
        skipped = len(runs) - len(logged)
        if skipped:
            print(f"  skipped {skipped} runs with no logged working-set estimate "
                  f"(no stasisd, or recorded before it logged them); --allow-fallback includes them")
        runs = logged
    per = {r: examples(r, horizon) for r in runs}
    runs = [r for r in runs if per[r]]
    if len(runs) < 2:
        sys.exit(f"need at least 2 runs with samples under {root}; found {len(runs)}")

    random.Random(0).shuffle(runs)                  # split by run, reproducibly
    k = max(1, len(runs) // 4)
    test_runs, train_runs = runs[:k], runs[k:]
    train = [e for r in train_runs for e in per[r]]
    test = [e for r in test_runs for e in per[r]]

    mu, sd = standardise(train)
    w_s, b_s = fit(train, mu, sd)
    w, b = unstandardise(w_s, b_s, mu, sd)
    held = evaluate(test, w, b, thr)

    model = {"features": FEATURES, "w": w, "b": b, "threshold": thr,
             "horizon_s": horizon,
             "trained_on_runs": len(train_runs), "held_out_runs": len(test_runs),
             "train_samples": len(train), "positives": sum(y for _, y in train),
             "held_out": held,
             "working_set_source": dict(SOURCE)}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(model, open(out, "w"), indent=2)
    print(f"model -> {out}")
    print(f"  trained on {len(train_runs)} runs ({len(train)} samples, "
          f"{model['positives']} positive), held out {len(test_runs)} runs")
    print(f"  held-out precision {held['precision']}  recall {held['recall']}")
    tot = sum(SOURCE.values()) or 1
    print(f"  working sets: {SOURCE['logged_est']} from the controller's log, "
          f"{SOURCE['peak_fallback']} from the peak fallback")
    if SOURCE["peak_fallback"] / tot > 0.5:
        print("  WARNING: most samples had no logged estimate and used the peak fallback; "
              "the model was trained on a different working set than stasisd will give it")
    if len(test_runs) < 3:
        print("  WARNING: fewer than 3 held-out runs; this estimate is not reportable")
    # Physical sanity. A rising PSI and a growing swapped-out share must not
    # *lower* the probability of thrashing, and more headroom must not raise
    # it. A model that learned the opposite has fitted run identity, not
    # pressure - the usual result with too few runs - and is unsafe to deploy.
    expect = {"psi_slope": +1, "missing_frac": +1, "fit_margin": -1}
    wrong = [k for k, s in expect.items() if w[k] * s < 0]
    if wrong:
        print(f"  WARNING: weights have the wrong sign for {', '.join(wrong)}. "
              f"This model has learned something other than pressure; do not "
              f"use it with stasisd --predict. Record more runs and retrain.")
        model["sign_check"] = {"passed": False, "wrong": wrong}
        json.dump(model, open(out, "w"), indent=2)
        sys.exit(3)
    model["sign_check"] = {"passed": True, "wrong": []}
    json.dump(model, open(out, "w"), indent=2)


if __name__ == "__main__":
    main()
