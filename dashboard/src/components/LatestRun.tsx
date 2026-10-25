// Results tab, top card: the run you just watched. Its memory-stall and swap
// graph, its readings, and the controller's decision log, taken from the run's
// own recording, so a live run that has finished can be read here without
// opening Replay. Follows the newest finished run unless one is picked.
import { useMemo, type ReactNode } from 'react';
import { S } from '../store';
import { selectResultRun } from '../controller';
import { fixed, fmtBytes, fmtClock, fmtNum, isNum } from '../lib/format';
import { stallSeries, type StallPoint } from '../lib/results';
import { CONFIG_NAMES, groupEvents } from '../view';
import { describe } from './Rail';
import type { Frame, RunListing } from '../types';

const nameOf = (l: string): string => CONFIG_NAMES[l] || l;
const TRIGGER = 10;     // stasisd's PSI trigger, % of each second (100 ms of full stall in 1 s)

function StallChart({ pts }: { pts: StallPoint[] }) {
  const W = 760, H = 230, L = 46, R = 52, T = 12, B = 34;
  const pw = W - L - R, ph = H - T - B;
  const tmax = Math.max(1, pts[pts.length - 1]?.t ?? 1);
  const smax = Math.max(1, ...pts.map((p) => p.swap));
  const smaxNice = smax <= 10 ? 10 : smax <= 100 ? Math.ceil(smax / 20) * 20 : Math.ceil(smax / 100) * 100;
  const X = (t: number) => L + (t / tmax) * pw;
  const Yp = (v: number) => T + ph * (1 - Math.min(100, v) / 100);
  const Ys = (v: number) => T + ph * (1 - v / smaxNice);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + X(p.t).toFixed(1) + ' ' + Yp(p.full).toFixed(1)).join('');
  const area = pts.length
    ? 'M' + X(pts[0].t).toFixed(1) + ' ' + Ys(0).toFixed(1) + pts.map((p) => 'L' + X(p.t).toFixed(1) + ' ' + Ys(p.swap).toFixed(1)).join('')
      + 'L' + X(pts[pts.length - 1].t).toFixed(1) + ' ' + Ys(0).toFixed(1) + 'Z'
    : '';
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * tmax);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto' }}
      aria-label="Memory stall and swap traffic over the run">
      {[0, 25, 50, 75, 100].map((v) => (
        <g key={v}>
          <line x1={L} x2={L + pw} y1={Yp(v)} y2={Yp(v)} stroke="var(--grid)" strokeWidth={1} />
          <text x={L - 8} y={Yp(v) + 4} textAnchor="end" fontSize={11} fill="var(--ink-3)">{v}%</text>
        </g>
      ))}
      {[0, 0.5, 1].map((f) => (
        <text key={f} x={W - R + 8} y={Ys(smaxNice * f) + 4} fontSize={11} fill="var(--ink-3)">{fmtNum(smaxNice * f, 0)}</text>
      ))}
      <text x={W - 6} y={T + 4} textAnchor="end" fontSize={10.5} fill="var(--ink-3)">MiB/s</text>
      {ticks.map((t) => <text key={t} x={X(t)} y={T + ph + 18} textAnchor="middle" fontSize={11} fill="var(--ink-3)">{fmtClock(t)}</text>)}
      <path d={area} fill="var(--frost-ink)" fillOpacity={0.16} stroke="none" />
      <line x1={L} x2={L + pw} y1={Yp(TRIGGER)} y2={Yp(TRIGGER)} stroke="var(--ink-3)" strokeWidth={1} strokeDasharray="4 4" />
      <text x={L + pw - 4} y={Yp(TRIGGER) - 5} textAnchor="end" fontSize={10.5} fill="var(--ink-3)">stasisd trigger · {TRIGGER}%</text>
      <path d={line} fill="none" stroke="var(--psi-full)" strokeWidth={2} strokeLinejoin="round" />
    </svg>
  );
}

function cell(l: string, v: ReactNode) {
  return <div key={l}><div className="l">{l}</div><div className="v">{v}</div></div>;
}

