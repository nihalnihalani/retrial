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
// Colour encodes the decision the confidence interval supports, not the
// magnitude of the rate. A smooth hue ramp would paint a 34% flake rate
// green-ish, which is the opposite of what the evidence says.
function verdictTone(rate: number | null, ci: WilsonCI | null, thr: number) {
  if (rate == null) return 'unknown';
  const [low, high] = ci ?? [rate, rate];
  if (high <= thr) return 'clean'; // CI entirely below the threshold
  if (low > thr) return 'flaky'; // CI entirely above it
  return 'undecided'; // CI straddles it — no honest call yet
}

export function FlakeMeter({ rate, ci, label = 'flake rate', big = false, threshold }: Props) {
  const r = rate ?? 0;
  const thr = threshold ?? DEFAULT_THRESHOLD;
  const tone = verdictTone(rate, ci, thr);
  const fill = `${Math.min(100, r * 100)}%`;
  const ciLeft = ci ? `${ci[0] * 100}%` : null;
  const ciWidth = ci ? `${(ci[1] - ci[0]) * 100}%` : null;
  const thrPct = `${Math.min(100, Math.max(0, thr * 100))}%`;

  return (
    <div className={`flake-meter tone-${tone} ${big ? 'big' : ''}`}>
      <div className="flake-meter-head">
        <span className="flake-meter-label">{label}</span>
        <span className="flake-meter-value">{rate == null ? '—' : pct(rate)}</span>
      </div>
      <div className="flake-meter-track">
        <div className="flake-meter-fill" style={{ width: fill }} />
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
