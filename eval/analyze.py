#!/usr/bin/env python3
"""analyze.py - metrics, figures and the pass/fail table for STASIS runs.

  analyze.py metrics   ROOT                 -> ROOT/metrics.csv
  analyze.py calibrate ROOT [--config eevdf] -> PFF distribution, PFF_HI hint
  analyze.py figures   ROOT                 -> ROOT/figures/F1..F5.png
  analyze.py score     ROOT PREREG.md       -> ROOT/score.md

ROOT holds LABEL/N<n>/run<k>/ directories written by run_mix.sh.

Metric definitions (build guide, Phase 5):
  throughput   total passes / (time until the last job finishes); a run that
               timed out is scored with the passes completed by the timeout
  kills        lines in kills.txt + oomd_kills.txt
  jain         (sum x)^2 / (n * sum x^2), x_i = passes job i completed between
               pressure onset and the first job finishing
  p99_us       p99 of the probe's cyclictest histogram
  ttfp_s       first sample with PSI full avg10 > 0 -> first freeze decision
  swap_pages   delta (pswpin + pswpout) across the run
Pressure onset is the earlier of the first PSI full avg10 > 0 sample and the
controller's first logged pressure event, so every config uses one definition.
"""
import csv, glob, json, math, os, re, statistics, sys

FIELDS = ["label", "config", "N", "run", "status", "throughput", "passes",
          "kills", "jain", "p99_us", "max_us", "ttfp_s", "first_src",
          "swap_pages", "duration_s"]

# ---- loading ----------------------------------------------------------------

def jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass                # truncated last line after a kill
    except FileNotFoundError:
        pass
    return out

def read_float(path):
    try:
        with open(path) as f:
            return float(f.read().split()[0])
    except (FileNotFoundError, IndexError, ValueError):
        return None

def job_logs(run):
    """job name -> list of (pass, t); pass 0 is the start marker."""
    jobs = {}
    for p in sorted(glob.glob(f"{run}/job-*.log")):
        pts = []
        with open(p) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        pts.append((int(parts[0]), float(parts[1])))
                    except ValueError:
                        pass
        jobs[os.path.basename(p)[:-4]] = pts
    return jobs

def probe_hist(run):
    """cyclictest -h histogram -> (sorted [(latency_us, count)], max_us)."""
    hist, mx, overflow = [], None, 0
    try:
        with open(f"{run}/probe.txt") as f:
            for line in f:
                m = re.match(r"^(\d+)\s+(\d+)\s*$", line)
                if m:
                    us, n = int(m.group(1)), int(m.group(2))
                    if n:
                        hist.append((us, n))
                elif line.startswith("# Max Latencies:"):
                    mx = max(int(x) for x in line.split(":")[1].split())
                elif line.startswith("# Histogram Overflows:"):
                    overflow = sum(int(x) for x in line.split(":")[1].split())
    except FileNotFoundError:
        return [], None
    if overflow and mx is not None:
        hist.append((mx, overflow))     # samples past the last bucket
    return sorted(hist), mx

def percentile(hist, q):
    total = sum(n for _, n in hist)
    if not total:
        return None
    acc = 0
    for us, n in hist:
        acc += n
        if acc >= q * total:
            return us
    return hist[-1][0]

def jain(xs):
    xs = [x for x in xs]
    if not xs or not any(xs):
        return None
    return sum(xs) ** 2 / (len(xs) * sum(x * x for x in xs))

def runs(root):
    """Finished runs as (label, N, run, dir). A run whose status still says
    RUNNING never finished (the guest wedged or was reset) and is skipped."""
    for d in sorted(glob.glob(f"{root}/*/N*/run*")):
        try:
            with open(f"{d}/status") as f:
                if f.read().strip() == "RUNNING":
                    continue
        except FileNotFoundError:
            continue
        label = d.split(os.sep)[-3]
        n = int(d.split(os.sep)[-2][1:])
        k = int(d.split(os.sep)[-1][3:])
        yield label, n, k, d

