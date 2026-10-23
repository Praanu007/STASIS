// The simulator is the model behind the Simulator tab and the simulated
// results. The times below pin it: they are what the model produced when the
// dashboard moved from plain JavaScript to TypeScript (identical, frame for
// frame). If you change the model on purpose, update them.
import { SC, Sim, simResults, type SimConfig } from '../src/sim/sim';
import type { Policy } from '../src/types';
import { check, eq, type Test } from './check';

const MiB = 1048576;
const usable = (SC.ram - SC.sys - SC.cache) * MiB;   // what the jobs can have resident

interface Outcome { t: number; done: boolean; passes: number; parked: boolean }

/** Runs a configuration second by second, checking the model's invariants on every frame. */
function run(n: number, policy: Policy, opts: Partial<SimConfig> = {}, cap = 3000, seed = 11 + n): Outcome {
  const s = new Sim(n, policy, seed, 0, opts);
  const last = new Map<string, number>();
  let parked = false;
  for (let sec = 1; sec <= cap; sec++) {
    const f = s.frame();
    check(f.jobs.length <= n && (sec < 3 || f.jobs.length === n), `${n} jobs, ${f.jobs.length} in the frame at ${sec} s`);
    let res = 0;
    for (const j of f.jobs) {
      if (j.state === 'park') parked = true;
      if (j.state !== 'done') res += j.res;
      const p = j.passes ?? 0;
      check(p >= (last.get(j.id) ?? 0), `${j.id} lost progress at ${sec} s`);
      check(p <= SC.passes, `${j.id} did ${p} of ${SC.passes} passes`);
      last.set(j.id, p);
    }
    // each job's size is rounded to the byte, so allow a byte per job
    check(res <= usable + n, `${res - usable} bytes more resident than there is RAM at ${sec} s`);
    if (policy === 'eevdf') check(!parked, 'EEVDF alone parked a job');
    if (f.jobs.length === n && f.jobs.every((j) => j.state === 'done')) return { t: s.t, done: true, passes: n * SC.passes, parked };
  }
  return { t: s.t, done: false, passes: s.jobs.reduce((a, j) => a + j.passes, 0), parked };
}

export const tests: Test[] = [
  ['the Simulator tab default: 8 jobs, full STASIS', () => {
    const o = run(8, 'full');
    check(o.done, 'did not finish');
    eq(o.t, 174, 'seconds to finish');
    check(o.parked, 'never parked a job');
    eq(run(8, 'ctrl').t, 174, 'controller only, seconds to finish');
  }],
  ['EEVDF alone thrashes at 8 jobs and is fine at 4', () => {
    const o = run(8, 'eevdf', {}, 900);
    check(!o.done, 'finished');
    check(o.passes < 0.05 * 8 * SC.passes, `${o.passes} passes in 900 s`);
    const fits = run(4, 'eevdf', {}, 900);
    check(fits.done, '4 jobs did not finish');
    eq(fits.t, 62, '4 jobs, seconds to finish');
  }],
  ['measured working sets admit more than footprints', () => {
    const measured = run(8, 'full', { hotFrac: 0.6, wsMode: 'refs' });
    const footprint = run(8, 'full', { hotFrac: 0.6, wsMode: 'footprint' });
    eq(measured.t, 146, 'measured, seconds to finish');
    eq(footprint.t, 159, 'footprint, seconds to finish');
  }],
  ['slow swap: one swap at a time, no livelock', () => {
    eq(run(8, 'full', { bwIn: 20, bwOut: 30 }).t, 293, '20 MB/s in, seconds to finish');
    const slow = run(8, 'full', { bwIn: 3, bwOut: 4.5 });
    check(slow.done, '3 MB/s in: did not finish in 3000 s');
    eq(slow.t, 1171, '3 MB/s in, seconds to finish');
  }],
  ['rotation shortens the run at 12 jobs', () => {
    eq(run(12, 'full').t, 264, 'with rotation');
    eq(run(12, 'full', { rotation: false }).t, 293, 'without rotation');
  }],
  ['every job finishes under the controller, 1 to 12 jobs', () => {
    for (const policy of ['ctrl', 'full'] as const) for (let n = 1; n <= 12; n++) for (const hotFrac of [1, 0.6]) {
      check(run(n, policy, { hotFrac }).done, `${policy}, ${n} jobs, hot ${hotFrac}: did not finish`);
    }
  }],
  ['the same seed gives the same run', () => {
    const a = new Sim(6, 'full', 5, 0), b = new Sim(6, 'full', 5, 0);
    for (let i = 0; i < 200; i++) eq(JSON.stringify(a.frame()), JSON.stringify(b.frame()), `frame ${i}`);
  }],
  ['simulated results: 72 runs, as the Results tab describes them', async () => {
    const rows = await simResults();
    eq(rows.length, 72, 'rows');
    for (const r of rows) {
      if (r.label === 'stasis') check(r.status === 'OK', `STASIS N=${r.N} run ${r.run}: ${r.status}`);
      if (r.label === 'eevdf') eq(r.status, r.N <= 5 ? 'OK' : 'TIMEOUT', `EEVDF N=${r.N} run ${r.run}`);
      if (r.label === 'ctrl') {
        // "controller-only and full STASIS draw one line"
        const s = rows.find((x) => x.label === 'stasis' && x.N === r.N && x.run === r.run);
        eq(r.throughput, s?.throughput, `N=${r.N} run ${r.run}, controller only vs full STASIS`);
      }
    }
  }],
];
