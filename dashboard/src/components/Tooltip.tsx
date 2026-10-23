// The one floating tooltip: the second under the pointer on the timeline, or
// one job count on the throughput curve.
import { useLayoutEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { S, useHoverVersion, type Tip } from '../store';
import { fmtMiB, fmtNum, fmtPct, fmtPff, pagesMiBs } from '../lib/format';
import { heatIndex } from '../lib/theme';
import { latestWS, runClockLabel } from '../view';

function Row({ val, label, color, box }: { val: ReactNode; label: ReactNode; color?: string; box?: boolean }) {
  const k: CSSProperties = color ? { background: color } : { visibility: 'hidden' };
  return <div className="tr"><i className={'k' + (box ? ' box' : '')} style={k} /><b>{val}</b><span>{label}</span></div>;
}

function frameTip(lane: string | null): ReactNode {
  const f = S.hover == null ? undefined : S.frames[S.hover];
  if (!f) return null;
  const C = S.colors;
  const rows: ReactNode[] = [];
  if (lane) {
    const j = f._jobs.get(lane);
    if (!j) rows.push(<Row key="none" val="–" label="not running yet" />);
    else {
      const st = j.state === 'run' ? 'Running' : j.state === 'park' ? 'Parked' : 'Done';
      const col = j.state === 'run' ? C.heat[heatIndex(j.pff)] : j.state === 'park' ? C.frostInk : C.done;
      rows.push(<Row key="state" val={st} label="" color={col} box />);
      if (j.state !== 'done') {
        rows.push(<Row key="res" val={`${fmtMiB(j.res)} MiB`} label={`resident, peak ${fmtMiB(j.peak)} MiB`} />);
        rows.push(<Row key="pff" val={fmtPff(j.pff)} label="faults per CPU-ms" />);
        const w = (latestWS(S.hover as number) || {})[lane];
        if (w && w.est != null) {
          rows.push(<Row key="ws" val={`${fmtMiB(w.est)} MiB`}
            label={w.m != null ? `working set, measured (footprint ${fmtMiB(w.fp)} MiB)` : 'working set, by footprint'} />);
        }
      }
      if (j.passes != null) rows.push(<Row key="passes" val={String(Math.floor(j.passes))} label={f.run && f.run.passes ? `of ${f.run.passes} passes` : 'passes'} />);
      rows.push(<Row key="cpu" val={`${j.cpu.toFixed(1)} s`} label="CPU time so far" />);
    }
  } else {
    rows.push(<Row key="full" val={`${fmtPct(f.psi.full)}%`} label="full stall" color={C.full} />);
    rows.push(<Row key="some" val={`${fmtPct(f.psi.some)}%`} label="some stall" color={C.some} />);
    rows.push(<Row key="in" val={`${fmtNum(pagesMiBs(f.rate.swpin))} MiB/s`} label="swap in" />);
    rows.push(<Row key="out" val={`${fmtNum(pagesMiBs(f.rate.swpout))} MiB/s`} label="swap out" />);
    rows.push(<Row key="jobs" val={`${f._n.run} / ${f._n.park}`} label="running / parked" />);
    const ev = (f.ev || []).filter((e) => e.kind === 'park' || e.kind === 'wake');
    if (ev.length) rows.push(<Row key="ev" val={String(ev.length)} label="controller decisions this second" />);
  }
  return (
    <>
      <div className="th"><span>{runClockLabel(f.t)}</span>{lane && <span>{lane}</span>}</div>
      {rows}
    </>
  );
}

function curveTip(tip: Extract<Tip, { kind: 'curve' }>): ReactNode {
  return (
    <>
      <div className="th"><span>N = {tip.n}</span><span>passes/s</span></div>
      {tip.items.length
        ? tip.items.map((c) => (
          <div className="tr" key={c.label}>
            <i className="k" style={{ background: c.color }} /><b>{fmtNum(c.med, 3)}</b>
            <span>{c.name} · {fmtNum(c.min, 3)}–{fmtNum(c.max, 3)}</span>
          </div>
        ))
        : <div className="tr">No runs at this N</div>}
    </>
  );
}

export function Tooltip() {
  useHoverVersion();
  const ref = useRef<HTMLDivElement>(null);
  const tip = S.tip;
  const body = !tip ? null : tip.kind === 'curve' ? curveTip(tip) : frameTip(tip.lane);
  // Beside the pointer, flipped to the other side near the window's edges.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !tip || !body) return;
    const tw = el.offsetWidth, th = el.offsetHeight;
    let left = tip.x + 16, top = tip.kind === 'curve' ? tip.y - 20 : tip.y + 14;
    if (left + tw > window.innerWidth - 8) left = tip.x - tw - 16;
    if (tip.kind === 'frame' && top + th > window.innerHeight - 8) top = tip.y - th - 14;
    el.style.left = Math.max(8, left) + 'px';
    el.style.top = Math.max(8, top) + 'px';
  });
  return <div className="tip" id="tip" ref={ref} hidden={!body}>{body}</div>;
}
