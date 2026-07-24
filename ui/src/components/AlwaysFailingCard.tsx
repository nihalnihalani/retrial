import { ciText, pct } from '../format';
import type { AlwaysFailingState } from '../types';

interface Props {
  state: AlwaysFailingState;
}

// The test isn't flaky — it fails every time. That's a regression, not
// nondeterminism, and it earns its own blunt red verdict so nobody mistakes a
// broken test for a flaky one.
export function AlwaysFailingCard({ state }: Props) {
  const rate = state.flakeRate;
  const trials = state.trials;
  const fails = state.fails;

  return (
    <section className="phase always-failing-phase">
      <div className="always-failing-card">
        <div className="always-failing-badge">VERDICT · ALWAYS FAILING</div>

        <h2 className="always-failing-headline">This test isn't flaky — it always fails.</h2>
        <p className="always-failing-lead">
          That's a regression, not nondeterminism. Every rerun failed, so there's nothing to
          quarantine and no fix to prove — the test is correctly reporting broken code.
        </p>

        <div className="always-failing-numbers">
          <span className="af-value">{rate == null ? '100%' : pct(rate)}</span>
          <span className="af-label">fail rate</span>
        </div>
        <p className="always-failing-ci mono">
          {fails != null && trials != null ? `${fails}/${trials} runs failed · ` : ''}
          {ciText(state.wilsonCi)}
        </p>

        <p className="winner-foot af-foot">
          Fix the code, not the test. Retrial won't paper over a real failure.
        </p>
      </div>
    </section>
  );
}
