import type { TrialCell } from '../types';

interface Props {
  trials: TrialCell[];
  // expected total so pending cells render as placeholders (optional)
  total?: number;
  size?: 'sm' | 'md' | 'lg';
  muted?: boolean;
}

// The visual star: a dense grid of rerun cells that fill in green (pass) or
// red (fail) as trials land across the sandbox swarm.
export function TrialGrid({ trials, total, size = 'md', muted = false }: Props) {
  const landed = trials.length;
  const count = Math.max(total ?? 0, landed);
  const byIndex = new Map(trials.map((t) => [t.index, t]));
  const newest = landed > 0 ? Math.max(...trials.map((t) => t.index)) : -1;

  const cells = [];
  for (let i = 0; i < count; i++) {
    const cell = byIndex.get(i);
    const cls = cell ? (cell.passed ? 'pass' : 'fail') : 'pending';
    const fresh = cell && cell.index === newest ? ' fresh' : '';
    cells.push(<div key={i} className={`cell ${cls}${fresh}`} />);
  }

  return (
    <div className={`trial-grid ${size} ${muted ? 'muted' : ''}`} aria-hidden="true">
      {cells}
    </div>
  );
}
