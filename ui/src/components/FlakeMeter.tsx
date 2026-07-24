import { ciText, pct } from '../format';
import { DEFAULT_THRESHOLD } from '../constants';
import type { WilsonCI } from '../types';

interface Props {
  rate: number | null;
  ci: WilsonCI | null;
  label?: string;
  big?: boolean;
  threshold?: number | null; // decision threshold as a 0..1 fraction
}

// Horizontal flake-rate meter with the Wilson CI band drawn over the fill.
export function FlakeMeter({ rate, ci, label = 'flake rate', big = false, threshold }: Props) {
  const r = rate ?? 0;
  const hue = 120 - Math.round(r * 120); // green(120) -> red(0)
  const fill = `${Math.min(100, r * 100)}%`;
  const ciLeft = ci ? `${ci[0] * 100}%` : null;
  const ciWidth = ci ? `${(ci[1] - ci[0]) * 100}%` : null;
  const thr = threshold ?? DEFAULT_THRESHOLD;
  const thrPct = `${Math.min(100, Math.max(0, thr * 100))}%`;

  return (
    <div className={`flake-meter ${big ? 'big' : ''}`}>
      <div className="flake-meter-head">
        <span className="flake-meter-label">{label}</span>
        <span className="flake-meter-value" style={{ color: `hsl(${hue} 85% 60%)` }}>
          {rate == null ? '—' : pct(rate)}
        </span>
      </div>
      <div className="flake-meter-track">
        <div
          className="flake-meter-fill"
          style={{ width: fill, background: `hsl(${hue} 85% 52%)` }}
        />
        {ciLeft && ciWidth && (
          <div className="flake-meter-ci" style={{ left: ciLeft, width: ciWidth }} />
        )}
        <div
          className="flake-meter-threshold"
          style={{ left: thrPct }}
          title={`decision threshold (${pct(thr)})`}
        />
      </div>
      <div className="flake-meter-ci-text">{ciText(ci)}</div>
    </div>
  );
}
