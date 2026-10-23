import { byJobNumber, fmtBytes, fmtClock, fmtNum, fmtPct, fmtPff, fmtUs, GiB, median, MiB, niceMax } from '../src/lib/format';
import { eq, type Test } from './check';

export const tests: Test[] = [
  ['clock', () => {
    eq(fmtClock(65), '1:05', '65 s');
    eq(fmtClock(3725), '1:02:05', 'over an hour');
    eq(fmtClock(-5), '−0:05', 'before the run started');
    eq(fmtClock(59.6), '1:00', 'rounds to the second');
    eq(fmtClock(null), '–', 'missing');
  }],
  ['sizes and latencies', () => {
    eq(fmtBytes(300 * MiB), '300 MiB', 'MiB');
    eq(fmtBytes(5 * GiB), '5.00 GiB', 'GiB, two decimals');
    eq(fmtBytes(12 * GiB), '12.0 GiB', 'GiB, one decimal from 10');
    eq(fmtUs(850), '850 µs', 'below a millisecond');
    eq(fmtUs(6800), '6.80 ms', 'milliseconds');
    eq(fmtUs(13500), '13.5 ms', 'tens of milliseconds');
  }],
  ['numbers', () => {
    eq(fmtNum(1234), '1.2K', 'thousands');
    eq(fmtNum(56789), '57K', 'tens of thousands');
    eq(fmtNum(12.345, 3), '12.3', 'one decimal from 10');
    eq(fmtNum(0.0123, 3), '0.012', 'small');
    eq(fmtNum(NaN), '–', 'not a number');
    eq(fmtPff(0.004), '<0.01', 'tiny fault rate');
    eq(fmtPff(3.14159), '3.14', 'fault rate');
    eq(fmtPff(250.4), '250', 'high fault rate');
    eq(fmtPct(10.4), '10', 'percent from 10');
  }],
  ['median, axis maximum, job order', () => {
    eq(median([3, 1, 2]), 2, 'odd count');
    eq(median([4, 1, 2, 3]), 2.5, 'even count');
    eq(median([]), null, 'empty');
    eq(niceMax(1.84 * 1.05), 2, 'rounds up to a nice step');
    eq(niceMax(0), 1, 'no data');
    eq(['job-10', 'job-2', 'job-1'].sort(byJobNumber).join(), 'job-1,job-2,job-10', 'by number, not text');
  }],
];
