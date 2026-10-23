// Everything that changes state: switching views, the live stream, replay,
// the simulator, results, hover and the theme. Components call these; they
// mutate S and schedule a render.
import type { ReactNode } from 'react';
import { S, schedule, scheduleHover } from './store';
import { Sim, simResults } from './sim/sim';
import { bsearchT, clamp, enrich } from './lib/format';
import { readColors } from './lib/theme';
import { viewWindow } from './view';
import type { Frame, Policy, ResultsData, RunData, RunListing, ServerInfo, View, WsMode } from './types';

const VIEWS: View[] = ['live', 'replay', 'results', 'sim'];
const MAX_FRAMES = 3600;

export const needsServer = (v: View | null): boolean => (v === 'live' || v === 'replay') && !S.server;
export const monitorShown = (): boolean => S.view !== null && S.view !== 'results' && !needsServer(S.view);

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json() as Promise<T>;
}
const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function showNotice(node: ReactNode): void { S.notice = node; schedule(); }
export function hideNotice(): void { S.notice = null; schedule(); }

// ---- views

export function setView(want: string): void {
  const v: View = (VIEWS as string[]).includes(want) ? (want as View) : S.server ? 'live' : 'sim';
  S.view = v;
  if (location.hash !== '#' + v) {
    try { history.replaceState(null, '', '#' + v); } catch { /* sandboxed */ }
  }
  S.notice = null;
  S.hover = null; S.tip = null;
  if (v !== 'live') stopLive();
  if (v !== 'sim') simStop();
  if (v !== 'replay') stopPlay();
  if (needsServer(v)) { schedule(); return; }
  if (v === 'live') {
    S.frames = S.live.frames; S.end = S.frames.length - 1;
    startLive();
  } else if (v === 'sim') {
    if (!S.sim.eng) simStart();
    S.frames = S.sim.frames; S.end = S.frames.length - 1;
    simRun();
    S.notice = (
      <>
        <b>Simulated machine.</b> 4 GiB of RAM, 4 CPUs, 700 MiB random-access jobs, and stasisd's control law step for
        step. It shows the mechanism; the numbers are a model, not a measurement. Switch the policy
        to <b>EEVDF only</b> to watch the machine thrash; set <b>Hot memory</b> to 60% to see measured working sets
        admit jobs that footprints would park.
      </>
    );
  } else if (v === 'replay') {
    S.frames = S.replay.frames; S.end = S.replay.pos ?? S.frames.length - 1;
    void enterReplay();
  } else {
    void enterResults();
  }
  schedule();
}

export function setWindow(v: number): void { S.win = v; schedule(); }

export function togglePsiTable(): void { S.showPsiTable = !S.showPsiTable; schedule(); }
export function toggleCurveTable(): void { S.showCurveTable = !S.showCurveTable; schedule(); }

// ---- live

function setConn(c: typeof S.live.conn): void { S.live.conn = c; schedule(); }

function startLive(): void {
  if (S.live.es || !S.server) return;
  const es = new EventSource('api/stream');
  S.live.es = es;
  setConn('connecting');
  es.addEventListener('history', (e) => {
    S.live.frames = (JSON.parse((e as MessageEvent<string>).data) as Frame[]).map(enrich);
    setConn('ok');
    if (S.view === 'live') { S.frames = S.live.frames; S.end = S.frames.length - 1; schedule(); }
  });
  es.addEventListener('frame', (e) => {
    const f = enrich(JSON.parse((e as MessageEvent<string>).data) as Frame);
    const fr = S.live.frames;
    fr.push(f);
    if (fr.length > MAX_FRAMES) fr.splice(0, fr.length - MAX_FRAMES);
    setConn('ok');
    if (S.view === 'live') { S.frames = fr; S.end = fr.length - 1; schedule(); }
  });
  es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? 'closed' : 'retry');
}
function stopLive(): void {
  if (S.live.es) { S.live.es.close(); S.live.es = null; }
}

// ---- replay

