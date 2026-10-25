// The rail beside the timeline: the replayed run's summary, stasisd's
// decisions in plain words, the scheduler and memory.
import { useMemo, type ReactNode } from 'react';
import { S } from '../store';
import { bsearchT, clamp, fmtBytes, fmtClock, fmtNum, fmtPff, fmtUs } from '../lib/format';
import { IconIdle, IconOk, IconWarn } from '../lib/icons';
import { WHY, firstT, groupEvents, waitedText, type Group } from '../view';
import type { DashEvent, EFrame } from '../types';

function RunSummary() {
  const R = S.replay, m = R.metrics || {}, p = R.probe;
  const cell = (l: string, v: ReactNode) => <div><div className="l">{l}</div><div className="v">{v}</div></div>;
  return (
    <article className="card" id="card-run">
      <div className="card-h"><h2>Run summary</h2><span className="meta">{R.meta ? R.meta.status || '' : ''}</span></div>
      <div className="summary">
        {cell('Throughput', m.throughput != null ? <>{m.throughput.toFixed(3)} <span className="small">passes/s</span></> : '–')}
        {cell('Processes killed', m.kills != null ? String(m.kills) : '–')}
        {cell('Jain fairness', m.jain != null ? m.jain.toFixed(3) : '–')}
        {cell('Probe p99 latency', p && p.p99 != null ? fmtUs(p.p99) : '–')}
        {cell('Time to first park', m.ttfp_s != null ? <>{m.ttfp_s.toFixed(1)} <span className="small">s</span></> : '–')}
        {cell('Swap traffic', m.swap_pages != null ? fmtBytes(m.swap_pages * 4096) : '–')}
        {cell('Longest park', m.max_wait_s != null ? <>{Math.round(m.max_wait_s)} <span className="small">s</span></> : '–')}
        {cell('Working sets', m.ws === 'refs' ? 'Measured' : m.ws === 'footprint' ? 'Footprint' : '–')}
      </div>
    </article>
  );
}

export function describe(g: Group): { tag: [string, string]; title: string; text: string } {
  switch (g.kind) {
    case 'halve': {
      const text = g.pff && g.pff.pff != null
        ? `${g.pff.job} faulted ${fmtPff(g.pff.pff)} times per CPU-ms without its memory growing. Halved before PSI fired.`
        : WHY[g.why] || g.why;
      return { tag: ['halve', 'Halve'], title: `Parked ${g.jobs.join(', ')}`, text };
    }
    case 'rotate':
      return { tag: ['rotate', 'Rotate'], title: [g.out && `${g.out} out`, g.in && `${g.in} in`].filter(Boolean).join(', '),
        text: `Its turn was up: the job with the most of its turn out, the longest-waiting in${waitedText(g.waited)}` };
    case 'bound':
      return { tag: ['rotate', 'Bound'], title: [g.in && `${g.in} in`, g.out && `${g.out} out`].filter(Boolean).join(', '),
        text: `Starvation bound: ${g.in || 'a job'} had waited${g.waited != null ? ` ${Math.round(g.waited)} s` : ' too long'}, so it went in without waiting for a turn to end` };
    case 'wsreset':
      return { tag: ['park', 'Re-measure'], title: `Working sets of ${g.jobs.join(', ')} forgotten`,
        text: 'The set fitted by its measured working sets yet thrashed, so footprints stand in until they are measured again' };
    case 'refused':
      return { tag: ['stop', 'Refused'], title: `Not managing ${g.jobs.join(', ')}`, text: 'Its cgroup contains stasisd itself' };
    case 'wake':
      return { tag: ['wake', 'Admit'], title: `Thawed ${g.jobs.join(', ')}`, text: (WHY[g.why] || g.why) + waitedText(g.waited) };
    case 'stop':
      return { tag: ['stop', 'Stop'], title: `Thawed ${g.jobs.length} job${g.jobs.length > 1 ? 's' : ''}`, text: WHY.shutdown };
    case 'reclaim':
      return { tag: ['park', 'Page out'], title: `Paging out ${g.jobs.join(', ')} (${fmtBytes(g.bytes)})`,
        text: 'Swapping out parked memory so the running jobs have room' };
    case 'start': {
      const e = g.e;
      return { tag: ['start', 'Start'], title: `stasisd started, ${e.signal || 'memstat'} signal`,
        text: (e.signal === 'bpf' ? `PSI trigger plus PFF early warning (PFF_HI ${e.pff_hi ?? '?'})` : 'PSI trigger only') +
          (e.ws ? `; working sets ${e.ws === 'refs' ? 'measured' : 'from footprints'}` : '') +
          (e.rotation === false ? '; no rotation' : e.max_wait ? `; starvation bound ${Math.round(e.max_wait)} s` : '') };
    }
    case 'park':
      return { tag: ['park', 'Park'], title: `Parked ${g.jobs.join(', ')}`, text: g.why || '' };
  }
}

