// Results: the throughput curve against load, the pre-registered hypotheses
// with their verdicts, and every run's metrics.
import { Fragment, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { S } from '../store';
import { hoverEnd, showCurveTip, toggleCurveTable } from '../controller';
import { clamp, fixed, fmtBytes, fmtNum, fmtUs, isNum, median, niceMax } from '../lib/format';
import { VERDICT_ICON } from '../lib/icons';
import { CONFIG_NAMES, SHORT_NAMES } from '../view';
import { Notice, useWidth } from './controls';
import { LatestRun } from './LatestRun';
import type { ResultRow, ScoreRow } from '../types';

// Fixed hue order (--s1..--s6). Every pair inside each panel below passes the
// all-pairs colour check in both themes; STASIS's ablations and sweeps share
// its hue with a dashed line instead of taking new ones.
const SERIES = ['stasis', 'eevdf', 'ctrl', 'thrashprotect', 'oomd', 'mglru'];
const isVariant = (l: string): boolean => l.startsWith('stasis-') || l.startsWith('sens-');
const PANELS = [
  { title: 'The thrashing curve', has: (l: string) => ['eevdf', 'stasis', 'ctrl'].includes(l) || isVariant(l) },
  { title: 'Against other defences', has: (l: string) => ['stasis', 'thrashprotect', 'oomd', 'mglru'].includes(l) },
];
const color = (l: string): string => { const i = SERIES.indexOf(isVariant(l) ? 'stasis' : l); return i >= 0 ? `var(--s${i + 1})` : 'var(--ink-3)'; };
const nameOf = (l: string): string => CONFIG_NAMES[l] || l;
const shortOf = (l: string): string => SHORT_NAMES[l] || l;

interface Cell { med: number; min: number; max: number; runs: number }
interface Curve { labels: string[]; Ns: number[]; cells: Map<string, Cell>; ymax: number; panels: { title: string; labels: string[] }[] }

function buildCurve(rows: ResultRow[]): Curve {
  const labels = SERIES.filter((l) => rows.some((r) => r.label === l));
  for (const r of rows) if (!labels.includes(r.label)) labels.push(r.label);
  const Ns = Array.from(new Set(rows.filter((r) => r.N > 0).map((r) => r.N))).sort((a, b) => a - b);
  const cells = new Map<string, Cell>();
  for (const l of labels) for (const n of Ns) {
    const v = rows.filter((r) => r.label === l && r.N === n && r.throughput != null).map((r) => r.throughput as number);
    if (v.length) cells.set(l + '|' + n, { med: median(v) as number, min: Math.min(...v), max: Math.max(...v), runs: v.length });
  }
  // panels: a curve needs two N, except for the main configurations
  const nPts = (l: string) => Ns.filter((n) => cells.has(l + '|' + n)).length;
  const panels = PANELS.map((p) => ({ title: p.title, labels: labels.filter((l) => p.has(l) && (isVariant(l) ? nPts(l) >= 2 : nPts(l) >= 1)) }))
    .filter((p, i) => p.labels.length && (i === 0 || p.labels.some((l) => l !== 'stasis')));
  const ymax = cells.size ? niceMax(Math.max(...Array.from(cells.values()).map((c) => c.max)) * 1.05) : 1;
  return { labels, Ns, cells, ymax, panels };
}

/** One throughput panel at its real width: median per (configuration, N),
 *  band = min to max, every line labelled at its end, crosshair snapping to N. */
function CurvePanel({ curve, labels }: { curve: Curve; labels: string[] }) {
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [hN, setHN] = useState<number | null>(null);
  const { Ns, cells, ymax } = curve;
  const W = Math.max(300, Math.round(width || 640));
  const H = Math.round(clamp(W * 0.45, 240, 320));
  const L = 46, R = 122, T = 14, B = 36;
  const pw = W - L - R, ph = H - T - B;
  const X = (n: number) => L + (Ns.length === 1 ? pw / 2 : ((n - Ns[0]) / (Ns[Ns.length - 1] - Ns[0])) * pw);
  const Y = (v: number) => T + ph * (1 - v / ymax);
  const drawOrder = labels.filter((l) => l !== 'stasis').concat(labels.includes('stasis') ? ['stasis'] : []);
  const series = drawOrder.map((l) => ({ l, pts: Ns.filter((n) => cells.has(l + '|' + n)).map((n) => ({ n, ...(cells.get(l + '|' + n) as Cell) })) }))
    .filter((s) => s.pts.length);
  // end labels: no two closer than 16 units, all inside the plot
  const ends = series.map(({ l, pts }) => { const p = pts[pts.length - 1]; return { l, x: X(p.n), y: Y(p.med), ly: 0 }; })
    .sort((a, b) => a.y - b.y);
  ends.forEach((e, i) => { e.ly = i ? Math.max(e.y, ends[i - 1].ly + 16) : e.y; });
  for (let i = ends.length - 1; i >= 0; i--) {
    const lim = i === ends.length - 1 ? T + ph : ends[i + 1].ly - 16;
    if (ends[i].ly > lim) ends[i].ly = lim;
  }
  const show = (n: number, cx: number, cy: number) => {
    setHN(n);
    const items = labels.map((l) => [l, cells.get(l + '|' + n)] as const).filter((x): x is readonly [string, Cell] => !!x[1])
      .sort((a, b) => b[1].med - a[1].med)
      .map(([l, c]) => ({ label: l, name: nameOf(l), color: color(l), med: c.med, min: c.min, max: c.max }));
    showCurveTip({ kind: 'curve', x: cx, y: cy, n, items });
  };
  const svgRef = useRef<SVGSVGElement>(null);
  const onMove = (e: PointerEvent) => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return;
    const sx = ((e.clientX - r.left) / r.width) * W;
    let best = Ns[0];
    for (const n of Ns) if (Math.abs(X(n) - sx) < Math.abs(X(best) - sx)) best = n;
    show(best, e.clientX, e.clientY);
  };
  const leave = () => { setHN(null); hoverEnd(); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = clamp(Ns.indexOf(hN ?? Ns[0]) + (e.key === 'ArrowLeft' ? -1 : 1), 0, Ns.length - 1);
    const r = svgRef.current?.getBoundingClientRect();
    if (r) show(Ns[i], r.left + (X(Ns[i]) / W) * r.width, r.top + r.height / 3);
  };
  return (
    <div ref={box}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={'Throughput against number of concurrent jobs: ' + labels.map(nameOf).join(', ')}>
        {[0, 1, 2, 3, 4].map((i) => {
          const v = (ymax / 4) * i, y = Y(v);
          return (
            <g key={i}>
              <line x1={L} x2={L + pw} y1={y} y2={y} stroke="var(--grid)" strokeWidth={1} />
              <text x={L - 8} y={y + 4} textAnchor="end" fontSize={11} fill="var(--ink-3)">{fmtNum(v, 2)}</text>
            </g>
          );
        })}
        {Ns.map((n) => <text key={n} x={X(n)} y={T + ph + 18} textAnchor="middle" fontSize={11} fill="var(--ink-3)">{n}</text>)}
        <text x={L + pw / 2} y={H - 4} textAnchor="middle" fontSize={11.5} fill="var(--ink-2)">Concurrent jobs (N)</text>
        <text x={12} y={T + ph / 2} transform={`rotate(-90 12 ${T + ph / 2})`} textAnchor="middle" fontSize={11.5} fill="var(--ink-2)">Passes per second</text>
        {series.map(({ l, pts }) => {
          // Lines join measured N at most 2 apart (the campaign's grids step by 1
          // or 2); a wider gap stays open rather than inventing the cells between.
          const segs: (typeof pts)[] = [];
          for (const p of pts) { const sg = segs[segs.length - 1]; if (sg && p.n - sg[sg.length - 1].n <= 2) sg.push(p); else segs.push([p]); }
          const variant = isVariant(l);
          return (
            <g key={l}>
              {segs.filter((sg) => sg.length > 1).map((seg, k) => (
                <g key={k}>
                  {!variant && (
                    <path d={seg.map((p, i) => (i ? 'L' : 'M') + X(p.n) + ' ' + Y(p.max)).join('') + seg.slice().reverse().map((p) => 'L' + X(p.n) + ' ' + Y(p.min)).join('') + 'Z'}
                      fill={color(l)} fillOpacity={0.1} stroke="none" />
                  )}
                  <path d={seg.map((p, i) => (i ? 'L' : 'M') + X(p.n) + ' ' + Y(p.med)).join('')} fill="none" stroke={color(l)} strokeWidth={2}
                    strokeLinejoin="round" strokeLinecap="round" strokeDasharray={variant ? '6 4' : undefined} />
                </g>
              ))}
              {pts.map((p) => (
                <circle key={p.n} cx={X(p.n)} cy={Y(p.med)} r={4} fill={variant ? 'var(--panel)' : color(l)}
                  stroke={variant ? color(l) : 'var(--panel)'} strokeWidth={2} />
              ))}
            </g>
          );
        })}
        {ends.map((e) => (
          <g key={e.l}>
            <line x1={e.x + 7} x2={e.x + 16} y1={e.y} y2={e.ly} stroke={color(e.l)} strokeWidth={1.5} />
            <text x={e.x + 20} y={e.ly + 4} fontSize={12} fontWeight={600} fill="var(--ink)">{shortOf(e.l)}</text>
          </g>
        ))}
        {hN != null && <line x1={X(hN)} x2={X(hN)} y1={T} y2={T + ph} stroke="var(--ink-2)" strokeOpacity={0.5} strokeWidth={1} />}
        <rect x={L - 10} y={T} width={pw + 20} height={ph} fill="transparent" tabIndex={0}
          onPointerMove={onMove} onPointerLeave={leave} onBlur={leave} onKeyDown={onKey} />
      </svg>
    </div>
  );
}

