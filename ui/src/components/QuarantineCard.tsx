import { ciText, pct } from '../format';
import { ReceiptTiles } from './ReceiptTiles';
import type { DetectState, Hypothesis, QuarantineState } from '../types';

interface Props {
  quarantine: QuarantineState;
  bestHypothesis: Hypothesis | undefined;
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

// The no-dead-end verdict: nothing stabilized the test, so we quarantine it
// WITH the full evidence dossier. Amber tone — a real, useful outcome, not a
// failure. The receipts still ship (Braintrust autopsy + quarantine PR).
export function QuarantineCard({ quarantine, bestHypothesis, detect, prUrl }: Props) {
  const before = detect.flakeRate ?? 0.48;

  return (
    <section className="phase quarantine-phase">
      <div className="quarantine-card">
        <div className="quarantine-badge">VERDICT · QUARANTINED WITH EVIDENCE</div>

        <h2 className="quarantine-headline">No fix survived the tournament.</h2>
        <p className="quarantine-lead">
          Every candidate still flaked. We quarantined the test so CI goes green — and attached
          the full autopsy so a human can finish the job.
        </p>

        <div className="quarantine-numbers">
          <div className="qn-block">
            <span className="qn-value fail">{pct(before)}</span>
            <span className="qn-label">baseline flake</span>
          </div>
          <span className="qn-arrow">→</span>
          <div className="qn-block">
            <span className="qn-value amber">{pct(quarantine.flakeRate)}</span>
            <span className="qn-label">best effort, still flaking</span>
          </div>
        </div>
        <p className="quarantine-ci mono">
          best candidate over {quarantine.trials} trials · {ciText(quarantine.wilsonCi)}
        </p>

        {bestHypothesis && (
          <div className="quarantine-best">
            <span className={`cause-tag cause-${bestHypothesis.causeClass}`}>
              {causeLabel(bestHypothesis.causeClass)}
            </span>
            <span className="quarantine-best-label">closest cause</span>
            <p className="quarantine-best-exp">{bestHypothesis.explanation}</p>
          </div>
        )}

        <div className="quarantine-reason">{quarantine.reason}</div>

        <ReceiptTiles
          braintrustUrl={quarantine.braintrustUrl}
          braintrustLabel="Braintrust autopsy"
          prUrl={prUrl}
          prLabel="Quarantine PR opened"
        />

        <p className="winner-foot amber-foot">
          Worst case, we still hand you a real, reproducible verdict.
        </p>
      </div>
    </section>
  );
}
