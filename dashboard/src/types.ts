// Data as the STASIS server (dashboard/server.py) and the simulator produce it.

export type View = 'live' | 'replay' | 'results' | 'sim';
export type JobState = 'run' | 'park' | 'done';
export type Hold = 'growing' | 'reloading' | 'making-room';
export type WsMode = 'refs' | 'footprint';
export type Policy = 'eevdf' | 'ctrl' | 'full';

export interface FrameJob {
  id: string;
  state: JobState;
  res: number;              // resident bytes
  peak: number;             // peak resident bytes
  pff: number | null;       // major faults per ms of the job's own CPU
  cpu: number;              // CPU seconds so far
  passes: number | null;
}

export interface WsEstimate {
  fp: number | null;        // footprint, bytes
  est: number | null;       // the estimate stasisd uses, bytes
  m: number | null;         // measured working set, bytes (null: not measured)
}

export type DashEvent =
  | { t: number; kind: 'park' | 'wake'; job: string; why: string; waited?: number | null }
  | { t: number; kind: 'pressure'; src: string; active: number | null; parked: number | null; warming: boolean; hold: Hold | null }
  | { t: number; kind: 'pff'; job: string; pff: number | null; growth: number | null }
  | { t: number; kind: 'reclaim'; job: string; bytes: number | null }
  | { t: number; kind: 'ws'; jobs: Record<string, WsEstimate> }
  | { t: number; kind: 'wsreset'; jobs: string[] }
  | { t: number; kind: 'refused'; job: string }
  | {
      t: number; kind: 'start'; signal: string | null; pff_hi: number | null; quantum: number | null;
      ws: WsMode | null; max_wait: number | null; rotation: boolean;
    };

export interface RunInfo {
  id: string;
  config?: string;
  n?: number | null;
  size?: number | null;
  passes?: number | null;
  t0?: number | null;
  finished?: boolean;
  status?: string | null;
  sim?: boolean;
}

export interface Frame {
  t: number;
  psi: { full: number; some: number; full10: number; some10: number };
  mem: { total?: number; avail?: number; swap_total?: number; swap_free?: number };
  rate: { swpin: number; swpout: number; majflt: number };
  oom: number;
  sched: { state?: string; ops?: string; shared?: number; prio?: number; local?: number };
  ctrl: { on?: boolean; signal?: string | null };
  run: RunInfo | null;
  jobs: FrameJob[];
  ev: DashEvent[];
}

export interface Counts { run: number; park: number; done: number; passes: number; res: number }

/** A frame with the per-frame summaries every view needs, computed once. */
export interface EFrame extends Frame {
  _n: Counts;
  _jobs: Map<string, FrameJob>;
}

export interface ServerInfo { server: 'stasis'; host?: string; kernel?: string; results?: boolean }

export interface RunListing { id: string; label: string; n: number; run: number; status: string }

export interface RunMeta extends RunInfo { t_end?: number | null }

export interface RunMetrics {
  throughput?: number | null;
  kills?: number | null;
  jain?: number | null;
  ttfp_s?: number | null;
  swap_pages?: number | null;
  max_wait_s?: number | null;
  ws?: string | null;
}

export interface ProbeSummary { p50?: number | null; p99?: number | null; max?: number | null }

export interface RunData { meta: RunMeta; frames: Frame[]; metrics: RunMetrics | null; probe: ProbeSummary | null }

/** One finished run's metrics (eval/analyze.py run_metrics, or the simulator). */
export interface ResultRow {
  label: string;
  config?: string;
  N: number;
  run: number;
  status?: string;
  throughput?: number | null;
  passes?: number | null;
  kills?: number | null;
  jain?: number | null;
  p99_us?: number | null;
  ttfp_s?: number | null;
  swap_pages?: number | null;
  duration_s?: number | null;
  max_wait_s?: number | null;
  max_gap_s?: number | null;
  psi_full_pct?: number | null;
}

/** One hypothesis at one N, scored by eval/analyze.py score_table. */
export interface ScoreRow {
  id: string;
  claim: string;
  N: number;
  stasis: number[] | null;
  baseline: number[] | null;
  baseline_label: string | null;
  equals?: number | null;
  at_most?: number | null;
  result: 'PASS' | 'FAIL' | 'MISSING' | 'NO BASELINE' | string;
}

export interface ResultsData { rows: ResultRow[]; score: ScoreRow[]; score_error?: string | null }