function CurveCard({ rows }: { rows: ResultRow[] }) {
  const curve = useMemo(() => buildCurve(rows), [rows]);
  const { labels, Ns, cells, panels } = curve;
  const legend = labels.filter((x) => panels.some((p) => p.labels.includes(x)));
  return (
    <article className="card">
      <div className="card-h">
        <h2>Throughput vs load</h2>
        <span className="meta">median per cell, band = min to max</span>
        <div className="right">
          <button className="tbl-toggle" type="button" aria-expanded={S.showCurveTable} onClick={toggleCurveTable}>
            {S.showCurveTable ? 'Hide table' : 'Table'}
          </button>
        </div>
      </div>
      <div className="legend" id="curve-legend" style={{ padding: '12px 16px 0' }}>
        {legend.map((l) => (
          <span key={l}>
            <i className={'key-line' + (isVariant(l) ? ' dashed' : '')} style={{ color: color(l), background: isVariant(l) ? undefined : color(l) }} />
            {nameOf(l)}
          </span>
        ))}
      </div>
      <div className="chart-box" id="curve">
        {!Ns.length
          ? <div className="empty"><h3>No throughput data yet</h3><p>Finished runs appear here, one point per configuration and job count.</p></div>
          : (
            <div className={'facets' + (panels.length > 1 ? ' two' : '')}>
              {panels.map((p) => (
                <div className="facet" key={p.title}>
                  {panels.length > 1 && <h3>{p.title}</h3>}
                  <CurvePanel curve={curve} labels={p.labels} />
                </div>
              ))}
            </div>
          )}
      </div>
      {S.showCurveTable && (
        <div className="tbl-wrap">
          <table className="data">
            <thead><tr><th>Configuration</th>{Ns.map((n) => <th key={n} className="num">N={n}</th>)}</tr></thead>
            <tbody>
              {labels.map((l) => (
                <tr key={l}>
                  <td><span className="swatch" style={{ background: color(l) }} />{nameOf(l)}</td>
                  {Ns.map((n) => {
                    const c = cells.get(l + '|' + n);
                    return <td key={n} className="num">{c ? <>{fmtNum(c.med, 3)} <span className="small">[{fmtNum(c.min, 3)}–{fmtNum(c.max, 3)}]</span></> : '–'}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

function Values({ v }: { v: number[] | null }) {
  if (!v || !v.length) return <>–</>;
  return <>{fmtNum(median(v), 3)} <span className="small">[{fmtNum(Math.min(...v), 3)}–{fmtNum(Math.max(...v), 3)}]</span></>;
}

function Verdict({ r }: { r: ScoreRow }) {
  const k = r.result === 'PASS' ? 'pass' : r.result === 'FAIL' ? 'fail' : 'missing';
  const t = r.result === 'PASS' ? 'Pass' : r.result === 'FAIL' ? 'Fail' : r.result === 'MISSING' ? 'No data' : 'No baseline';
  const Icon = VERDICT_ICON[k];
  return <span className={'verdict ' + k}><Icon />{t}</span>;
}

function Hypotheses({ score, error, simulated }: { score: ScoreRow[] | null; error: string | null; simulated: boolean }) {
  let body;
  if (simulated) {
    body = (
      <div className="empty"><h3>Scored on measured runs only</h3><p>The hypotheses come from <code>docs/00-prereg.md</code> and
        are judged against real runs, never against the model. Start the server with <code>--results</code> once the
        campaign has data.</p></div>
    );
  } else if (error) {
    body = <div className="empty"><h3>Could not read the pre-registration</h3><p>{error}</p></div>;
  } else if (!score || !score.length) {
    body = <div className="empty"><h3>No hypotheses scored yet</h3><p>The table fills in as runs for each hypothesis finish.</p></div>;
  } else {
    const groups: { id: string; claim: string; rows: ScoreRow[] }[] = [];   // one hypothesis, one block of rows (one per N)
    for (const r of score) {
      const g = groups[groups.length - 1];
      if (g && g.id === r.id && g.claim === r.claim) g.rows.push(r); else groups.push({ id: r.id, claim: r.claim, rows: [r] });
    }
    body = (
      <div className="tbl-wrap">
        <table className="data">
          <thead><tr><th>Hypothesis</th><th className="claimcell">Claim</th><th className="num">N</th><th className="num m-hide">STASIS</th><th className="num m-hide">Deciding baseline</th><th>Verdict</th></tr></thead>
          <tbody>
            {groups.map((g, gi) => <Fragment key={gi}>{g.rows.map((r, i) => (
              <tr key={i} className={i < g.rows.length - 1 ? 'cont' : undefined}>
                {i === 0 && (
                  <>
                    <td rowSpan={g.rows.length}><b>{g.id}</b><span className="claim m-only">{g.claim}</span></td>
                    <td rowSpan={g.rows.length} className="claimcell">{g.claim}</td>
                  </>
                )}
                <td className="num">{r.N}</td>
                <td className="num m-hide"><Values v={r.stasis} /></td>
                <td className="num m-hide">
                  {r.equals != null ? `must equal ${r.equals}` : r.at_most != null ? `at most ${r.at_most}`
                    : r.baseline ? <>{nameOf(r.baseline_label || '')}<br /><Values v={r.baseline} /></> : '–'}
                </td>
                <td><Verdict r={r} /></td>
              </tr>
            ))}</Fragment>)}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <article className="card">
      <div className="card-h"><h2>Pre-registered hypotheses</h2><span className="meta">every STASIS run must beat every baseline run</span></div>
      {body}
    </article>
  );
}

function Runs({ rows }: { rows: ResultRow[] }) {
  const sorted = rows.slice().sort((a, b) => SERIES.indexOf(a.label) - SERIES.indexOf(b.label) || a.N - b.N || a.run - b.run);
  return (
    <article className="card wide">
      <div className="card-h"><h2>All runs</h2><span className="meta" id="runs-meta">{rows.length} runs</span></div>
      <div className="tbl-wrap" id="runs-tbl">
        {!rows.length ? <div className="empty"><p>No finished runs yet.</p></div> : (
          <table className="data">
            <thead>
              <tr>
                <th>Configuration</th><th className="num">N</th><th className="num">Run</th><th>Status</th><th className="num">Passes/s</th>
                <th className="num">Kills</th><th className="num">Jain</th><th className="num">Probe p99</th><th className="num">First park s</th>
                <th className="num">Longest park s</th><th className="num">No progress s</th><th className="num">Full stall</th><th className="num">Swap</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={i}>
                  <td><span className={'swatch' + (isVariant(r.label) ? ' dashed' : '')} style={{ background: color(r.label), color: color(r.label) }} />{nameOf(r.label)}</td>
                  <td className="num">{r.N}</td>
                  <td className="num">{r.run}</td>
                  <td>{r.status || ''}</td>
                  <td className="num">{fixed(r.throughput, 3)}</td>
                  <td className="num">{r.kills ?? '–'}</td>
                  <td className="num">{fixed(r.jain, 3)}</td>
                  <td className="num">{fmtUs(r.p99_us)}</td>
                  <td className="num">{fixed(r.ttfp_s, 1)}</td>
                  <td className="num">{fixed(r.max_wait_s, 0)}</td>
                  <td className="num">{fixed(r.max_gap_s, 0)}</td>
                  <td className="num">{isNum(r.psi_full_pct) ? fixed(r.psi_full_pct, 1) + '%' : '–'}</td>
                  <td className="num">{r.swap_pages != null ? fmtBytes(r.swap_pages * 4096) : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </article>
  );
}

export function Results() {
  const RS = S.results;
  const measured = !!(S.server && S.server.results);
  const rows = measured ? RS.data?.rows ?? null : RS.simRows;
  return (
    <>
      <div className="bar"><div className="bar-group"><span className="bar-text" id="res-src">{RS.status}</span></div></div>
      <Notice id="res-notice">{S.resNotice}</Notice>
      {rows && (
        <div className="res-grid">
          {measured && <LatestRun />}
          <CurveCard rows={rows} />
          <Hypotheses score={measured ? RS.data?.score ?? null : null} error={measured ? RS.data?.score_error ?? null : null} simulated={!measured} />
          <Runs rows={rows} />
        </div>
      )}
    </>
  );
}
