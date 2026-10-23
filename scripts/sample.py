#!/usr/bin/env python3
"""sample.py OUT - one JSON line per second: PSI, swap, per-job state.

Per job it also records major faults and CPU ticks summed over the job's
processes (from /proc/PID/stat), which Phase 1 uses to calibrate PFF_HI.
"""
import ctypes, json, os, sys, time

CG = os.environ.get("STASIS_CG", "/sys/fs/cgroup/stasis.slice")
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

def proc_faults(cgdir):
    """(majflt, utime+stime ticks) summed over the cgroup's processes."""
    flt = ticks = 0
    for pid in read(f"{cgdir}/cgroup.procs").split():
        try:
            stat = read(f"/proc/{pid}/stat")
        except (FileNotFoundError, ProcessLookupError):
            continue
        f = stat[stat.rindex(")") + 2:].split()   # fields from 3 onwards
        flt += int(f[9])                          # field 12: majflt
        ticks += int(f[11]) + int(f[12])          # fields 14, 15
    return flt, ticks

def main():
    with open(sys.argv[1], "a", buffering=1) as out:
        while True:
            vm = kv("/proc/vmstat")
            rec = {"t": time.time(), "psi": psi(), "pswpin": vm["pswpin"],
                   "pswpout": vm["pswpout"], "pgmajfault": vm["pgmajfault"],
                   "jobs": {}}
            names = os.listdir(CG) if os.path.isdir(CG) else []
            for j in (n for n in names if n.startswith("stasis-job-")):
                d = f"{CG}/{j}"
                try:
                    flt, ticks = proc_faults(d)
                    rec["jobs"][j] = {
                        "cur": int(read(f"{d}/memory.current")),
                        "cpu_us": kv(f"{d}/cpu.stat")["usage_usec"],
                        "refault_anon": kv(f"{d}/memory.stat").get("workingset_refault_anon", 0),
                        "frozen": read(f"{d}/cgroup.freeze") == "1",
                        "majflt": flt, "ticks": ticks}
                except (FileNotFoundError, KeyError):
                    pass                              # job exited mid-read
            out.write(json.dumps(rec) + "\n")
            time.sleep(1)

if __name__ == "__main__":
    main()
