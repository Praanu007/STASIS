#!/usr/bin/env python3
"""stasisd: working-set load control for jobs in stasis.slice (run as root).

Control law (build guide, Phase 2):
  pressure  -> halve the active set, parking the highest fault rate first
               (at most once per COOLDOWN)
  calm      -> after CALM s with no pressure, admit the least-served parked
               job if its working-set estimate fits in free RAM
  rotation  -> every QUANTUM s while any job is parked, swap the most-served
               active job for the least-served parked one
  exit      -> SIGTERM/SIGINT thaws every job

--signal=memstat  fault signal = workingset_refault_anon from memory.stat
                  (controller-only; the Phase 5 ablation baseline)
--signal=bpf      fault signal = page-fault frequency (faults per CPU-ms) from
                  scx_stasis's 100 ms export in /run/stasis/pff; PFF above
                  --pff-hi for 3 consecutive samples counts as pressure even
                  if PSI hasn't fired (Phase 4 early warning).
Stdlib only.
"""
import argparse, ctypes, json, os, select, signal, sys, time

CG = os.environ.get("STASIS_CG", "/sys/fs/cgroup/stasis.slice")
PFF_FILE = os.environ.get("STASIS_PFF", "/run/stasis/pff")
CALM, COOLDOWN, RESERVE = 5.0, 2.0, 0.10
PFF_STREAK = 3                       # consecutive 100 ms samples above PFF_HI

LOG = None

def rd(path):
    with open(path) as f:
        return f.read()

def field(path, key):
    for line in rd(path).splitlines():
        k, _, v = line.partition(" ")
        if k == key:
            return int(v)
    return 0

def meminfo(key):
    for line in rd("/proc/meminfo").splitlines():
        if line.startswith(key + ":"):
            return int(line.split()[1]) * 1024
    raise KeyError(key)

def jobs():
    try:
        return sorted(d for d in os.listdir(CG) if d.startswith("stasis-job-"))
    except FileNotFoundError:
        return []

def frozen(j):  return rd(f"{CG}/{j}/cgroup.freeze").strip() == "1"
def served(j):  return field(f"{CG}/{j}/cpu.stat", "usage_usec")
def peak(j):    return int(rd(f"{CG}/{j}/memory.peak"))
def current(j): return int(rd(f"{CG}/{j}/memory.current"))

def log(**rec):
    LOG.write(json.dumps({"t": time.time(), **rec}) + "\n")

def freeze(j, on, why):
    with open(f"{CG}/{j}/cgroup.freeze", "w") as f:
        f.write("1" if on else "0")
    log(job=j, frozen=on, why=why)

# ---- fault signals ---------------------------------------------------------

_last = {}
def fault_rate_memstat(j, _snap=None):
    """Anonymous refaults since the previous call (controller-only signal)."""
    now = field(f"{CG}/{j}/memory.stat", "workingset_refault_anon")
    rate, _last[j] = now - _last.get(j, now), now
    return rate

def pff_snapshot():
    """tgid -> (maj_flt, run_ns) from the scheduler's 100 ms export."""
    out = {}
    try:
        with open(PFF_FILE) as f:
            for line in f:
                tgid, flt, ns = map(int, line.split())
                out[tgid] = (flt, ns)
    except FileNotFoundError:
        pass
    return out

_prev = {}
def fault_rate_bpf(j, snap):
    pids = [int(p) for p in rd(f"{CG}/{j}/cgroup.procs").split()]
    flt = sum(snap.get(p, (0, 0))[0] for p in pids)
    ns = sum(snap.get(p, (0, 0))[1] for p in pids)
    f0, n0 = _prev.get(j, (flt, ns))
    _prev[j] = (flt, ns)
    return (flt - f0) / max(1e-3, (ns - n0) / 1e6)   # faults per CPU-ms

# ---- control loop ----------------------------------------------------------

def fits(j):
    need = max(0, peak(j) - current(j))
    spare = meminfo("MemAvailable") - RESERVE * meminfo("MemTotal")
    return need <= spare

