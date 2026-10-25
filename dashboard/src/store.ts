// The app's state lives outside React, like the data it shows: live and
// simulated frames arrive many times a second and are appended in place.
// Components subscribe to two counters and read the state directly:
// `version` moves when the data or a setting changes, `hoverVersion` when
// only the pointer does, so hovering redraws the canvases and nothing else.
import { useSyncExternalStore, type ReactNode } from 'react';
import type { Colors } from './lib/theme';
import type { Sim } from './sim/sim';
import type {
  EFrame, Policy, ProbeSummary, ResultRow, ResultsData, RunData, RunListing, RunMeta, RunMetrics, ServerInfo, View, WsMode,
} from './types';

export type Conn = 'idle' | 'connecting' | 'ok' | 'retry' | 'closed';

export interface CurveTipItem { label: string; name: string; color: string; med: number; min: number; max: number }

export type Tip =
  | { kind: 'frame'; x: number; y: number; lane: string | null }   // the timeline under the pointer
  | { kind: 'curve'; x: number; y: number; n: number; items: CurveTipItem[] };

export interface AppState {
  view: View | null;
  server: ServerInfo | null;
  booted: boolean;
  frames: EFrame[];            // the frames of the current view
  end: number;                 // index of the frame shown as "now"
  win: number;                 // seconds of history on screen; 0 = all
  hover: number | null;        // frame index under the pointer
  tip: Tip | null;
  notice: ReactNode;           // above the monitor
  resNotice: ReactNode;        // above the results
  showPsiTable: boolean;
  showCurveTable: boolean;
  colors: Colors;
  live: { es: EventSource | null; frames: EFrame[]; conn: Conn };
  replay: {
    runs: RunListing[] | null; error: string | null; id: string | null; loadedId: string | null;
    frames: EFrame[]; meta: RunMeta | null; metrics: RunMetrics | null; probe: ProbeSummary | null;
    timer: number | null; speed: number; loading: boolean; pos: number | null;
  };
  sim: {
    eng: Sim | null; frames: EFrame[]; timer: number | null; speed: number;
    policy: Policy; n: number; hot: number; ws: WsMode;
  };
  results: {
    data: ResultsData | null; loading: boolean; simRows: ResultRow[] | null; status: ReactNode;
    runs: RunListing[] | null;     // finished runs, as the server lists them
    sig: string;                   // signature of `runs`: what the poll compares
    sel: string | null;            // run picked in the card; null = follow the latest
    latest: RunData | null;        // the run shown in the "Latest run" card
    latestId: string | null;
    latestLoading: boolean;
    timer: number | null;          // the 4 s poll while the tab is open
    checkedAt: number | null;      // when the poll last looked (ms)
  };
  version: number;
  hoverVersion: number;
}

const noColors: Colors = {
  ink: '', ink2: '', ink3: '', grid: '', line: '', panel: '', panel2: '', accent: '',
  full: '', some: '', heat: [], frost: '', frostInk: '', done: '', track: '', series: [],
};

export const S: AppState = {
  view: null, server: null, booted: false,
  frames: [], end: -1, win: 300, hover: null, tip: null,
  notice: null, resNotice: null, showPsiTable: false, showCurveTable: false,
  colors: noColors,
  live: { es: null, frames: [], conn: 'idle' },
  replay: {
    runs: null, error: null, id: null, loadedId: null, frames: [], meta: null, metrics: null, probe: null,
    timer: null, speed: 4, loading: false, pos: null,
  },
  sim: { eng: null, frames: [], timer: null, speed: 4, policy: 'full', n: 8, hot: 1, ws: 'refs' },
  results: {
    data: null, loading: false, simRows: null, status: 'Loading results',
    runs: null, sig: '', sel: null, latest: null, latestId: null, latestLoading: false, timer: null, checkedAt: null,
  },
  version: 0, hoverVersion: 0,
};

const listeners = new Set<() => void>();
let raf = 0, dirty = false, hoverDirty = false;

function flush(): void {
  raf = 0;
  if (dirty) { S.version++; dirty = false; }
  if (hoverDirty) { S.hoverVersion++; hoverDirty = false; }
  for (const l of listeners) l();
}
function request(): void { if (!raf) raf = requestAnimationFrame(flush); }

/** Something the screen shows changed: re-render on the next frame. */
export function schedule(): void { dirty = true; request(); }
/** Only the pointer moved: redraw the canvases and the tooltip. */
export function scheduleHover(): void { hoverDirty = true; request(); }

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}
export const useVersion = (): number => useSyncExternalStore(subscribe, () => S.version);
export const useHoverVersion = (): number => useSyncExternalStore(subscribe, () => S.hoverVersion);