# ---- metrics ----------------------------------------------------------------

def run_metrics(label, n, k, d):
    cfg = open(f"{d}/config").read().split()
    passes_target = int(next((c.split("=")[1] for c in cfg if c.startswith("PASSES=")), 0))
    status = open(f"{d}/status").read().strip()
    t0 = read_float(f"{d}/t0")
    t_end = read_float(f"{d}/t_end")
    jobs = job_logs(d)
    samples = jsonl(f"{d}/samples.jsonl")
    decisions = jsonl(f"{d}/decisions.jsonl")

    done = {j: p[-1][1] for j, p in jobs.items() if p and p[-1][0] >= passes_target}
    passes = sum(p[-1][0] for p in jobs.values() if p)
    finish = max(done.values()) if len(done) == len(jobs) and jobs else t_end
    throughput = passes / (finish - t0) if (t0 and finish and finish > t0 and n) else None

    kills = 0
    for f in ("kills.txt", "oomd_kills.txt"):
        try:
            kills += sum(1 for line in open(f"{d}/{f}") if line.strip())
        except FileNotFoundError:
            pass

    psi_t = next((s["t"] for s in samples if s["psi"].get("full", {}).get("avg10", 0) > 0), None)
    pres = [e for e in decisions if e.get("event") == "pressure"]
    dec_t = pres[0]["t"] if pres else None
    onset = min((t for t in (psi_t, dec_t) if t is not None), default=None)
    first_src = pres[0].get("src") if pres else None

    J = None
    if onset is not None and jobs:
        t_first = min(done.values()) if done else t_end
        xs = []
        for p in jobs.values():
            xs.append(sum(1 for (i, t) in p if i > 0 and onset <= t <= t_first))
        J = jain(xs)

    freezes = [e["t"] for e in decisions if e.get("frozen") is True]
    ttfp = (freezes[0] - psi_t) if (freezes and psi_t is not None) else None

    hist, mx = probe_hist(d)
    swap = None
    if len(samples) >= 2:
        swap = (samples[-1]["pswpin"] + samples[-1]["pswpout"]
                - samples[0]["pswpin"] - samples[0]["pswpout"])

    return {"label": label, "config": cfg[0], "N": n, "run": k, "status": status,
            "throughput": throughput, "passes": passes, "kills": kills, "jain": J,
            "p99_us": percentile(hist, 0.99), "max_us": mx, "ttfp_s": ttfp,
            "first_src": first_src, "swap_pages": swap,
            "duration_s": (t_end - t0) if t0 and t_end else None}

def load_metrics(root):
    path = f"{root}/metrics.csv"
    if not os.path.exists(path):
        cmd_metrics(root)
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            for k in FIELDS:
                if k in ("label", "config", "status", "first_src"):
                    continue
                r[k] = float(r[k]) if r[k] not in ("", "None") else None
            r["N"] = int(r["N"]); r["run"] = int(r["run"])
            rows.append(r)
    return rows

