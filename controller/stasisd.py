#!/usr/bin/env python3
"""stasisd: working-set load control for jobs in stasis.slice (run as root).

STASIS is a medium-term scheduler. It decides how many of the jobs it manages
run at once, parks the others with the cgroup v2 freezer, and rotates them
fairly, so memory overcommit costs waiting instead of thrashing or killed
processes. docs/architecture.md describes the four layers; this file is
layers 1-3 (scx_stasis is layer 4).

Control law (build guide, Phase 2, with the changes in docs/00-prereg.md):
  pressure  -> unless it is warm-up (below), halve the active set, at most
               once per COOLDOWN, parking the jobs that have had the most of
               their current turn first
  calm      -> after CALM s with no thrashing, admit the longest-waiting
               parked job if the working sets of the active jobs plus its own
               fit
  rotation  -> while any job is parked, once an active job has had QUANTUM s
               of user CPU in its turn, swap it for the longest-waiting parked
               job (at most once per QUANTUM s, one swap at a time)
  bound     -> a job parked for MAX_WAIT s is swapped in at the first step
               where no swap is still reloading, even if no quantum is up
               (the starvation bound; --max-wait)
  exit      -> SIGTERM/SIGINT, or any crash, thaws every job

--signal=memstat  fault signal = workingset_refault_anon from memory.stat
                  (controller-only; the Phase 5 ablation baseline)
--signal=bpf      fault signal = page-fault frequency (faults per CPU-ms) from
                  scx_stasis's 100 ms export in /run/stasis/pff; PFF above
                  --pff-hi for 3 consecutive samples counts as pressure even
                  if PSI hasn't fired (Phase 4 early warning).

Working sets (--ws). Denning's working set W(t, tau) is the memory a job
referenced during the last tau of its own execution time. With --ws=refs (the
default) stasisd measures it: it clears the job's referenced bits through
/proc/PID/clear_refs, lets the job run tau = WS_TAU_CPU s of user CPU, and reads
how much memory is referenced again from /proc/PID/smaps_rollup. A sample is
trusted only if the job ran the whole window unfrozen, didn't stall on memory
(its cgroup's memory.pressure) and wasn't still allocating: a thrashing job
references only what it manages to keep resident, which understates its
demand. Clearing referenced bits makes the job's pages look idle to reclaim,
so a window opens only when the job has had none for WS_PERIOD s and isn't
stalling on memory (its PSI 'some' avg10 is under 2%, and it hasn't stalled
since the sampler last looked, 0.5 s before): under pressure the window would
be discarded anyway, and the clearing would push reclaim onto the job's hot
pages. A job's estimate is WS_MARGIN times its largest recent
trusted sample,
never more than its footprint (resident plus swapped-out memory: everything it
has touched). Until a job has a trusted sample, and always with
--ws=footprint, the footprint is used: an upper bound, so the controller errs
on the side of parking. docs/architecture.md discusses the sampler's cost.
(DAMON would sample the same thing more cheaply, but Ubuntu 26.04's kernel is
built without it.)

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
  - reloading:   at least RELOAD_FRAC of their working sets is not resident,
                 so they are still faulting it back in;
  - making room: parked jobs are still being paged out to make that room.
Warm-up pressure doesn't restart the calm period either. Pressure while the
set fits, is resident, has room and isn't growing means an estimate is wrong:
the set is halved anyway, and the active jobs' measured working sets are
forgotten until they are measured again. A halving restarts the survivors'
growth windows: growth measured across a halving mixes two different sets.
The PFF early warning only counts a job whose resident memory grew by less
than GROW_FRAC of its working set per second over the last GROW_WINDOW
seconds. Without these rules, halvings cascade (8 -> 4 -> 2 -> 1) while the
survivors are still faulting their pages back in.

Fairness. Every job takes turns. A turn starts when the job is thawed; it ends
when the job is rotated out after QUANTUM s of user CPU (faulting pages back
in is kernel time, so a slow swap device can't use up the quantum), or when a
halving parks it. Halvings and rotations park the jobs that have had the most
of their turn, and the job resumed is always the one that has waited longest.
One swap at a time: a swap (a rotation, or the bound below) trades a resident
job for one that must fault its working set back in, and swaps that overlap
compete for the swap device. On a slow one they take longer than the turns
they buy, until every active job is reloading and none computes (the
simulator livelocks this way at 3 MB/s). So a swap waits until the job resumed
before it has reloaded: until it has had MIN_TURN = min(10 s, MAX_WAIT / 4,
QUANTUM) not stalled on memory (its cgroup's PSI), or MAX_WAIT s have passed.
The starvation bound covers the case where no turn ends: a job that has waited
MAX_WAIT s is swapped in at the first such moment, for the active job that has
had the most of its turn.
Each swap waits at most MAX_WAIT s for the one before it, so with P jobs
parked none waits longer than about P x MAX_WAIT even if no turn ever ends;
with fast reloads it is MAX_WAIT plus a few seconds per job ahead of it.
--no-rotation turns rotation and the bound off (the fairness ablation):
parked jobs then wait until memory frees up.

Options added 2026-10-24 (arms registered in docs/01-prereg-arms.md). Every
one is off by default, so with none given the control law above runs
unchanged. docs/06-added-features.md gives what each claims and the evidence
for and against it.

--derivative-hi R  (arm 4) D-term: pressure when PSI full avg10 rises at R
                   percentage points per second over --derivative-window s,
                   for 3 consecutive samples, even below the PSI trigger. A
                   lead term on the signal already being read, not a forecast.
--ws damon         (arm 5) a third working-set estimate beside refs and
                   footprint: DAMON's monitored access footprint, written to
                   /run/stasis/damon/<job> by scripts/damon_wss.sh, capped at the
                   footprint like the refs estimate. A job with no fresh reading
                   falls back to its footprint. Refuses to start without DAMON
                   sysfs: Ubuntu 26.04's kernel is built without DAMON, and
                   silently falling back for every job would turn the arm into a
                   footprint run that looks like a DAMON one.
--predict MODEL    (arm 6) logistic model over (PSI slope, mean PFF, missing
                   share, fit margin) from eval/train_predictor.py. Pressure when
                   p(thrash) exceeds its threshold for 3 consecutive samples.
                   Refuses a model that failed the trainer's sign check. The
                   nearest published work predicts fragmentation, not pressure,
                   and --derivative-hi supports the same claim more cheaply.
--actuator criu    (arm 7) checkpoint a parked job with CRIU and kill it,
                   restoring it on resume, instead of freezing it in place. The
                   evidence is against this (HFSP: suspend/resume traffic at disk
                   raw speed; CRIU's cost is the memory image). Any CRIU error
                   falls back to the freezer for that job. NOT YET RUNNABLE: jobs
                   are systemd transient units, and killing a job's processes
                   ends its unit and removes its cgroup, so run_mix.sh would count
                   the job as finished. The harness needs RemainAfterExit and
                   CRIU's --manage-cgroups validated on the lab VM first; see
                   docs/06-added-features.md.

Safety. stasisd only ever freezes cgroups named JOB_PREFIX* (stasis-job-* unless
STASIS_JOB_PREFIX says otherwise, for Kubernetes) directly under STASIS_CG,
refuses any that contain itself, never sends a signal, and thaws everything on
exit. Frozen tasks aren't runnable, so parking can't trip sched_ext's watchdog,
which aborts a BPF scheduler that leaves a runnable task waiting longer than
its timeout (30 s at most). STASIS never kills; the kernel's OOM killer still
can, and the evaluation counts it. The one exception is --actuator=criu, which
sends SIGKILL to a job it has checkpointed: that arm alone gives up both
"never sends a signal" and "never kills", which is part of what it measures.
Stdlib only.
"""
import argparse, collections, ctypes, errno, json, math, os, select, shutil, signal, subprocess, sys, threading, time, traceback

