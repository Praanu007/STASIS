// The system timeline: memory stall on top, one lane per job beneath it, one
// shared time axis. The plots are canvases redrawn after every render.
import { useLayoutEffect, useRef, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';
import { S, useHoverVersion } from '../store';
import { hoverAt, hoverEnd, hoverStep, togglePsiTable } from '../controller';
import { clamp, fmtClock, fmtMiB, fmtNum, fmtPct, fmtPff, pagesMiBs } from '../lib/format';
import { fitCanvas, hatchPattern, heatIndex, type Colors } from '../lib/theme';
import { STATE_ICON } from '../lib/icons';
import { laneIds, latestWS, runClockLabel, viewWindow, type ViewWindow } from '../view';
import { useSizeKey, useWidth } from './controls';
import type { WsEstimate } from '../types';

const PSI_PAD_T = 16, PSI_PAD_B = 4;
const TICKS = [100, 75, 50, 25, 0];
const hasFrames = (): boolean => S.frames.length > 0 && S.end >= 0;

function drawCrosshair(ctx: CanvasRenderingContext2D, C: Colors, vw: ViewWindow, w: number, h: number): void {
  if (S.hover == null || S.hover < vw.i0 || S.hover > vw.i1) return;
  const x = Math.round(vw.X(S.frames[S.hover].t, w)) + 0.5;
  ctx.strokeStyle = C.ink2; ctx.globalAlpha = 0.55; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); ctx.globalAlpha = 1;
}

/** Pointer and keyboard handlers shared by the stall chart and the lanes. */
function hoverHandlers(ref: RefObject<HTMLCanvasElement | null>, lane: string | null) {
  return {
    onPointerMove: (e: PointerEvent) => { if (ref.current) hoverAt(ref.current, e.clientX, e.clientY, lane); },
    onPointerLeave: hoverEnd,
    onBlur: hoverEnd,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        if (ref.current) hoverStep(ref.current, e.key === 'ArrowLeft' ? -1 : 1, lane);
      } else if (e.key === 'Escape') hoverEnd();
    },
  };
}

