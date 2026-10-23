import type { CSSProperties } from 'react';
import { S } from '../store';
import { clamp, fmtNum, fmtPct, pagesMiBs, sum } from '../lib/format';
import { IconCrit, IconIdle, IconOk, IconWarn } from '../lib/icons';
import { passesPerMin } from '../view';

function Spark({ vals, color }: { vals: number[]; color: string }) {
  const w = 200, h = 28;
  if (vals.length < 2) return <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true" />;
  const mx = Math.max(1e-9, ...vals);
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 2 - (v / mx) * (h - 5)] as const);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');
  const last = pts[pts.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={d + `L${w} ${h}L0 ${h}Z`} fill={color} fillOpacity="0.10" stroke="none" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={2.4} fill={color} />
    </svg>
  );
}

export function Kpis() {
  const fr = S.frames, end = S.end, f = fr[end];
  if (!f) {
    return (
      <div className="kpis">
        <div className="hero">
          <div className="meter" aria-hidden="true"><i style={{ height: '0%' }} /></div>
          <div className="hv">
            <div className="k-label"><span>Memory stall · all tasks waiting</span><span className="status neutral"><IconIdle />Idle</span></div>
            <div className="k-value">–</div>
            <div className="k-sub">PSI full, last second</div>
          </div>
        </div>
        {['Jobs', 'Throughput', 'Swap traffic', 'Processes killed'].map((l) => (
          <div className="tile" key={l}><div className="k-label">{l}</div><div className="k-value">–</div><div className="k-sub" /></div>
        ))}
      </div>
    );
  }
  // hero: PSI full over the last second; state from the last 5 s so it doesn't flicker
  const recent = fr.slice(Math.max(0, end - 4), end + 1).map((x) => x.psi.full);
  const avg5 = sum(recent) / recent.length;
  const running = f._n.run + f._n.park > 0;
  let status = { cls: 'good', text: 'Calm', Icon: IconOk, glow: 'transparent' };
  if (avg5 >= 10) status = { cls: 'critical pulse', text: 'Thrashing', Icon: IconCrit, glow: 'color-mix(in srgb, var(--psi-full) 22%, transparent)' };
  else if (avg5 >= 1) status = { cls: 'warning', text: 'Under pressure', Icon: IconWarn, glow: 'color-mix(in srgb, var(--warning) 12%, transparent)' };
  else if (!running) status = { cls: 'neutral', text: 'Idle', Icon: IconIdle, glow: 'transparent' };
  const n = f._n, total = n.run + n.park + n.done;
  const ppm = passesPerMin(end);
  const thru: number[] = [];
  for (let i = Math.max(1, end - 119); i <= end; i += 2) thru.push(passesPerMin(i, 15) ?? 0);
  const swap: number[] = [];
  for (let i = Math.max(0, end - 119); i <= end; i++) swap.push(pagesMiBs(fr[i].rate.swpin + fr[i].rate.swpout));
  const kills = f.oom || 0;
  const C = S.colors;
  return (
    <div className="kpis">
      <div className="hero" style={{ '--hero-glow': status.glow } as CSSProperties}>
        <div className="meter" aria-hidden="true"><i style={{ height: clamp(f.psi.full, 0, 100) + '%' }} /></div>
        <div className="hv">
          <div className="k-label">
            <span>Memory stall · all tasks waiting</span>
            <span className={'status ' + status.cls}><status.Icon />{status.text}</span>
          </div>
          <div className="k-value">{fmtPct(f.psi.full)}<small>%</small></div>
          <div className="k-sub">Some <b>{fmtPct(f.psi.some)}%</b> · kernel avg10: full {fmtPct(f.psi.full10)}%, some {fmtPct(f.psi.some10)}%</div>
        </div>
      </div>
      <div className="tile">
        <div className="k-label">Jobs</div>
        <div className="k-value">{total ? <>{n.run}<small>running</small></> : '0'}</div>
        <div className="k-sub">{total ? `${n.park} parked · ${n.done} done` : 'No jobs in stasis.slice'}</div>
        <div className="minibar">
          {total > 0 && ([['run', n.run], ['park', n.park], ['done', n.done]] as const).map(([k, v]) => (v ? <i key={k} className={k} style={{ flex: v }} /> : null))}
        </div>
      </div>
      <div className="tile">
        <div className="k-label">Throughput</div>
        <div className="k-value">{ppm == null ? '–' : <>{fmtNum(ppm)}<small>passes/min</small></>}</div>
        <div className="k-sub">{Math.floor(n.passes).toLocaleString('en-US')} passes in total · last 30 s</div>
        <Spark vals={thru} color={C.accent} />
      </div>
      <div className="tile">
        <div className="k-label">Swap traffic</div>
        <div className="k-value">{fmtNum(pagesMiBs(f.rate.swpin + f.rate.swpout))}<small>MiB/s</small></div>
        <div className="k-sub">in {fmtNum(pagesMiBs(f.rate.swpin))} · out {fmtNum(pagesMiBs(f.rate.swpout))} · {fmtNum(f.rate.majflt)} major faults/s</div>
        <Spark vals={swap} color={C.full} />
      </div>
      <div className="tile">
        <div className="k-label">Processes killed</div>
        <div className="k-value">{kills}</div>
        <div className="k-sub">
          {kills
            ? <span className="status critical"><IconCrit />{kills} OOM kill{kills > 1 ? 's' : ''}</span>
            : <span className="status good"><IconOk />None</span>}
        </div>
      </div>
    </div>
  );
}