CG = os.environ.get("STASIS_CG", "/sys/fs/cgroup/stasis.slice")
PFF_FILE = os.environ.get("STASIS_PFF", "/run/stasis/pff")
PSI_FILE = os.environ.get("STASIS_PSI", "/proc/pressure/memory")   # tests use a plain file
MEMINFO = os.environ.get("STASIS_MEMINFO", "/proc/meminfo")        # tests use a fake one
PROC = os.environ.get("STASIS_PROC", "/proc")                      # tests use a fake one
JOB_PREFIX = os.environ.get("STASIS_JOB_PREFIX", "stasis-job-")    # arm 10: Kubernetes pod cgroups
DAMON_DIR = os.environ.get("STASIS_DAMON", "/run/stasis/damon")    # arm 5
DAMON_SYSFS = "/sys/kernel/mm/damon"                               # arm 5: absent without CONFIG_DAMON
CRIU_DIR = os.environ.get("STASIS_CRIU", "/var/lib/stasis/criu")   # arm 7
CALM, COOLDOWN, RESERVE = 5.0, 2.0, 0.10
PFF_STREAK = 3                       # consecutive 100 ms samples above PFF_HI
DERIV_WINDOW = 3.0                   # s of PSI history behind the D-term (arm 4)
DAMON_STALE = 5.0                    # s after which a DAMON reading is ignored (arm 5)
GROW_FRAC = 0.005                    # one job's resident growth per s, fraction of its working set
SET_GROW_FRAC = 0.001                # the active set's growth per s, fraction of its working sets
GROW_WINDOW = 5.0                    # s of history behind every growth figure (smooths noise)
RELOAD_FRAC = 0.02                   # non-resident share of the active set's working sets that counts as reloading
WS_TAU_CPU = 1.0                     # working-set window: s of the job's own user CPU
WS_MAX_WALL = 10.0                   # a window that takes longer than this is discarded
WS_STALL = 0.02                      # trusted only if stalled on memory < 2% of the window
WS_MARGIN = 1.10                     # headroom on a measured working set
WS_KEEP = 10                         # trusted samples kept per job
WS_PERIOD = 10.0                     # s from one job's window to its next (each clears its referenced bits)
RESCUE_SPACING = 5.0                 # s between starvation-bound rescues

LOG = None
LOG_LOCK = threading.Lock()

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
        return sorted(d for d in os.listdir(CG) if d.startswith(JOB_PREFIX))
    except FileNotFoundError:
        return []

