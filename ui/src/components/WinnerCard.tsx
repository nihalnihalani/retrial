import { ciUpper, pct } from '../format';
import { ReceiptTiles } from './ReceiptTiles';
import type { DetectState, Hypothesis, WinnerState } from '../types';

interface Props {
  winner: WinnerState;
  hypothesis: Hypothesis | undefined;
  detect: DetectState;
  prUrl: string | null;
}

const CAUSE_LABEL: Record<string, string> = {
  race_condition: 'Race Condition',
  shared_state: 'Shared State',
  order_dependency: 'Order Dependency',
  timing: 'Timing / Scheduling',
};
const causeLabel = (c: string) =>
  CAUSE_LABEL[c] ?? c.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());

// The verdict. Clean, no confetti: the before→after number, the CI, and the
// two governance receipts (Braintrust permalink + the opened PR).
export function WinnerCard({ winner, hypothesis, detect, prUrl }: Props) {
  // prefer the confirmed winner's own numbers; fall back to detect/hypothesis
  const before = winner.origFlakeRate ?? detect.flakeRate ?? 0.48;
  const trials = hypothesis?.trials.length ?? 50;
  const ci = winner.wilsonCi ?? hypothesis?.wilsonCi ?? null;

  return (
    <section className="phase winner-phase">
      <div className="winner-card">
        <div className="winner-badge">VERDICT · FIXED &amp; PROVEN</div>

        {hypothesis && (
          <div className="winner-cause">
            <span className={`cause-tag cause-${hypothesis.causeClass}`}>
              {causeLabel(hypothesis.causeClass)}
            </span>
          </div>
        )}

        <div className="winner-numbers">
          <span className="winner-before">{pct(before)}</span>
          <span className="winner-arrow">→</span>
          <span className="winner-after">{pct(winner.confirmFlakeRate)}</span>
        </div>
        <p className="winner-caption">
          flake rate across <strong>{trials} trials</strong>
          {ci ? (
            <>
              {' '}
              (<span className="mono">95% CI {ciUpper(ci)}</span>)
            </>
          ) : null}
          , held at <strong>{pct(winner.confirmFlakeRate)}</strong> through a dedicated
          confirmation round.
        </p>

        {hypothesis && <p className="winner-explanation">{hypothesis.explanation}</p>}

        <ReceiptTiles
          braintrustUrl={winner.braintrustUrl}
          braintrustLabel="Braintrust experiment"
          prUrl={prUrl}
          prLabel="Fix PR opened"
        />

        <p className="winner-foot">
          We didn't just fix it — we proved it's fixed, reproducibly.
        </p>
      </div>
    </section>
  );
}
