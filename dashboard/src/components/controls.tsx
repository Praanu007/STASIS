import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { IconInfo } from '../lib/icons';

/** A row of radio buttons; arrow keys move the choice. */
export function Seg<T extends string | number>({ label, labelledBy, options, value, onPick, id }: {
  label?: string; labelledBy?: string; id?: string;
  options: readonly (readonly [T, string])[]; value: T; onPick: (v: T) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = options.findIndex(([v]) => v === value);
    const n = Math.max(0, Math.min(options.length - 1, i + (e.key === 'ArrowLeft' ? -1 : 1)));
    refs.current[n]?.focus();
    onPick(options[n][0]);
  };
  return (
    <div className="seg" id={id} role="radiogroup" aria-label={label} aria-labelledby={labelledBy} onKeyDown={onKey}>
      {options.map(([v, text], i) => (
        <button key={String(v)} ref={(el) => { refs.current[i] = el; }} type="button" role="radio" data-v={String(v)}
          aria-checked={v === value} tabIndex={v === value ? 0 : -1} onClick={() => onPick(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function Notice({ id, children }: { id?: string; children: ReactNode }) {
  if (children == null || children === false) return null;
  return <div className="notice" id={id}><IconInfo /><div>{children}</div></div>;
}

/** Width of an element, kept up to date; measured before the first paint. */
export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/** Changes whenever an element's size does (for canvases to redraw). */
export function useSizeKey(ref: RefObject<HTMLElement | null>): string {
  const [key, setKey] = useState('');
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setKey(`${el.clientWidth}x${el.clientHeight}`));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return key;
}
