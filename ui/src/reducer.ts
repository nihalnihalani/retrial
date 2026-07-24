import type {
  BisectCheckpoint,
  BisectState,
  BoardState,
  Hypothesis,
  RetrialEvent,
  TrialCell,
} from './types';

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
  bisect: null,
  promotion: null,
  poolDegraded: null,
  hermetic: null,
};

// Clears everything that belongs to a single run so a second live run can't
// inherit the first run's board, while PRESERVING the cumulative genome and
// the sticky poolDegraded badge (pool state outlives runs) — and the
// diagnosing pre-phase's model list / test name, which the caller re-sets.
// A stale pending promotion is also dropped, mirroring the server's
// _accept_run wipe: a new run of ANY type dismisses an unclicked gate.
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
    bisect: null,
    promotion: null,
    hermetic: null,
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
      event.type === 'pr_opened' ||
      // A new bisect run resets cleanly, same as diagnosing/run_started.
      event.type === 'bisect_started' ||
      // Late post-terminal traffic that is honest to show: the promote gate
      // (emitted after tournament_done), pool degradation (pool-level, not
      // run-level), and the hermetic finding (a detect-phase diagnostic).
      event.type === 'promotion_pending' ||
      event.type === 'promotion_closed' ||
      event.type === 'pool_degraded' ||
      event.type === 'hermetic_diagnosis';
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

    case 'hermetic_diagnosis': {
      // Previously silently dropped (missing from the union) — now stored.
      return {
        ...state,
        hermetic: {
          verdict: event.verdict,
          networkedRate: event.networked_rate,
          hermeticRate: event.hermetic_rate,
          networkedCi: event.networked_ci,
          hermeticCi: event.hermetic_ci,
        },
      };
    }

    case 'pool_degraded': {
      // An honest badge, not an error: the fork backend fell back to the
      // snapshot pool and the run continues on it.
      return { ...state, poolDegraded: { reason: event.reason } };
    }

    case 'bisect_started': {
      return {
        ...resetPerRun(state),
        phase: 'bisect',
        testName: event.suspect,
        bisect: {
          suite: event.suite,
          suspect: event.suspect,
          nTests: event.n_tests,
          checkpoints: [],
          window: null,
          polluter: null,
          polluterIndex: null,
          reason: null,
          done: false,
          error: null,
        },
      };
    }

    case 'checkpoint_created': {
      const bisect = ensureBisect(state.bisect);
      return {
        ...state,
        phase: 'bisect',
        bisect: {
          ...bisect,
          checkpoints: upsertCheckpoint(bisect.checkpoints, event.k, (c) => ({
            ...c,
            label: event.label,
            testPassed: event.test_passed ?? c.testPassed,
          })),
        },
      };
    }

    case 'checkpoint_probed': {
      const bisect = ensureBisect(state.bisect);
      return {
        ...state,
        bisect: {
          ...bisect,
          checkpoints: upsertCheckpoint(bisect.checkpoints, event.k, (c) => ({
            ...c,
            probe: {
              flakeRate: event.flake_rate,
              wilsonCi: event.wilson_ci,
              trials: event.trials,
              verdict: event.verdict ?? null,
            },
          })),
        },
      };
    }

    case 'bisect_narrowed': {
      const bisect = ensureBisect(state.bisect);
      const window: [number, number] = [event.lo, event.hi];
      return {
        ...state,
        bisect: {
          ...bisect,
          window,
          checkpoints: bisect.checkpoints.map((c) => ({
            ...c,
            inWindow: c.k >= event.lo && c.k <= event.hi,
          })),
        },
      };
    }

    case 'bisect_done': {
      const bisect = ensureBisect(state.bisect);
      // Fold the final probe table in (upsert-by-k, out-of-order safe).
      let checkpoints = bisect.checkpoints;
      for (const p of event.probes ?? []) {
        if (!p) continue;
        checkpoints = upsertCheckpoint(checkpoints, p.k, (c) => ({
          ...c,
          probe: {
            flakeRate: p.flake_rate,
            wilsonCi: p.wilson_ci,
            trials: p.trials,
            verdict: p.verdict ?? null,
          },
        }));
      }
      return {
        ...state,
        bisect: {
          ...bisect,
          checkpoints,
          polluter: event.polluter_test ?? null,
          polluterIndex: event.polluter_index ?? null,
          reason: event.reason ?? null,
          error: event.error ?? null,
          done: true,
        },
      };
    }

    case 'promotion_pending': {
      return {
        ...state,
        promotion: {
          testName: event.test_name,
          verdict: event.verdict,
          winnerId: event.winner_id ?? null,
          flakeRate: event.flake_rate ?? null,
          confirmFlakeRate: event.confirm_flake_rate ?? null,
          braintrustUrl: event.braintrust_url ?? null,
          open: true,
          approved: null,
        },
      };
    }

    case 'promotion_closed': {
      if (!state.promotion) return state;
      // Keep the record (approved feeds the "awaiting PR…" hint until
      // pr_opened fills prUrl); only the modal closes.
      return {
        ...state,
        promotion: { ...state.promotion, open: false, approved: event.approved },
      };
    }

    default:
      return state;
  }
}

// A checkpoint event can land before bisect_started on a lossy replay; start
// from an empty rail rather than dropping it.
function ensureBisect(bisect: BisectState | null): BisectState {
  return (
    bisect ?? {
      suite: '',
      suspect: '',
      nTests: 0,
      checkpoints: [],
      window: null,
      polluter: null,
      polluterIndex: null,
      reason: null,
      done: false,
      error: null,
    }
  );
}

// Rail rows can arrive out of order (probes especially); k is the key, and the
// list stays sorted by k so the spine renders in suite order.
function upsertCheckpoint(
  checkpoints: BisectCheckpoint[],
  k: number,
  update: (c: BisectCheckpoint) => BisectCheckpoint,
): BisectCheckpoint[] {
  const blank: BisectCheckpoint = {
    k,
    label: k === 0 ? 'pristine' : `checkpoint ${k}`,
    testPassed: null,
    probe: null,
    inWindow: false,
  };
  const i = checkpoints.findIndex((c) => c.k === k);
  const next = i === -1 ? [...checkpoints, update(blank)] : checkpoints.slice();
  if (i !== -1) next[i] = update(checkpoints[i]);
  return next.sort((a, b) => a.k - b.k);
}

// Cells can arrive out of order across parallel sandboxes; index is the key.
function upsertCell(cells: TrialCell[], cell: TrialCell): TrialCell[] {
  const i = cells.findIndex((c) => c.index === cell.index);
  if (i === -1) return [...cells, cell];
  const next = cells.slice();
  next[i] = cell;
  return next;
}
