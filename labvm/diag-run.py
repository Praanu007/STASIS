#!/usr/bin/env python3
"""One run, second by second: is the sampler's record complete, and when did
memory pressure and swapping happen? Usage: diag-run.py RUN_DIR"""
import json, os, sys
d = sys.argv[1]
S = [json.loads(l) for l in open(f"{d}/samples.jsonl") if l.strip()]
t0 = float(open(f"{d}/t0").read()); t1 = float(open(f"{d}/t_end").read())
print(f"run {d}: {t1 - t0:.0f} s long; {len(S)} samples from {S[0]['t'] - t0:+.0f} s to {S[-1]['t'] - t0:+.0f} s")
gaps = [(b["t"] - a["t"], a["t"] - t0) for a, b in zip(S, S[1:]) if b["t"] - a["t"] > 3]
print(f"gaps over 3 s in the samples: {len(gaps)}" + (f", largest {max(gaps)[0]:.0f} s at {max(gaps)[1]:+.0f} s" if gaps else ""))
print(f"swap in+out across the samples: {(S[-1]['pswpin'] + S[-1]['pswpout'] - S[0]['pswpin'] - S[0]['pswpout']) * 4 / 1024:.0f} MiB")
print("\n   t   PSI full avg10   mem avail MiB   swap used MiB   jobs: resident MiB (sum)  passes")
logs = {}
for f in os.listdir(d):
    if f.startswith("job-") and f.endswith(".log"):
        logs[f[:-4]] = [l.split() for l in open(f"{d}/{f}") if len(l.split()) == 2 and l.split()[0].isdigit()]
step = max(1, len(S) // 14)
for s in S[::step] + [S[-1]]:
    t = s["t"]
    done = sum(max([int(p) for p, ts in L if float(ts) <= t] or [0]) for L in logs.values())
    m = s["mem"]
    print(f"{t - t0:5.0f}   {s['psi']['full']['avg10']:12.1f}   {m['avail'] / 2**20:12.0f}"
          f"   {(m['swap_total'] - m['swap_free']) / 2**20:12.0f}"
          f"   {sum(v['cur'] for v in s['jobs'].values()) / 2**20:20.0f}  {done:7d}")