interface Feed {
  groups: Group[]; t0: number; nPark: number; nWake: number; total: number;
  psiN: number; pffN: number; warmN: number; firstSrc: string | null; hold: Record<string, number>;
}

function buildFeed(fr: EFrame[], end: number): Feed {
  const evs: DashEvent[] = [];
  let psiN = 0, pffN = 0, warmN = 0, firstSrc: string | null = null;
  const hold: Record<string, number> = { growing: 0, reloading: 0, 'making-room': 0 };
  const from = Math.max(0, bsearchT(fr, firstT()));
  for (let i = from; i <= end; i++) for (const e of fr[i].ev || []) {
    evs.push(e);
    if (e.kind === 'pressure') {
      if (e.src === 'pff') pffN++; else psiN++;
      if (e.warming) { warmN++; if (e.hold && e.hold in hold) hold[e.hold]++; }
      if (!firstSrc) firstSrc = e.src;
    }
  }
  const f = fr[end];
  return {
    groups: groupEvents(evs).reverse().slice(0, 80),
    t0: f.run && f.run.t0 ? f.run.t0 : fr[0].t,
    nPark: evs.filter((e) => e.kind === 'park').length, nWake: evs.filter((e) => e.kind === 'wake').length, total: evs.length,
    psiN, pffN, warmN, firstSrc, hold,
  };
}

function Decisions() {
  const fr = S.frames, end = S.end;
  const f = fr[end];
  // Rebuild the feed only when the events in view change, not on every frame.
  let count = 0, lastT = 0;
  const from = Math.max(0, bsearchT(fr, firstT()));
  for (let i = from; i <= end; i++) { const ev = fr[i].ev; if (ev && ev.length) { count += ev.length; lastT = ev[ev.length - 1].t; } }
  const key = `${S.view}:${count}:${lastT}:${f.run && f.run.t0 ? f.run.t0 : fr[0].t}:${S.replay.loadedId ?? ''}`;
  const feed = useMemo(() => buildFeed(fr, end), [key]);   // `key` is everything the feed depends on
  const holds: [number, string][] = [
    [feed.hold.growing, 'memory still growing'], [feed.hold.reloading, 'pages still reloading'], [feed.hold['making-room'], 'room still being made'],
  ];
  return (
    <article className="card">
      <div className="card-h"><h2>Controller decisions</h2><span className="meta" id="ev-meta">{feed.total ? `${feed.nPark} parks · ${feed.nWake} wakes` : ''}</span></div>
      <ol className="ev-list" id="ev-list" aria-live="polite">
        {!feed.groups.length && (
          <li className="ev-empty">
            {f.ctrl && f.ctrl.on ? 'No decisions yet. stasisd acts when memory pressure appears.'
              : 'stasisd is not running, so nothing gets parked. Under EEVDF alone every job keeps competing for memory.'}
          </li>
        )}
        {feed.groups.map((g, i) => {
          const d = describe(g);
          return (
            <li className="ev" key={`${g.t}:${i}`}>
              <time>{fmtClock(g.t - feed.t0)}</time>
              <span className={'tag ' + d.tag[0]}>{d.tag[1]}</span>
              <div className="b"><b>{d.title}</b><span>{d.text}</span></div>
            </li>
          );
        })}
      </ol>
      {feed.psiN + feed.pffN > 0 && (
        <div className="ev-sum" id="ev-sum">
          Pressure signals: <b>{feed.psiN}</b> from PSI, <b>{feed.pffN}</b> from PFF. First signal came
          from <b>{feed.firstSrc === 'pff' ? 'PFF' : 'PSI'}</b>.
          {feed.warmN > 0 && (
            <>
              {' '}<b>{feed.warmN}</b> were warm-up, not thrashing, so nothing was halved
              {holds.some((h) => h[0]) && (
                <> ({holds.filter((h) => h[0]).map((h, i) => <span key={h[1]}>{i ? ', ' : ''}<b>{h[0]}</b> {h[1]}</span>)})</>
              )}
              .
            </>
          )}
        </div>
      )}
    </article>
  );
}

