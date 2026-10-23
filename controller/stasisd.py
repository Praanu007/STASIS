#!/usr/bin/env python3
"""stasisd: working-set load control for jobs in stasis.slice (run as root).

Control law (build guide, Phase 2, with the fixes in docs/00-prereg.md):
  pressure  -> halve the active set, parking the highest fault rate first
               (at most once per COOLDOWN), unless it is warm-up (below)
  calm      -> after CALM s with no thrashing, admit the least-served parked
               job if the working sets of the active jobs plus its own fit
  rotation  -> while any job is parked, swap the most-served active job that
               has had QUANTUM s of user CPU since it was last thawed for the
               least-served parked one (at most once per QUANTUM s)
  exit      -> SIGTERM/SIGINT thaws every job

--signal=memstat  fault signal = workingset_refault_anon from memory.stat
                  (controller-only; the Phase 5 ablation baseline)
--signal=bpf      fault signal = page-fault frequency (faults per CPU-ms) from
                  scx_stasis's 100 ms export in /run/stasis/pff; PFF above
                  --pff-hi for 3 consecutive samples counts as pressure even
                  if PSI hasn't fired (Phase 4 early warning).

Working set. A job's working set is estimated as everything it has touched:
memory.current + memory.swap.current - swapcached (pages in the swap cache are
counted in both). For a job that touches all of its memory, like wss, this is
exactly its working set; in general it is an upper bound, so admission errs on
the safe side. memory.peak alone underestimates it for any job that started
under memory pressure.

Admission follows Denning: a parked job is admitted when the working sets of
the active jobs plus its own fit in the memory jobs can use. Parked jobs'
memory counts as reclaimable, because they are frozen. Working sets are only
known once they have settled, so admission waits until the active set's
resident memory has stopped growing, and a job parked while it was still
growing is assumed to be as large as the largest settled job.

Making room. Linux's reclaim doesn't strongly prefer a frozen job's pages to a
running job's, so without help the running jobs evict each other while parked
jobs keep their memory (seen on the rehearsal VM: a parked job held 500 MB for
30 s while a running one was squeezed). When the active set needs room,
stasisd writes to the parked jobs' memory.reclaim (cgroup v2) from a
background thread, so every page swapped out is one nobody is using. --no-reclaim
turns this off.

Warm-up is not thrashing. A job that was just thawed, or that survived a
halving, has to fault its pages back in from swap, which takes seconds.
Meanwhile its PFF is high and, if every running job is doing it, PSI's full
stall is high too. If the active set's working sets fit, that pressure is a
transition, not thrashing: halving more jobs can't make the survivors' pages
come back any faster. So a pressure event is warm-up, and halves nothing,
while the active set's working sets fit and any of these holds:
  - growing:     their resident memory grew by at least SET_GROW_FRAC of their
                 working sets per second over the last GROW_WINDOW seconds;
  - reloading:   at least RELOAD_FRAC of their working sets is swapped out,
                 so they are still faulting it back in;
  - making room: parked jobs are still being paged out to make that room.
Warm-up pressure doesn't restart the calm period either. Pressure while the
set fits, is resident, has room and isn't growing means an estimate is wrong,
and the set is halved anyway. A halving restarts the survivors' growth
windows: growth measured across a halving mixes two different sets. The PFF
early warning only counts a job whose resident memory grew by less than
GROW_FRAC of its working set over the last GROW_WINDOW seconds.
Without these rules, halvings cascade (8 -> 4 -> 2 -> 1) while the survivors
are still faulting their pages back in. Growth alone wasn't enough on the
rehearsal VM's slow swap (about 3 MB/s in): a reloading job grew by only 0.3%
of its working set per second, and sets that fit were halved again.
Stdlib only.
"""
import argparse, collections, ctypes, errno, json, os, select, signal, sys, threading, time, traceback

