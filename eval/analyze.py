#!/usr/bin/env python3
"""analyze.py - metrics, figures and the pass/fail table for STASIS runs.

  analyze.py metrics   ROOT                 -> ROOT/metrics.csv
  analyze.py calibrate ROOT [--config eevdf] -> PFF distribution, PFF_HI hint
  analyze.py figures   ROOT                 -> ROOT/figures/F1..F8.png
  analyze.py score     ROOT PREREG.md       -> ROOT/score.md

ROOT holds LABEL/N<n>/run<k>/ directories written by run_mix.sh.

Metric definitions (build guide, Phase 5, plus the revised design's):
  throughput    total passes / (time until the last job finishes); a run that
                timed out is scored with the passes completed by the timeout
  kills         workload processes killed by the kernel's OOM killer, by
                systemd-oomd or by earlyoom (kills.txt + oomd_kills.txt +
                earlyoom_kills.txt). STASIS itself never kills; this counts
                what the system did anyway.
  jain          Jain's index (sum x)^2 / (n * sum x^2) over each job's progress
                rate, x_i = passes job i completed / time from the run's start
                until it finished (or the run ended): fairness of slowdowns over
                the whole run. A killed or starved job scores low.
  jain_window   the build guide's version: x_i = passes completed between
                pressure onset and the first job finishing. Reported, not
                scored: the window ends when the first job does, which is
                before a load controller has rotated through the parked jobs,
                so it reads about (jobs resident) / N for any admission
                controller (docs/00-prereg.md, deviation 20).
  p99_us        p99 of the probe's cyclictest histogram
  ttfp_s        first sample with PSI full avg10 > 0 -> first freeze decision
  swap_pages    delta (pswpin + pswpout) across the run
  max_wait_s    longest time any job spent parked by stasisd in one stretch
                (stasisd configs only; the starvation bound is tested on it)
  parked_pct    share of all job-time spent parked (stasisd configs only)
  max_gap_s     longest time any job went without finishing a pass, from its
                start until it finished (or the run ended): the starvation
                metric every configuration has, whatever stopped the job
  psi_full_pct  share of the run in which every task waited on memory (PSI full)
  majflt        major page faults across the run (whole machine)
  jobs_done_600 jobs finished within 600 s of the start
  makespan_s    time until the last job finished (empty if any didn't)
  hot, ws       the job's hot share (%) and stasisd's working-set estimate
  jump_s        experiment C: when the first job's working set jumped (s from
                the start; the job logs "jump <t>")
  detect_s      experiment C: from that jump to the first park stasisd made
                because of pressure (pressure-psi or pressure-pff)
  jump_src      experiment C: the signal of the first pressure after the jump
                that stasisd acted on (not warm-up): psi or pff
Pressure onset is the earlier of the first PSI full avg10 > 0 sample and the
controller's first logged pressure event, so every config uses one definition.

Added 2026-10-24 (arm 8; these only read what the run already wrote, so they
can be computed over runs recorded before that date wherever the field
exists):
  jain_frozen  Jain's index over per-job parked time. jain measures fairness
               in work done; this measures fairness in the thing a load
               controller actually rations. Uses cgroup.stat.local:frozen_usec
               when the kernel has it and any job was frozen; otherwise counts
               the samples each job spent parked, by cgroup.freeze (STASIS) or
               SIGSTOP (thrash-protect). None for configs that never park.
  ctrl_cpu_s   the controller's own CPU seconds, from its periodic "self" log
  ctrl_rss_kb  the controller's resident size, same source
               Together these are the honest energy proxy: RAPL is unavailable
               or meaningless in a VM, so the cost of running the controller is
               reported directly instead of being converted into joules.
"""
import csv, glob, json, math, os, re, statistics, sys

PAGE = 4096                      # for swap_pages -> bytes in the energy proxy

