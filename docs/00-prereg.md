# STASIS pre-registration

Fill this in at the end of Phase 1 (Gate 1), then commit it and write the
commit hash below. **From that commit on, no threshold changes.** Anything
changed later is reported as a deviation, with the reason.

- Frozen at commit: `________` (fill in after `git commit`)
- Date: `____-__-__`

## Workload

| Parameter | Value | How it was chosen |
|---|---|---|
| SIZE (MB per job) | `700` | 4 jobs (2.8 GB) fit in 4 GB RAM; 8 don't |
| PASSES | `__` | `scripts/calibrate.sh 700` → one job alone runs ~60 s |
| N range | 1–8 | |
| Timeout per run | 1200 s | |
| Runs per cell | 5 | |

## Controller parameters

| Parameter | Value |
|---|---|
| PSI trigger | `full 100000 1000000` (100 ms full stall per 1 s window) |
| CALM / COOLDOWN / QUANTUM / RESERVE | 5 s / 2 s / 15 s / 10 % of MemTotal |
| PFF_HI (faults per CPU-ms) | `____` from `eval/analyze.py calibrate` |
| PFF streak / grace | 3 consecutive 100 ms samples / 2 s after a job starts or is thawed |

## Deviations from the build guide (decided before any Phase 5 run)

1. `wss` timestamps use `CLOCK_REALTIME` so that job logs, samples and the
   decision log share one clock (the fairness metric and time-to-first-park
   both compare them).
2. The PFF grace period also applies for 2 s after a thaw, not only after a
   job starts. A thawed job has to fault its working set back in, and that
   shouldn't count as thrashing.
3. Pressure onset for Jain's index is the earlier of the first PSI
   `full avg10 > 0` sample and the controller's first pressure event, so that
   every configuration uses the same definition.

## Metrics

See the docstring of `eval/analyze.py`: throughput, kills, Jain's J, probe
p99, time-to-first-park, and swap traffic.

## Hypotheses

Decision rule (strict): a hypothesis passes only if **every** STASIS run beats
**every** baseline run on that metric at that N. With 5 runs a side, luck can't
carry it. `kills` uses an exact match instead.

These are a proposed set: edit them before you freeze this file.

```json
{
  "hypotheses": [
    {"id": "H1",  "claim": "Full STASIS has higher throughput than every baseline under overload",
     "metric": "throughput", "better": "higher", "stasis": "stasis", "N": [6, 8],
     "baselines": ["eevdf", "oomd", "simple", "thrashprotect"]},
    {"id": "H1c", "claim": "Controller-only beats EEVDF on throughput",
     "metric": "throughput", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["eevdf"]},
    {"id": "H2",  "claim": "Full STASIS kills nothing",
     "metric": "kills", "equals": 0, "stasis": "stasis", "N": [4, 6, 8]},
    {"id": "H2c", "claim": "Controller-only kills nothing",
     "metric": "kills", "equals": 0, "stasis": "ctrl", "N": [4, 6, 8]},
    {"id": "H3",  "claim": "Full STASIS is fairer (Jain) than thrash-protect and oomd",
     "metric": "jain", "better": "higher", "stasis": "stasis", "N": [8],
     "baselines": ["thrashprotect", "oomd"]},
    {"id": "H3c", "claim": "Controller-only is fairer than thrash-protect",
     "metric": "jain", "better": "higher", "stasis": "ctrl", "N": [8],
     "baselines": ["thrashprotect"]},
    {"id": "H4",  "claim": "The protected probe's p99 latency is lower under full STASIS",
     "metric": "p99_us", "better": "lower", "stasis": "stasis", "N": [8],
     "baselines": ["eevdf", "ctrl", "oomd", "thrashprotect"]},
    {"id": "H5",  "claim": "Scheduler PFF parks jobs earlier than the controller alone",
     "metric": "ttfp_s", "better": "lower", "stasis": "stasis", "N": [6, 8],
     "baselines": ["ctrl"]}
  ]
}
```

Expected losses, reported honestly: at N ≤ 4 everything fits, so the
controller can only add overhead.