def frozen(j):
    # A CRIU-parked job (arm 7) has no tasks left to freeze, so the freezer
    # cannot answer for it; stasisd's own record is the only truth.
    if j in _criu_parked:
        return True
    return rd(f"{CG}/{j}/cgroup.freeze").strip() == "1"
def served(j):  return field(f"{CG}/{j}/cpu.stat", "usage_usec")
def worked(j):  return field(f"{CG}/{j}/cpu.stat", "user_usec")   # excludes fault handling
def peak(j):    return int(rd(f"{CG}/{j}/memory.peak"))
def current(j): return int(rd(f"{CG}/{j}/memory.current"))
def procs(j):   return [int(p) for p in rd(f"{CG}/{j}/cgroup.procs").split()]

def footprint(j, cur=None):
    """Everything the job has touched: resident plus swapped-out memory, swap
    cache counted once. Never less than memory.peak. An upper bound on its
    working set."""
    try:
        swap = int(rd(f"{CG}/{j}/memory.swap.current"))
    except FileNotFoundError:
        swap = 0
    cached = field(f"{CG}/{j}/memory.stat", "swapcached")
    return max(peak(j), (current(j) if cur is None else cur) + swap - cached)

def stall_some(j):
    """The job's memory PSI 'some' line: (avg10 in %, total stall in us)."""
    for line in rd(f"{CG}/{j}/memory.pressure").splitlines():
        if line.startswith("some "):
            f = dict(kv.split("=", 1) for kv in line.split()[1:])
            return float(f["avg10"]), int(f["total"])
    raise ValueError(f"no PSI line in {j}/memory.pressure")

def stall_us(j):
    """Microseconds the job's cgroup has spent stalled on memory (PSI 'some')."""
    return stall_some(j)[1]

def stalled_s(j):
    """Seconds the job has spent stalled on memory, 0 where PSI can't say."""
    try:
        return stall_us(j) / 1e6
    except (OSError, ValueError):
        return 0.0

def log(**rec):
    with LOG_LOCK:
        LOG.write(json.dumps({"t": time.time(), **rec}) + "\n")

_epoch = collections.Counter()   # job -> number of freezes and thaws (the sampler checks it)
def freeze(j, on, why, **extra):
    with open(f"{CG}/{j}/cgroup.freeze", "w") as f:
        f.write("1" if on else "0")
    _epoch[j] += 1
    log(job=j, frozen=on, why=why, **extra)

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
    pids = procs(j)
    flt = sum(snap.get(p, (0, 0))[0] for p in pids)
    ns = sum(snap.get(p, (0, 0))[1] for p in pids)
    f0, n0 = _prev.get(j, (flt, ns))
    _prev[j] = (flt, ns)
    return (flt - f0) / max(1e-3, (ns - n0) / 1e6)   # faults per CPU-ms

_res = {}
def track(j, now, cur):
    """Record memory.current; return its growth (bytes/s) over the last
    GROW_WINDOW seconds, or None while there is less than half a second of
    history. One second was too noisy on a slow swap device: a single flat
    reading looked like thrashing."""
    h = _res.setdefault(j, collections.deque())
    h.append((now, cur))
    while len(h) > 2 and now - h[1][0] >= GROW_WINDOW:
        h.popleft()                   # keep the oldest sample at least GROW_WINDOW old
    (t0, c0), (t1, c1) = h[0], h[-1]
    return (c1 - c0) / (t1 - t0) if t1 - t0 >= 0.5 else None

def growth(rates_b, js, W):
    """Combined resident growth of jobs js as a fraction of their working sets,
    per second. Infinite while any of them lacks history (just started or thawed)."""
    if any(rates_b.get(j) is None for j in js):
        return float("inf")
    return sum(rates_b[j] for j in js) / max(1, sum(W[j] for j in js))

def finite(x, nd=4):
    """For the log: JSON has no Infinity."""
    return round(x, nd) if x not in (float("inf"), float("-inf")) and x == x else None

# ---- layer 1: measured working sets ----------------------------------------

def referenced(pid):
    """Bytes of this process's memory referenced since its bits were cleared."""
    for line in rd(f"{PROC}/{pid}/smaps_rollup").splitlines():
        if line.startswith("Referenced:"):
            return int(line.split()[1]) * 1024
    return 0

def clear_refs(pid):
    with open(f"{PROC}/{pid}/clear_refs", "w") as f:
        f.write("1")

