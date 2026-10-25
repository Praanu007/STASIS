#!/usr/bin/env python3
"""Compare diagnostic conditions against the campaign's EEVDF N=8 runs:
throughput, how many jobs truly ran at once, swap traffic, stall."""
import csv, glob, os, statistics as st

def overlap(d):
    t0 = float(open(f"{d}/t0").read())
    spans = []
    for f in glob.glob(f"{d}/job-*.log"):
        L = [l.split() for l in open(f)]
        L = [(int(p), float(t)) for p, t in (x for x in L if len(x) == 2 and x[0].isdigit()) if int(p) > 0]
        if L:
            spans.append((L[0][1] - t0, L[-1][1] - t0))
    if not spans:
        return None
    return max(sum(1 for a, b in spans if a <= t <= b) for t, _ in spans)

def row(label, d, m):
    f = lambda k: float(m[k]) if m.get(k) not in ("", None, "None") else float("nan")
    sw = f("swap_pages") * 4096 / 2**20
    return (f"{label:22s} {m['status']:8s} {f('throughput'):8.1f}  {str(overlap(d)):>4s}/8"
            f"  {sw:9.0f}  {f('psi_full_pct'):7.1f}  {f('duration_s'):7.0f}  {int(f('kills'))}")

print(f"{'run':22s} {'status':8s} {'tput':>8s}  {'conc':>6s}  {'swap MiB':>9s}  {'PSIfull%':>7s}  {'secs':>7s}  kills")
for root in ("/srv/results", "/srv/diag"):
    ms = {(r["label"], r["N"], r["run"]): r for r in csv.DictReader(open(f"{root}/metrics.csv"))}
    for (label, n, k), m in sorted(ms.items()):
        if n != "8" or (root == "/srv/results" and label != "eevdf"):
            continue
        name = ("campaign " if root == "/srv/results" else "diag ") + f"{label} r{k}"
        print(row(name, f"{root}/{label}/N8/run{k}", m))
