import { prettyModel } from '../models';
import type { GenomeState, Hypothesis } from '../types';

interface Props {
  hypotheses: Hypothesis[];
  genome: GenomeState | null;
}

// The model win-rate leaderboard, built ONLY from real engine data
// (genome.byModel). Never synthesized — absent data renders nothing.
function modelRows(genome: GenomeState | null) {
  if (!genome?.byModel) return [];
  return Object.entries(genome.byModel)
    .filter(([, s]) => s.attempts > 0)
    .map(([slug, s]) => ({
      model: prettyModel(slug),
      wins: s.wins,
      attempts: s.attempts,
      rate: s.wins / s.attempts,
    }))
    .sort((a, b) => b.rate - a.rate || b.attempts - a.attempts);
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
  const leaders = modelRows(genome);

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
      {leaders.length > 0 && (
        <div className="genome-leaderboard">
          <p className="genome-leaderboard-head">Model win rate</p>
          <ul className="genome-leaderboard-list">
            {leaders.map((m) => (
              <li key={m.model}>
                <span className="glb-model">{m.model}</span>
                <span className="glb-record mono">
                  {m.wins}/{m.attempts}
                </span>
                <span className="glb-rate mono">{Math.round(m.rate * 100)}%</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="genome-foot">
        {rows.length > 0
          ? `${rows.map(([c, n]) => plural(n, label(c))).join(', ')} ${
              cumulative ? 'on record' : 'diagnosed'
            }.`
          : 'diagnosing…'}{' '}
        {leaders.length > 0
          ? 'Model win rates compound as the repo logs more runs.'
          : 'Compounds into a repo leaderboard over time.'}
      </p>
    </aside>
  );
}