export function LatestRun() {
  const RS = S.results;
  const d = RS.latest;
  const runs: RunListing[] = RS.runs ?? [];
  const listed = runs.find((r) => r.id === RS.latestId) ?? null;
  const frames: Frame[] = d ? d.frames : [];
  const pts = useMemo(() => stallSeries(frames), [RS.latestId, d]);
  const groups = useMemo(() => groupEvents(frames.flatMap((f) => f.ev || [])), [RS.latestId, d]);
  const controllerOn = frames.some((f) => f.ctrl && f.ctrl.on);
  const last = frames[frames.length - 1];
  const nDone = last ? last.jobs.filter((j) => j.state === 'done').length : 0;
  const m = d?.metrics ?? {}, meta = d?.meta;
  const t0 = meta?.t0 ?? frames[0]?.t ?? 0;
  const ordered = runs.slice().sort((a, b) => (b.t ?? 0) - (a.t ?? 0));
  const total = meta && meta.n && meta.passes ? meta.n * meta.passes : null;
  const finishedAt = listed?.t ? new Date(listed.t * 1000).toLocaleTimeString() : '';

  return (
    <article className="card wide" id="card-latest">
      <div className="card-h">
        <h2>{RS.sel ? 'Selected run' : 'Latest finished run'}</h2>
        <span className="meta">
          {meta ? <>{nameOf(meta.config || listed?.label || '')} · N={meta.n ?? listed?.n}{meta.size ? ` · ${meta.size} MB` : ''}{finishedAt ? ` · finished ${finishedAt}` : ''}</> : ''}
        </span>
        <div className="right">
          <label className="small" htmlFor="latest-pick">Show</label>
          <select id="latest-pick" value={RS.sel ?? 'latest'} onChange={(e) => selectResultRun(e.target.value)}>
            <option value="latest">Latest finished run (follows live runs)</option>
            {ordered.map((r) => <option key={r.id} value={r.id}>{nameOf(r.label)} · N={r.n} · run {r.run}{r.status !== 'OK' ? ` · ${r.status}` : ''}</option>)}
          </select>
        </div>
      </div>
      {!d ? (
        <div className="empty">
          {RS.latestLoading ? <p>Loading the run…</p>
            : <><h3>No finished run yet</h3><p>Start a run (for example <code>./demo.sh run eevdf</code>). When it ends it shows up here by itself, with its graph, readings and decisions.</p></>}
        </div>
      ) : (
        <>
          <div className="summary">
            {cell('Result', <>{m.status || meta?.status || '–'}{controllerOn ? <span className="small"> · stasisd on</span> : <span className="small"> · stock Linux, no controller</span>}</>)}
            {cell('Work done', m.passes != null ? <>{m.passes.toLocaleString()}{total ? <span className="small"> of {total.toLocaleString()} passes</span> : null}</> : '–')}
            {cell('Jobs finished', last ? <>{nDone}<span className="small"> of {last.jobs.length}</span></> : '–')}
            {cell('Time', isNum(m.duration_s) ? fmtClock(m.duration_s) : '–')}
            {cell('Throughput', m.throughput != null ? <>{m.throughput.toFixed(2)} <span className="small">passes/s</span></> : '–')}
            {cell('Processes killed', m.kills != null ? String(m.kills) : '–')}
            {cell('Time stalled on memory', isNum(m.psi_full_pct) ? fixed(m.psi_full_pct, 1) + '%' : '–')}
            {cell('Longest wait without progress', isNum(m.max_gap_s) ? <>{Math.round(m.max_gap_s)} <span className="small">s</span></> : '–')}
            {cell('Swap traffic', m.swap_pages != null ? fmtBytes(m.swap_pages * 4096) : '–')}
            {cell('Jain fairness', m.jain != null ? m.jain.toFixed(3) : '–')}
          </div>
          <div className="chart-box">
            <h3 className="small" style={{ margin: '0 0 6px' }}>Memory stall (line, % of each second spent waiting on memory) and swap traffic (shaded, MiB/s)</h3>
            {pts.length ? <StallChart pts={pts} /> : <div className="empty"><p>This run has no recorded samples.</p></div>}
          </div>
          <div className="card-h" style={{ borderTop: '1px solid var(--line)' }}>
            <h2>Controller decisions in this run</h2>
            <span className="meta">{groups.length ? `${groups.length} decisions` : ''}</span>
          </div>
          <ol className="ev-list" aria-live="polite" style={{ maxHeight: 340, overflow: 'auto' }}>
            {!groups.length && (
              <li className="ev-empty">
                {controllerOn ? 'The controller made no decisions: memory pressure never reached its trigger.'
                  : 'stasisd was not running, so nothing was parked. Under stock Linux alone every job kept competing for memory.'}
              </li>
            )}
            {groups.map((g, i) => {
              const x = describe(g);
              return (
                <li className="ev" key={`${g.t}:${i}`}>
                  <time>{fmtClock(g.t - t0)}</time>
                  <span className={'tag ' + x.tag[0]}>{x.tag[1]}</span>
                  <div className="b"><b>{x.title}</b><span>{x.text}</span></div>
                </li>
              );
            })}
          </ol>
        </>
      )}
    </article>
  );
}
