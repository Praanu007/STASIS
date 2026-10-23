// A model of the 4 GiB, 4-CPU lab VM running 700 MiB random-access jobs, with
// stasisd's control law reproduced step for step. Illustrative, not measured.
// Each job keeps touching its hot share (hotFrac) after the first fill, like
// wss with HOT_PCT; reclaim takes cold pages before hot ones. With identical
// jobs PSI's full stall and the PFF early warning fire together, so
// controller-only and full STASIS decide alike here; on the rehearsal VM they
// didn't (docs/rehearsal).
import { MiB, clamp, sum } from '../lib/format';
import type { DashEvent, Frame, FrameJob, Hold, JobState, Policy, ResultRow, WsMode } from '../types';

export interface SimConfig {
  ram: number; sys: number; cache: number; swap: number; cpus: number;   // MiB, CPUs
  ws: number; passes: number; secPerPass: number;                        // one job alone: 60 s
  touch: number; K: number;                                              // page touches/s; fault latency / touch time
  bwIn: number; bwOut: number; fill: number;                             // MiB/s: swap-in, swap-out, first touch
  faultCpu: number;                                                      // CPU s per major fault
  hotFrac: number;                                                       // share of each job's memory it keeps touching
  pffHi: number; grace: number; grow: number; setGrow: number; reload: number;
  calm: number; cooldown: number; quantum: number; reserve: number; trig: number;
  wsMode: WsMode; wsMargin: number; maxWait: number; rotation: boolean;  // stasisd: measured working sets, fair turns
  dt: number;
}

export const SC: SimConfig = {
  ram: 4096, sys: 620, cache: 150, swap: 8192, cpus: 4,
  ws: 700, passes: 40, secPerPass: 1.5,
  touch: 6e6, K: 600,
  bwIn: 120, bwOut: 180, fill: 1200,
  faultCpu: 5e-6,
  hotFrac: 1.0,
  pffHi: 1.0, grace: 2, grow: 0.005, setGrow: 0.001, reload: 0.02, calm: 5, cooldown: 2, quantum: 15, reserve: 0.10, trig: 0.10,
  wsMode: 'refs', wsMargin: 1.1, maxWait: 120, rotation: true,
  dt: 0.25,
};