class WorkingSets(threading.Thread):
    """Measures each running job's working set W(t, tau) in the background:
    clear its processes' referenced bits, wait until it has run tau of user
    CPU, read how much is referenced again. Keeps the trusted samples; the
    control loop asks for estimates. Never blocks the control loop."""
    POLL = 0.5

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.win = {}                      # job -> open window
        self.trusted = {}                  # job -> deque of trusted samples (bytes)
        self.prev = {}                     # job -> (time, memory stall us) at the last poll
        self.due = {}                      # job -> when its next window may open

    def run(self):
        failed = set()
        while True:
            live = set(jobs())
            for j in live:
                try:
                    self.step(j)
                except (FileNotFoundError, ProcessLookupError):
                    self.win.pop(j, None)   # the job exited mid-window
                except (OSError, ValueError, IndexError) as e:
                    self.win.pop(j, None)   # being frozen, or no PSI: start again
                    if j not in failed:     # say so once per job, then keep trying
                        failed.add(j)
                        log(event="ws-error", job=j, error=repr(e))
            for d in (self.prev, self.due):
                for j in [j for j in d if j not in live]:
                    del d[j]
            time.sleep(self.POLL)

    def maybe_open(self, j, now):
        """Open a window if one is due and the job isn't stalling on memory:
        its PSI 'some' avg10 is under WS_STALL and it hasn't stalled since the
        last poll. A stalled window would be discarded anyway, and clearing
        referenced bits makes the job's pages look idle to reclaim: under
        pressure that evicts its hot pages."""
        avg10, s = stall_some(j)           # checks the job's cgroup before touching a process
        prev, self.prev[j] = self.prev.get(j), (now, s)
        if prev is None or now < self.due.get(j, 0.0):
            return
        if avg10 > WS_STALL * 100 or s - prev[1] > WS_STALL * (now - prev[0]) * 1e6:
            return
        pids = procs(j)
        for p in pids:
            clear_refs(p)
        self.win[j] = dict(t0=now, cpu0=worked(j), stall0=s, fp0=footprint(j),
                           pids=pids, epoch=_epoch[j])

    def step(self, j):
        now = time.monotonic()
        w = self.win.get(j)
        if frozen(j) or (w is not None and w["epoch"] != _epoch[j]):
            self.win.pop(j, None)          # parked (and maybe thawed) meanwhile: start over,
            self.prev.pop(j, None)         # judging its stall afresh once it runs again
            return
        if w is None:
            self.maybe_open(j, now)
            return
        if worked(j) - w["cpu0"] < WS_TAU_CPU * 1e6:
            if now - w["t0"] > WS_MAX_WALL:
                self.close(j, now, None, False)
            return
        size = sum(referenced(p) for p in w["pids"])
        span = now - w["t0"]
        stalled = stall_us(j) - w["stall0"]
        grew = footprint(j) - w["fp0"]
        if w["epoch"] != _epoch[j] or frozen(j):
            self.win.pop(j, None)          # parked while we read
            self.prev.pop(j, None)
            return
        self.close(j, now, size, stalled <= WS_STALL * span * 1e6
                   and grew <= GROW_FRAC * span * max(1, w["fp0"]))

    def close(self, j, now, size, ok):
        self.note(j, size, ok)
        self.win.pop(j, None)
        self.due[j] = now + WS_PERIOD

    def note(self, j, size, ok):
        with self.lock:
            if ok:
                self.trusted.setdefault(j, collections.deque(maxlen=WS_KEEP)).append(size)

    def measured(self, j):
        """Largest recent trusted sample, or None."""
        with self.lock:
            d = self.trusted.get(j)
            return max(d) if d else None

    def forget(self, js):
        with self.lock:
            for j in js:
                self.trusted.pop(j, None)

def estimate(j, fp, sampler):
    """Working-set estimate: the measured one with headroom, never above the
    footprint; the footprint until there is a trusted measurement."""
    m = sampler.measured(j) if sampler else None
    return fp if m is None else min(fp, int(WS_MARGIN * m))

# ---- arm 5: DAMON working-set estimate (--ws damon) -------------------------

_damon_warned = set()

def estimate_damon(j, fp, cur):
    """Working set from DAMON's access footprint, read from the file
    scripts/damon_wss.sh writes ("<bytes> <unix_time>"). Like the refs
    estimate it never exceeds the footprint; it is also never below what is
    resident now. A reading older than DAMON_STALE s, or none, falls back to
    the footprint, as the refs estimate does before its first trusted sample.

    Known accuracy caveat: DAMON reads Accessed bits without a TLB flush, so a
    working set small enough to sit in the TLB can read as idle."""
    try:
        b, t = rd(f"{DAMON_DIR}/{j}").split()[:2]
        if time.time() - float(t) <= DAMON_STALE:
            return min(fp, max(int(b), cur))
    except (OSError, ValueError, IndexError):
        pass
    if j not in _damon_warned:
        _damon_warned.add(j)
        log(event="damon_stale", job=j, fallback="footprint")
    return fp

# ---- arm 6: predictive trigger (--predict) ----------------------------------

class Predictor:
    """Logistic model over four features, from eval/train_predictor.py.

    Stdlib only and tiny: the controller is mlockall'd and must not grow a
    numerical stack. The model file is JSON:
        {"w": {feature: weight}, "b": bias, "threshold": p, "features": [...]}"""

    def __init__(self, path):
        m = json.load(open(path))
        if m.get("sign_check", {}).get("passed") is False:
            sys.exit(f"{path} failed train_predictor.py's sign check "
                     f"({', '.join(m['sign_check'].get('wrong', []))}): it has not "
                     f"learned pressure. Refusing to run with it.")
        self.w = m["w"]
        self.b = m.get("b", 0.0)
        self.threshold = m.get("threshold", 0.5)
        self.names = m.get("features", sorted(self.w))

    def p(self, feats):
        z = self.b + sum(self.w.get(k, 0.0) * feats.get(k, 0.0) for k in self.names)
        z = max(-60.0, min(60.0, z))          # no overflow on a wild feature
        return 1.0 / (1.0 + math.exp(-z))