def cmd_metrics(root):
    rows = [run_metrics(*r) for r in runs(root)]
    with open(f"{root}/metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    print(f"{len(rows)} runs -> {root}/metrics.csv")
    return rows

# ---- PFF calibration (Phase 1 step 4) ---------------------------------------

def cmd_calibrate(root, config="eevdf"):
    """Faults per CPU-ms per job per 1 s sample, fitting vs thrashing runs."""
    hz = os.sysconf("SC_CLK_TCK")
    by_n = {}
    for label, n, k, d in runs(root):
        if label != config or n == 0:
            continue
        prev = {}
        for s in jsonl(f"{d}/samples.jsonl"):
            for j, v in s["jobs"].items():
                if "pgmajfault" in v:              # cgroup counters, CPU in µs
                    cur = (v["pgmajfault"], v["cpu_us"] / 1000)
                elif "majflt" in v:                # older samples: /proc, CPU in ticks
                    cur = (v["majflt"], v["ticks"] * 1000 / hz)
                else:
                    continue
                if j in prev:
                    df, dms = cur[0] - prev[j][0], cur[1] - prev[j][1]
                    if dms > 0:
                        by_n.setdefault(n, []).append(df / dms)
                prev[j] = cur
    if not by_n:
        print("no per-job fault samples; run the Phase 1 sweep first")
        return
    print(f"{'N':>3} {'samples':>8} {'p50':>9} {'p90':>9} {'p99':>9}   faults per CPU-ms")
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]
    for n in sorted(by_n):
        xs = by_n[n]
        print(f"{n:>3} {len(xs):>8} {q(xs, .5):>9.3f} {q(xs, .9):>9.3f} {q(xs, .99):>9.3f}")
    fit = [x for n, xs in by_n.items() if n <= 4 for x in xs]
    thr = [x for n, xs in by_n.items() if n >= 7 for x in xs]
    if fit and thr:
        lo, hi = q(fit, .99), q(thr, .5)
        hint = math.sqrt(max(lo, 1e-3) * max(hi, 1e-3))
        print(f"\nfitting (N<=4) p99 = {lo:.3f}; thrashing (N>=7) p50 = {hi:.3f}")
        print(f"suggested PFF_HI ~ {hint:.3f} (geometric midpoint). Freeze it in docs/00-prereg.md.")

# ---- figures ----------------------------------------------------------------

ORDER = ["eevdf", "stasis", "ctrl", "scxonly", "oomd", "simple", "thrashprotect"]
NAMES = {"eevdf": "EEVDF", "stasis": "Full STASIS", "ctrl": "STASIS controller-only",
         "oomd": "EEVDF + systemd-oomd", "simple": "scx_simple", "scxonly": "scx_stasis alone",
         "thrashprotect": "thrash-protect"}

