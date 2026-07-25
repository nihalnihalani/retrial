import { FlakeMeter } from './FlakeMeter';
import { TrialGrid } from './TrialGrid';
import { modelForHypothesis, displayModel } from '../models';
import type { Hypothesis } from '../types';

interface Props {
  hypotheses: Hypothesis[];
  // real model slugs (round-robined across hypotheses in order); null => omit
  modelNames: string[] | null;
  plannedTrials: number | null; // per-lane rerun budget (run_started.planned_trials)
  threshold?: number | null; // decision threshold (0..1) for each lane's meter
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
export function TournamentPhase({ hypotheses, modelNames, plannedTrials, threshold }: Props) {
  return (
    <section className="phase tournament-phase">
      <header className="phase-head">
        <h2 className="phase-title">Act 2 · Tournament</h2>
        <p className="phase-sub">{tournamentSubtitle(plannedTrials)}</p>
      </header>

      <div className="lanes">
        {hypotheses.map((h, i) => (
          <Lane
            key={h.id}
            h={h}
            model={displayModel(h.model, modelForHypothesis(modelNames, i))}
            threshold={threshold}
            plannedTrials={plannedTrials}
          />
        ))}
      </div>
    </section>
  );
}

// Derive the honest cap from the run itself; early-stop means "up to N", not N.
function tournamentSubtitle(plannedTrials: number | null): string {
  const n = plannedTrials ?? null;
  const budget = n ? `Up to ${n} reruns per lane` : 'Reruns';
  return `One hypothesis per lane. Evidence eliminates. ${budget} decide the winner — early-stop when the confidence interval is decisive.`;
}

const LANE_STATUS_TEXT: Record<Hypothesis['status'], string> = {
  winner: 'WINNER',
  eliminated: 'ELIMINATED',
  verified: 'VERIFIED',
  inconclusive: 'INCONCLUSIVE',
  racing: 'RACING',
};

function Lane({
  h,
  model,
  threshold,
  plannedTrials,
}: {
  h: Hypothesis;
  model: string | null;
  threshold?: number | null;
  plannedTrials: number | null;
}) {
  // A model that returned no parseable patch never races — no trials, no meter,
  // just an honest "no valid patch" note. Render it as its own thing.
  if (h.noValidPatch) {
    return (
      <article className="lane no-valid-patch">
        <div className="lane-left">
          <div className="lane-head">
            <span className={`cause-tag cause-${h.causeClass}`}>{causeLabel(h.causeClass)}</span>
            {model && <span className="model-chip">· {model}</span>}
            <span className="lane-status status-no-patch">NO VALID PATCH</span>
          </div>
          <p className="lane-explanation">
            {model ? `${model} named a cause but ` : 'Named a cause but '}
            didn&apos;t produce a patch we could test — excluded from the tournament rather than
            raced against the original.
          </p>
        </div>
        <div className="lane-mid np-note mono">no reruns</div>
        <div className="lane-right" aria-hidden="true" />
      </article>
    );
  }

  const eliminated = h.status === 'eliminated';
  const inconclusive = h.status === 'inconclusive';
  const statusText = LANE_STATUS_TEXT[h.status];

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
        {inconclusive && (
          <p className="lane-reason inconclusive-reason">
            INCONCLUSIVE — {h.eliminatedReason ?? 'CI straddles threshold'}
          </p>
        )}
      </div>

      <div className="lane-mid">
        <div className="lane-counter mono">
          {h.trials.length} <span className="dim">reruns</span>
        </div>
        {/* `total` gives every lane the SAME track, so four lanes read as a
            race to a shared finish line instead of four ragged strips of
            different lengths. Without it you cannot tell ahead from behind
            from early-stopped. */}
        <TrialGrid trials={h.trials} total={plannedTrials ?? undefined} size="sm"
                   muted={eliminated || inconclusive} />
      </div>

      <div className="lane-right">
        <FlakeMeter
          rate={h.flakeRate}
          ci={h.wilsonCi}
          label={h.status === 'racing' ? 'live flake rate' : 'flake rate'}
          threshold={threshold}
        />
      </div>

      {eliminated && <div className="lane-strike" aria-hidden="true" />}
    </article>
  );
}
