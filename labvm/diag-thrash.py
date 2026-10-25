#!/usr/bin/env python3
"""How much did the EEVDF runs actually thrash? Per N: PSI full stall share,
swap traffic, and major faults, medians over the runs in metrics.csv."""
import csv, statistics as st

rows = [r for r in csv.DictReader(open("/srv/results/metrics.csv")) if r["label"] == "eevdf"]
by = {}
for r in rows:
    by.setdefault(int(r["N"]), []).append(r)

def med(rs, k):
    v = [float(r[k]) for r in rs if r.get(k) not in ("", "None", None)]
    return st.median(v) if v else float("nan")

print(" N  PSI full %  swap MiB  swap MiB/s  major faults  duration s  throughput")
for n in sorted(by):
    rs = by[n]
    sw = med(rs, "swap_pages") * 4096 / 2**20
    d = med(rs, "duration_s")
    print(f"{n:2d}  {med(rs, 'psi_full_pct'):9.1f}  {sw:8.0f}  {sw / d if d else 0:10.1f}"
          f"  {med(rs, 'majflt'):12.0f}  {d:10.0f}  {med(rs, 'throughput'):10.1f}")