function drawPsi(cv: HTMLCanvasElement, ylab: HTMLDivElement, ticks: (HTMLSpanElement | null)[]): void {
  const fit = fitCanvas(cv);
  if (!fit) return;
  const { ctx, w, h } = fit;
  ctx.clearRect(0, 0, w, h);
  const ph = h - PSI_PAD_T - PSI_PAD_B;
  const Y = (v: number) => PSI_PAD_T + ph * (1 - clamp(v, 0, 100) / 100);
  // y ticks live in the label column so they share the lanes' left edge
  const ylabTop = cv.getBoundingClientRect().top - ylab.getBoundingClientRect().top;
  TICKS.forEach((v, i) => { const s = ticks[i]; if (s) s.style.top = (ylabTop + Y(v)) + 'px'; });
  if (!hasFrames()) return;
  const C = S.colors;
  ctx.lineWidth = 1;
  ctx.strokeStyle = C.grid;
  for (const v of [0, 25, 50, 75, 100]) { const y = Math.round(Y(v)) + 0.5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  // stasisd's trigger: 100 ms of full stall in a 1 s window = 10 %
  ctx.save(); ctx.setLineDash([4, 4]); ctx.strokeStyle = C.ink3;
  const yt = Math.round(Y(10)) + 0.5; ctx.beginPath(); ctx.moveTo(0, yt); ctx.lineTo(w, yt); ctx.stroke(); ctx.restore();
  ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
  const lbl = 'stasisd trigger · 10%', lw = ctx.measureText(lbl).width;
  ctx.fillStyle = C.panel; ctx.globalAlpha = 0.85;
  ctx.fillRect(w - lw - 14, yt - 17, lw + 10, 15); ctx.globalAlpha = 1;
  ctx.fillStyle = C.ink3; ctx.textAlign = 'right'; ctx.fillText(lbl, w - 9, yt - 6);
  const fr = S.frames, vw = viewWindow();
  const pts = (key: 'full' | 'some') => {
    const out: [number, number][] = [];
    for (let i = vw.i0; i <= vw.i1; i++) out.push([vw.X(fr[i].t, w), Y(fr[i].psi[key])]);
    return out;
  };
  const line = (p: [number, number][], color: string, width: number) => {
    if (p.length < 2) return;
    ctx.beginPath(); ctx.moveTo(p[0][0], p[0][1]);
    for (let i = 1; i < p.length; i++) ctx.lineTo(p[i][0], p[i][1]);
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();
  };
  const some = pts('some'), full = pts('full');
  line(some, C.some, 1.5);
  if (full.length >= 2) {
    ctx.beginPath(); ctx.moveTo(full[0][0], Y(0));
    for (const p of full) ctx.lineTo(p[0], p[1]);
    ctx.lineTo(full[full.length - 1][0], Y(0)); ctx.closePath();
    ctx.globalAlpha = 0.13; ctx.fillStyle = C.full; ctx.fill(); ctx.globalAlpha = 1;
  }
  line(full, C.full, 2);
  // decision marks in the top band: park (frost), wake (accent)
  for (let i = vw.i0; i <= vw.i1; i++) {
    for (const e of fr[i].ev || []) {
      if (e.kind !== 'park' && e.kind !== 'wake') continue;
      const x = vw.X(e.t, w);
      if (x < 0 || x > w) continue;
      ctx.fillStyle = e.kind === 'park' ? C.frostInk : C.accent;
      ctx.beginPath();
      if (e.kind === 'park') { ctx.moveTo(x - 4, 3); ctx.lineTo(x + 4, 3); ctx.lineTo(x, 10); }
      else { ctx.moveTo(x - 4, 10); ctx.lineTo(x + 4, 10); ctx.lineTo(x, 3); }
      ctx.closePath(); ctx.fill();
    }
  }
  if (full.length) {
    const p = full[full.length - 1];
    ctx.beginPath(); ctx.arc(p[0], p[1], 4, 0, Math.PI * 2);
    ctx.fillStyle = C.full; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = C.panel; ctx.stroke();
  }
  drawCrosshair(ctx, C, vw, w, h);
}

function PsiRow() {
  useHoverVersion();
  const cv = useRef<HTMLCanvasElement>(null);
  const ylab = useRef<HTMLDivElement>(null);
  const ticks = useRef<(HTMLSpanElement | null)[]>([]);
  useLayoutEffect(() => {
    if (cv.current && ylab.current) drawPsi(cv.current, ylab.current, ticks.current);
  });
  const f = hasFrames() ? S.frames[S.end] : null;
  return (
    <div className="irow psi-row">
      <div className="ylab" ref={ylab}>
        <div className="t">Memory stall</div>
        <div className="d">% of each second spent waiting on memory</div>
        {TICKS.map((v, i) => <span key={v} className="tick" ref={(el) => { ticks.current[i] = el; }}>{v}%</span>)}
      </div>
      <div className="plot">
        <canvas ref={cv} id="cv-psi" tabIndex={0} role="img" aria-label="Memory stall over time" {...hoverHandlers(cv, null)} />
      </div>
      <div className="readout">
        {f && (
          <>
            <div className="ro"><span className="l"><i className="key-line" style={{ background: 'var(--psi-full)' }} />Full</span><span className="v">{fmtPct(f.psi.full)}<small>%</small></span></div>
            <div className="ro"><span className="l"><i className="key-line" style={{ background: 'var(--psi-some)' }} />Some</span><span className="v">{fmtPct(f.psi.some)}<small>%</small></span></div>
            <div className="ro"><span className="l">Swap in</span><span className="v">{fmtNum(pagesMiBs(f.rate.swpin))}<small>MiB/s</small></span></div>
            <div className="ro"><span className="l">Swap out</span><span className="v">{fmtNum(pagesMiBs(f.rate.swpout))}<small>MiB/s</small></span></div>
          </>
        )}
      </div>
    </div>
  );
}

function PsiTable() {
  const fr = S.frames, end = S.end;
  const rows = [];
  for (let i = end; i >= Math.max(0, end - 29); i--) {
    const f = fr[i];
    rows.push(
      <tr key={i}>
        <td>{runClockLabel(f.t)}</td>
        <td className="num">{fmtPct(f.psi.full)}%</td>
        <td className="num">{fmtPct(f.psi.some)}%</td>
        <td className="num">{fmtNum(pagesMiBs(f.rate.swpin))}</td>
        <td className="num">{fmtNum(pagesMiBs(f.rate.swpout))}</td>
        <td className="num">{f._n.run}</td>
        <td className="num">{f._n.park}</td>
      </tr>,
    );
  }
  return (
    <div className="tbl-wrap">
      <table className="data">
        <thead><tr><th>Time</th><th className="num">Full stall</th><th className="num">Some stall</th><th className="num">Swap in MiB/s</th><th className="num">Swap out MiB/s</th><th className="num">Running</th><th className="num">Parked</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}

function drawLane(cv: HTMLCanvasElement, id: string): void {
  const fit = fitCanvas(cv);
  if (!fit) return;
  const { ctx, w, h } = fit;
  ctx.clearRect(0, 0, w, h);
  if (!hasFrames()) return;
  const C = S.colors, fr = S.frames, vw = viewWindow();
  const y0 = Math.round(h * 0.22), bh = Math.max(6, Math.round(h * 0.56));
  ctx.fillStyle = C.track; ctx.fillRect(0, y0, w, bh);
  let segKey: number | string | null = null, segX = 0, segEnd = 0;
  const flush = () => {
    if (segKey == null || segEnd - segX < 0.2) return;
    const x = Math.max(0, segX), ww = Math.min(w, segEnd) - x;
    if (ww <= 0) return;
    if (segKey === 'park') ctx.fillStyle = hatchPattern(ctx, C) || C.frost;
    else if (segKey === 'done') ctx.fillStyle = C.done;
    else ctx.fillStyle = C.heat[segKey as number];
    ctx.fillRect(x, y0, ww, bh);
  };
  for (let i = vw.i0; i <= vw.i1; i++) {
    const j = fr[i]._jobs.get(id);
    const x1 = vw.X(fr[i].t, w);
    const x0 = vw.X(i > 0 ? fr[i - 1].t : fr[i].t - 1, w);
    if (!j || x1 <= 0) { flush(); segKey = null; continue; }
    const key = j.state === 'run' ? heatIndex(j.pff) : j.state;
    if (key === segKey && Math.abs(x0 - segEnd) < 1.5) segEnd = x1;
    else { flush(); segKey = key; segX = x0; segEnd = x1; }
  }
  flush();
  drawCrosshair(ctx, C, vw, w, h);
}

function Lane({ id, ws }: { id: string; ws: WsEstimate | undefined }) {
  useHoverVersion();
  const cv = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => { if (cv.current) drawLane(cv.current, id); });
  const f = S.frames[S.end];
  const target = f.run && f.run.passes;
  const j = f._jobs.get(id);
  const st = j ? j.state : 'done';
  const label = st === 'run' ? 'Running' : st === 'park' ? 'Parked' : 'Done';
  const Icon = STATE_ICON[st];
  const passes = j && j.passes != null ? j.passes : null;
  const pct = target && passes != null ? clamp(passes / target, 0, 1) * 100 : 0;
  const color = st === 'done' ? 'var(--good)' : st === 'park' ? 'var(--frost-ink)' : 'var(--ink-2)';
  const showWs = j && st !== 'done' && ws && ws.est != null;
  return (
    <div className="irow lane">
      <div className="lab">
        <span className="name">{id}</span>
        <span className={'chipst ' + st}><Icon />{label}</span>
      </div>
      <div className="plot">
        <canvas ref={cv} tabIndex={0} role="img" aria-label={`${id} state over time`} {...hoverHandlers(cv, id)} />
      </div>
      <div className="stats">
        <div className="prog"><i style={{ width: pct + '%', background: color }} /></div>
        <span className="pnum">{passes == null ? '–' : target ? `${Math.floor(passes)} / ${target}` : `${Math.floor(passes)} passes`}</span>
        <span className="mstat">
          {j && st !== 'done' ? <><b>{fmtMiB(j.res)}</b> of {fmtMiB(j.peak)} MiB · PFF <b>{fmtPff(j.pff)}</b></> : 'finished'}
        </span>
        <span className="wstat">
          {showWs ? <>working set <b>{fmtMiB(ws.est)}</b> MiB {ws.m != null ? 'measured' : 'by footprint'}</> : null}
        </span>
      </div>
    </div>
  );
}

function Lanes() {
  if (!hasFrames()) {
    const msg = S.view === 'live' ? (S.live.conn === 'ok' ? 'Collecting the first samples…' : 'Connecting to the server…')
      : S.view === 'replay' ? 'No run loaded.' : 'Starting…';
    return <div id="lanes"><div className="empty-lanes">{msg}</div></div>;
  }
  const ids = laneIds(viewWindow());
  if (!ids.length) {
    return <div id="lanes"><div className="empty-lanes">No jobs in this window. Jobs appear here when <code>stasis.slice</code> has any.</div></div>;
  }
  const wsNow = latestWS(S.end);
  return (
    <div id="lanes" className={wsNow ? 'ws' : undefined}>
      {ids.map((id) => <Lane key={id} id={id} ws={wsNow?.[id]} />)}
    </div>
  );
}

function Axis() {
  const ref = useRef<HTMLDivElement>(null);
  const w = useWidth(ref);
  const ticks: { x: number; text: string }[] = [];
  if (hasFrames() && w > 0) {
    const vw = viewWindow(), f = S.frames[S.end];
    const span = vw.t1 - vw.t0;
    const steps = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
    const maxTicks = Math.max(2, Math.floor(w / 80));
    const step = steps.find((s) => span / s <= maxTicks) || 7200;
    const t0 = f.run && f.run.t0 ? f.run.t0 : null;
    const base = t0 ?? vw.t1;
    for (let v = Math.ceil((vw.t0 - base) / step) * step; base + v <= vw.t1 + 0.01; v += step) {
      const x = vw.X(base + v, w);
      if (x < 18 || x > w - 18) continue;
      ticks.push({ x, text: t0 != null ? fmtClock(v) : v === 0 ? 'now' : fmtClock(v) });
    }
  }
  return (
    <div className="irow xaxis-row">
      <div />
      <div className="xaxis" ref={ref}>{ticks.map((t) => <span key={t.text} style={{ left: t.x + 'px' }}>{t.text}</span>)}</div>
      <div />
    </div>
  );
}

export function Timeline() {
  const card = useRef<HTMLElement>(null);
  useSizeKey(card);              // a resize re-renders the card, and every canvas redraws
  return (
    <article className="card" id="inst" ref={card}>
      <div className="card-h">
        <h2>System timeline</h2>
        <div className="legend">
          <span><i className="key-line" style={{ background: 'var(--psi-full)' }} />Full stall</span>
          <span><i className="key-line" style={{ background: 'var(--psi-some)' }} />Some stall</span>
          <span><i className="key-dash" />stasisd trigger</span>
        </div>
        <div className="right">
          <button className="tbl-toggle" type="button" aria-expanded={S.showPsiTable} onClick={togglePsiTable}>
            {S.showPsiTable ? 'Hide table' : 'Table'}
          </button>
        </div>
      </div>
      <PsiRow />
      {S.showPsiTable && hasFrames() && <PsiTable />}
      <div className="irow lanes-h">
        <div>Job</div>
        <div>
          <span>Running, shaded by page-fault rate</span><span className="key-heat" />
          <span className="key-box key-frost" /><span>Parked</span>
          <span className="key-box" style={{ background: 'var(--done)' }} /><span>Done</span>
        </div>
        <div>Progress · memory · faults</div>
      </div>
      <Lanes />
      <Axis />
    </article>
  );
}
