import type { BoardState, Hypothesis, RetrialEvent, TrialCell } from './types';

export const initialState: BoardState = {
  phase: 'detect',
  testName: null,
  diagnoseModels: null,
  diagnoseModelNames: null,
  plannedTrials: null,
  threshold: null,
  detect: {
    trials: [],
    flakeRate: null,
    wilsonCi: null,
    totalTrials: null,
    fails: null,
    done: false,
  },
  hypotheses: [],
  winner: null,
  quarantine: null,
  alwaysFailing: null,
  genome: null,
  prUrl: null,
  tournamentDone: false,
};

// Clears everything that belongs to a single run so a second live run can't
// inherit the first run's board, while PRESERVING the cumulative genome (and
// the diagnosing pre-phase's model list / test name, which the caller re-sets).
function resetPerRun(state: BoardState): BoardState {
  return {
    ...state,
    phase: 'detect',
    detect: { ...initialState.detect, trials: [] },
    hypotheses: [],
    winner: null,
    quarantine: null,
    alwaysFailing: null,
    prUrl: null,
    tournamentDone: false,
  };
}

function liveFlakeRate(trials: TrialCell[]): number | null {
  if (trials.length === 0) return null;
  const fails = trials.filter((t) => !t.passed).length;
  return fails / trials.length;
}