FIELDS = ["label", "config", "N", "run", "status", "throughput", "passes",
          "kills", "jain", "jain_window", "p99_us", "max_us", "ttfp_s", "first_src",
          "swap_pages", "duration_s", "max_wait_s", "parked_pct", "max_gap_s",
          "psi_full_pct", "majflt", "jobs_done_600", "makespan_s", "hot", "ws",
          "jump_s", "detect_s", "jump_src",
          "jain_frozen", "ctrl_cpu_s", "ctrl_rss_kb"]     # arm 8, all numeric
TEXT = ("label", "config", "status", "first_src", "ws", "jump_src")

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

def job_jumps(run):
    """Times at which jobs changed phase (wss JUMP_PASS), sorted."""
    out = []
    for p in glob.glob(f"{run}/job-*.log"):
        with open(p) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[0] == "jump":
                    try:
                        out.append(float(parts[1]))
                    except ValueError:
                        pass
    return sorted(out)

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
    RUNNING never finished (the guest wedged or was reset), and an UNAVAILABLE
    one never started (its baseline can't run on this kernel): both skipped."""
    for d in sorted(glob.glob(f"{root}/*/N*/run*")):
        try:
            with open(f"{d}/status") as f:
                if f.read().strip() in ("RUNNING", "UNAVAILABLE"):
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
    for f in ("kills.txt", "oomd_kills.txt", "earlyoom_kills.txt"):
        try:
            kills += sum(1 for line in open(f"{d}/{f}") if line.strip())
        except FileNotFoundError:
            pass

    psi_t = next((s["t"] for s in samples if s["psi"].get("full", {}).get("avg10", 0) > 0), None)
    pres = [e for e in decisions if e.get("event") == "pressure"]
    dec_t = pres[0]["t"] if pres else None
    onset = min((t for t in (psi_t, dec_t) if t is not None), default=None)
    first_src = pres[0].get("src") if pres else None

    J = Jw = None
    if jobs and t0 and t_end:
        J = jain([(p[-1][0] if p else 0) / (done.get(j, t_end) - t0) for j, p in jobs.items()])
    t_first = min(done.values()) if done else t_end
    if onset is not None and jobs and t_first is not None:
        xs = []
        for p in jobs.values():
            xs.append(sum(1 for (i, t) in p if i > 0 and onset <= t <= t_first))
        Jw = jain(xs)

    freezes = [e["t"] for e in decisions if e.get("frozen") is True]
    ttfp = (freezes[0] - psi_t) if (freezes and psi_t is not None) else None

    hist, mx = probe_hist(d)
    swap = psi_pct = majflt = None
    if len(samples) >= 2:
        a, b = samples[0], samples[-1]
        swap = b["pswpin"] + b["pswpout"] - a["pswpin"] - a["pswpout"]
        if b["t"] > a["t"]:
            psi_pct = 100 * (b["psi"]["full"]["total"] - a["psi"]["full"]["total"]) / 1e6 / (b["t"] - a["t"])
        majflt = b.get("pgmajfault", 0) - a.get("pgmajfault", 0)

    wait, parked_pct = parking(decisions, t_end)
    gap = None
    if jobs and t0:
        gaps = []
        for p in jobs.values():
            ts = [t0] + [t for (i, t) in p if i > 0]
            if not (p and p[-1][0] >= passes_target) and t_end:
                ts.append(t_end)                   # still unfinished at the end
            gaps.append(max(b - a for a, b in zip(ts, ts[1:])) if len(ts) > 1 else 0)
        gap = max(gaps)
    start = next((e for e in decisions if e.get("event") == "start"), {})
    jumps = job_jumps(d)
    jump_s = detect = jump_src = None
    if jumps:
        tj = jumps[0]
        jump_s = tj - t0 if t0 else None
        parks = [e["t"] for e in decisions if e.get("frozen") is True and e["t"] >= tj
                 and str(e.get("why", "")).startswith("pressure-")]
        detect = parks[0] - tj if parks else None
        acted = next((e for e in decisions if e.get("event") == "pressure" and e["t"] >= tj
                      and not e.get("warming")), None)
        jump_src = acted.get("src") if acted else None
    hot = int(next((c.split("=")[1] for c in cfg if c.startswith("HOT=")), 100))

    # Fairness over parked time (arm 8). Prefer the kernel's cumulative
    # frozen_usec when it shows any freezing. Otherwise count, per job, the
    # samples in which it was parked by either mechanism: cgroup.freeze
    # (STASIS) or SIGSTOP (thrash-protect, which frozen_usec never sees). Each
    # such sample stands for one sampling interval.
    JF = None
    if samples:
        last = samples[-1]["jobs"]
        fu = [v.get("frozen_usec") for v in last.values()] if last else []
        if fu and all(x is not None for x in fu) and any(fu):
            JF = jain(fu)
        else:
            held = {}
            for s in samples:
                for j, v in s["jobs"].items():
                    held[j] = held.get(j, 0) + (1 if v.get("frozen") or v.get("stopped") else 0)
            JF = jain(list(held.values())) if held else None

    selfs = [e for e in decisions if e.get("event") == "self"]

    return {"label": label, "config": cfg[0], "N": n, "run": k, "status": status,
            "throughput": throughput, "passes": passes, "kills": kills, "jain": J, "jain_window": Jw,
            "p99_us": percentile(hist, 0.99), "max_us": mx, "ttfp_s": ttfp,
            "first_src": first_src, "swap_pages": swap,
            "duration_s": (t_end - t0) if t0 and t_end else None,
            "max_wait_s": wait, "parked_pct": parked_pct, "max_gap_s": gap,
            "psi_full_pct": psi_pct, "majflt": majflt,
            "jobs_done_600": sum(1 for t in done.values() if t0 and t - t0 <= 600) if jobs else None,
            "makespan_s": (finish - t0) if (t0 and len(done) == len(jobs) and jobs) else None,
            "hot": hot, "ws": start.get("ws", "footprint" if start else None),
            "jump_s": jump_s, "detect_s": detect, "jump_src": jump_src,
            "jain_frozen": JF,
            "ctrl_cpu_s": selfs[-1].get("cpu_s") if selfs else None,
            "ctrl_rss_kb": selfs[-1].get("rss_kb") if selfs else None}

def parking(decisions, t_end):
    """(longest single parked stretch, share of job-time parked) from the
    decision log, or (None, None) if stasisd didn't run."""
    if not any(e.get("event") == "start" for e in decisions):
        return None, None
    t_start = decisions[0]["t"]
    since, longest, parked, first, last = {}, 0.0, 0.0, {}, {}
    for e in decisions:
        if "frozen" not in e:
            continue
        j, t = e["job"], e["t"]
        first.setdefault(j, t_start); last[j] = t
        if e["frozen"]:
            since[j] = t
        elif j in since:
            dt = t - since.pop(j)
            longest, parked = max(longest, dt), parked + dt
    end = t_end or (decisions[-1]["t"] if decisions else t_start)
    for j, t in since.items():                   # still parked when the run ended
        longest, parked = max(longest, end - t), parked + (end - t)
    span = sum(max(0.0, end - first[j]) for j in first)
    return round(longest, 1), (round(100 * parked / span, 1) if span else 0.0)

def load_metrics(root):
    path = f"{root}/metrics.csv"
    if not os.path.exists(path):
        cmd_metrics(root)
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            for k in FIELDS:
                if k in TEXT:
                    continue
                if k not in r:                 # metrics.csv from an older analyze.py
                    r[k] = None
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

ORDER = ["eevdf", "stasis", "ctrl", "stasis-norot", "stasis-fp", "mglru", "oomd",
         "thrashprotect", "earlyoom", "scxonly", "simple"]
NAMES = {"eevdf": "EEVDF", "stasis": "Full STASIS", "ctrl": "STASIS controller-only",
         "stasis-norot": "STASIS, no rotation", "stasis-fp": "STASIS, footprint estimate",
         "mglru": "EEVDF + MGLRU min_ttl", "oomd": "EEVDF + systemd-oomd",
         "thrashprotect": "thrash-protect", "earlyoom": "earlyoom (arm 2)",
         "simple": "scx_simple", "scxonly": "scx_stasis alone"}
# Arm 1's baselines, used to score sensitivity (H10). earlyoom (arm 2) is
# deliberately not added: that would change how arm 1's H10 is scored.
BASELINES = ("eevdf", "mglru", "oomd", "thrashprotect")

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
    plotted = 0
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
        plotted += 1
    if plotted:                                   # no N=8 runs yet: no F4
        ax.set_xscale("log"); ax.set_xlabel("probe wake-up latency (µs)"); ax.set_ylabel("CDF")
        ax.set_title("F4  Interactive latency at N=8"); ax.legend(fontsize=8); ax.grid(alpha=.3)
        fig.tight_layout(); fig.savefig(f"{out}/F4_latency_cdf.png", dpi=150)
    plt.close(fig)

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

    # F6: starvation. Longest time without progress per run at N=8, every
    # config; the starvation bound applies to the stasisd ones.
    shown = [l for l in labels if any(r["label"] == l and r["N"] == 8 and r["max_gap_s"] is not None
                                      for r in rows)]
    if shown:
        fig, ax = plt.subplots(figsize=(max(6, 1.1 * len(shown) + 2), 4))
        for i, l in enumerate(shown):
            v = [r["max_gap_s"] for r in rows if r["label"] == l and r["N"] == 8 and r["max_gap_s"] is not None]
            ax.scatter([i] * len(v), v, s=16, color=f"C{i}")
            ax.hlines(statistics.median(v), i - .25, i + .25, color=f"C{i}")
        bound = next((e.get("max_wait") for r in rows if r["label"] == "stasis" and r["N"] == 8
                      for e in jsonl(f"{run_dir(r)}/decisions.jsonl")[:1] if e.get("max_wait")), None)
        if bound:
            ax.axhline(bound, color="k", ls="--", lw=1)
            ax.text(len(shown) - .5, bound, f" starvation bound {bound:g} s", va="bottom", ha="right", fontsize=8)
        ax.set_xticks(range(len(shown)), [NAMES.get(l, l) for l in shown], rotation=20, ha="right", fontsize=8)
        ax.set_ylabel("longest time without progress (s)")
        ax.set_title("F6  Starvation at N=8: the longest any job went without finishing a pass")
        ax.grid(alpha=.3, axis="y"); fig.tight_layout(); fig.savefig(f"{out}/F6_starvation.png", dpi=150)

    # F7: working sets, measured vs footprint, in one full-STASIS run (the
    # median-throughput run at the largest N that logged measurements)
    for n in (8, 6, 4):
        r = median_run(rows, "stasis", n)
        if not r:
            continue
        ws = [e for e in jsonl(f"{run_dir(r)}/decisions.jsonl") if e.get("event") == "ws"]
        if not any(v.get("m") for e in ws for v in e["jobs"].values()):
            continue
        t0 = read_float(f"{run_dir(r)}/t0") or ws[0]["t"]
        fig, ax = plt.subplots(figsize=(8, 4))
        names = sorted({j for e in ws for j in e["jobs"]}, key=lambda j: int(re.sub(r"\D", "", j) or 0))
        for i, j in enumerate(names):
            pts = [(e["t"] - t0, e["jobs"][j]) for e in ws if j in e["jobs"]]
            ax.plot([t for t, _ in pts], [v["fp"] / 2**20 for _, v in pts], color=f"C{i % 10}", ls=":", lw=1)
            m = [(t, v["m"] / 2**20) for t, v in pts if v.get("m")]
            if m:
                ax.plot([t for t, _ in m], [x for _, x in m], color=f"C{i % 10}", lw=1.5,
                        label=j.replace("stasis-", "").replace(".service", ""))
        ax.set_xlabel("time (s)"); ax.set_ylabel("MiB")
        ax.set_title(f"F7  Working sets in one STASIS run (N={n}): measured (solid) vs footprint (dotted)")
        ax.legend(fontsize=7, ncol=4); ax.grid(alpha=.3)
        fig.tight_layout(); fig.savefig(f"{out}/F7_working_sets.png", dpi=150)
        break

    # F8: sensitivity (H10). Full STASIS at N=8 under each swept setting,
    # against the best baseline run at N=8: the result must not hinge on one
    # lucky setting.
    sens = [r for r in rows if r["label"].startswith("sens-") and r["N"] == 8 and r["throughput"] is not None]
    if sens:
        best = max((r["throughput"] for r in rows if r["label"] in BASELINES and r["N"] == 8
                    and r["throughput"] is not None), default=None)
        sweeps = [("q", "rotation quantum (s of CPU)", "15"), ("w", "starvation bound (s)", "120"),
                  ("t", "PSI trigger (ms of full stall per s)", "100")]
        fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True)
        for ax, (key, name, default) in zip(axes, sweeps):
            settings = sorted({r["label"][6:] for r in sens if r["label"][5] == key}, key=float)
            for i, v in enumerate(settings):
                ys = [r["throughput"] for r in sens if r["label"] == f"sens-{key}{v}"]
                ax.scatter([i] * len(ys), ys, s=16, color="C0")
                ax.hlines(statistics.median(ys), i - .25, i + .25, color="C0")
            ax.set_xticks(range(len(settings)), [v + (" (default)" if v == default else "") for v in settings], fontsize=8)
            if best is not None:
                ax.axhline(best, color="k", ls="--", lw=1)
            ax.set_title(name, fontsize=10); ax.grid(alpha=.3, axis="y")
        axes[0].set_ylabel("throughput (passes / s)")
        if best is not None:
            axes[-1].text(1, best, "best baseline run ", transform=axes[-1].get_yaxis_transform(),
                          ha="right", va="bottom", fontsize=8)
        fig.suptitle("F8  Sensitivity: full STASIS at N=8 under each setting"); fig.tight_layout()
        fig.savefig(f"{out}/F8_sensitivity.png", dpi=150)
    print(f"figures -> {out}/")

