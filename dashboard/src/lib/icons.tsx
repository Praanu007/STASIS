// Small inline icons. Each states its meaning in text beside it; colour comes
// from the surrounding status class.

export const IconOk = () => (
  <svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="5" fill="currentColor" /><path d="m3.6 6.2 1.6 1.6 3.3-3.4" stroke="#fff" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
export const IconWarn = () => (
  <svg viewBox="0 0 12 12"><path d="M6 1 11.2 10.5H.8Z" fill="currentColor" /><path d="M6 4.6v2.6" stroke="#000" strokeWidth="1.3" strokeLinecap="round" /><circle cx="6" cy="8.7" r=".75" fill="#000" /></svg>
);
export const IconCrit = () => (
  <svg viewBox="0 0 12 12"><path d="M3.9 1h4.2L11 3.9v4.2L8.1 11H3.9L1 8.1V3.9Z" fill="currentColor" /><path d="M6 3.4v3.2" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" /><circle cx="6" cy="8.4" r=".8" fill="#fff" /></svg>
);
export const IconIdle = () => (
  <svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.5" fill="currentColor" /></svg>
);
export const IconRun = () => (
  <svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="currentColor" /></svg>
);
export const IconPark = () => (
  <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"><path d="M5 .8v8.4M1.4 2.9l7.2 4.2M1.4 7.1l7.2-4.2" /></svg>
);
export const IconDone = () => (
  <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m1.8 5.2 2.1 2.1 4.3-4.6" /></svg>
);
export const IconPass = () => (
  <svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill="currentColor" /><path d="m4.2 7.2 1.9 1.9 3.8-4" stroke="#fff" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
export const IconFail = () => (
  <svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill="currentColor" /><path d="m4.8 4.8 4.4 4.4m0-4.4-4.4 4.4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" /></svg>
);
export const IconMissing = () => (
  <svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5.3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2.2 2" /></svg>
);
export const IconInfo = () => (
  <svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M8 7v4.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><circle cx="8" cy="4.9" r=".9" fill="currentColor" /></svg>
);
export const IconPlay = ({ playing }: { playing: boolean }) => (
  <svg viewBox="0 0 16 16" aria-hidden="true"><path d={playing ? 'M4 2.5h3v11H4Zm5 0h3v11H9Z' : 'M4 2.5v11l9-5.5Z'} fill="currentColor" /></svg>
);
export const IconRestart = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5a5.5 5.5 0 1 1-5.3 4H1l2.6-3 2.6 3H4.3A4 4 0 1 0 8 4Z" fill="currentColor" /></svg>
);
export const IconTheme = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm0 1.6v9.8a4.9 4.9 0 0 1 0-9.8Z" fill="currentColor" /></svg>
);
export const Logo = () => (
  <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path d="M16 2.5 27.7 9.25v13.5L16 29.5 4.3 22.75V9.25Z" stroke="var(--accent)" strokeWidth="1.6" fill="var(--accent-wash)" />
    <rect x="11.2" y="10.5" width="3" height="11" rx="1" fill="var(--accent)" />
    <rect x="17.8" y="10.5" width="3" height="11" rx="1" fill="var(--accent)" />
  </svg>
);

export const STATE_ICON = { run: IconRun, park: IconPark, done: IconDone } as const;
export const VERDICT_ICON = { pass: IconPass, fail: IconFail, missing: IconMissing } as const;