export function reduce(state: BoardState, event: RetrialEvent): BoardState {
  switch (event.type) {
    case 'diagnosing': {
      return {
        ...resetPerRun(state),
        phase: 'diagnosing',
        testName: event.test_name,
        diagnoseModels: event.n,
        diagnoseModelNames: event.models ?? null,
      };
    }

    case 'run_started': {
      return {
        ...resetPerRun(state),
        // leave the diagnosing pre-phase; the live run begins
        phase: 'detect',
        testName: event.test_name,
        plannedTrials: event.planned_trials,
        threshold: event.threshold ?? state.threshold,
      };
    }

    case 'trial_done': {
      const cell: TrialCell = { index: event.trial_index, passed: event.passed };
      if (event.hypothesis_id === null) {
        // DETECT-phase rerun of the unmodified test.
        const trials = upsertCell(state.detect.trials, cell);
        return {
          ...state,
          detect: {
            ...state.detect,
            trials,
            // running estimate until detect_done lands the official number
            flakeRate: state.detect.done ? state.detect.flakeRate : liveFlakeRate(trials),
          },
        };
      }
      // Tournament rerun of a specific hypothesis.
      const hypotheses = state.hypotheses.map((h) => {
        if (h.id !== event.hypothesis_id) return h;
        const trials = upsertCell(h.trials, cell);
        return {
          ...h,
          trials,
          flakeRate:
            h.status === 'verified' || h.status === 'winner'
              ? h.flakeRate
              : liveFlakeRate(trials),
        };
      });
      return { ...state, hypotheses };
    }

    case 'detect_done': {
      const detect = {
        ...state.detect,
        flakeRate: event.flake_rate,
        wilsonCi: event.wilson_ci,
        totalTrials: event.trials,
        fails: event.fails,
        done: true,
      };
      // A regression, not a flake: the detect pass tags it ALWAYS_FAILING, so we
      // short-circuit straight to the red verdict (no tournament to run).
      if (event.verdict === 'ALWAYS_FAILING') {
        return {
          ...state,
          detect,
          phase: 'always_failing',
          alwaysFailing: {
            flakeRate: event.flake_rate,
            wilsonCi: event.wilson_ci,
            trials: event.trials,
            fails: event.fails,
          },
        };
      }
      return { ...state, detect };
    }

    case 'hypothesis_created': {
      const h: Hypothesis = {
        id: event.id,
        causeClass: event.cause_class,
        explanation: event.explanation,
        trials: [],
        flakeRate: null,
        wilsonCi: null,
        status: 'racing',
      };
      const exists = state.hypotheses.some((x) => x.id === h.id);
      return {
        ...state,
        // first hypothesis flips us into the tournament view
        phase: state.phase === 'detect' ? 'tournament' : state.phase,
        hypotheses: exists ? state.hypotheses : [...state.hypotheses, h],
      };
    }

    case 'hypothesis_verified': {
      const hypotheses = state.hypotheses.map((h) =>
        h.id === event.id
          ? {
              ...h,
              status: h.status === 'winner' ? h.status : ('verified' as const),
              flakeRate: event.flake_rate,
              wilsonCi: event.wilson_ci,
            }
          : h,
      );
      return { ...state, hypotheses };
    }

    case 'hypothesis_eliminated': {
      const hypotheses = state.hypotheses.map((h) =>
        h.id === event.id
          ? { ...h, status: 'eliminated' as const, eliminatedReason: event.reason }
          : h,
      );
      return { ...state, hypotheses };
    }

    case 'hypothesis_inconclusive': {
      // The CI straddles the threshold: not verified, not eliminated, and not
      // winner-eligible. A winner event later can still promote it if it wins.
      const hypotheses = state.hypotheses.map((h) =>
        h.id === event.id
          ? {
              ...h,
              status: h.status === 'winner' ? h.status : ('inconclusive' as const),
              flakeRate: event.flake_rate ?? h.flakeRate,
              wilsonCi: event.wilson_ci ?? h.wilsonCi,
              eliminatedReason: event.reason ?? h.eliminatedReason,
            }
          : h,
      );
      return { ...state, hypotheses };
    }

    case 'winner_confirmed': {
      const hypotheses = state.hypotheses.map((h) =>
        h.id === event.id ? { ...h, status: 'winner' as const, flakeRate: event.flake_rate } : h,
      );
      return {
        ...state,
        phase: 'winner',
        hypotheses,
        winner: {
          id: event.id,
          flakeRate: event.flake_rate,
          confirmFlakeRate: event.confirm_flake_rate,
          confirmTrials: event.confirm_trials ?? null,
          wilsonCi: event.wilson_ci ?? null,
          origFlakeRate: event.orig_flake_rate ?? null,
          braintrustUrl: event.braintrust_url ?? null,
          model: event.model ?? null,
        },
      };
    }

    case 'always_failing': {
      // Not flaky — a regression. Terminal red verdict, its own phase.
      return {
        ...state,
        phase: 'always_failing',
        alwaysFailing: {
          flakeRate: event.flake_rate ?? state.detect.flakeRate,
          wilsonCi: event.wilson_ci ?? state.detect.wilsonCi,
          trials: event.trials ?? state.detect.totalTrials,
          fails: event.fails ?? state.detect.fails,
        },
      };
    }

    case 'quarantine_confirmed': {
      // Mark the best-effort hypothesis so the card can label it; leave every
      // lane's own eliminated/verified status intact.
      const hypotheses = state.hypotheses.map((h) =>
        h.id === event.best_id ? { ...h, status: 'verified' as const } : h,
      );
      return {
        ...state,
        phase: 'quarantine',
        hypotheses,
        quarantine: {
          bestId: event.best_id,
          flakeRate: event.dossier.flake_rate,
          wilsonCi: event.dossier.wilson_ci,
          trials: event.dossier.trials,
          reason: event.dossier.reason,
          braintrustUrl: event.braintrust_url ?? null,
        },
      };
    }

    case 'genome_updated': {
      return {
        ...state,
        genome: { runs: event.runs, byCauseClass: event.by_cause_class },
      };
    }

    case 'pr_opened': {
      return { ...state, prUrl: event.url };
    }

    case 'tournament_done': {
      return { ...state, tournamentDone: true };
    }

    default:
      return state;
  }
}

// Cells can arrive out of order across parallel sandboxes; index is the key.
function upsertCell(cells: TrialCell[], cell: TrialCell): TrialCell[] {
  const i = cells.findIndex((c) => c.index === cell.index);
  if (i === -1) return [...cells, cell];
  const next = cells.slice();
  next[i] = cell;
  return next;
}
