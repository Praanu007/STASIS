# python3 race.py RUN_DIR... : after the first working-set jump, which signal
# fires first, and how long until the controller parks something.
import json, sys, glob, os
for d in sys.argv[1:]:
    t0 = float(open(f"{d}/t0").read())
    cfg = open(f"{d}/config").read().split()[0]
    jumps = sorted(float(l.split()[1]) - t0 for f in glob.glob(f"{d}/job-*.log") for l in open(f) if l.startswith("jump"))
    ends = {os.path.basename(f)[:-4]: (open(f).read().split("\n")[-2:-1] or [""])[0] for f in glob.glob(f"{d}/job-*.log")}
    dec = [json.loads(l) for l in open(f"{d}/decisions.jsonl") if l.strip()]
    samp = [json.loads(l) for l in open(f"{d}/samples.jsonl") if l.strip()]
    status = open(f"{d}/status").read().strip()
    print(f"== {cfg} {d}: {status}; jumps at {[round(j, 1) for j in jumps]} s")
    if not jumps:
        continue
    j0 = jumps[0]
    rel = lambda e: round(e["t"] - t0, 1)
    after = [e for e in dec if e["t"] - t0 >= j0 - 1]
    for e in after[:400]:
        k = e.get("event") or e.get("why")
        if k in ("ws", "reclaim"):
            continue
        if k == "pressure":
            print(f"  {rel(e):7.1f} pressure {e['src']:4s} active={e['active']} warming={e['warming']} hold={e['hold']} fits={e['fits']} growth={e['growth']}")
        elif k == "pff_hi":
            print(f"  {rel(e):7.1f} pff_hi {e['job']} pff={e['pff']} growth={e['growth']}")
        elif "frozen" in e:
            print(f"  {rel(e):7.1f} {'PARK' if e['frozen'] else 'thaw'} {e['job']} ({e['why']})")
        else:
            print(f"  {rel(e):7.1f} {k}")
    # machine-wide signals around the jumps, one line per 5 s
    prev = None
    for a, s in zip(samp, samp[1:]):
        t = s["t"] - t0
        if j0 - 10 <= t <= j0 + 120 and (prev is None or t - prev >= 5):
            prev = t
            dt = s["t"] - a["t"]
            fz = sum(1 for v in s["jobs"].values() if v.get("frozen"))
            print(f"  [{t:6.1f}] psi full avg10={s['psi']['full']['avg10']:5.1f} some avg10={s['psi']['some']['avg10']:5.1f}"
                  f" majflt/s={(s['pgmajfault'] - a['pgmajfault']) / dt:7.0f} swpin/s={(s['pswpin'] - a['pswpin']) / dt:6.0f}"
                  f" avail={s['mem']['avail'] >> 20} MiB parked={fz}")
