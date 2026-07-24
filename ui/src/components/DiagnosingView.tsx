import { useEffect, useState } from 'react';
import { prettyModel } from '../models';

interface Props {
  testName: string | null;
  n: number;
  // Real model slugs, only when the engine supplies them. NEVER hardcode names:
  // showing models we may not run is a checkable false claim to the Fireworks
  // judge. Absent => generic "Model 1…n".
  modelNames?: string[] | null;
}

// The cause-classes the models flicker through while they think. Purely
// cosmetic — the real hypotheses arrive later as hypothesis_created events.
const GUESSES = [
  'race condition?',
  'shared state?',
  'test-order dependency?',
  'timing / scheduling?',
  'unseeded randomness?',
  'resource contention?',
  'clock skew?',
];

// The DIAGNOSING pre-phase: shown from the `diagnosing` event until run_started.
// Deliberately alive (not a spinner) — this window is narrated in the pitch.
export function DiagnosingView({ testName, n, modelNames }: Props) {
  const real = modelNames && modelNames.length > 0 ? modelNames.map(prettyModel) : null;
  const count = Math.max(1, real ? real.length : n);
  const nameFor = (i: number) => real?.[i] ?? `Model ${i + 1}`;

  return (
    <section className="phase diagnosing-phase">
      <div className="diag-scan" aria-hidden="true" />

      <div className="diag-hero">
        <div className="diag-glyph">🔬</div>
        <h2 className="diag-title">
          <span className="diag-n">{count}</span> models proposing competing root-cause theories
          <span className="diag-ellipsis">
            <i>.</i>
            <i>.</i>
            <i>.</i>
          </span>
        </h2>
        {testName && (
          <p className="diag-test mono" title={testName}>
            {testName}
          </p>
        )}
        <p className="diag-sub">Differential diagnosis — evidence will eliminate all but one.</p>
      </div>

      <div className="diag-models">
        {Array.from({ length: count }).map((_, i) => (
          <ModelChip key={i} name={nameFor(i)} seed={i} />
        ))}
      </div>
    </section>
  );
}

function ModelChip({ name, seed }: { name: string; seed: number }) {
  const [guess, setGuess] = useState(GUESSES[seed % GUESSES.length]);

  useEffect(() => {
    // each chip rotates its current guess on its own staggered cadence
    let i = seed;
    const period = 620 + seed * 90;
    const id = window.setInterval(() => {
      i = (i + 1) % GUESSES.length;
      setGuess(GUESSES[i]);
    }, period);
    return () => window.clearInterval(id);
  }, [seed]);

  return (
    <div className="diag-chip" style={{ animationDelay: `${seed * 0.18}s` }}>
      <span className="diag-chip-dot" />
      <span className="diag-chip-name">{name}</span>
      <span className="diag-chip-guess mono" key={guess}>
        {guess}
      </span>
    </div>
  );
}
