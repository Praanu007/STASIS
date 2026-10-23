// Number formatting and small helpers shared by every view.
import type { Counts, EFrame, Frame } from '../types';

export const MiB = 1048576;
export const GiB = 1073741824;

export const clamp = (v: number, a: number, b: number): number => Math.max(a, Math.min(b, v));

export function sum<T>(xs: readonly T[], f?: (x: T) => number): number {
  let s = 0;
  for (const x of xs) s += f ? f(x) : (x as unknown as number);
  return s;
}

export const isNum = (v: number | null | undefined): v is number => v != null && isFinite(v);

export function fmtBytes(b: number | null | undefined): string {
  if (!isNum(b)) return '–';
  if (b >= GiB) return (b / GiB).toFixed(b >= 10 * GiB ? 1 : 2) + ' GiB';
  return Math.round(b / MiB) + ' MiB';
}
export function fmtMiB(b: number | null | undefined): string {
  return b == null ? '–' : Math.round(b / MiB).toLocaleString('en-US');
}
export function fmtPct(v: number | null | undefined): string {
  if (!isNum(v)) return '–';
  return v >= 10 ? String(Math.round(v)) : v.toFixed(1);
}
export function fmtNum(v: number | null | undefined, d = 1): string {
  if (!isNum(v)) return '–';
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (a >= 1e4) return (v / 1e3).toFixed(0) + 'K';
  if (a >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  if (a >= 100) return v.toFixed(0);
  return v.toFixed(a >= 10 ? Math.min(d, 1) : d);
}
export function fmtPff(v: number | null | undefined): string {
  if (!isNum(v)) return '–';
  if (v === 0) return '0';
  if (v < 0.01) return '<0.01';
  if (v < 10) return v.toFixed(2);
  if (v < 1000) return v.toFixed(0);
  return fmtNum(v);
}
/** Latency: µs below 1 ms, then ms. */
export function fmtUs(us: number | null | undefined): string {
  if (!isNum(us)) return '–';
  return us < 1000 ? `${Math.round(us)} µs` : `${(us / 1000).toFixed(us < 10000 ? 2 : 1)} ms`;
}
export function fmtClock(s: number | null | undefined): string {
  if (!isNum(s)) return '–';
  const neg = s < 0;
  const x = Math.abs(Math.round(s));
  const h = Math.floor(x / 3600), m = Math.floor(x / 60) % 60, sec = x % 60;
  const t = h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
  return (neg ? '−' : '') + t;
}
/** Fixed decimals, or a dash. */
export const fixed = (v: number | null | undefined, d: number): string => (isNum(v) ? v.toFixed(d) : '–');

export const pagesMiBs = (p: number): number => (p * 4096) / MiB;

export function median(v: readonly number[]): number | null {
  const s = v.slice().sort((a, b) => a - b);
  const n = s.length;
  return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : null;
}
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/** First index with frame.t >= t. */
export function bsearchT(frames: readonly { t: number }[], t: number): number {
  let lo = 0, hi = frames.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (frames[m].t < t) lo = m + 1; else hi = m;
  }
  return lo;
}

/** Adds the counts and the job index every view uses. */
export function enrich(f: Frame): EFrame {
  const n: Counts = { run: 0, park: 0, done: 0, passes: 0, res: 0 };
  for (const j of f.jobs) {
    if (j.state === 'run') n.run++; else if (j.state === 'park') n.park++; else n.done++;
    n.passes += j.passes || 0;
    if (j.state !== 'done') n.res += j.res || 0;
  }
  const e = f as EFrame;
  e._n = n;
  e._jobs = new Map(f.jobs.map((j) => [j.id, j]));
  return e;
}

/** Job ids in number order: job-2 before job-10. */
export function byJobNumber(a: string, b: string): number {
  const num = (id: string) => { const m = /(\d+)$/.exec(id); return m ? +m[1] : 1e9; };
  return num(a) - num(b) || a.localeCompare(b);
}
