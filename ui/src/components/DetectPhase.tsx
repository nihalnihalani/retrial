import { FlakeMeter } from './FlakeMeter';
import { TrialGrid } from './TrialGrid';
import type { DetectState, TrialCell } from '../types';

interface Props {
  detect: DetectState;
  expectedTrials: number;
}

// Act 1 — "the lie detector". Rerun the unmodified test many times; the grid
// flickers green/red and the meter climbs to the true flake rate.
export function DetectPhase({ detect, expectedTrials }: Props) {
  const firstPass = detect.trials.find((t) => t.passed);
  const firstFail = detect.trials.find((t) => !t.passed);
  const caughtInTheAct = Boolean(firstPass && firstFail);

  const landed = detect.trials.length;
  const total = detect.totalTrials ?? expectedTrials;

  return (
    <section className="phase detect-phase">
      <header className="phase-head">
        <h2 className="phase-title">Act 1 · The Lie Detector</h2>
        <p className="phase-sub">
          Same test. Same code. Rerun {total}× in fresh sandboxes.
        </p>
      </header>

      <div className="detect-body">
        <div className="detect-grid-wrap">
          <div className="detect-counter">
            <span className="mono">{landed}</span>
            <span className="detect-counter-sep">/</span>
            <span className="mono dim">{total}</span>
            <span className="detect-counter-label">reruns landed</span>
          </div>
          <TrialGrid trials={detect.trials} total={total} size="md" />
        </div>

        <div className="detect-side">
          <FlakeMeter
            rate={detect.flakeRate}
            ci={detect.wilsonCi}
            label={detect.done ? 'confirmed flake rate' : 'flake rate (live)'}
            big
          />
          {detect.done && (
            <div className="verdict-line">
              <span className="verdict-badge lie">CI LIED</span>
              <span>
                {detect.fails}/{detect.totalTrials} runs failed — this test is{' '}
                <strong>non-deterministic</strong>.
              </span>
            </div>
          )}
        </div>
      </div>

      {caughtInTheAct && (
        <SplitScreen pass={firstPass!} fail={firstFail!} />
      )}
    </section>
  );
}

// The screenshot moment: the SAME test passing in one sandbox and failing in
// another, side by side. The lie made visible.
function SplitScreen({ pass, fail }: { pass: TrialCell; fail: TrialCell }) {
  return (
    <div className="split-screen">
      <div className="split-cell split-pass">
        <div className="split-glyph">✓</div>
        <div className="split-label">PASS</div>
        <div className="split-meta mono">sandbox #{pass.index}</div>
      </div>
      <div className="split-caption">
        <div className="split-caption-main">same code, same test, same minute</div>
        <div className="split-caption-sub">one of these is lying to your CI</div>
      </div>
      <div className="split-cell split-fail">
        <div className="split-glyph">✕</div>
        <div className="split-label">FAIL</div>
        <div className="split-meta mono">sandbox #{fail.index}</div>
      </div>
    </div>
  );
}
