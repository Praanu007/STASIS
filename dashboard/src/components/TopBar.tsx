import type { ReactNode } from 'react';
import { S } from '../store';
import { monitorShown, setView, toggleTheme } from '../controller';
import { IconTheme, Logo } from '../lib/icons';
import type { View } from '../types';

const TABS: [View, string][] = [['live', 'Live'], ['replay', 'Replay'], ['results', 'Results'], ['sim', 'Simulator']];

function Pill({ dot, children }: { dot: string; children: ReactNode }) {
  return <span className="pill"><i className={'dot ' + dot} /><span>{children}</span></span>;
}

function SourcePill() {
  const v = S.view;
  if (v === 'sim') return <Pill dot="frost"><b>Simulated</b></Pill>;
  if (v === 'replay') return <Pill dot="">{S.replay.meta ? <>Replay <b>{S.replay.meta.id}</b></> : <b>Replay</b>}</Pill>;
  if (v === 'results') return <Pill dot=""><b>{S.server ? 'Results' : 'Simulated results'}</b></Pill>;
  if (!S.server) return <Pill dot=""><b>{S.booted ? 'No server' : 'Starting'}</b></Pill>;
  const c = S.live.conn;
  return (
    <Pill dot={c === 'ok' ? 'good' : c === 'retry' ? 'warning' : ''}>
      {c === 'ok' ? 'Live' : c === 'retry' ? 'Reconnecting' : 'Connecting'} <b>{S.server.host || ''}</b>
    </Pill>
  );
}

export function TopBar() {
  const f = S.frames[S.end];
  const showSystem = !!f && monitorShown();
  const sd = f?.sched || {};
  const cf = f?.ctrl || {};
  return (
    <header className="top">
      <div className="top-in">
        <a className="brand" href="#" title="Scheduling To Avert Swap-Induced Stalls"
          onClick={(e) => { e.preventDefault(); setView(S.server ? 'live' : 'sim'); }}>
          <Logo />
          <span className="word">STASIS</span>
          <span className="sub">Control Room</span>
        </a>
        <nav className="tabs" role="tablist" aria-label="Views">
          {TABS.map(([v, label]) => (
            <button key={v} type="button" role="tab" id={'tab-' + v} aria-selected={S.view === v} onClick={() => setView(v)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="pills">
          <SourcePill />
          {showSystem && (sd.state === 'enabled'
            ? <Pill dot={sd.ops === 'stasis' ? 'good' : 'frost'}>sched_ext <b>{sd.ops === 'stasis' ? 'scx_stasis' : 'scx_' + (sd.ops || '?')}</b></Pill>
            : <Pill dot="">Scheduler <b>EEVDF</b></Pill>)}
          {showSystem && (cf.on
            ? <Pill dot="good">stasisd <b>{cf.signal || ''}</b></Pill>
            : <Pill dot="">stasisd <b>off</b></Pill>)}
          {S.server && (
            <button className="icon-btn" type="button" aria-label="Switch between light and dark" onClick={toggleTheme}>
              <IconTheme />
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
