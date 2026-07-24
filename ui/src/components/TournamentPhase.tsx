import { FlakeMeter } from './FlakeMeter';
import { TrialGrid } from './TrialGrid';
import { modelForHypothesis } from '../models';
import type { Hypothesis } from '../types';

interface Props {
  hypotheses: Hypothesis[];
  // real model slugs (round-robined across hypotheses in order); null => omit
  modelNames: string[] | null;
}

const CAUSE_LABEL: Record<string, string> = {
  race_condition: 'Race Condition',
  shared_state: 'Shared State',
  order_dependency: 'Order Dependency',
  timing: 'Timing / Scheduling',
};

const causeLabel = (c: string) =>
  CAUSE_LABEL[c] ?? c.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());

// Act 2 — the fix tournament. One lane per hypothesis; each races its patched
// test through fresh reruns. Losers grey out with a strike.
export function TournamentPhase({ hypotheses, modelNames }: Props) {
  return (
    <section className="phase tournament-phase">
      <header className="phase-head">
        <h2 className="phase-title">Act 2 · The Tournament</h2>
        <p className="phase-sub">
          One hypothesis per lane. Evidence eliminates. Fifty reruns decide the winner.
        </p>
      </header>

      <div className="lanes">
        {hypotheses.map((h, i) => (
          <Lane key={h.id} h={h} model={modelForHypothesis(modelNames, i)} />
        ))}
      </div>
    </section>
  );
}

function Lane({ h, model }: { h: Hypothesis; model: string | null }) {
  const eliminated = h.status === 'eliminated';
  const winner = h.status === 'winner';
  const statusText =
    winner ? 'WINNER' : eliminated ? 'ELIMINATED' : h.status === 'verified' ? 'VERIFIED' : 'RACING';

  return (
    <article className={`lane ${h.status}`}>
      <div className="lane-left">
        <div className="lane-head">
          <span className={`cause-tag cause-${h.causeClass}`}>{causeLabel(h.causeClass)}</span>
          {model && <span className="model-chip">· {model}</span>}
          <span className={`lane-status status-${h.status}`}>{statusText}</span>
        </div>
        <p className="lane-explanation">{h.explanation}</p>
        {eliminated && h.eliminatedReason && (
          <p className="lane-reason">ELIMINATED — {h.eliminatedReason}</p>
        )}
      </div>

      <div className="lane-mid">
        <div className="lane-counter mono">
          {h.trials.length} <span className="dim">reruns</span>
        </div>
        <TrialGrid trials={h.trials} size="sm" muted={eliminated} />
      </div>

      <div className="lane-right">
        <FlakeMeter
          rate={h.flakeRate}
          ci={h.wilsonCi}
          label={winner ? 'flake rate' : 'live flake rate'}
        />
      </div>

      {eliminated && <div className="lane-strike" aria-hidden="true" />}
    </article>
  );
}
