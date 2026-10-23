// Canvas colours come from the CSS tokens, so both themes stay in one place
// (styles.css). Read them again whenever the theme changes.

export interface Colors {
  ink: string; ink2: string; ink3: string; grid: string; line: string;
  panel: string; panel2: string; accent: string;
  full: string; some: string;
  heat: string[];
  frost: string; frostInk: string; done: string; track: string;
  series: string[];
}

let hatch: CanvasPattern | null = null;

export function readColors(): Colors {
  const cs = getComputedStyle(document.documentElement);
  const g = (n: string) => cs.getPropertyValue(n).trim();
  hatch = null;
  return {
    ink: g('--ink'), ink2: g('--ink-2'), ink3: g('--ink-3'), grid: g('--grid'), line: g('--line'),
    panel: g('--panel'), panel2: g('--panel-2'), accent: g('--accent'),
    full: g('--psi-full'), some: g('--psi-some'),
    heat: [0, 1, 2, 3, 4, 5].map((i) => g('--heat-' + i)),
    frost: g('--frost'), frostInk: g('--frost-ink'), done: g('--done'), track: g('--track'),
    series: [1, 2, 3, 4, 5, 6, 7].map((i) => g('--s' + i)),
  };
}

/** The frost hatch for parked stretches of a job's lane. */
export function hatchPattern(ctx: CanvasRenderingContext2D, C: Colors): CanvasPattern | null {
  if (hatch) return hatch;
  const dpr = window.devicePixelRatio || 1;
  const s = Math.round(6 * dpr);
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const x = c.getContext('2d');
  if (!x) return null;
  x.fillStyle = C.frost; x.fillRect(0, 0, s, s);
  x.strokeStyle = C.frostInk; x.lineWidth = 1.4 * dpr; x.lineCap = 'square';
  x.beginPath();
  x.moveTo(0, s); x.lineTo(s, 0);
  x.moveTo(-s / 2, s / 2); x.lineTo(s / 2, -s / 2);
  x.moveTo(s / 2, s * 1.5); x.lineTo(s * 1.5, s / 2);
  x.stroke();
  hatch = ctx.createPattern(c, 'repeat');
  if (hatch && hatch.setTransform && window.DOMMatrix) hatch.setTransform(new DOMMatrix().scale(1 / dpr));
  return hatch;
}

/** Sizes a canvas to its box at the device's pixel ratio; returns CSS pixels. */
export function fitCanvas(cv: HTMLCanvasElement): { ctx: CanvasRenderingContext2D; w: number; h: number } | null {
  const r = cv.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext('2d');
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: r.width, h: r.height };
}

// PFF -> heat step. Bins are log-spaced: calm jobs barely fault, thrashing ones fault on most touches.
const HEAT_EDGES = [0.05, 0.3, 1, 5, 30];
export function heatIndex(pff: number | null | undefined): number {
  if (pff == null || !(pff > 0)) return 0;
  let i = 0;
  while (i < HEAT_EDGES.length && pff >= HEAT_EDGES[i]) i++;
  return i;
}