function Scheduler({ f }: { f: EFrame }) {
  const sd = f.sched || {}, cf = f.ctrl || {};
  const on = sd.state === 'enabled';
  const name = on ? (sd.ops === 'stasis' ? 'scx_stasis' : 'scx_' + (sd.ops || '?')) : 'EEVDF';
  const rows: [string, number | undefined][] = [['Shared vtime queue', sd.shared], ['Priority queue (protected tasks)', sd.prio], ['Direct to an idle CPU', sd.local]];
  const mx = Math.max(1, ...rows.map((r) => r[1] || 0));
  return (
    <article className="card">
      <div className="card-h"><h2>Scheduler</h2><span className="meta">sched_ext</span></div>
      <div className="kv">
        <div className="sched-head">
          <span className="sched-name">{name}</span>
          {on ? <span className="status good"><IconOk />Attached</span>
            : sd.state === 'absent' ? <span className="status warning"><IconWarn />No sched_ext</span>
              : <span className="status neutral"><IconIdle />Kernel default</span>}
        </div>
        {on && sd.ops === 'stasis' && sd.shared != null
          ? rows.map(([l, v]) => (
            <div className="kv-row" key={l}>
              <span className="l">{l}</span>
              <span className="v">{fmtNum(v || 0)}<span className="small"> /s</span></span>
              <span className="bar2"><i style={{ width: ((v || 0) / mx) * 100 + '%' }} /></span>
            </div>
          ))
          : on
            ? <div className="small">{S.view === 'sim' ? 'The simulator models scheduling decisions, not dispatch counts.' : 'Dispatch counters appear when scx_stasis is loaded.'}</div>
            : <div className="small">{S.view === 'sim' ? "In this policy the kernel's own scheduler runs every job." : 'No sched_ext scheduler is attached.'} Fault frequency still comes from cgroup counters.</div>}
        <div className="kv-row"><span className="l">Load controller</span><span className="v">{cf.on ? 'stasisd · ' + (cf.signal || '?') : 'off'}</span></div>
      </div>
    </article>
  );
}

function Memory({ f }: { f: EFrame }) {
  const m = f.mem || {};
  if (!m.total) {
    return (
      <article className="card">
        <div className="card-h"><h2>Memory</h2><span className="meta" /></div>
        <div className="kv"><div className="small">Memory totals were not recorded for this run.</div></div>
      </article>
    );
  }
  const total = m.total;
  const jobs = f._n.res;
  const avail = m.avail ?? 0;
  const other = Math.max(0, total - avail - jobs);
  const pct = (v: number) => clamp((v / total) * 100, 0, 100);
  const swapTotal = m.swap_total || 0;
  const swapUsed = swapTotal - (m.swap_free || 0);
  return (
    <article className="card">
      <div className="card-h"><h2>Memory</h2><span className="meta">{fmtBytes(total)} RAM</span></div>
      <div className="kv">
        <div className="stack" role="img" aria-label={`RAM: ${fmtBytes(jobs)} jobs, ${fmtBytes(other)} other, ${fmtBytes(avail)} available`}>
          <i className="jobs" style={{ width: pct(jobs) + '%' }} /><i className="other" style={{ width: pct(other) + '%' }} />
        </div>
        <div className="mem-legend">
          <span><i className="key-box" style={{ background: 'var(--heat-1)' }} />Jobs <b>{fmtBytes(jobs)}</b></span>
          <span><i className="key-box" style={{ background: 'var(--ink-3)' }} />Other <b>{fmtBytes(other)}</b></span>
          <span><i className="key-box" style={{ background: 'var(--track)', boxShadow: 'inset 0 0 0 1px var(--line)' }} />Available <b>{fmtBytes(avail)}</b></span>
        </div>
        {swapTotal
          ? (
            <div className="kv-row">
              <span className="l">Swap used</span>
              <span className="v">{fmtBytes(swapUsed)} <span className="small">of {fmtBytes(swapTotal)}</span></span>
              <span className="bar2"><i style={{ width: clamp((swapUsed / swapTotal) * 100, 0, 100) + '%', background: 'var(--frost-ink)' }} /></span>
            </div>
          )
          : <div className="small">No swap configured.</div>}
      </div>
    </article>
  );
}

export function Rail() {
  const f = S.frames[S.end];
  return (
    <aside className="rail">
      {S.view === 'replay' && <RunSummary />}
      {f ? <Decisions /> : (
        <article className="card">
          <div className="card-h"><h2>Controller decisions</h2><span className="meta" id="ev-meta" /></div>
          <ol className="ev-list" id="ev-list" aria-live="polite" />
        </article>
      )}
      {f && <Scheduler f={f} />}
      {f && <Memory f={f} />}
    </aside>
  );
}
