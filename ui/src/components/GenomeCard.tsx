import type { Hypothesis } from '../types';

interface Props {
  hypotheses: Hypothesis[];
}

const CAUSE_LABEL: Record<string, string> = {
  race_condition: 'race condition',
  shared_state: 'shared state',
  order_dependency: 'order dependency',
  timing: 'timing',
};

const plural = (n: number, s: string) => `${n} ${s}${n === 1 ? '' : 's'}`;
const label = (c: string) => CAUSE_LABEL[c] ?? c.replace(/_/g, ' ');

// The flywheel close: the repo's flake genome — which cause-classes this repo
// keeps producing. Compounds across runs into a repo-specific leaderboard.
export function GenomeCard({ hypotheses }: Props) {
  const counts = new Map<string, number>();
  for (const h of hypotheses) {
    counts.set(h.causeClass, (counts.get(h.causeClass) ?? 0) + 1);
  }
  const rows = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  return (
    <aside className="genome-card">
      <div className="genome-head">
        <span className="genome-dot" />
        <h3>Flake Genome</h3>
      </div>
      <p className="genome-sub">this repo, this run</p>
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
          ? `${rows.map(([c, n]) => plural(n, label(c))).join(', ')} diagnosed.`
          : 'diagnosing…'}{' '}
        Compounds into a repo leaderboard over time.
      </p>
    </aside>
  );
}
