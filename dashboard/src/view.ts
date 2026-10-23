// What the monitor shows, derived from the frames: the time window, labels,
// throughput, working sets and the decision feed's grouping.
import { S } from './store';
import { bsearchT, byJobNumber, fmtClock } from './lib/format';
import type { DashEvent, WsEstimate } from './types';

export const CONFIG_NAMES: Record<string, string> = {
  eevdf: 'EEVDF only', stasis: 'Full STASIS', ctrl: 'Controller only', scxonly: 'scx_stasis alone',
  oomd: 'EEVDF + systemd-oomd', simple: 'scx_simple', thrashprotect: 'thrash-protect', mglru: 'EEVDF + MGLRU min_ttl',
  'stasis-norot': 'STASIS, no rotation', 'stasis-fp': 'STASIS, footprint estimate',
  // the sensitivity sweeps (campaign.sh --sensitivity)
  'sens-q5': 'STASIS, quantum 5 s', 'sens-q15': 'STASIS, quantum 15 s', 'sens-q30': 'STASIS, quantum 30 s',
  'sens-w60': 'STASIS, bound 60 s', 'sens-w120': 'STASIS, bound 120 s', 'sens-w240': 'STASIS, bound 240 s',
  'sens-t50': 'STASIS, trigger 50 ms', 'sens-t100': 'STASIS, trigger 100 ms', 'sens-t200': 'STASIS, trigger 200 ms',
};
export const SHORT_NAMES: Record<string, string> = {
  eevdf: 'EEVDF', stasis: 'STASIS', ctrl: 'Controller only', thrashprotect: 'thrash-protect', oomd: 'oomd',
  mglru: 'MGLRU', 'stasis-norot': 'No rotation', 'stasis-fp': 'Footprint est.',
};

export interface ViewWindow { t0: number; t1: number; i0: number; i1: number; X: (t: number, w: number) => number }

/** Live history can span several runs; while one is being followed, start just before it. */
export function firstT(): number {
  const fr = S.frames, f = fr[S.end];
  let first = fr[0].t - 1;
  if (S.view === 'live' && f.run && f.run.t0) first = Math.max(first, f.run.t0 - 10);
  return first;
}

export function viewWindow(): ViewWindow {
  const fr = S.frames, end = S.end;
  const t1 = fr[end].t;
  // Start at the first sample until there is enough history to fill the window.
  const first = firstT();
  const t0 = S.win ? Math.max(t1 - S.win, first) : first;
  const i0 = Math.max(0, bsearchT(fr, t0) - 1);
  return { t0, t1, i0, i1: end, X: (t, w) => ((t - t0) / Math.max(1e-9, t1 - t0)) * w };
}

/** Time since the run started, or relative to now when there is no run. */
export function runClockLabel(t: number): string {
  const f = S.frames[S.end];
  const run = f && f.run;
  if (run && run.t0) return fmtClock(t - run.t0);
  return t >= f.t - 0.5 ? 'now' : fmtClock(t - f.t);
}

/** Passes per minute over the last `span` seconds, never across two runs. */
export function passesPerMin(i: number, span = 30): number | null {
  const fr = S.frames, f = fr[i];
  const runStart = f.run && f.run.t0 ? bsearchT(fr, f.run.t0) : 0;
  const j = Math.max(0, runStart, bsearchT(fr, f.t - span));
  if (j >= i) return null;
  const dt = f.t - fr[j].t;
  return dt > 0 ? ((f._n.passes - fr[j]._n.passes) / dt) * 60 : null;
}

/** Latest working-set estimates at frame `end` (stasisd logs them every 5 s). */
export function latestWS(end: number): Record<string, WsEstimate> | null {
  const fr = S.frames;
  for (let i = end; i >= 0 && i >= end - 30; i--) for (const e of fr[i].ev || []) if (e.kind === 'ws') return e.jobs || {};
  return null;
}