_thawed = False
def thaw_all(*_):
    global _thawed
    if _thawed:                 # signal handler and finally can both get here
        sys.exit(0)
    _thawed = True
    for j in jobs():
        try:
            freeze(j, False, "shutdown")
        except OSError:
            pass
    sys.exit(0)

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--signal", choices=["memstat", "bpf"], default="memstat")
    ap.add_argument("--log", default="/var/log/stasis/decisions.jsonl")
    ap.add_argument("--trigger-ms", type=int, default=100,
                    help="PSI full stall (ms) per 1 s window that counts as pressure")
    ap.add_argument("--quantum", type=float, default=15.0, help="rotation quantum (s)")
    ap.add_argument("--pff-hi", type=float, default=None,
                    help="faults per CPU-ms; frozen in docs/00-prereg.md (bpf mode)")
    ap.add_argument("--grace", type=float, default=2.0,
                    help="ignore a job's PFF for this long after it starts or is thawed")
    return ap.parse_args()

def main():
    global LOG
    a = parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
    LOG = open(a.log, "a", buffering=1)
    bpf = a.signal == "bpf"
    if bpf and a.pff_hi is None:
        print("warning: --signal=bpf without --pff-hi; PFF early warning disabled",
              file=sys.stderr)

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.mlockall(3) != 0:                 # never let the controller swap
        sys.exit(f"mlockall failed, errno {ctypes.get_errno()}")
    signal.signal(signal.SIGTERM, thaw_all)
    signal.signal(signal.SIGINT, thaw_all)

    fd = os.open("/proc/pressure/memory", os.O_RDWR | os.O_NONBLOCK)
    os.write(fd, f"full {a.trigger_ms * 1000} 1000000\0".encode())
    poller = select.poll()
    poller.register(fd, select.POLLPRI)
    rate_fn = fault_rate_bpf if bpf else fault_rate_memstat
    poll_ms = 100 if bpf else 250
    log(event="start", signal=a.signal, trigger_ms=a.trigger_ms,
        quantum=a.quantum, pff_hi=a.pff_hi, grace=a.grace)

    last_pressure = last_halve = last_rotate = time.monotonic()
    since = {}             # job -> monotonic time it started or was last thawed
    hot = {}               # job -> consecutive PFF samples above PFF_HI
    rates = {}
    snap_mtime = None
    try:
        while True:
            events = poller.poll(poll_ms)
            now = time.monotonic()
            try:
                js = jobs()
                active = [j for j in js if not frozen(j)]
                parked = [j for j in js if j not in active]
                for j in active:
                    since.setdefault(j, now)

                # Fault rates. In bpf mode only take a new sample when the
                # scheduler has exported a fresh snapshot.
                fresh = True
                snap = None
                if bpf:
                    try:
                        m = os.stat(PFF_FILE).st_mtime_ns
                    except FileNotFoundError:
                        m = None
                    fresh = m is not None and m != snap_mtime
                    snap_mtime = m
                    if fresh:
                        snap = pff_snapshot()
                if fresh:
                    rates = {j: rate_fn(j, snap) for j in active}

                src = "psi" if events else None
                if bpf and fresh and a.pff_hi is not None and not src:
                    for j in active:
                        warm = now - since[j] >= a.grace
                        hot[j] = hot.get(j, 0) + 1 if warm and rates.get(j, 0) > a.pff_hi else 0
                        if hot[j] >= PFF_STREAK:
                            src = "pff"
                            log(event="pff_hi", job=j, pff=rates[j])

                if src:
                    last_pressure = now
                    log(event="pressure", src=src, active=len(active), parked=len(parked))
                    if now - last_halve >= COOLDOWN and len(active) > 1:
                        keep = max(1, len(active) // 2)
                        worst = sorted(active, key=lambda j: rates.get(j, 0), reverse=True)
                        for j in worst[:len(active) - keep]:
                            freeze(j, True, f"pressure-{src}")
                        last_halve = now
                        hot.clear()
                elif parked and now - last_pressure >= CALM:
                    j = min(parked, key=served)
                    if fits(j):
                        freeze(j, False, "admit")
                        since[j] = now
                        last_pressure = now       # wait a full calm period again
                elif parked and active and now - last_rotate >= a.quantum:
                    act, par = max(active, key=served), min(parked, key=served)
                    if served(par) < served(act):
                        freeze(act, True, "rotate-out")
                        freeze(par, False, "rotate-in")
                        since[par] = now
                        hot.pop(par, None)
                    last_rotate = now
            except FileNotFoundError:
                continue                          # a job exited mid-scan
    finally:
        thaw_all()

if __name__ == "__main__":
    main()
