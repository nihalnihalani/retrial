import { DecryptText } from './fx/DecryptText';
import { ciText, pct } from '../format';
import type { BaselineVerdictState } from '../types';

interface Props {
  state: BaselineVerdictState;
}

// The four non-FLAKY detect verdicts each get an honest terminal card. The
// original test never entered a tournament, so there are no lanes and no PR —
// just the measured baseline and what it means.
interface Variant {
  tone: 'red' | 'green' | 'amber' | 'grey';
  badge: string;
  headline: string;
  lead: string;
  valueLabel: string;
  foot: string;
}

const VARIANTS: Record<string, Variant> = {
  ALWAYS_FAILING: {
    tone: 'red',
    badge: 'VERDICT · ALWAYS FAILING',
    headline: "This test isn't flaky — it always fails.",
    lead: "That's a regression, not nondeterminism. Every rerun failed, so there's nothing to quarantine and no fix to prove — the test is correctly reporting broken code.",
    valueLabel: 'fail rate',
    foot: "Fix the code, not the test. Retrial won't paper over a real failure.",
  },
  STABLE: {
    tone: 'green',
    badge: 'VERDICT · ALREADY STABLE',
    headline: 'Not flaky — this test is stable.',
    lead: 'Every rerun agreed. There is nothing to fix and nothing to quarantine — your CI is telling the truth about this one.',
    valueLabel: 'flake rate',
    foot: 'No action needed. Retrial only intervenes when the evidence says a test is lying.',
  },
  INCONCLUSIVE: {
    tone: 'amber',
    badge: 'VERDICT · INCONCLUSIVE',
    headline: "Inconclusive — we won't guess.",
    lead: 'The confidence interval straddles the decision threshold at this trial count, so we can call it neither flaky nor stable. More reruns would settle it.',
    valueLabel: 'flake rate (CI straddles threshold)',
    foot: 'Retrial reports uncertainty honestly rather than forcing a verdict.',
  },
  ERROR: {
    tone: 'grey',
    badge: 'VERDICT · ERROR',
    headline: "Couldn't reach a verdict — the test errored.",
    lead: 'The detect pass hit infrastructure or execution errors instead of clean pass/fail signal, so there is no reliable flake rate to report.',
    valueLabel: 'no clean signal',
    foot: 'Check the test harness, then rerun — Retrial never invents a number it did not measure.',
  },
};

export function TerminalVerdictCard({ state }: Props) {
  const v = VARIANTS[state.verdict] ?? VARIANTS.ERROR;
  const rate = state.flakeRate;
  const trials = state.trials;
  const fails = state.fails;
  const showValue = v.tone !== 'grey' && rate != null;

  return (
    <section className="phase terminal-phase">
      <div className={`terminal-card tv-${v.tone}`}>
        <div className="terminal-badge">{v.badge}</div>

        <h2 className="terminal-headline"><DecryptText text={v.headline} /></h2>
        <p className="terminal-lead">{v.lead}</p>

        <div className="terminal-numbers">
          <span className="tv-value">
            {showValue ? pct(rate) : v.tone === 'red' && rate == null ? '100%' : '—'}
          </span>
          <span className="tv-label">{v.valueLabel}</span>
        </div>
        <p className="terminal-ci mono">
          {fails != null && trials != null ? `${fails}/${trials} runs failed · ` : ''}
          {ciText(state.wilsonCi)}
        </p>

        <p className="terminal-foot">{v.foot}</p>
      </div>
    </section>
  );
}