CG = os.environ.get("STASIS_CG", "/sys/fs/cgroup/stasis.slice")
PFF_FILE = os.environ.get("STASIS_PFF", "/run/stasis/pff")
PSI_FILE = os.environ.get("STASIS_PSI", "/proc/pressure/memory")   # tests use a plain file
MEMINFO = os.environ.get("STASIS_MEMINFO", "/proc/meminfo")        # tests use a fake one
CALM, COOLDOWN, RESERVE = 5.0, 2.0, 0.10
PFF_STREAK = 3                       # consecutive 100 ms samples above PFF_HI
GROW_FRAC = 0.005                    # one job's resident growth per s, fraction of its working set
SET_GROW_FRAC = 0.001                # the active set's growth per s, fraction of its working sets
GROW_WINDOW = 5.0                    # s of history behind every growth figure (smooths noise)
RELOAD_FRAC = 0.02                   # swapped-out share of the active set's working sets that counts as reloading

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
    for line in rd(MEMINFO).splitlines():
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
def worked(j):  return field(f"{CG}/{j}/cpu.stat", "user_usec")   # excludes fault handling
def peak(j):    return int(rd(f"{CG}/{j}/memory.peak"))
def current(j): return int(rd(f"{CG}/{j}/memory.current"))

def ws(j):
    """Working-set estimate: resident plus swapped-out memory, swap cache
    counted once. Never less than memory.peak."""
    try:
        swap = int(rd(f"{CG}/{j}/memory.swap.current"))
    except FileNotFoundError:
        swap = 0
    cached = field(f"{CG}/{j}/memory.stat", "swapcached")
    return max(peak(j), current(j) + swap - cached)

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

_res = {}
def track(j, now):
    """Record memory.current; return its growth (bytes/s) over the last
    GROW_WINDOW seconds, or None while there is less than half a second of
    history. One second was too noisy on a slow swap device: a single flat
    reading looked like thrashing."""
    h = _res.setdefault(j, collections.deque())
    h.append((now, current(j)))
    while len(h) > 2 and now - h[1][0] >= GROW_WINDOW:
        h.popleft()                   # keep the oldest sample at least GROW_WINDOW old
    (t0, c0), (t1, c1) = h[0], h[-1]
    return (c1 - c0) / (t1 - t0) if t1 - t0 >= 0.5 else None

def growth(rates_b, js):
    """Combined resident growth of jobs js as a fraction of their working sets,
    per second. Infinite while any of them lacks history (just started or thawed)."""
    if any(rates_b.get(j) is None for j in js):
        return float("inf")
    return sum(rates_b[j] for j in js) / max(1, sum(ws(j) for j in js))

def finite(x, nd=4):
    """For the log: JSON has no Infinity."""
    return round(x, nd) if x not in (float("inf"), float("-inf")) and x == x else None

_prev = {}
def fault_rate_bpf(j, snap):
    pids = [int(p) for p in rd(f"{CG}/{j}/cgroup.procs").split()]
    flt = sum(snap.get(p, (0, 0))[0] for p in pids)
    ns = sum(snap.get(p, (0, 0))[1] for p in pids)
    f0, n0 = _prev.get(j, (flt, ns))
    _prev[j] = (flt, ns)
    return (flt - f0) / max(1e-3, (ns - n0) / 1e6)   # faults per CPU-ms

# ---- making room: page parked jobs out ahead of need -------------------------