/** Every job seen in the window, in number order. */
export function laneIds(vw: ViewWindow): string[] {
  const ids = new Set<string>();
  for (let i = vw.i0; i <= vw.i1; i++) for (const j of S.frames[i].jobs) ids.add(j.id);
  return Array.from(ids).sort(byJobNumber);
}

// ---- the decision feed

export type Group =
  | { t: number; kind: 'start'; e: Extract<DashEvent, { kind: 'start' }> }
  | { t: number; kind: 'reclaim'; jobs: string[]; bytes: number }
  | { t: number; kind: 'halve'; jobs: string[]; why: string; pff: Extract<DashEvent, { kind: 'pff' }> | null }
  | { t: number; kind: 'wsreset' | 'refused' | 'stop'; jobs: string[] }
  | { t: number; kind: 'rotate' | 'bound'; out: string | null; in: string | null; waited?: number | null }
  | { t: number; kind: 'wake' | 'park'; jobs: string[]; why: string; waited?: number | null };

/** Collapses the raw events into what a person would call one decision. */
export function groupEvents(evs: DashEvent[]): Group[] {
  const out: Group[] = [];
  let pff: Extract<DashEvent, { kind: 'pff' }> | null = null;
  for (const e of evs) {
    const prev = out[out.length - 1];
    switch (e.kind) {
      case 'pressure': continue;
      case 'pff': pff = e; continue;
      case 'ws': continue;                          // working-set estimates feed the lanes, not the list
      case 'start': out.push({ t: e.t, kind: 'start', e }); continue;
      case 'reclaim':
        if (prev && prev.kind === 'reclaim' && e.t - prev.t < 1.5) { prev.jobs.push(e.job); prev.bytes += e.bytes || 0; continue; }
        out.push({ t: e.t, kind: 'reclaim', jobs: [e.job], bytes: e.bytes || 0 });
        continue;
      case 'wsreset': out.push({ t: e.t, kind: 'wsreset', jobs: e.jobs || [] }); continue;
      case 'refused': out.push({ t: e.t, kind: 'refused', jobs: [e.job] }); continue;
      case 'park':
      case 'wake': break;
    }
    if (e.kind !== 'park' && e.kind !== 'wake') continue;
    if (e.kind === 'park' && (e.why || '').startsWith('pressure')) {
      if (prev && prev.kind === 'halve' && e.t - prev.t < 1) { prev.jobs.push(e.job); continue; }
      out.push({ t: e.t, kind: 'halve', jobs: [e.job], why: e.why, pff: pff && e.t - pff.t < 1.5 ? pff : null });
      continue;
    }
    if (e.why === 'rotate-out' || e.why === 'starve-out') {
      out.push({ t: e.t, kind: e.why === 'rotate-out' ? 'rotate' : 'bound', out: e.job, in: null });
      continue;
    }
    if (e.why === 'rotate-in' || e.why === 'starve-in') {
      const k = e.why === 'rotate-in' ? 'rotate' : 'bound';
      if (prev && prev.kind === k && !prev.in && e.t - prev.t < 1) { prev.in = e.job; prev.waited = e.waited; }
      else out.push({ t: e.t, kind: k, out: null, in: e.job, waited: e.waited });
      continue;
    }
    if (e.why === 'shutdown') {
      if (prev && prev.kind === 'stop' && e.t - prev.t < 1) prev.jobs.push(e.job);
      else out.push({ t: e.t, kind: 'stop', jobs: [e.job] });
      continue;
    }
    out.push({ t: e.t, kind: e.kind === 'wake' ? 'wake' : 'park', jobs: [e.job], why: e.why, waited: e.waited });
  }
  return out;
}

export const WHY: Record<string, string> = {
  'pressure-psi': 'PSI saw over 100 ms of full stall in 1 s, so the active set was halved',
  'pressure-pff': 'Fault-frequency early warning, before PSI fired, so the active set was halved',
  admit: 'Calm for 5 s and the working sets fit, its own included',
  shutdown: 'Controller stopped and thawed every job',
};
export const waitedText = (w: number | null | undefined): string => (w != null ? `, after waiting ${Math.round(w)} s` : '');
