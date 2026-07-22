import type { GenomeState, Hypothesis } from '../types';

interface Props {
  hypotheses: Hypothesis[];
  genome: GenomeState | null;
}

const CAUSE_LABEL: Record<string, string> = {
  race_condition: 'race condition',
  shared_state: 'shared state',
  order_dependency: 'order dependency',
  timing: 'timing',
};

const label = (c: string) => CAUSE_LABEL[c] ?? c.replace(/_/g, ' ');

// "1 order dependency" / "3 order dependencies" — handle consonant+y → ies.
const pluralize = (word: string) =>
  /[^aeiou]y$/.test(word) ? word.replace(/y$/, 'ies') : `${word}s`;
const plural = (n: number, s: string) => `${n} ${n === 1 ? s : pluralize(s)}`;

// The flywheel close: the repo's flake genome — which cause-classes this repo
// keeps producing. Prefers the engine's cumulative genome_updated data (real,
// compounding across runs); falls back to this run's hypotheses when absent.
export function GenomeCard({ hypotheses, genome }: Props) {
  const cumulative = Boolean(genome);
  const counts = new Map<string, number>();
  if (genome) {
    for (const [cause, n] of Object.entries(genome.byCauseClass)) counts.set(cause, n);
  } else {
    for (const h of hypotheses) counts.set(h.causeClass, (counts.get(h.causeClass) ?? 0) + 1);
  }
  const rows = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  return (
    <aside className="genome-card">
      <div className="genome-head">
        <span className="genome-dot" />
        <h3>Flake Genome</h3>
      </div>
      <p className="genome-sub">
        {cumulative ? `this repo · ${plural(genome!.runs, 'run')} logged` : 'this repo, this run'}
      </p>
      <ul className="genome-list">
        {rows.map(([cause, n]) => (
          <li key={cause}>
            <span className="genome-count mono">{n}</span>
            <span className="genome-cause">{plural(n, label(cause))}</span>
          </li>
        ))}
      </ul>
      <p className="genome-foot">
        {rows.length > 0
          ? `${rows.map(([c, n]) => plural(n, label(c))).join(', ')} ${
              cumulative ? 'on record' : 'diagnosed'
            }.`
          : 'diagnosing…'}{' '}
        Compounds into a repo leaderboard over time.
      </p>
    </aside>
  );
}
