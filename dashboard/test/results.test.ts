import { latestRun, runsSignature, stallSeries } from '../src/lib/results';
import type { Frame, RunListing } from '../src/types';
import { eq, type Test } from './check';

const run = (id: string, t?: number, status = 'OK'): RunListing => ({ id, label: id.split('/')[0], n: 8, run: 1, status, t });
const frame = (t: number, full: number, swpin = 0, swpout = 0): Frame => ({
  t, psi: { full, some: full, full10: full, some10: full }, mem: {}, rate: { swpin, swpout, majflt: 0 },
  oom: 0, sched: {}, ctrl: {}, run: null, jobs: [], ev: [],
});

export const tests: Test[] = [
  ['the latest run is the one that finished last', () => {
    eq(latestRun([run('a/N8/run1', 100), run('b/N8/run1', 300), run('c/N8/run1', 200)])?.id, 'b/N8/run1', 'newest finish time');
    eq(latestRun([run('a/N8/run1'), run('b/N8/run1')])?.id, 'b/N8/run1', 'no times: the last listed');
    eq(latestRun([]), null, 'empty');
    eq(latestRun(null), null, 'not loaded');
  }],
  ['the run-list signature moves when a run appears or finishes', () => {
    const a = [run('a/N8/run1', 100)];
    const same = runsSignature(a);
    eq(runsSignature([run('a/N8/run1', 100)]), same, 'unchanged list, same signature');
    eq(runsSignature([...a, run('b/N8/run1', 200)]) !== same, true, 'a new run changes it');
    eq(runsSignature([run('a/N8/run1', 101)]) !== same, true, 'a changed finish time changes it');
    eq(runsSignature([run('a/N8/run1', 100, 'TIMEOUT')]) !== same, true, 'a changed status changes it');
    eq(runsSignature(null), '', 'not loaded');
  }],
  ['the stall series keeps short spikes when it thins a long run', () => {
    const fr: Frame[] = [];
    for (let i = 0; i < 1000; i++) fr.push(frame(1000 + i, i === 503 ? 87 : 1, i === 503 ? 256 * 100 : 0));
    const s = stallSeries(fr, 100);
    eq(s.length <= 100, true, 'at most 100 points');
    eq(Math.max(...s.map((p) => p.full)), 87, 'the one-second spike survives thinning');
    eq(Math.round(Math.max(...s.map((p) => p.swap))), 100, 'swap pages convert to MiB/s (25600 pages = 100 MiB)');
    eq(s[0].t, 0, 'time starts at zero');
    eq(stallSeries([]).length, 0, 'empty run');
  }],
];