# ---- scoring against the frozen pre-registration ----------------------------

def load_hypotheses(prereg, experiment=None):
    """The ```json hypotheses block of the frozen pre-registration; with an
    experiment ("A" or "B"), only that experiment's (no "experiment" key = A)."""
    text = open(prereg).read()
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        raise ValueError(f"no ```json hypotheses block in {prereg}")
    hyps = json.loads(m.group(1))["hypotheses"]
    if experiment:
        hyps = [h for h in hyps if h.get("experiment", "A") == experiment]
    return hyps

def experiment_of(root):
    try:
        return open(f"{root}/experiment").read().strip() or "A"
    except FileNotFoundError:
        return "A"

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
                   "better": h.get("better"), "equals": h.get("equals"), "at_most": h.get("at_most"),
                   "stasis": vals(h["stasis"]), "baseline": None, "baseline_label": None}
            s = row["stasis"]
            if not s:
                row["result"] = "MISSING"
            elif "equals" in h:
                row["result"] = "PASS" if all(v == h["equals"] for v in s) else "FAIL"
            elif "at_most" in h:
                row["result"] = "PASS" if all(v <= h["at_most"] for v in s) else "FAIL"
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
        hyps = load_hypotheses(prereg, experiment_of(root))
    except ValueError as e:
        sys.exit(str(e))
    fmt = lambda v: f"{statistics.median(v):.3g} [{min(v):.3g}, {max(v):.3g}]"
    lines = ["| H | claim | N | STASIS (median, range) | best baseline | result |",
             "|---|---|---|---|---|---|"]
    for r in score_table(load_metrics(root), hyps):
        stasis = fmt(r["stasis"]) if r["stasis"] else "no data"
        if r["equals"] is not None:
            base = f"(= {r['equals']})"
        elif r["at_most"] is not None:
            base = f"(at most {r['at_most']})"
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
