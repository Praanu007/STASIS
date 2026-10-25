// Pure helpers behind the Results tab's "Latest run" card and its auto-refresh.
// Kept apart from the components so the tests can run them under Node.
import type { Frame, RunListing } from '../types';
import { pagesMiBs } from './format';

/** The run that finished most recently (by the server's finish time `t`);
 *  without times, the last listed. Null when there are no runs. */
export function latestRun(runs: readonly RunListing[] | null): RunListing | null {
  if (!runs || !runs.length) return null;
  let best = runs[runs.length - 1];
  let bestT = best.t ?? -Infinity;
  for (const r of runs) {
    if (r.t != null && r.t > bestT) { best = r; bestT = r.t; }
  }
  return best;
}

/** Changes whenever a run appears, disappears or changes status or finish
 *  time: the cheap check the Results tab polls to know it must reload. */
export function runsSignature(runs: readonly RunListing[] | null): string {
  if (!runs) return '';
  return runs.map((r) => `${r.id}|${r.status}|${r.t ?? ''}`).join(';');
}

export interface StallPoint { t: number; full: number; swap: number }

/** Memory stall (% of each second, PSI full) and swap traffic (MiB/s) over a
 *  run, thinned to at most `max` points by taking the peak of each bucket so a
 *  short spike never disappears from the chart. */
export function stallSeries(frames: readonly Frame[], max = 240): StallPoint[] {
  if (!frames.length) return [];
  const t0 = frames[0].t;
  const step = Math.max(1, Math.ceil(frames.length / max));
  const out: StallPoint[] = [];
  for (let i = 0; i < frames.length; i += step) {
    let full = 0, swap = 0;
    for (let j = i; j < Math.min(frames.length, i + step); j++) {
      full = Math.max(full, frames[j].psi.full || 0);
      swap = Math.max(swap, pagesMiBs((frames[j].rate.swpin || 0) + (frames[j].rate.swpout || 0)));
    }
    out.push({ t: frames[i].t - t0, full, swap });
  }
  return out;
}