async function enterReplay(): Promise<void> {
  const R = S.replay;
  if (!R.runs) {
    try { R.runs = (await getJSON<{ runs: RunListing[] }>('api/runs')).runs; }
    catch (e) { R.runs = []; R.error = message(e); }
    schedule();
  }
  if (S.view !== 'replay') return;
  if (!R.runs.length) {
    S.frames = []; S.end = -1;
    showNotice(S.server && S.server.results
      ? <>No finished runs yet. Runs appear here as soon as <code>sweep.sh</code> or <code>campaign.sh</code> completes one.</>
      : <>Replay needs recorded runs. Restart the server with <code>--results /srv/results</code> (the directory your sweeps write to).</>);
    return;
  }
  if (!R.id) {
    const pick = R.runs.find((r) => r.label === 'stasis' && r.n === 8) || R.runs[R.runs.length - 1];
    R.id = pick.id;
  }
  if (!R.frames.length || R.loadedId !== R.id) await loadRun(R.id);
  else { S.frames = R.frames; S.end = R.pos ?? R.frames.length - 1; schedule(); }
}

export function selectRun(id: string): void {
  S.replay.id = id; S.replay.pos = null;
  void loadRun(id);
}

async function loadRun(id: string): Promise<void> {
  const R = S.replay;
  stopPlay();
  R.loading = true;
  showNotice(<>Loading <b>{id}</b></>);
  try {
    const d = await getJSON<RunData>('api/run?id=' + encodeURIComponent(id));
    R.frames = d.frames.map(enrich); R.meta = d.meta; R.metrics = d.metrics; R.probe = d.probe; R.loadedId = id;
    S.notice = null;
  } catch (e) {
    R.frames = [];
    S.notice = <>Could not load {id}: {message(e)}</>;
  }
  R.loading = false;
  if (S.view !== 'replay') { schedule(); return; }
  S.frames = R.frames; S.end = R.frames.length - 1; R.pos = S.end;
  S.win = 0;
  schedule();
}

export function stopPlay(): void {
  const R = S.replay;
  if (R.timer != null) { clearInterval(R.timer); R.timer = null; schedule(); }
}
export function startPlay(): void {
  const R = S.replay;
  if (!R.frames.length) return;
  if (S.end >= R.frames.length - 1) S.end = 0;
  if (S.win === 0) S.win = 300;
  R.timer = window.setInterval(() => {
    if (S.view !== 'replay') return;
    if (S.end >= R.frames.length - 1) { stopPlay(); return; }
    S.end++; R.pos = S.end; schedule();
  }, 1000 / R.speed);
  schedule();
}
export function togglePlay(): void { if (S.replay.timer != null) stopPlay(); else startPlay(); }
export function setReplaySpeed(v: number): void {
  S.replay.speed = v;
  if (S.replay.timer != null) { stopPlay(); startPlay(); }
  schedule();
}
export function scrubTo(i: number): void {
  stopPlay();
  S.end = i; S.replay.pos = i;
  schedule();
}

// ---- simulator

export function simStart(preroll = 90): void {
  const M = S.sim;
  M.eng = new Sim(M.n, M.policy, 11 + M.n, Date.now() / 1000 - preroll, { hotFrac: M.hot, wsMode: M.ws });
  M.frames = [];
  for (let i = 0; i < preroll; i++) M.frames.push(enrich(M.eng.frame()));
  if (S.view === 'sim') { S.frames = M.frames; S.end = M.frames.length - 1; schedule(); }
}
function simTick(): void {
  const M = S.sim;
  if (!M.eng || S.view !== 'sim') return;
  M.frames.push(enrich(M.eng.frame()));
  if (M.frames.length > MAX_FRAMES) M.frames.splice(0, M.frames.length - MAX_FRAMES);
  S.frames = M.frames; S.end = M.frames.length - 1;
  schedule();
}
export function simRun(): void {
  const M = S.sim;
  if (M.timer != null) clearInterval(M.timer);
  M.timer = window.setInterval(simTick, 1000 / M.speed);
}
export function simStop(): void {
  if (S.sim.timer != null) { clearInterval(S.sim.timer); S.sim.timer = null; }
}
export function setSimSpeed(v: number): void { S.sim.speed = v; if (S.view === 'sim') simRun(); schedule(); }
export function setSimPolicy(p: Policy): void { S.sim.policy = p; S.sim.eng?.setPolicy(p); schedule(); }
export function setSimHot(v: number): void { S.sim.hot = v; simStart(); }
export function setSimWs(v: WsMode): void { S.sim.ws = v; simStart(); }
export function setSimJobs(n: number): void { S.sim.n = clamp(n, 1, 12); simStart(); }

// ---- results