def median_run(rows, label, n, key="throughput"):
    rs = [r for r in rows if r["label"] == label and r["N"] == n and r[key] is not None]
    if not rs:
        return None
    rs.sort(key=lambda r: r[key])
    return rs[len(rs) // 2]

def cmd_figures(root):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = load_metrics(root)
    out = f"{root}/figures"
    os.makedirs(out, exist_ok=True)
    labels = [l for l in ORDER if any(r["label"] == l for r in rows)]
    run_dir = lambda r: f"{root}/{r['label']}/N{r['N']}/run{r['run']}"

    # F1: throughput vs N, median with min-max range. Lines join measured N at
    # most 2 apart (the campaign's grids step by 1 or 2); a wider gap stays
    # open rather than drawing cells that weren't measured.
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for l in labels:
        ns = sorted({r["N"] for r in rows if r["label"] == l and r["N"] > 0})
        segs = []
        for n in ns:
            if segs and n - segs[-1][-1] <= 2:
                segs[-1].append(n)
            else:
                segs.append([n])
        color = None
        for i, seg in enumerate(segs):
            med, lo, hi = [], [], []
            for n in seg:
                v = [r["throughput"] for r in rows if r["label"] == l and r["N"] == n and r["throughput"]]
                m = statistics.median(v) if v else float("nan")
                med.append(m); lo.append(m - min(v) if v else 0); hi.append(max(v) - m if v else 0)
            eb = ax.errorbar(seg, med, yerr=[lo, hi], marker="o", capsize=3, color=color,
                             label=NAMES.get(l, l) if i == 0 else "_nolegend_")
            color = eb.lines[0].get_color()
    ax.set_xlabel("concurrent jobs N"); ax.set_ylabel("throughput (passes / s)")
    ax.set_title("F1  Throughput vs load: the thrashing curve"); ax.legend(fontsize=8)
    ax.grid(alpha=.3); fig.tight_layout(); fig.savefig(f"{out}/F1_throughput.png", dpi=150)

    # F2: timeline of one full-STASIS N=8 run
    r = median_run(rows, "stasis", 8)
    if r:
        d = run_dir(r)
        S = jsonl(f"{d}/samples.jsonl"); D = jsonl(f"{d}/decisions.jsonl")
        t0 = read_float(f"{d}/t0") or S[0]["t"]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot([s["t"] - t0 for s in S], [s["psi"]["full"]["avg10"] for s in S],
                color="tab:red", label="PSI full avg10 (%)")
        ax.set_xlabel("time (s)"); ax.set_ylabel("PSI full avg10 (%)", color="tab:red")
        ax2 = ax.twinx()
        ax2.step([s["t"] - t0 for s in S],
                 [sum(not v["frozen"] for v in s["jobs"].values()) for s in S],
                 where="post", color="tab:blue", label="active jobs")
        ax2.set_ylabel("active jobs", color="tab:blue"); ax2.set_ylim(0, r["N"] + 0.5)
        for e in D:
            if "frozen" in e:
                ax.axvline(e["t"] - t0, color="k" if e["frozen"] else "tab:green",
                           alpha=.25, lw=.8)
        ax.set_title(f"F2  One N=8 STASIS run (black = freeze, green = thaw)")
        fig.tight_layout(); fig.savefig(f"{out}/F2_timeline.png", dpi=150)

    # F3: per-job progress, STASIS vs thrash-protect (median-throughput N=8 run)
    pair = [(l, median_run(rows, l, 8)) for l in ("stasis", "thrashprotect", "eevdf")]
    pair = [(l, r) for l, r in pair if r][:2]
    if pair:
        fig, axes = plt.subplots(1, len(pair), figsize=(4.5 * len(pair), 3.6), squeeze=False)
        for ax, (l, r) in zip(axes[0], pair):
            jobs = job_logs(run_dir(r))
            names = sorted(jobs, key=lambda j: int(j.split("-")[1]))
            ax.barh(names, [jobs[j][-1][0] if jobs[j] else 0 for j in names])
            jtxt = f"{r['jain']:.3f}" if r["jain"] is not None else "n/a"
            ax.set_title(f"{NAMES.get(l, l)}  (Jain J = {jtxt})", fontsize=10)
            ax.set_xlabel("passes completed"); ax.invert_yaxis()
        fig.suptitle("F3  Per-job progress at N=8"); fig.tight_layout()
        fig.savefig(f"{out}/F3_fairness.png", dpi=150)

    # F4: CDF of probe wake-up latency at N=8, plus the unloaded reference
    fig, ax = plt.subplots(figsize=(7, 4.2))
    series = [(l, 8) for l in labels] + [("eevdf", 0)]
    for l, n in series:
        pooled = {}
        for r in rows:
            if r["label"] == l and r["N"] == n:
                for us, c in probe_hist(run_dir(r))[0]:
                    pooled[us] = pooled.get(us, 0) + c
        if not pooled:
            continue
        xs = sorted(pooled); tot = sum(pooled.values()); acc = 0; ys = []
        for x in xs:
            acc += pooled[x]; ys.append(acc / tot)
        ax.plot([max(x, 1) for x in xs], ys,
                label="unloaded" if n == 0 else NAMES.get(l, l),
                ls="--" if n == 0 else "-")
    ax.set_xscale("log"); ax.set_xlabel("probe wake-up latency (µs)"); ax.set_ylabel("CDF")
    ax.set_title("F4  Interactive latency at N=8"); ax.legend(fontsize=8); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(f"{out}/F4_latency_cdf.png", dpi=150)

    # F5: ablation, controller-only vs full STASIS
    metrics = [("ttfp_s", "time to first park (s)"), ("p99_us", "probe p99 (µs)"),
               ("throughput", "throughput (passes/s)")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    for ax, (m, name) in zip(axes, metrics):
        for i, l in enumerate(("ctrl", "stasis")):
            for j, n in enumerate((4, 6, 8)):
                v = [r[m] for r in rows if r["label"] == l and r["N"] == n and r[m] is not None]
                x = j + (i - .5) * .3
                ax.scatter([x] * len(v), v, s=14, color=f"C{i}",
                           label=NAMES[l] if j == 0 else None)
                if v:
                    ax.hlines(statistics.median(v), x - .12, x + .12, color=f"C{i}")
        ax.set_xticks(range(3), ["N=4", "N=6", "N=8"]); ax.set_title(name, fontsize=10)
    axes[0].legend(fontsize=8)
    fig.suptitle("F5  Ablation: what the scheduler adds"); fig.tight_layout()
    fig.savefig(f"{out}/F5_ablation.png", dpi=150)
    print(f"figures -> {out}/")

# ---- scoring against the frozen pre-registration ----------------------------

def load_hypotheses(prereg):
    """The ```json hypotheses block of the frozen pre-registration."""
    text = open(prereg).read()
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        raise ValueError(f"no ```json hypotheses block in {prereg}")
    return json.loads(m.group(1))["hypotheses"]

def score_table(rows, hyps):
    """One row per (hypothesis, N): the STASIS values, the deciding baseline's
    values and the verdict. Strict rule: every STASIS run beats every baseline
    run; 'equals' hypotheses need every STASIS run to match exactly."""
    out = []
    for h in hyps:
        for n in h["N"]:
            vals = lambda l: [r[h["metric"]] for r in rows
                              if r["label"] == l and r["N"] == n and r[h["metric"]] is not None]
            row = {"id": h["id"], "claim": h["claim"], "N": n, "metric": h["metric"],
                   "better": h.get("better"), "equals": h.get("equals"),
                   "stasis": vals(h["stasis"]), "baseline": None, "baseline_label": None}
            s = row["stasis"]
            if not s:
                row["result"] = "MISSING"
            elif "equals" in h:
                row["result"] = "PASS" if all(v == h["equals"] for v in s) else "FAIL"
            else:
                ok, best = True, None
                for b in h["baselines"]:
                    bv = vals(b)
                    if not bv:
                        continue
                    if h["better"] == "higher":
                        ok &= min(s) > max(bv)
                        if best is None or max(bv) > max(best[1]):
                            best = (b, bv)
                    else:
                        ok &= max(s) < min(bv)
                        if best is None or min(bv) < min(best[1]):
                            best = (b, bv)
                if best:
                    row["baseline_label"], row["baseline"] = best
                row["result"] = "PASS" if ok and best else ("FAIL" if best else "NO BASELINE")
            out.append(row)
    return out

def cmd_score(root, prereg):
    try:
        hyps = load_hypotheses(prereg)
    except ValueError as e:
        sys.exit(str(e))
    fmt = lambda v: f"{statistics.median(v):.3g} [{min(v):.3g}, {max(v):.3g}]"
    lines = ["| H | claim | N | STASIS (median, range) | best baseline | result |",
             "|---|---|---|---|---|---|"]
    for r in score_table(load_metrics(root), hyps):
        stasis = fmt(r["stasis"]) if r["stasis"] else "no data"
        if r["equals"] is not None:
            base = f"(= {r['equals']})"
        else:
            base = f"{r['baseline_label']} {fmt(r['baseline'])}" if r["baseline"] else "-"
        lines.append(f"| {r['id']} | {r['claim']} | {r['N']} | {stasis} | {base} | {r['result']} |")
    out = "\n".join(lines) + "\n"
    open(f"{root}/score.md", "w").write(out)
    print(out)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, root = sys.argv[1], sys.argv[2]
    if cmd == "metrics":
        cmd_metrics(root)
    elif cmd == "calibrate":
        cmd_calibrate(root, sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--config" else "eevdf")
    elif cmd == "figures":
        cmd_figures(root)
    elif cmd == "score":
        cmd_score(root, sys.argv[3])
    else:
        sys.exit(__doc__)
