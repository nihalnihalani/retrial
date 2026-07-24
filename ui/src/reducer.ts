import type {
  BisectCheckpoint,
  BisectState,
  BoardState,
  Hypothesis,
  ObservatorySandbox,
  RetrialEvent,
  SandboxRecordWire,
  TrialCell,
} from './types';

const EXEC_FEED_CAP = 20; // client-side ring cap, mirrors RETRIAL_EXEC_HISTORY

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
  preflight: null,
  hermetic: null,
  narration: null,
  observatory: {
    sandboxes: {},
    counts: { live: 0, totalEver: 0, destroyed: 0 },
    seen: false,
    spend: null,
  },
};

// Clears everything that belongs to a single run so a second live run can't
// inherit the first run's board, while PRESERVING the cumulative genome, the
// sticky poolDegraded badge, the boot `preflight` result, and the sandbox
// `observatory` — all of which outlive a single run (the pool is shared and
// pre-warmed across runs, exactly like the server's REGISTRY, which _accept_run
// re-seeds but never wipes) — and
// the diagnosing pre-phase's model list / test name, which the caller re-sets.
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
    // Per-run, unlike genome/poolDegraded/observatory: the audio describes ONE
    // verdict. Leaving it would let run 2 offer a play button that speaks run
    // 1's numbers — the audio equivalent of the ring-buffer stale-bleed bug.
    narration: null,
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
      // Preflight is pool-level (a boot fact), honest to show post-terminal.
      event.type === 'preflight_done' ||
      event.type === 'hermetic_diagnosis' ||
      // Sandbox registry traffic is pool-level and honest post-terminal (the
      // pool keeps living after a verdict), same rationale as pool_degraded.
      event.type === 'sandbox_registered' ||
      event.type === 'sandbox_state' ||
      event.type === 'sandbox_exec' ||
      event.type === 'sandbox_destroyed' ||
      event.type === 'registry_snapshot';
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

    case 'narration_ready': {
      // Post-terminal by design (fired after tournament_done), so this must NOT
      // touch phase or tournamentDone — same contract as pr_opened.
      return {
        ...state,
        narration: {
          url: event.url,
          durationS: event.duration_s,
          script: event.script,
          voiceId: event.voice_id,
          modelId: event.model_id,
        },
      };
    }

    case 'pool_degraded': {
      // An honest badge, not an error: the fork backend fell back to the
      // snapshot pool and the run continues on it.
      return { ...state, poolDegraded: { reason: event.reason } };
    }

    case 'preflight_done': {
      // Upsert (last write wins): a second preflight_done is the live deep-check
      // updating the config-level result. Sticky like poolDegraded — never
      // cleared in resetPerRun (a pool-level boot fact). Banner RENDERING lands
      // in PKG-B; the state plumbing lands HERE so the event is never silently
      // dropped (reducer.ts's default-return-state would swallow it otherwise —
      // the hermetic_diagnosis lesson).
      return {
        ...state,
        preflight: {
          ok: event.ok,
          liveChecked: event.live_checked,
          checks: event.checks,
        },
      };
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
          wilsonCi: event.wilson_ci ?? null,
          confirmFlakeRate: event.confirm_flake_rate ?? null,
          confirmWilsonCi: event.confirm_wilson_ci ?? null,
          confirmTrials: event.confirm_trials ?? null,
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

    // ---- Sandbox Observatory ----
    // All arms are pure upserts, out-of-order safe. The default replay emits
    // NONE of these types, so every existing transition above is untouched and
    // default behavior stays byte-for-byte identical by construction.

    case 'sandbox_registered': {
      const obs = state.observatory;
      const existed = obs.sandboxes[event.id] !== undefined;
      const prev = obs.sandboxes[event.id];
      const rec: ObservatorySandbox = {
        id: event.id,
        role: event.role,
        backend: event.backend,
        state: event.state,
        parent_id: event.parent_id,
        created_ts: event.created_ts,
        labels: event.labels,
        isolation: event.isolation,
        exec_count: event.exec_count,
        preview_url: event.preview_url,
        currentCmd: null,
        recentExecs: prev?.recentExecs ?? [],
        lastExecSeq: prev?.lastExecSeq ?? 0,
      };
      return {
        ...state,
        observatory: {
          ...obs,
          seen: true,
          sandboxes: { ...obs.sandboxes, [event.id]: rec },
          counts: existed
            ? obs.counts
            : {
                ...obs.counts,
                live: obs.counts.live + 1,
                totalEver: obs.counts.totalEver + 1,
              },
        },
      };
    }

    case 'sandbox_state': {
      const obs = state.observatory;
      const prev = obs.sandboxes[event.id];
      // Unknown id ⇒ synthesize a stub (lossy-replay rule, same spirit as
      // ensureBisect) so a state event that outran its registration isn't lost.
      const base = prev ?? stubSandbox(event.id);
      const wasDestroyed = base.state === 'destroyed';
      const nowDestroyed = event.state === 'destroyed';
      return {
        ...state,
        observatory: {
          ...obs,
          seen: true,
          sandboxes: {
            ...obs.sandboxes,
            [event.id]: {
              ...base,
              state: event.state,
              currentCmd: event.current_cmd,
            },
          },
          // sandbox_state can carry a 'degraded'/'destroyed' transition; only
          // adjust counts on the destroyed edge (guard double-count by state).
          counts:
            !wasDestroyed && nowDestroyed
              ? {
                  ...obs.counts,
                  live: Math.max(0, obs.counts.live - 1),
                  destroyed: obs.counts.destroyed + 1,
                }
              : obs.counts,
        },
      };
    }

    case 'sandbox_exec': {
      const obs = state.observatory;
      const prev = obs.sandboxes[event.id] ?? stubSandbox(event.id);
      const entry = {
        cmd: event.cmd,
        exit_code: event.exit_code,
        output_tail: event.output_tail,
        duration_s: event.duration_s,
        ts: 0,
      };
      const recentExecs = [...prev.recentExecs, entry].slice(-EXEC_FEED_CAP);
      return {
        ...state,
        observatory: {
          ...obs,
          seen: true,
          sandboxes: {
            ...obs.sandboxes,
            [event.id]: {
              ...prev,
              recentExecs,
              exec_count: event.exec_count,
              lastExecSeq: prev.lastExecSeq + 1,
              // The exec event IS the running-cmd -> warm transition (engine
              // volume discipline: no paired sandbox_state per exec).
              state: prev.state === 'destroyed' ? prev.state : 'warm',
              currentCmd: null,
            },
          },
        },
      };
    }

    case 'sandbox_destroyed': {
      const obs = state.observatory;
      const prev = obs.sandboxes[event.id];
      const base = prev ?? { ...stubSandbox(event.id), role: event.role };
      const wasDestroyed = base.state === 'destroyed';
      return {
        ...state,
        observatory: {
          ...obs,
          seen: true,
          sandboxes: {
            ...obs.sandboxes,
            [event.id]: { ...base, state: 'destroyed', currentCmd: null },
          },
          counts: wasDestroyed
            ? obs.counts
            : {
                ...obs.counts,
                live: Math.max(0, obs.counts.live - 1),
                destroyed: obs.counts.destroyed + 1,
              },
        },
      };
    }

    case 'registry_snapshot': {
      // The authoritative re-seed (emitted at every run acceptance, and on
      // demand): REPLACE the map wholesale from event.sandboxes + event.counts.
      // Preserve each existing record's client-grown recentExecs/lastExecSeq by
      // id so the drawer's exec feed and the card pulse don't blank on re-seed.
      // Safe for memory: the server bounds `sandboxes` to live + last-50
      // destroyed (B1 retention window); the header counters stay exact from
      // event.counts regardless.
      const prevMap = state.observatory.sandboxes;
      const sandboxes: Record<string, ObservatorySandbox> = {};
      for (const wire of event.sandboxes) {
        const prev = prevMap[wire.id];
        sandboxes[wire.id] = mergeWire(wire, prev);
      }
      return {
        ...state,
        observatory: {
          seen: true,
          sandboxes,
          counts: {
            live: event.counts.live,
            totalEver: event.counts.total_ever,
            destroyed: event.counts.destroyed,
          },
          // The snapshot is the one honest spend source (per-event spend
          // updates would be fake precision); the live poll refreshes it too.
          spend: event.spend,
        },
      };
    }

    default:
      return state;
  }
}

// A placeholder sandbox for a state/exec/destroyed event that outran its
// registration on a lossy replay — the drawer stays populated rather than
// dropping the event. Real data overwrites every field on the next snapshot.
function stubSandbox(id: string): ObservatorySandbox {
  return {
    id,
    role: 'snapshot-pool',
    backend: 'snapshot',
    state: 'creating',
    parent_id: null,
    created_ts: 0,
    labels: {},
    isolation: null,
    exec_count: 0,
    preview_url: null,
    currentCmd: null,
    recentExecs: [],
    lastExecSeq: 0,
  };
}

// Fold a wire record into an ObservatorySandbox, carrying over the client-grown
// exec feed / pulse counter from any prior record with the same id.
function mergeWire(
  wire: SandboxRecordWire,
  prev: ObservatorySandbox | undefined,
): ObservatorySandbox {
  return {
    ...wire,
    currentCmd: prev?.currentCmd ?? null,
    recentExecs: prev?.recentExecs ?? [],
    lastExecSeq: prev?.lastExecSeq ?? 0,
  };
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
