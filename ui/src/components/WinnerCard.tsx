import { DecryptText } from './fx/DecryptText';
import { ciUpper, pct } from '../format';
import { ReceiptTiles } from './ReceiptTiles';
import type { DetectState, Hypothesis, WinnerState } from '../types';

interface Props {
  winner: WinnerState;
  hypothesis: Hypothesis | undefined;
  detect: DetectState;
  prUrl: string | null;
  model: string | null; // prettified winning model, when known
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
export function WinnerCard({ winner, hypothesis, detect, prUrl, model }: Props) {
  // prefer the confirmed winner's own numbers; fall back to detect/hypothesis
  const before = winner.origFlakeRate ?? detect.flakeRate ?? 0.48;
  // `winner.wilsonCi` is the CONFIRMATION round's interval, so it must be
  // paired with the confirmation round's n. Pairing it with the tournament
  // lane's trial count rendered "across 40 trials (95% CI ≤7.4%)" while the
  // chip below said "confirmed 0/48" — ≤7.4% is the bound for 0/48, not 0/40
  // (which is 8.76%). A checkable arithmetic mismatch on the hero screen is
  // exactly the class of error this product exists to catch.
  const laneTrials = hypothesis?.trials.length ?? null;
  const ci = winner.wilsonCi ?? hypothesis?.wilsonCi ?? null;
  const ciTrials = winner.wilsonCi != null
    ? (winner.confirmTrials ?? null)
    : laneTrials;
  const confirmChip =
    winner.confirmTrials != null
      ? `confirmed ${Math.round(winner.confirmFlakeRate * winner.confirmTrials)}/${winner.confirmTrials} in a fresh round`
      : `confirmed ${pct(winner.confirmFlakeRate)} in a fresh round`;

  return (
    <section className="phase winner-phase">
      <div className="winner-card">
        <div className="winner-badge"><DecryptText text="VERDICT · FIXED & PROVEN" /></div>

        {hypothesis && (
          <div className="winner-cause">
            <span className={`cause-tag cause-${hypothesis.causeClass}`}>
              {causeLabel(hypothesis.causeClass)}
            </span>
            {model && <span className="winner-model">{model}'s fix</span>}
          </div>
        )}

        <div className="winner-numbers">
          <span className="winner-before">{pct(before)}</span>
          <span className="winner-arrow">→</span>
          {/* NEVER a bare rate here. CLAUDE.md's statistics law: "0/50 is
              reported as '<=7% at 95% confidence', never '0%'". This is the
              single biggest number in the product; it was the one place that
              broke the rule. */}
          <span className="winner-after">{ci ? ciUpper(ci) : pct(winner.confirmFlakeRate)}</span>
        </div>
        <p className="winner-caption">
          {ci && ciTrials != null ? (
            <>
              <strong>{Math.round(winner.confirmFlakeRate * ciTrials)} of {ciTrials}</strong>{' '}
              reruns failed in a dedicated confirmation round —{' '}
              <span className="mono">{ciUpper(ci)} at 95% confidence</span>.
            </>
          ) : (
            <>
              held through a dedicated confirmation round
              {laneTrials != null ? <> after {laneTrials} tournament reruns</> : null}.
            </>
          )}
        </p>

        <div className="verdict-chips">
          <span className="verdict-chip">{confirmChip}</span>
          {laneTrials != null && (
            <span className="verdict-chip">{laneTrials} tournament reruns</span>
          )}
          {ci && <span className="verdict-chip mono">95% CI {ciUpper(ci)}</span>}
        </div>

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
