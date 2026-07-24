import type { BoardState, Hypothesis, RetrialEvent, TrialCell } from './types';

export const initialState: BoardState = {
  phase: 'detect',
  testName: null,
  plannedTrials: null,
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
  tournamentDone: false,
};

function liveFlakeRate(trials: TrialCell[]): number | null {
  if (trials.length === 0) return null;
  const fails = trials.filter((t) => !t.passed).length;
  return fails / trials.length;
}

export function reduce(state: BoardState, event: RetrialEvent): BoardState {
  switch (event.type) {
    case 'run_started': {
      return {
        ...state,
        testName: event.test_name,
        plannedTrials: event.planned_trials,
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
      return {
        ...state,
        detect: {
          ...state.detect,
          flakeRate: event.flake_rate,
          wilsonCi: event.wilson_ci,
          totalTrials: event.trials,
          fails: event.fails,
          done: true,
        },
      };
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
        },
      };
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
