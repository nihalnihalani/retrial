import type { WilsonCI } from './types';

export const pct = (x: number | null | undefined): string =>
  x == null ? '—' : `${Math.round(x * 100)}%`;

export const pct1 = (x: number | null | undefined): string =>
  x == null ? '—' : `${(x * 100).toFixed(1)}%`;

export const ciText = (ci: WilsonCI | null): string =>
  ci ? `95% CI ${pct(ci[0])}–${pct(ci[1])}` : '95% CI —';

// "≤8.8%" style upper-bound phrasing used on the winner card (one decimal so a
// tight real CI reads precisely rather than rounding to a whole percent).
export const ciUpper = (ci: WilsonCI | null): string =>
  ci ? `≤${pct1(ci[1])}` : '—';
