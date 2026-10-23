import { S } from '../store';
import {
  scrubTo, selectRun, setReplaySpeed, setSimHot, setSimJobs, setSimPolicy, setSimSpeed, setSimWs, setWindow, simStart, togglePlay,
} from '../controller';
import { fmtClock } from '../lib/format';
import { IconPlay, IconRestart } from '../lib/icons';
import { CONFIG_NAMES } from '../view';
import { Seg } from './controls';
import type { Policy, RunListing, WsMode } from '../types';

const SPEEDS = [[1, '1×'], [4, '4×'], [16, '16×']] as const;
const WINDOWS = [[120, '2 min'], [300, '5 min'], [900, '15 min'], [0, 'All']] as const;
const POLICIES = [['eevdf', 'EEVDF only'], ['ctrl', 'Controller only'], ['full', 'Full STASIS']] as const satisfies readonly (readonly [Policy, string])[];
const HOT = [[1, '100%'], [0.6, '60%']] as const;
const WS = [['refs', 'Measured'], ['footprint', 'Footprint']] as const satisfies readonly (readonly [WsMode, string])[];

function LiveBar() {
  const f = S.frames[S.end];
  const r = f?.run;
  if (!f) return <div className="bar-group"><span className="bar-text">Waiting for the first sample</span></div>;
  if (!r) {
    return (
      <div className="bar-group">
        <span className="bar-text">No run in progress. Showing the whole machine; start one with <code>scripts/run_mix.sh</code>.</span>
      </div>
    );
  }
  const cfg = (r.config && CONFIG_NAMES[r.config]) || r.config || '?';
  const state = r.finished ? `finished, ${r.status || ''}` : `running ${fmtClock(f.t - (r.t0 ?? f.t))}`;
  return (
    <div className="bar-group">
      <span className="bar-text">
        Following <b className="mono">{r.id}</b> · <b>{cfg}</b> · N={r.n ?? '?'} · {r.size ?? '?'} MB × {r.passes ?? '?'} passes · {state}
      </span>
    </div>
  );
}

function groupRuns(runs: RunListing[]): [string, RunListing[]][] {
  const groups = new Map<string, RunListing[]>();
  for (const r of runs) {
    const g = groups.get(r.label);
    if (g) g.push(r); else groups.set(r.label, [r]);
  }
  return Array.from(groups);
}

function ReplayBar() {
  const R = S.replay, fr = S.frames, f = fr[S.end];
  const playing = R.timer != null;
  const r = f?.run;
  const clock = f && r && r.t0 ? `${fmtClock(f.t - r.t0)} / ${fmtClock(fr[fr.length - 1].t - r.t0)}` : fmtClock(Math.max(0, S.end));
  return (
    <div className="bar-group">
      <label className="bar-label" htmlFor="run-sel">Run</label>
      <select className="sel" id="run-sel" value={R.id ?? ''} onChange={(e) => selectRun(e.target.value)}>
        {groupRuns(R.runs || []).map(([label, rs]) => (
          <optgroup key={label} label={label}>
            {rs.map((x) => (
              <option key={x.id} value={x.id}>{`${label} · N=${x.n} · run ${x.run}${x.status !== 'OK' ? ' · ' + x.status : ''}`}</option>
            ))}
          </optgroup>
        ))}
      </select>
      <button className="btn primary" id="play" type="button" aria-label={playing ? 'Pause' : 'Play'} onClick={togglePlay}>
        <IconPlay playing={playing} /><span>{playing ? 'Pause' : 'Play'}</span>
      </button>
      <Seg id="rp-speed" label="Playback speed" options={SPEEDS} value={R.speed as 1 | 4 | 16} onPick={setReplaySpeed} />
      <input className="scrub" type="range" id="scrub" min={0} max={Math.max(0, fr.length - 1)} value={Math.max(0, S.end)}
        aria-label="Position in run" onChange={(e) => scrubTo(+e.target.value)} />
      <span className="bar-text mono">{clock}</span>
    </div>
  );
}

function SimBar() {
  const M = S.sim;
  return (
    <div className="bar-group">
      <span className="ctl">
        <span className="bar-label" id="jobs-l">Jobs</span>
        <div className="stepper" role="group" aria-labelledby="jobs-l">
          <button type="button" aria-label="Fewer jobs" onClick={() => setSimJobs(M.n - 1)}>−</button>
          <output aria-live="polite">{M.n}</output>
          <button type="button" aria-label="More jobs" onClick={() => setSimJobs(M.n + 1)}>+</button>
        </div>
      </span>
      <span className="ctl">
        <span className="bar-label">Policy</span>
        <Seg id="sim-policy" label="Policy" options={POLICIES} value={M.policy} onPick={setSimPolicy} />
      </span>
      <span className="ctl">
        <span className="bar-label">Hot memory</span>
        <Seg id="sim-hot" label="Share of each job's memory it keeps touching" options={HOT} value={M.hot as 1 | 0.6} onPick={setSimHot} />
      </span>
      <span className="ctl">
        <span className="bar-label">Working sets</span>
        <Seg id="sim-ws" label="Working-set estimate" options={WS} value={M.ws} onPick={setSimWs} />
      </span>
      <Seg id="sim-speed" label="Simulation speed" options={SPEEDS} value={M.speed as 1 | 4 | 16} onPick={setSimSpeed} />
      <button className="btn" type="button" onClick={() => simStart()}><IconRestart />Restart</button>
    </div>
  );
}

export function ControlBar() {
  return (
    <div className="bar">
      {S.view === 'live' && <LiveBar />}
      {S.view === 'replay' && <ReplayBar />}
      {S.view === 'sim' && <SimBar />}
      <span className="spacer" />
      <div className="bar-group">
        <span className="bar-label" id="win-l">Window</span>
        <Seg id="win" labelledBy="win-l" options={WINDOWS} value={S.win as 120 | 300 | 900 | 0} onPick={setWindow} />
      </div>
    </div>
  );
}
