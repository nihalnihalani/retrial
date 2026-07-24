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
  baselineVerdict: null,
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
    baselineVerdict: null,
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
  // A baseline verdict (the original test wasn't FLAKY) is terminal: there is no
  // tournament to run. Once detect declares one, ignore any late tournament/
  // winner traffic — e.g. a hardcoded failure shown as "FIXED" would be
  // dishonest. A brand-new run (diagnosing/run_started) still resets cleanly;
  // cumulative genome and the receipt PR are still allowed through.
  if (state.phase === 'baseline_verdict') {
    const passthrough =
      event.type === 'diagnosing' ||
      event.type === 'run_started' ||
      event.type === 'genome_updated' ||
      event.type === 'pr_opened';
    if (!passthrough) return state;
  }

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
      // Any non-FLAKY detect verdict is terminal — the engine's stable-test gate
      // runs no tournament, so we short-circuit to the matching verdict card
      // (ALWAYS_FAILING regression, STABLE, INCONCLUSIVE, ERROR). An absent or
      // "FLAKY" verdict proceeds into the tournament as before (replays too).
      if (event.verdict && event.verdict !== 'FLAKY') {
        return {
          ...state,
          detect,
          phase: 'baseline_verdict',
          baselineVerdict: {
            verdict: event.verdict,
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
        model: event.model ?? null,
        noValidPatch: event.status === 'no_valid_patch',
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
      const hypotheses = state.hypotheses.map((h) => {
        if (h.id !== event.id) return h;
        // INCONCLUSIVE (CI straddles the threshold) is measured but never
        // winner-eligible — show it as its own grey-amber lane, not "verified".
        const inconclusive = event.verdict === 'INCONCLUSIVE';
        const status =
          h.status === 'winner'
            ? h.status
            : inconclusive
              ? ('inconclusive' as const)
              : ('verified' as const);
        return {
          ...h,
          status,
          verdict: event.verdict ?? h.verdict,
          model: event.model ?? h.model,
          flakeRate: event.flake_rate,
          wilsonCi: event.wilson_ci,
        };
      });
      return { ...state, hypotheses };
    }

    case 'hypothesis_eliminated': {
      const noValidPatch = event.status === 'no_valid_patch';
      const hypotheses = state.hypotheses.map((h) => {
        if (h.id !== event.id) return h;
        // An INCONCLUSIVE lane is technically eliminated (can't win) but should
        // read as INCONCLUSIVE, not struck-out FLAKY. A no-valid-patch lane gets
        // its own render. Everything else is a normal elimination.
        const keepInconclusive = h.verdict === 'INCONCLUSIVE' && !noValidPatch;
        return {
          ...h,
          status: keepInconclusive ? ('inconclusive' as const) : ('eliminated' as const),
          eliminatedReason: event.reason ?? h.eliminatedReason,
          model: event.model ?? h.model,
          noValidPatch: noValidPatch || h.noValidPatch,
        };
      });
      return { ...state, hypotheses };
    }

    case 'winner_confirmed': {
      const hypotheses = state.hypotheses.map((h) =>
        h.id === event.id
          ? {
              ...h,
              status: 'winner' as const,
              flakeRate: event.flake_rate,
              model: event.model ?? h.model,
            }
          : h,
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