function mulberry32(seed: number): () => number {
  let a = seed;
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/** One simulated job. Sizes are MiB, times are simulated seconds. */
interface SimJob {
  id: string;
  state: JobState;
  start: number;
  res: number; rh: number; rc: number;      // resident: hot and cold parts
  touched: number; peak: number;
  hset: number;                             // hot set
  wsM: number | null;                       // measured working set
  parkedAt: number | null;
  filled: boolean;
  passes: number; cpu: number; ucpu: number; flt: number;
  luck: number;
  since: number; ucpu0: number; stall0: number; stallT: number;
  hot: number;                              // consecutive PFF samples above PFF_HI
  hist: [number, number][];                 // (t, resident) over the growth window
  settled: boolean;
  reclaimLogged: number | null;
  // per-step working values
  m: number; fl: number; g: number; want: number; f: number; cpuF: number; pffNow: number;
}

interface Snapshot { t: number; cnt: Counters; jobs: Map<string, { flt: number; cpu: number }> }
interface Counters { swpin: number; swpout: number; majflt: number; fullUs: number; someUs: number }

type OmitEach<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
type SimEvent = OmitEach<DashEvent, 't'>;   // the simulator stamps the time

export class Sim {
  readonly c: SimConfig;
  readonly n: number;
  policy: Policy;
  epoch: number;
  t = 0;
  private steps = 0;
  private readonly rnd: () => number;
  readonly jobs: SimJob[] = [];
  readonly cnt: Counters = { swpin: 0, swpout: 0, majflt: 0, fullUs: 0, someUs: 0 };
  private readonly avg = { full: 0, some: 0 };
  private readonly fullWin: number[] = [];
  private lastTrig = -9; private lastPressure = 0; private lastHalve = -9;
  private lastRotate = 0; private lastRescue = -9; private lastWS = -9;
  private makingRoom = false;
  private pending: DashEvent[] = [];
  private prev: Snapshot;
  firstPark: number | null = null;
  firstPressure: number | null = null;
  firstFull: number | null = null;

  constructor(n: number, policy: Policy, seed = 11, epoch = Date.now() / 1000, opts: Partial<SimConfig> = {}) {
    this.c = { ...SC, ...opts };
    this.rnd = mulberry32(seed);
    this.n = n; this.policy = policy; this.epoch = epoch;
    for (let i = 0; i < n; i++) {
      this.jobs.push({
        id: 'job-' + (i + 1), state: 'run', start: i * 0.2, res: 0, rh: 0, rc: 0, touched: 0, peak: 0,
        hset: this.c.ws * this.c.hotFrac, wsM: null, parkedAt: null,
        filled: false, passes: 0, cpu: 0, ucpu: 0, flt: 0, luck: this.rnd() - 0.5,
        since: i * 0.2, ucpu0: 0, stall0: 0, stallT: 0, hot: 0, hist: [], settled: false, reclaimLogged: null,
        m: 0, fl: 0, g: 0, want: 0, f: 0, cpuF: 0, pffNow: 0,
      });
    }
    if (policy !== 'eevdf') this.emitStart();
    this.prev = this.snapshot();
  }

  private emit(e: SimEvent): void { this.pending.push({ ...e, t: this.epoch + this.t } as DashEvent); }
  private emitStart(): void {
    const c = this.c, full = this.policy === 'full';
    this.emit({ kind: 'start', signal: full ? 'bpf' : 'memstat', pff_hi: full ? c.pffHi : null, quantum: c.quantum,
      ws: c.wsMode, max_wait: c.rotation ? c.maxWait : null, rotation: c.rotation });
  }
  setPolicy(p: Policy): void {
    if (p === this.policy) return;
    if (p === 'eevdf') for (const j of this.jobs) if (j.state === 'park') this.wake(j, 'shutdown');
    this.policy = p;
    if (p !== 'eevdf') {
      this.emitStart();
      this.lastPressure = this.t; this.lastRotate = this.t; this.lastHalve = -9;
    }
  }
  private park(j: SimJob, why: string): void {
    j.state = 'park'; j.hot = 0; j.parkedAt = this.t;
    this.emit({ kind: 'park', job: j.id, why });
    if (this.firstPark == null) this.firstPark = this.t;
  }
  private wake(j: SimJob, why: string): void {
    const waited = j.parkedAt != null ? +(this.t - j.parkedAt).toFixed(1) : null;
    j.state = 'run'; j.since = this.t; j.ucpu0 = j.ucpu; j.stall0 = j.stallT; j.hot = 0; j.hist = []; j.parkedAt = null;
    this.emit({ kind: 'wake', job: j.id, why, waited });
  }
  // eviction takes a job's cold pages before its hot ones, as an LRU would
  private evictFrom(j: SimJob, x: number): number {
    const c = Math.min(j.rc, x); j.rc -= c;
    const h = Math.min(j.rh, x - c); j.rh -= h;
    j.res = j.rh + j.rc;
    return c + h;
  }

  step(): void {
    const c = this.c, dt = c.dt;
    this.t += dt; this.steps++;
    const live = this.jobs.filter((j) => j.state !== 'done' && this.t >= j.start);
    const act = live.filter((j) => j.state === 'run');
    const par = live.filter((j) => j.state === 'park');
    const cap = c.ram - c.sys - c.cache;
    if (this.steps % 4 === 0) for (const j of act) j.luck = clamp(j.luck + (this.rnd() - 0.5) * 0.12, -0.5, 0.5);

    // 1. what each running job asks for this step: first-touch fill, or refaults
    //    of the hot pages it is missing (it only ever touches its hot set)
    let majWant = 0;
    for (const j of act) {
      if (!j.filled) { j.m = 0; j.fl = 0; j.g = 0; j.want = Math.min(c.fill * dt, c.ws - j.touched); continue; }
      j.m = Math.max(0, 1 - j.rh / j.hset);
      j.fl = j.m > 1e-4 ? j.m * c.touch / (1 + c.K * j.m) : 0;      // faults/s, latency bound
      j.want = Math.min(j.fl * dt / 256, j.hset - j.rh);
      majWant += j.want;
    }
    // 2. the swap device delivers at most bwIn
    const io = majWant > c.bwIn * dt ? (c.bwIn * dt) / majWant : 1;
    // 3. CPUs: memset is CPU-bound; a faulting job computes g of the time
    let cpuDem = 0;
    for (const j of act) {
      if (!j.filled) { j.cpuF = 1; cpuDem += 1; continue; }
      j.f = j.fl * io;
      j.g = j.m > 1e-4 ? j.f / (j.m * c.touch) : 1;
      j.cpuF = j.g + j.f * c.faultCpu;
      cpuDem += j.cpuF;
    }
    const share = cpuDem > c.cpus ? c.cpus / cpuDem : 1;
    // 4. memory: growth is granted from free memory plus what reclaim can swap out this step
    for (const j of act) j.want = j.filled ? j.f * share * dt / 256 : j.want * share;
    const resAll = sum(live, (j) => j.res);
    const free = Math.max(0, cap - resAll);
    const reclaimable = resAll;
    const grant = free + Math.min(c.bwOut * dt, reclaimable);
    const want = sum(act, (j) => j.want);
    const k = want > grant ? grant / want : 1;
    let evict = Math.max(0, want * k - free);
    const need0 = evict;
    const swOut = evict;
    if (evict > 0) {                                  // parked jobs' pages go first: stasisd pages them out (memory.reclaim)
      const pr = sum(par, (j) => j.res);
      const fromPar = Math.min(evict, pr);
      if (pr > 0) for (const j of par) this.evictFrom(j, fromPar * (j.res / pr));
      if (fromPar > 0 && this.policy !== 'eevdf') {   // stasisd logs each job at most every 10 s
        const big = par.reduce((m, j) => (j.res > m.res ? j : m), par[0]);
        if (this.t - (big.reclaimLogged ?? -1e9) >= 10) {
          big.reclaimLogged = this.t;
          this.emit({ kind: 'reclaim', job: big.id, bytes: Math.round(Math.min(pr, need0) * MiB) });
        }
      }
      evict -= fromPar;
      const cold = sum(act, (j) => j.rc);             // then running jobs' cold pages
      if (evict > 0 && cold > 0) { const x = Math.min(evict, cold); for (const j of act) this.evictFrom(j, x * (j.rc / cold)); evict -= x; }
      if (evict > 0) {                                // then their hot pages, unevenly (some get luckier)
        const w = sum(act, (j) => j.rh * (1 - 0.7 * j.luck));
        if (w > 0) for (const j of act) { j.rh = Math.max(0, j.rh - evict * (j.rh * (1 - 0.7 * j.luck)) / w); j.res = j.rh + j.rc; }
      }
    }
    // 5. advance each running job
    let fullP = act.length ? 1 : 0, someQ = 1, swIn = 0, faults = 0;
    for (const j of act) {
      const got = j.want * k;
      let stall = 0;
      if (!j.filled) {                                // memset runs front to back: the hot part first
        const toHot = Math.max(0, Math.min(got, j.hset - j.touched));
        j.touched = Math.min(c.ws, j.touched + got);
        j.rh = Math.min(j.hset, j.rh + toHot); j.rc = Math.min(c.ws - j.hset, j.rc + got - toHot); j.res = j.rh + j.rc;
        j.cpu += share * k * dt;
        if (j.touched >= c.ws - 1e-6) j.filled = true;
        stall = 1 - k;
        j.pffNow = 0;
      } else {
        const g = j.g * share * k, f = j.f * share * k;
        j.rh = Math.min(j.hset, j.rh + got); j.res = j.rh + j.rc;
        j.passes = Math.min(c.passes, j.passes + g * dt / c.secPerPass);
        const cpu = g + f * c.faultCpu;
        j.cpu += cpu * dt; j.ucpu += g * dt; j.flt += f * dt; faults += f * dt; swIn += f * dt / 256;
        j.pffNow = cpu > 1e-9 ? f / (cpu * 1000) : 0;
        stall = j.m > 1e-4 ? clamp(1 - (g + f * c.faultCpu) / share, 0, 1) : 0;
      }
      j.peak = Math.max(j.peak, j.res);
      j.stallT += stall * dt;                         // its memory.pressure total
      fullP *= stall; someQ *= 1 - stall;
      j.hist.push([this.t, j.res]);
      while (j.hist.length > 2 && this.t - j.hist[1][0] >= 5) j.hist.shift();   // 5 s growth window
      // stasisd's sampler: a window counts when the job ran unconstrained (not
      // allocating, barely faulting) for a second; it measures the hot set
      if (c.wsMode === 'refs' && j.filled && j.m < 0.02 && this.t - j.since >= 1) j.wsM = j.hset;
      if (j.passes >= c.passes - 1e-9) { j.passes = c.passes; j.state = 'done'; j.res = j.rh = j.rc = 0; }
    }
    // 6. would stasisd's reclaimer still have work? (the room itself was made above)
    this.makingRoom = false;
    if (this.policy !== 'eevdf' && par.length) {
      const run = act.filter((j) => j.state === 'run');
      const resNow = sum(live, (j) => (j.state === 'done' ? 0 : j.res));
      const room = this.missing(run) - Math.max(0, (c.ram - c.sys - resNow) - c.reserve * c.ram);
      this.makingRoom = room >= 8 && sum(par, (j) => j.res) >= 8;   // MiB: under a quarter chunk is ignored
    }
    const full = act.length ? fullP : 0, some = act.length ? 1 - someQ : 0;
    this.cnt.fullUs += full * dt * 1e6; this.cnt.someUs += some * dt * 1e6;
    const a = 1 - Math.exp(-dt / 10);
    this.avg.full += (full * 100 - this.avg.full) * a; this.avg.some += (some * 100 - this.avg.some) * a;
    this.cnt.swpin += swIn * 256; this.cnt.swpout += swOut * 256; this.cnt.majflt += faults;
    if (this.firstFull == null && this.avg.full > 0.5) this.firstFull = this.t;
    this.fullWin.push(full * dt); if (this.fullWin.length > 4) this.fullWin.shift();
    if (this.policy !== 'eevdf') this.control();
  }

  /** Resident growth over the growth window, MiB/s. */
  private rate(j: SimJob): number | null {
    if (j.hist.length < 2) return null;
    const [t0, r0] = j.hist[0], [t1, r1] = j.hist[j.hist.length - 1];
    return t1 - t0 < 0.5 ? null : (r1 - r0) / (t1 - t0);
  }
  /** Footprint: resident + swapped, everything the job has touched. */
  fp(j: SimJob): number { return Math.max(j.touched, j.peak); }
  /** Working-set estimate: measured with headroom, or the footprint. */
  ws(j: SimJob): number {
    return this.c.wsMode === 'refs' && j.wsM != null ? Math.min(this.fp(j), this.c.wsMargin * j.wsM) : this.fp(j);
  }
  /** Non-resident part of the working sets. */
  private missing(js: SimJob[]): number { return sum(js, (j) => Math.max(0, this.ws(j) - j.res)); }
  /** Combined growth as a fraction of the working sets, per s. */
  private growth(js: SimJob[]): number {
    let r = 0, p = 0;
    for (const j of js) { const x = this.rate(j); if (x == null) return Infinity; r += x; p += this.ws(j); }
    return r / Math.max(1, p);
  }
  /** Denning: do these working sets fit in usable memory? */
  private fits(js: SimJob[], extra = 0): boolean {
    const c = this.c;
    const usable = c.ram - c.sys - c.reserve * c.ram;   // parked memory counts as reclaimable
    return sum(js, (x) => this.ws(x)) + extra <= usable;
  }
  /** Parked while still growing: assume the largest settled size. */
  private wsEstimate(j: SimJob): number {
    if (j.settled) return this.ws(j);
    return Math.max(this.ws(j), ...this.jobs.filter((x) => x.settled && x.state !== 'done').map((x) => this.ws(x)));
  }

  private control(): void {
    const c = this.c, t = this.t;
    const act = this.jobs.filter((j) => j.state === 'run' && t >= j.start);
    const par = this.jobs.filter((j) => j.state === 'park');
    for (const j of act) j.settled = this.growth([j]) < c.grow;   // parked jobs keep their last value
    let src: 'psi' | 'pff' | null = null;
    if (sum(this.fullWin) >= c.trig && t - this.lastTrig >= 1) { src = 'psi'; this.lastTrig = t; }
    if (!src && this.policy === 'full') {
      for (const j of act) {
        const g = this.growth([j]);
        const hot = t - j.since >= c.grace && j.pffNow > c.pffHi && g < c.grow;
        j.hot = hot ? j.hot + 1 : 0;
        if (j.hot >= 3) { src = 'pff'; this.emit({ kind: 'pff', job: j.id, pff: +j.pffNow.toFixed(2), growth: +g.toFixed(4) }); break; }
      }
    }
    const turn = (j: SimJob) => j.ucpu - j.ucpu0;   // user CPU in its current turn
    // one swap at a time: a swap waits until the job resumed before it has
    // reloaded (minTurn s not stalled on memory, or maxWait s have passed)
    const minTurn = Math.min(10, c.maxWait / 4, c.quantum);
    const had = (j: SimJob) => t - j.since - (j.stallT - j.stall0) >= minTurn;
    const reloading = (js: SimJob[]) => js.some((j) => !had(j) && t - j.since < c.maxWait);
    const byWait = (x: SimJob, y: SimJob) => (x.parkedAt ?? 0) - (y.parkedAt ?? 0) || x.id.localeCompare(y.id, undefined, { numeric: true });
    const queue = par.slice().sort(byWait);   // longest-waiting first
    let halved = false;
    if (src) {
      if (this.firstPressure == null) this.firstPressure = t;
      // Warm-up: the active set fits and is still growing, reloading, or waiting
      // for room. Halving more jobs can't bring the survivors' pages back faster.
      let hold: Hold | null = null;
      const fit = act.length > 0 && this.fits(act);
      if (fit) {
        if (this.growth(act) >= c.setGrow) hold = 'growing';
        else if (this.missing(act) >= c.reload * sum(act, (j) => this.ws(j))) hold = 'reloading';
        else if (this.makingRoom) hold = 'making-room';
      }
      const warming = hold != null;
      this.emit({ kind: 'pressure', src, active: act.length, parked: par.length, warming, hold });
      if (!warming) {
        this.lastPressure = t;
        if (t - this.lastHalve >= c.cooldown && act.length > 1) {
          if (fit && act.some((j) => j.wsM != null)) {   // the set fitted: an estimate was wrong
            for (const j of act) j.wsM = null;
            this.emit({ kind: 'wsreset', jobs: act.map((j) => j.id) });
          }
          const keep = Math.max(1, Math.floor(act.length / 2));
          // park the jobs that have had the most of their turn
          const order = act.slice().sort((x, y) => turn(y) - turn(x) || y.res - x.res);
          order.slice(0, act.length - keep).forEach((j) => this.park(j, 'pressure-' + src));
          order.slice(act.length - keep).forEach((j) => { j.hist = []; });   // the survivors' growth starts over
          this.lastHalve = t;
          this.lastRotate = t;                   // the rotation clock starts when jobs park
          for (const j of act) j.hot = 0;
          halved = true;
        }
      }
    } else if (queue.length && t - this.lastPressure >= c.calm && this.growth(act) < c.setGrow &&
               this.fits(act, this.wsEstimate(queue[0]))) {
      this.wake(queue[0], 'admit');
      this.lastPressure = t;
    } else if (c.rotation && queue.length && act.length && t - this.lastRotate >= c.quantum) {
      // quantum = user CPU since the last thaw, so refaulting doesn't eat it
      const ready = act.filter((j) => turn(j) >= c.quantum);
      if (ready.length && !reloading(act)) {
        const out = ready.reduce((m, x) => (turn(x) > turn(m) ? x : m));
        this.park(out, 'rotate-out'); this.wake(queue[0], 'rotate-in');
        this.lastRotate = t;
      }
    }
    // the starvation bound: no turn ended, but the longest-waiting job has waited
    // long enough; swapped out: the job that has had the most of its turn
    const q = this.jobs.filter((j) => j.state === 'park').sort(byWait);
    const runners = this.jobs.filter((j) => j.state === 'run' && t >= j.start);
    if (c.rotation && !halved && q.length && runners.length && !reloading(runners) && t - (q[0].parkedAt ?? t) >= c.maxWait &&
        t - Math.max(this.lastRescue, this.lastRotate) >= 5) {
      const out = runners.reduce((m, x) => (turn(x) > turn(m) ? x : m));
      this.park(out, 'starve-out'); this.wake(q[0], 'starve-in');
      this.lastRescue = this.lastRotate = t;
    }
    if (t - this.lastWS >= 5) {                  // stasisd logs its estimates every 5 s
      this.lastWS = t;
      const jobs: Record<string, { fp: number; est: number; m: number | null }> = {};
      for (const j of this.jobs) if (j.state !== 'done' && t >= j.start) {
        jobs[j.id] = { fp: Math.round(this.fp(j) * MiB), est: Math.round(this.ws(j) * MiB),
          m: c.wsMode === 'refs' && j.wsM != null ? Math.round(j.wsM * MiB) : null };
      }
      this.emit({ kind: 'ws', jobs });
    }
  }

  private snapshot(): Snapshot {
    return { t: this.epoch + this.t, cnt: { ...this.cnt }, jobs: new Map(this.jobs.map((j) => [j.id, { flt: j.flt, cpu: j.cpu }])) };
  }

  /** One second of simulated time, as a server frame. */
  frame(): Frame {
    for (let i = 0; i < 4; i++) this.step();
    const c = this.c, now = this.snapshot(), p = this.prev;
    const dt = now.t - p.t;
    const jobs: FrameJob[] = this.jobs.filter((j) => this.t >= j.start).map((j) => {
      const q = p.jobs.get(j.id) || { flt: 0, cpu: 0 };
      const dms = (j.cpu - q.cpu) * 1000;
      const pff = j.state === 'run' ? (dms > 0.05 ? (j.flt - q.flt) / dms : 0) : null;
      return { id: j.id, state: j.state, res: Math.round(j.res * MiB), peak: Math.round(j.peak * MiB),
        pff: pff == null ? null : +pff.toFixed(3), cpu: +j.cpu.toFixed(2), passes: +j.passes.toFixed(2) };
    });
    const res = sum(this.jobs, (j) => (j.state === 'done' ? 0 : j.res));
    const swapUsed = sum(this.jobs, (j) => (j.state === 'done' ? 0 : Math.max(0, j.touched - j.res)));
    const allDone = this.jobs.every((j) => j.state === 'done');
    const f: Frame = {
      t: now.t,
      psi: { full: +((now.cnt.fullUs - p.cnt.fullUs) / (dt * 1e4)).toFixed(2), some: +((now.cnt.someUs - p.cnt.someUs) / (dt * 1e4)).toFixed(2),
        full10: +this.avg.full.toFixed(2), some10: +this.avg.some.toFixed(2) },
      mem: { total: c.ram * MiB, avail: Math.max(40, c.ram - c.sys - res) * MiB, swap_total: c.swap * MiB, swap_free: Math.max(0, c.swap - swapUsed) * MiB },
      rate: { swpin: (now.cnt.swpin - p.cnt.swpin) / dt, swpout: (now.cnt.swpout - p.cnt.swpout) / dt, majflt: (now.cnt.majflt - p.cnt.majflt) / dt },
      oom: 0,
      sched: this.policy === 'full' ? { state: 'enabled', ops: 'stasis' } : { state: 'disabled', ops: '' },
      ctrl: { on: this.policy !== 'eevdf', signal: this.policy === 'full' ? 'bpf' : this.policy === 'ctrl' ? 'memstat' : null },
      run: { id: 'simulated', config: this.policy === 'full' ? 'stasis' : this.policy, n: this.n, size: c.ws, passes: c.passes, t0: this.epoch, finished: allDone, sim: true },
      jobs, ev: this.pending,
    };
    this.pending = [];
    this.prev = now;
    return f;
  }

  done(): boolean { return this.jobs.every((j) => j.state === 'done'); }
  totalPasses(): number { return sum(this.jobs, (j) => j.passes); }
}

/**
 * Headless runs of the model: N = 1..8, three seeds, three policies, 900 s cap.
 * Yields to the browser between cells so the page stays responsive.
 */
export async function simResults(onProgress?: (cell: number, cells: number) => void): Promise<ResultRow[]> {
  const rows: ResultRow[] = [];
  const pol: [Policy, string][] = [['eevdf', 'eevdf'], ['ctrl', 'ctrl'], ['full', 'stasis']];
  let cell = 0;
  for (const [p, label] of pol) {
    for (let n = 1; n <= 8; n++) {
      if (onProgress) onProgress(cell++, 24);
      await new Promise((r) => setTimeout(r, 0));
      for (let seed = 1; seed <= 3; seed++) {
        const s = new Sim(n, p, seed * 97 + n, 0);
        let lastDone: number | null = null;
        let atOnset: number[] | null = null, atFirst: number[] | null = null;
        const passesAt = new Map<string, number>();
        while (s.t < 900 && !s.done()) {
          s.step();
          if (!atOnset && (s.firstFull != null || s.firstPressure != null)) atOnset = s.jobs.map((j) => j.passes);
          for (const j of s.jobs) if (j.state === 'done' && !passesAt.has(j.id)) {
            passesAt.set(j.id, s.t);
            if (atFirst == null) atFirst = s.jobs.map((x) => x.passes);
            lastDone = s.t;
          }
        }
        const jainOf = (xs: number[]): number | null => {
          const sx = sum(xs), sx2 = sum(xs, (x) => x * x);
          return sx2 > 0 ? (sx * sx) / (xs.length * sx2) : null;
        };
        // fairness of slowdowns over the whole run, as eval/analyze.py scores it
        const jain = jainOf(s.jobs.map((j) => j.passes / (passesAt.get(j.id) ?? s.t)));
        let jainWindow: number | null = null;   // the build guide's onset-to-first-finish window
        if (atOnset) {
          const onset = atOnset;
          const end = atFirst || s.jobs.map((j) => j.passes);
          jainWindow = jainOf(end.map((v, i) => v - onset[i]));
        }
        const allDone = s.done();
        const passes = s.totalPasses();
        const end = allDone && lastDone != null ? lastDone : s.t;
        rows.push({ label, config: label, N: n, run: seed, status: allDone ? 'OK' : 'TIMEOUT', throughput: passes / end,
          passes, kills: 0, jain, jain_window: jainWindow, p99_us: null, ttfp_s: s.firstPark != null && s.firstFull != null ? s.firstPark - s.firstFull : null,
          swap_pages: s.cnt.swpin + s.cnt.swpout, duration_s: end });
      }
    }
  }
  return rows;
}