class Reclaimer(threading.Thread):
    """Swaps parked jobs' memory out in the background, through memory.reclaim,
    in chunks so a job that is thawed meanwhile stops being reclaimed quickly.
    The control loop never blocks on it."""
    CHUNK = 32 << 20

    BACKOFF = 5.0                          # s before retrying a job the kernel refused

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.want = {}                     # job -> bytes still wanted out
        self.refused = {}                  # job -> monotonic time it may be retried
        self.wake = threading.Event()

    def set(self, want):
        """Replace the request: {job: bytes}. True if there is work it can do."""
        now = time.monotonic()
        with self.lock:
            self.want = {j: int(b) for j, b in want.items()
                         if b >= self.CHUNK // 4 and self.refused.get(j, 0) <= now}
            if self.want:
                self.wake.set()
            return bool(self.want)

    def run(self):
        while True:
            self.wake.wait()
            with self.lock:
                if not self.want:
                    self.wake.clear()
                    continue
                j, b = max(self.want.items(), key=lambda kv: kv[1])
            n = min(b, self.CHUNK)
            try:
                if rd(f"{CG}/{j}/cgroup.freeze").strip() != "1":
                    raise FileNotFoundError(j)          # thawed meanwhile: leave it
                with open(f"{CG}/{j}/memory.reclaim", "w") as f:
                    f.write(str(int(n)))           # "123.0" is EINVAL
                with self.lock:
                    if j in self.want:
                        self.want[j] -= n
                        if self.want[j] < self.CHUNK // 4:
                            del self.want[j]
            except OSError:                            # EAGAIN: nothing left; gone; thawed
                with self.lock:
                    self.want.pop(j, None)
                    self.refused[j] = time.monotonic() + self.BACKOFF

def missing(js):
    """Bytes of these jobs' working sets that are swapped out."""
    return sum(max(0, ws(j) - current(j)) for j in js)

def room_wanted(active, parked):
    """How much of each parked job to page out so the active jobs' working sets
    fit: the active jobs' missing pages minus what is free beyond the reserve,
    taken from the parked jobs holding the most memory first. Whole bytes:
    memory.reclaim rejects "123.0"."""
    spare = meminfo("MemAvailable") - int(RESERVE * meminfo("MemTotal"))
    room = missing(active) - max(0, spare)
    want = {}
    for j in sorted(parked, key=current, reverse=True):
        if room <= 0:
            break
        take = min(room, current(j))
        if take > 0:
            want[j] = take
            room -= take
    return want

# ---- control loop ----------------------------------------------------------

def fits(js, extra=0):
    """Do these jobs' working sets (plus `extra` bytes) fit in the memory jobs
    can use? Memory held by anything other than our jobs can't be freed; parked
    jobs' memory can (they are frozen, so their pages may be swapped out)."""
    total, avail = meminfo("MemTotal"), meminfo("MemAvailable")
    other = max(0, total - avail - sum(current(x) for x in jobs()))
    return sum(ws(x) for x in js) + extra <= total - other - RESERVE * total

def ws_estimate(j, settled):
    """A job parked while it was still growing hasn't shown its whole working
    set yet: assume it is as large as the largest settled job's."""
    if settled.get(j):
        return ws(j)
    known = [ws(x) for x in jobs() if settled.get(x)]
    return max([ws(j)] + known)

_thawed = False
def thaw_all(*_, code=0):
    global _thawed
    if _thawed:                 # signal handler and finally can both get here
        sys.exit(code)
    _thawed = True
    for j in jobs():
        try:
            freeze(j, False, "shutdown")
        except OSError:
            pass
    sys.exit(code)

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--signal", choices=["memstat", "bpf"], default="memstat")
    ap.add_argument("--log", default="/var/log/stasis/decisions.jsonl")
    ap.add_argument("--trigger-ms", type=int, default=100,
                    help="PSI full stall (ms) per window that counts as pressure")
    ap.add_argument("--window-ms", type=int, default=1000,
                    help="PSI trigger window. Below 2000 ms needs CAP_SYS_RESOURCE; "
                         "tests/test_controller.sh uses 2000 to run without root")
    ap.add_argument("--quantum", type=float, default=15.0,
                    help="rotation quantum: user CPU seconds a job runs after a thaw before it can be rotated out")
    ap.add_argument("--pff-hi", type=float, default=None,
                    help="faults per CPU-ms; frozen in docs/00-prereg.md (bpf mode)")
    ap.add_argument("--grace", type=float, default=2.0,
                    help="ignore a job's PFF for this long after it starts or is thawed")
    ap.add_argument("--no-reclaim", dest="reclaim", action="store_false",
                    help="don't page parked jobs out ahead of need")
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

    fd = os.open(PSI_FILE, os.O_RDWR | os.O_NONBLOCK)
    os.write(fd, f"full {a.trigger_ms * 1000} {a.window_ms * 1000}\0".encode())
    poller = select.poll()
    poller.register(fd, select.POLLPRI)
    rate_fn = fault_rate_bpf if bpf else fault_rate_memstat
    poll_ms = 100 if bpf else 250
    log(event="start", signal=a.signal, trigger_ms=a.trigger_ms, window_ms=a.window_ms,
        quantum=a.quantum, pff_hi=a.pff_hi, grace=a.grace, grow_frac=GROW_FRAC,
        set_grow_frac=SET_GROW_FRAC, grow_window=GROW_WINDOW, reload_frac=RELOAD_FRAC,
        reserve=RESERVE, reclaim=a.reclaim)
    reclaimer = Reclaimer()
    if a.reclaim:
        reclaimer.start()

    last_pressure = last_halve = last_rotate = time.monotonic()
    since = {}             # job -> monotonic time it started or was last thawed
    cpu0 = {}              # job -> its user CPU time (us) when it started or was last thawed
    settled = {}           # job -> its resident memory had stopped growing (last seen)
    reclaim_logged = {}    # job -> when its paging-out was last logged
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
                    cpu0.setdefault(j, worked(j))

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

                making_room = False
                if a.reclaim:
                    want = room_wanted(active, parked)
                    making_room = reclaimer.set(want)
                    for j, b in want.items():     # log each job at most every 10 s
                        if now - reclaim_logged.get(j, -1e9) >= 10:
                            reclaim_logged[j] = now
                            log(event="reclaim", job=j, bytes=int(b))
                grow_b = {j: track(j, now) for j in active}   # every loop: continuous history
                for j in active:                  # parked jobs keep their last value
                    settled[j] = growth(grow_b, [j]) < GROW_FRAC
                src = "psi" if events else None
                if bpf and fresh and a.pff_hi is not None and not src:
                    for j in active:
                        g = growth(grow_b, [j])
                        thrashing = (now - since[j] >= a.grace
                                     and rates.get(j, 0) > a.pff_hi and g < GROW_FRAC)
                        hot[j] = hot.get(j, 0) + 1 if thrashing else 0
                        if hot[j] >= PFF_STREAK:
                            src = "pff"
                            log(event="pff_hi", job=j, pff=round(rates[j], 3), growth=finite(g))
                            break

                if src:
                    fit = fits(active)
                    g = growth(grow_b, active)
                    short = missing(active)
                    hold = None                   # why this pressure is warm-up, if it is
                    if fit and active:
                        if g >= SET_GROW_FRAC:
                            hold = "growing"
                        elif short >= RELOAD_FRAC * sum(ws(j) for j in active):
                            hold = "reloading"
                        elif making_room:
                            hold = "making-room"
                    warming = hold is not None
                    log(event="pressure", src=src, active=len(active), parked=len(parked),
                        warming=warming, hold=hold, fits=fit, growth=finite(g), missing=short)
                    if not warming:
                        last_pressure = now
                        if now - last_halve >= COOLDOWN and len(active) > 1:
                            keep = max(1, len(active) // 2)
                            worst = sorted(active, key=lambda j: rates.get(j, 0), reverse=True)
                            for j in worst[:len(active) - keep]:
                                freeze(j, True, f"pressure-{src}")
                            for j in worst[len(active) - keep:]:
                                _res.pop(j, None)  # the survivors' growth starts over
                            last_halve = now
                            last_rotate = now     # the rotation clock starts when jobs park
                            hot.clear()
                # Admission and rotation are independent: when the next parked
                # job doesn't fit, calm must fall through to rotation, or parked
                # jobs starve until the active ones finish.
                # Admit only once the active set has settled (its working sets
                # are known), and only if the candidate's working set fits too.
                elif (parked and now - last_pressure >= CALM
                      and growth(grow_b, active) < SET_GROW_FRAC
                      and fits(active, ws_estimate(min(parked, key=served), settled))):
                    j = min(parked, key=served)
                    freeze(j, False, "admit")
                    since[j] = now
                    cpu0[j] = worked(j)
                    _res.pop(j, None)
                    last_pressure = now           # wait a full calm period again
                # The quantum is counted in the job's own user CPU time since it
                # was last thawed. Faulting its pages back in is kernel time, so
                # a slow swap device can't eat the quantum before any work is done.
                elif parked and active and now - last_rotate >= a.quantum:
                    ready = [j for j in active if worked(j) - cpu0.get(j, 0) >= a.quantum * 1e6]
                    if ready:
                        act, par = max(ready, key=served), min(parked, key=served)
                        if served(par) < served(act):
                            freeze(act, True, "rotate-out")
                            freeze(par, False, "rotate-in")
                            since[par] = now
                            cpu0[par] = worked(par)
                            hot.pop(par, None)
                            _res.pop(par, None)
                        last_rotate = now
            except (FileNotFoundError, ValueError):
                continue                          # a job exited mid-scan
            except OSError as e:
                if e.errno != errno.ENODEV:       # its cgroup was being removed
                    raise
    except Exception as e:                        # say why before thawing and exiting
        traceback.print_exc()
        log(event="crash", error=repr(e))
        thaw_all(code=1)
    finally:
        thaw_all()

if __name__ == "__main__":
    main()
