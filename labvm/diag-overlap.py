#!/usr/bin/env python3
"""Did a run's jobs actually run at the same time? Per job: when it finished
its first pass and its last, relative to the run's start. Usage: RUN_DIR..."""
import os, sys
for d in sys.argv[1:]:
    t0 = float(open(f"{d}/t0").read())
    rows = []
    for f in sorted(os.listdir(d), key=lambda x: (len(x), x)):
        if f.startswith("job-") and f.endswith(".log"):
            L = [l.split() for l in open(f"{d}/{f}")]
            L = [(int(p), float(t)) for p, t in (x for x in L if len(x) == 2 and x[0].isdigit()) if int(p) > 0]
            if L:
                rows.append((f[:-4], L[0][1] - t0, L[-1][1] - t0, L[-1][0]))
    print(f"\n{d}")
    print("  job     first pass at   last pass at   passes")
    for j, a, b, n in rows:
        print(f"  {j:7s} {a:10.0f} s   {b:10.0f} s   {n:7d}")
    if rows:
        ends = sorted(r[2] for r in rows); starts = sorted(r[1] for r in rows)
        conc = max(sum(1 for r in rows if r[1] <= t <= r[2]) for t in starts)
        print(f"  most jobs past their first pass at once: {conc} of {len(rows)}")