def predict_features(active, rates, slope, W, cur):
    """The four features, defined once so training and inference agree."""
    sets = sum(W[j] for j in active) or 1
    total = meminfo("MemTotal")
    return {"psi_slope": slope if slope is not None else 0.0,
            "pff_mean": (sum(rates.get(j, 0) for j in active) / len(active)) if active else 0.0,
            "missing_frac": missing(active, W, cur) / sets,
            "fit_margin": (total - RESERVE * total - sets) / total}

# ---- arm 7: actuator, freeze in place or checkpoint out (--actuator) ---------
#
# actuate() is what main()'s park() and thaw() call. The freezer is arm 1's
# actuator and the default. CRIU fails safe: any error falls back to freezing
# that job and logs why. See the module docstring: arm 7 is not yet runnable
# under run_mix.sh's systemd transient units.

ACTUATOR = "freeze"
_criu_parked = set()

def _criu(args, j, phase):
    d = f"{CRIU_DIR}/{j}"
    os.makedirs(d, mode=0o700, exist_ok=True)
    r = subprocess.run(["criu", *args, "-D", d, "--shell-job", "--tcp-established",
                        "--file-locks", "-v0"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        log(event="criu_failed", job=j, phase=phase, rc=r.returncode,
            err=r.stderr.strip()[-400:])
        return False
    return True

def criu_park(j, on, why, **extra):
    """Checkpoint the job out, or restore it; the freezer if CRIU fails."""
    if on:
        pids = rd(f"{CG}/{j}/cgroup.procs").split()
        if not pids or not _criu(["dump", "--tree", pids[0], "--leave-stopped"], j, "dump"):
            freeze(j, True, why + "-criu-fallback", **extra)
            return
        # --leave-stopped leaves the tree stopped rather than gone, so a failed
        # dump is recoverable. Killing it is what frees the memory: the point
        # of the arm, and the one place stasisd sends a signal.
        for p in pids:
            try:
                os.kill(int(p), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        _criu_parked.add(j)
        _epoch[j] += 1                    # the working-set sampler checks this
        log(job=j, frozen=True, why=why, actuator="criu", **extra)
    else:
        if j not in _criu_parked:          # frozen by the fallback, or never parked
            freeze(j, False, why, **extra)
            return
        _criu_parked.discard(j)
        if _criu(["restore", "--restore-detached"], j, "restore"):
            _epoch[j] += 1
            log(job=j, frozen=False, why=why, actuator="criu", **extra)
        else:                              # the dump killed it: nothing left to thaw
            log(event="criu_lost", job=j, why=why)

def actuate(j, on, why, **extra):
    if ACTUATOR == "criu":
        criu_park(j, on, why, **extra)
    else:
        freeze(j, on, why, **extra)

# ---- arm 4: PSI level and slope (the D-term) --------------------------------

def psi_full_avg10():
    """PSI 'full' avg10, in percent. None when the file has no such line (the
    regression tests write a plain file with only a trigger-shaped first line)."""
    try:
        for line in rd(PSI_FILE).splitlines():
            if line.startswith("full"):
                for part in line.split()[1:]:
                    k, _, v = part.partition("=")
                    if k == "avg10":
                        return float(v)
    except (OSError, ValueError):
        pass
    return None

_psi_hist = collections.deque()
def psi_slope(now):
    """Rise of PSI full avg10 in percentage points per second over the last
    DERIV_WINDOW s, or None while there is too little history."""
    v = psi_full_avg10()
    if v is None:
        return None
    _psi_hist.append((now, v))
    while len(_psi_hist) > 2 and now - _psi_hist[1][0] >= DERIV_WINDOW:
        _psi_hist.popleft()
    (t0, v0), (t1, v1) = _psi_hist[0], _psi_hist[-1]
    return (v1 - v0) / (t1 - t0) if t1 - t0 >= 0.5 else None

# ---- arm 8: the controller's own cost ---------------------------------------

def self_rss_kb():
    """This process's resident size, for the energy proxy."""
    try:
        return int(rd("/proc/self/statm").split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except (OSError, IndexError, ValueError):
        return None

# ---- layer 3: making room: page parked jobs out ahead of need ---------------

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

def missing(js, W, cur):
    """Bytes of these jobs' working sets that are not resident."""
    return sum(max(0, W[j] - cur[j]) for j in js)

def room_wanted(active, parked, W, cur):
    """How much of each parked job to page out so the active jobs' working sets
    fit: the active jobs' missing pages minus what is free beyond the reserve,
    taken from the parked jobs holding the most memory first. Whole bytes:
    memory.reclaim rejects "123.0"."""
    spare = meminfo("MemAvailable") - int(RESERVE * meminfo("MemTotal"))
    room = missing(active, W, cur) - max(0, spare)
    want = {}
    for j in sorted(parked, key=lambda x: cur[x], reverse=True):
        if room <= 0:
            break
        take = min(room, cur[j])
        if take > 0:
            want[j] = take
            room -= take
    return want

# ---- layer 2: load control --------------------------------------------------

def fits(js, W, cur, extra=0):
    """Do these jobs' working sets (plus `extra` bytes) fit in the memory jobs
    can use? Memory held by anything other than our jobs can't be freed; parked
    jobs' memory can (they are frozen, so their pages may be swapped out)."""
    total, avail = meminfo("MemTotal"), meminfo("MemAvailable")
    other = max(0, total - avail - sum(cur.values()))
    return sum(W[x] for x in js) + extra <= total - other - RESERVE * total

def ws_estimate(j, settled, W):
    """A job parked while it was still growing hasn't shown its whole working
    set yet: assume it is as large as the largest settled job's."""
    if settled.get(j):
        return W[j]
    return max([W[j]] + [W[x] for x in W if settled.get(x)])

_thawed = False
def thaw_all(*_, code=0):
    global _thawed
    if _thawed:                 # signal handler and finally can both get here
        sys.exit(code)
    _thawed = True
    for j in jobs():
        try:
            actuate(j, False, "shutdown")
        except (OSError, subprocess.SubprocessError):
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
    ap.add_argument("--max-wait", type=float, default=120.0,
                    help="starvation bound: seconds a parked job may wait before it is resumed anyway")
    ap.add_argument("--no-rotation", dest="rotation", action="store_false",
                    help="no rotation and no starvation bound (the fairness ablation)")
    ap.add_argument("--ws", choices=["refs", "footprint", "damon"], default="refs",
                    help="working-set estimate: measured from referenced bits, the footprint "
                         "upper bound, or DAMON (arm 5; needs a DAMON-enabled kernel)")
    ap.add_argument("--pff-hi", type=float, default=None,
                    help="faults per CPU-ms; frozen in docs/00-prereg.md (bpf mode)")
    ap.add_argument("--grace", type=float, default=2.0,
                    help="ignore a job's PFF for this long after it starts or is thawed")
    ap.add_argument("--no-reclaim", dest="reclaim", action="store_false",
                    help="don't page parked jobs out ahead of need")
    # Added 2026-10-24; all off by default (docs/01-prereg-arms.md).
    ap.add_argument("--derivative-hi", type=float, default=None,
                    help="arm 4: pressure when PSI full avg10 rises this many percentage points per second")
    ap.add_argument("--derivative-window", type=float, default=DERIV_WINDOW,
                    help="arm 4: seconds of PSI history behind the slope")
    ap.add_argument("--predict", default=None, metavar="MODEL.json",
                    help="arm 6: predictive trigger; a model from eval/train_predictor.py")
    ap.add_argument("--actuator", choices=["freeze", "criu"], default="freeze",
                    help="arm 7: how a parked job is stopped (criu is not yet runnable; see the docstring)")
    return ap.parse_args()

def refs_supported():
    """Can this kernel report referenced memory per process?"""
    try:
        return any(l.startswith("Referenced:") for l in rd(f"{PROC}/self/smaps_rollup").splitlines()) \
            and os.access(f"{PROC}/self/clear_refs", os.W_OK)
    except OSError:
        return False

def main():
    global LOG, ACTUATOR, DERIV_WINDOW
    a = parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
    LOG = open(a.log, "a", buffering=1)
    bpf = a.signal == "bpf"
    if bpf and a.pff_hi is None:
        print("warning: --signal=bpf without --pff-hi; PFF early warning disabled",
              file=sys.stderr)
    ws_mode = a.ws
    if ws_mode == "refs" and not refs_supported():
        print("warning: no /proc/PID/clear_refs or smaps_rollup Referenced; "
              "using footprints as working sets", file=sys.stderr)
        ws_mode = "footprint"
    # Arm 5 must not degrade silently: without DAMON every job would fall back
    # to its footprint, and the run would be a footprint run labelled DAMON.
    if ws_mode == "damon" and not os.path.isdir(DAMON_SYSFS):
        sys.exit(f"--ws=damon needs a DAMON-enabled kernel ({DAMON_SYSFS} is missing; "
                 f"Ubuntu 26.04's stock kernel is built without CONFIG_DAMON)")
    ACTUATOR, DERIV_WINDOW = a.actuator, a.derivative_window
    if ACTUATOR == "criu" and not shutil.which("criu"):
        sys.exit("--actuator=criu needs criu on PATH")
    predictor = Predictor(a.predict) if a.predict else None

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.mlockall(3) != 0:                 # never let the controller swap
        sys.exit(f"mlockall failed, errno {ctypes.get_errno()}")
    signal.signal(signal.SIGTERM, thaw_all)
    signal.signal(signal.SIGINT, thaw_all)
    threading.stack_size(256 << 10)           # threads are mlocked too

    fd = os.open(PSI_FILE, os.O_RDWR | os.O_NONBLOCK)
    os.write(fd, f"full {a.trigger_ms * 1000} {a.window_ms * 1000}\0".encode())
    poller = select.poll()
    poller.register(fd, select.POLLPRI)
    rate_fn = fault_rate_bpf if bpf else fault_rate_memstat
    poll_ms = 100 if bpf else 250
    log(event="start", signal=a.signal, trigger_ms=a.trigger_ms, window_ms=a.window_ms,
        quantum=a.quantum, max_wait=a.max_wait if a.rotation else None, rotation=a.rotation,
        ws=ws_mode, ws_tau_cpu=WS_TAU_CPU, ws_margin=WS_MARGIN,
        pff_hi=a.pff_hi, grace=a.grace, grow_frac=GROW_FRAC,
        set_grow_frac=SET_GROW_FRAC, grow_window=GROW_WINDOW, reload_frac=RELOAD_FRAC,
        reserve=RESERVE, reclaim=a.reclaim,
        # arms 4, 6, 7: all null/"freeze" means none of them is in use
        derivative_hi=a.derivative_hi, derivative_window=a.derivative_window,
        predict=a.predict, actuator=a.actuator)
    reclaimer = Reclaimer()
    if a.reclaim:
        reclaimer.start()
    sampler = WorkingSets() if ws_mode == "refs" else None
    if sampler:
        sampler.start()

    me = os.getpid()
    last_pressure = last_halve = last_rotate = last_rescue = last_self = time.monotonic()
    last_ws_log = 0.0
    hot_d = hot_ml = 0     # consecutive samples over the D-term / model threshold (arms 4, 6)
    min_turn = min(10.0, a.max_wait / 4, a.quantum)
    since = {}             # job -> monotonic time it started or was last thawed
    cpu0 = {}              # job -> its user CPU time (us) when it started or was last thawed
    stall0 = {}            # job -> its memory stall (s) when it started or was last thawed
    parked_at = {}         # job -> monotonic time it was parked
    settled = {}           # job -> its resident memory had stopped growing (last seen)
    reclaim_logged = {}    # job -> when its paging-out was last logged
    hot = {}               # job -> consecutive PFF samples above PFF_HI
    unsafe = set()         # jobs that contain stasisd itself: never touched
    rates = {}
    snap_mtime = None

    def turn(j):           # user CPU (us) in the job's current turn
        return worked(j) - cpu0.get(j, 0)

    def unstalled(j, now):  # s of the current turn not spent waiting for memory
        return now - since[j] - (stalled_s(j) - stall0.get(j, 0.0))

    def had_turn(j, now):   # has had min_turn s not stalled: it has reloaded
        return unstalled(j, now) >= min_turn

    def reloading(js, now):  # a resumed job is still faulting its pages back in
        return any(not had_turn(j, now) and now - since[j] < a.max_wait for j in js)

    def park(j, why, now, **extra):
        actuate(j, True, why, **extra)    # the freezer unless --actuator=criu
        parked_at[j] = now

    def thaw(j, why, now):
        actuate(j, False, why, waited=round(now - parked_at.pop(j, now), 1))
        since[j] = now
        cpu0[j] = worked(j)
        stall0[j] = stalled_s(j)
        hot.pop(j, None)
        _res.pop(j, None)

    try:
        while True:
            events = poller.poll(poll_ms)
            now = time.monotonic()
            # The controller's own cost, every 10 s: the honest part of the
            # energy question on a VM where RAPL is unavailable (arm 8).
            if now - last_self >= 10:
                last_self = now
                log(event="self", cpu_s=round(time.process_time(), 3), rss_kb=self_rss_kb())
            try:
                js = [j for j in jobs() if j not in unsafe]
                for j in js:
                    if j not in since and j not in parked_at and me in procs(j):
                        unsafe.add(j)
                        log(event="refused", job=j, why="contains stasisd")
                js = [j for j in js if j not in unsafe]
                active = [j for j in js if not frozen(j)]
                parked = [j for j in js if j not in active]
                for j in active:
                    if j not in since:
                        since[j] = now
                        cpu0[j] = worked(j)
                        stall0[j] = stalled_s(j)
                for j in parked:
                    parked_at.setdefault(j, now)
                # resumed first: longest waiting, ties by name
                queue = sorted(parked, key=lambda j: (parked_at[j], j))

                cur = {j: current(j) for j in js}
                fp = {j: footprint(j, cur[j]) for j in js}
                if ws_mode == "damon":
                    W = {j: estimate_damon(j, fp[j], cur[j]) for j in js}
                else:
                    W = {j: estimate(j, fp[j], sampler) for j in js}
                if now - last_ws_log >= 5:        # m: the measured sample, if any
                    last_ws_log = now
                    log(event="ws", jobs={j: {"fp": fp[j], "est": W[j],
                                              "m": sampler.measured(j) if sampler else None}
                                          for j in js})

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
                    want = room_wanted(active, parked, W, cur)
                    making_room = reclaimer.set(want)
                    for j, b in want.items():     # log each job at most every 10 s
                        if now - reclaim_logged.get(j, -1e9) >= 10:
                            reclaim_logged[j] = now
                            log(event="reclaim", job=j, bytes=int(b))
                grow_b = {j: track(j, now, cur[j]) for j in active}   # every loop: continuous history
                for j in active:                  # parked jobs keep their last value
                    settled[j] = growth(grow_b, [j], W) < GROW_FRAC
                src = "psi" if events else None
                if bpf and fresh and a.pff_hi is not None and not src:
                    for j in active:
                        g = growth(grow_b, [j], W)
                        thrashing = (now - since[j] >= a.grace
                                     and rates.get(j, 0) > a.pff_hi and g < GROW_FRAC)
                        hot[j] = hot.get(j, 0) + 1 if thrashing else 0
                        if hot[j] >= PFF_STREAK:
                            src = "pff"
                            log(event="pff_hi", job=j, pff=round(rates[j], 3), growth=finite(g))
                            break

                # Arms 4 and 6. The slope is tracked every loop, whatever the
                # arm, so its history is continuous; PSI and PFF keep priority
                # as the recorded source when they fire in the same loop.
                slope = psi_slope(now)
                if a.derivative_hi is not None and active and slope is not None:
                    hot_d = hot_d + 1 if slope >= a.derivative_hi else 0
                    if hot_d >= PFF_STREAK and not src:
                        src = "dpsi"
                        log(event="dpsi_hi", slope=finite(slope), hi=a.derivative_hi)
                else:
                    hot_d = 0
                if predictor and active:
                    feats = predict_features(active, rates, slope, W, cur)
                    p = predictor.p(feats)
                    hot_ml = hot_ml + 1 if p >= predictor.threshold else 0
                    if hot_ml >= PFF_STREAK and not src:
                        src = "ml"
                        log(event="ml_hi", p=round(p, 4),
                            feats={k: finite(v) for k, v in feats.items()})

                halved = False
                if src:
                    fit = fits(active, W, cur)
                    g = growth(grow_b, active, W)
                    short = missing(active, W, cur)
                    hold = None                   # why this pressure is warm-up, if it is
                    if fit and active:
                        if g >= SET_GROW_FRAC:
                            hold = "growing"
                        elif short >= RELOAD_FRAC * sum(W[j] for j in active):
                            hold = "reloading"
                        elif making_room:
                            hold = "making-room"
                    warming = hold is not None
                    log(event="pressure", src=src, active=len(active), parked=len(parked),
                        warming=warming, hold=hold, fits=fit, growth=finite(g), missing=short,
                        slope=finite(slope) if slope is not None else None)
                    if not warming:
                        last_pressure = now
                        if now - last_halve >= COOLDOWN and len(active) > 1:
                            if fit and sampler:   # the set fitted: an estimate was wrong
                                sampler.forget(active)
                                log(event="ws-reset", jobs=active)
                            keep = max(1, len(active) // 2)
                            # park the jobs that have had the most of their turn
                            order = sorted(active, key=lambda j: (turn(j), cur[j]), reverse=True)
                            for j in order[:len(active) - keep]:
                                park(j, f"pressure-{src}", now)
                            for j in order[len(active) - keep:]:
                                _res.pop(j, None)  # the survivors' growth starts over
                            last_halve = now
                            last_rotate = now     # the rotation clock starts when jobs park
                            hot.clear()
                            hot_d = hot_ml = 0    # the new set gets a fresh streak
                            halved = True
                # Admission and rotation are independent: when the next parked
                # job doesn't fit, calm must fall through to rotation, or parked
                # jobs starve until the active ones finish.
                # Admit only once the active set has settled (its working sets
                # are known), and only if the candidate's working set fits too.
                elif (queue and now - last_pressure >= CALM
                      and growth(grow_b, active, W) < SET_GROW_FRAC
                      and fits(active, W, cur, ws_estimate(queue[0], settled, W))):
                    thaw(queue[0], "admit", now)
                    last_pressure = now           # wait a full calm period again
                # The quantum is counted in the job's own user CPU time since it
                # was last thawed. Faulting its pages back in is kernel time, so
                # a slow swap device can't eat the quantum before any work is done.
                elif a.rotation and queue and active and now - last_rotate >= a.quantum:
                    ready = [j for j in active if turn(j) >= a.quantum * 1e6]
                    if ready and not reloading(active, now):
                        out = max(ready, key=turn)
                        park(out, "rotate-out", now)
                        thaw(queue[0], "rotate-in", now)
                        last_rotate = now
                # The starvation bound: rotation needs a finished turn, so if no
                # turn ends, the longest-waiting job is swapped in anyway, one
                # swap at a time, for the job that has had the most of its turn.
                # Nobody is mid-reload then, so no reload is thrown away.
                if (a.rotation and not halved and parked and active
                        and now - max(last_rescue, last_rotate) >= RESCUE_SPACING):
                    js2 = jobs()
                    queue = sorted((j for j in js2 if j in parked_at and j not in unsafe and frozen(j)),
                                   key=lambda j: (parked_at[j], j))
                    runners = [j for j in js2 if j in since and j not in unsafe and not frozen(j)]
                    if (queue and runners and now - parked_at[queue[0]] >= a.max_wait
                            and not reloading(runners, now)):
                        out = max(runners, key=turn)
                        park(out, "starve-out", now)
                        thaw(queue[0], "starve-in", now)
                        last_rescue = last_rotate = now
            except (FileNotFoundError, ValueError, ProcessLookupError):
                continue                          # a job exited mid-scan
            except OSError as e:
                if e.errno not in (errno.ENODEV, errno.ESRCH):   # its cgroup was being removed
                    raise
    except Exception as e:                        # say why before thawing and exiting
        traceback.print_exc()
        log(event="crash", error=repr(e))
        thaw_all(code=1)
    finally:
        thaw_all()

if __name__ == "__main__":
    main()