export async function enterResults(): Promise<void> {
  const RS = S.results;
  if (RS.loading) return;
  S.resNotice = null;
  if (S.server && S.server.results) {
    RS.loading = true;
    RS.status = 'Loading results…';
    schedule();
    try { RS.data = await getJSON<ResultsData>('api/results'); }
    catch (e) { RS.data = { rows: [], score: [], score_error: message(e) }; }
    RS.loading = false;
    RS.status = <>Measured runs from the server · <b>{RS.data.rows.length}</b> finished runs</>;
  } else {
    if (S.server) {
      S.resNotice = <>The server was started without <code>--results</code>, so these are simulated. Restart it
        with <code>--results /srv/results</code> to see your measured runs.</>;
    }
    if (!RS.simRows) {
      RS.loading = true;
      RS.simRows = await simResults((i, n) => {
        RS.status = <>Simulating 72 runs of the model… <b>{Math.round((i / n) * 100)}%</b></>;
        schedule();
      });
      RS.loading = false;
    }
    RS.status = <><b>Simulated example</b> · 3 policies × N = 1–8 × 3 seeds, from the model in the Simulator tab. Not
      measurements. In the model, controller-only and full STASIS draw one line: identical jobs trip PSI and the PFF
      warning together.</>;
  }
  schedule();
}

// ---- hover and tooltip

function frameAt(cv: HTMLElement, clientX: number): number {
  const r = cv.getBoundingClientRect();
  const vw = viewWindow();
  const t = vw.t0 + ((clientX - r.left) / Math.max(1, r.width)) * (vw.t1 - vw.t0);
  let i = bsearchT(S.frames, t);
  if (i > vw.i1) i = vw.i1;
  if (i > 0 && Math.abs(S.frames[i - 1].t - t) < Math.abs(S.frames[i].t - t)) i--;
  return Math.max(vw.i0, i);
}
export function hoverAt(cv: HTMLElement, clientX: number, clientY: number, lane: string | null): void {
  if (!S.frames.length || S.end < 0) return;
  S.hover = frameAt(cv, clientX);
  S.tip = { kind: 'frame', x: clientX, y: clientY, lane };
  scheduleHover();
}
export function hoverStep(cv: HTMLElement, dir: -1 | 1, lane: string | null): void {
  if (!S.frames.length || S.end < 0) return;
  const vw = viewWindow();
  S.hover = clamp((S.hover ?? vw.i1) + dir, vw.i0, vw.i1);
  const r = cv.getBoundingClientRect();
  S.tip = { kind: 'frame', x: r.left + vw.X(S.frames[S.hover].t, r.width), y: r.top + r.height / 2, lane };
  scheduleHover();
}
export function hoverEnd(): void {
  if (S.hover == null && S.tip == null) return;
  S.hover = null; S.tip = null;
  scheduleHover();
}
export function showCurveTip(tip: Extract<typeof S.tip, { kind: 'curve' }>): void { S.tip = tip; scheduleHover(); }

// ---- theme and boot

export function toggleTheme(): void {
  const root = document.documentElement;
  const cur = root.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  const next = cur === 'light' ? 'dark' : 'light';
  root.dataset.theme = next;
  try { localStorage.setItem('stasis-theme', next); } catch { /* storage blocked */ }
}

/** Canvas colours follow the theme: the OS setting, the viewer's toggle or ours. */
function watchTheme(): void {
  const update = () => { S.colors = readColors(); schedule(); };
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', update);
  new MutationObserver(update).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

async function probeServer(): Promise<ServerInfo | null> {
  // Only server.py marks the pages it serves; anywhere else (a file, the
  // published preview) there is no API to ask, so don't log a failed request.
  if (!document.querySelector('meta[name="stasis-server"]')) return null;
  try {
    const ac = new AbortController();
    const to = setTimeout(() => ac.abort(), 2500);
    const r = await fetch('api/info', { cache: 'no-store', signal: ac.signal });
    clearTimeout(to);
    if (!r.ok) return null;
    const j = (await r.json()) as ServerInfo | null;
    return j && j.server === 'stasis' ? j : null;
  } catch {
    return null;
  }
}

export async function boot(): Promise<void> {
  S.colors = readColors();
  watchTheme();
  window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (v && v !== S.view) setView(v); });
  document.addEventListener('visibilitychange', () => {
    if (S.view !== 'sim') return;
    if (document.hidden) simStop(); else simRun();
  });
  if (document.fonts && document.fonts.ready) void document.fonts.ready.then(() => schedule());
  S.server = await probeServer();
  if (S.server) {
    try { const t = localStorage.getItem('stasis-theme'); if (t) document.documentElement.dataset.theme = t; } catch { /* storage blocked */ }
    S.colors = readColors();
  }
  S.booted = true;
  setView(location.hash.slice(1));
}
