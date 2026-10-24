#!/usr/bin/env python3
"""sample.py OUT - one JSON line per second: PSI, memory, swap, scheduler and
per-job state. Locks itself into RAM so its samples keep arriving during
thrashing.

Also importable: record() returns one sample. The dashboard server builds its
live view from the same function, so live and replayed runs look identical.

Per job (stasis.slice/stasis-job-N.service) it records resident memory, peak
memory, CPU time, anonymous refaults, major faults and the freezer state.
Major faults per CPU-ms over time is the job's page-fault frequency, which
Phase 1 uses to calibrate PFF_HI.
"""
import ctypes, json, os, sys, time

CG = os.environ.get("STASIS_CG", "/sys/fs/cgroup/stasis.slice")
JOB_PREFIX = os.environ.get("STASIS_JOB_PREFIX", "stasis-job-")   # must match stasisd
RUN = os.environ.get("STASIS_RUN", "/run/stasis")
SCX = "/sys/kernel/sched_ext"
ctypes.CDLL("libc.so.6").mlockall(3)          # MCL_CURRENT | MCL_FUTURE

def kv(path):
    out = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2:
                out[parts[0]] = int(parts[1])
    return out

def psi():
    out = {}
    with open("/proc/pressure/memory") as f:
        for line in f:
            kind, *fields = line.split()
            out[kind] = {k: float(v) for k, v in (x.split("=") for x in fields)}
    return out

def read(path):
    with open(path) as f:
        return f.read().strip()

def meminfo():
    want = {"MemTotal": "total", "MemAvailable": "avail",
            "SwapTotal": "swap_total", "SwapFree": "swap_free"}
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            if key in want:
                out[want[key]] = int(rest.split()[0]) * 1024
    return out

def sched():
    """sched_ext state, and scx_stasis's dispatch counters when it's loaded."""
    try:
        state = read(f"{SCX}/state")
    except FileNotFoundError:
        return {"state": "absent", "ops": ""}
    ops = ""
    if state == "enabled":
        try:
            ops = read(f"{SCX}/root/ops")
        except FileNotFoundError:
            pass
    out = {"state": state, "ops": ops}
    if ops == "stasis":
        try:
            out.update(kv(f"{RUN}/stats"))       # local / shared / prio
        except FileNotFoundError:
            pass
    return out

def job(d):
    ms = kv(f"{d}/memory.stat")
    out = {"cur": int(read(f"{d}/memory.current")),
           "peak": int(read(f"{d}/memory.peak")),
           "cpu_us": kv(f"{d}/cpu.stat")["usage_usec"],
           "refault_anon": ms.get("workingset_refault_anon", 0),
           "pgmajfault": ms.get("pgmajfault", 0),
           "frozen": read(f"{d}/cgroup.freeze") == "1"}
    # Cumulative frozen time, the natural currency for a fairness metric over
    # the thing STASIS actually rations (added 2026-10-24). cgroup.stat.local
    # is recent; absent on older kernels, and analyze.py falls back to counting
    # frozen samples when it is missing.
    try:
        out["frozen_usec"] = kv(f"{d}/cgroup.stat.local").get("frozen_usec", 0)
    except (FileNotFoundError, KeyError, ValueError):
        pass
    # thrash-protect parks with SIGSTOP, which cgroup.freeze never sees. Without
    # this, fairness over parked time could not be measured for the one baseline
    # it matters most against. A job counts as stopped when any of its tasks is
    # in state T (stopped) or t (traced-stop).
    out["stopped"] = stopped(d)
    return out

def stopped(d):
    try:
        pids = read(f"{d}/cgroup.procs").split()
    except FileNotFoundError:
        return False
    for p in pids:
        try:
            # The state is the field after the parenthesised comm, which may
            # itself contain spaces or ')', so split on the last ')'.
            st = read(f"/proc/{p}/stat").rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, IndexError, ProcessLookupError):
            continue
        if st in ("T", "t"):
            return True
    return False

def record():
    vm = kv("/proc/vmstat")
    rec = {"t": time.time(), "psi": psi(), "pswpin": vm["pswpin"],
           "pswpout": vm["pswpout"], "pgmajfault": vm["pgmajfault"],
           "oom_kill": vm.get("oom_kill", 0), "mem": meminfo(),
           "sched": sched(), "jobs": {}}
    names = os.listdir(CG) if os.path.isdir(CG) else []
    for j in sorted(n for n in names if n.startswith(JOB_PREFIX)):
        try:
            rec["jobs"][j] = job(f"{CG}/{j}")
        except (FileNotFoundError, KeyError, ValueError):
            pass                                  # job exited mid-read
    return rec

def main():
    with open(sys.argv[1], "a", buffering=1) as out:
        while True:
            out.write(json.dumps(record()) + "\n")
            time.sleep(1)

if __name__ == "__main__":
    main()
