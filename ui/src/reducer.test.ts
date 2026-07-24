import { describe, it, expect } from 'vitest';
import { initialState, reduce } from './reducer';
import { buildMockScript } from './mockRun';
import type {
  PreflightDone,
  RegistrySnapshot,
  RetrialEvent,
  SandboxRecordWire,
  SpendWire,
} from './types';

// PKG-A binds the TypeScript half that the Python ast emit-site scan cannot see:
// reducer.ts's switch has a catch-all `default: return state`, so a missing
// `case 'preflight_done'` compiles clean and SILENTLY DROPS the event (the
// hermetic_diagnosis lesson). These tests make that impossible to reintroduce.
// PKG-B extends this same file (sandbox events, degrade banner state).

const preflight = (
  ok: boolean,
  liveChecked = false,
  checks: PreflightDone['checks'] = [{ name: 'daytona_api_key', status: 'pass', detail: 'present' }],
): PreflightDone => ({
  type: 'preflight_done',
  ok,
  live_checked: liveChecked,
  checks,
  timings: null,
});

describe('preflight_done reducer arm (PKG-A)', () => {
  it('upserts state.preflight (ok / liveChecked / checks land)', () => {
    const state = reduce(initialState, preflight(false, false, [
      { name: 'daytona_api_key', status: 'fail', detail: 'missing' },
    ]));
    expect(state.preflight).not.toBeNull();
    expect(state.preflight!.ok).toBe(false);
    expect(state.preflight!.liveChecked).toBe(false);
    expect(state.preflight!.checks[0].name).toBe('daytona_api_key');
    expect(state.preflight!.checks[0].status).toBe('fail');
  });

  it('a second preflight_done overwrites the first (live deep-check update)', () => {
    let state = reduce(initialState, preflight(false, false));
    state = reduce(state, preflight(true, true, [
      { name: 'live_smoke', status: 'pass', detail: 'ok' },
    ]));
    expect(state.preflight!.ok).toBe(true);
    expect(state.preflight!.liveChecked).toBe(true);
    expect(state.preflight!.checks[0].name).toBe('live_smoke');
  });

  it('survives the per-run reset (sticky, like poolDegraded)', () => {
    let state = reduce(initialState, preflight(false));
    state = reduce(state, {
      type: 'run_started',
      test_name: 't',
      planned_trials: 10,
    } as RetrialEvent);
    expect(state.preflight).not.toBeNull();
    expect(state.preflight!.ok).toBe(false);
  });

  it('is applied in the baseline_verdict terminal phase (passthrough allowlist)', () => {
    // Drive into baseline_verdict via a non-FLAKY detect verdict.
    let state = reduce(initialState, {
      type: 'detect_done',
      flake_rate: 1,
      wilson_ci: [0.9, 1],
      trials: 20,
      fails: 20,
      verdict: 'ALWAYS_FAILING',
    } as RetrialEvent);
    expect(state.phase).toBe('baseline_verdict');
    state = reduce(state, preflight(false));
    expect(state.preflight).not.toBeNull();
    expect(state.preflight!.ok).toBe(false);
  });
});

describe('default replay leaves preflight untouched (sacred-path complement)', () => {
  it('folding buildMockScript() through the reducer keeps preflight === null', () => {
    let state = initialState;
    for (const { event } of buildMockScript()) {
      state = reduce(state, event);
    }
    expect(state.preflight).toBeNull();
    expect(state.poolDegraded).toBeNull();
    // PKG-B: the new spend state stays null on the sacred path too — the
    // default replay emits no registry_snapshot, so no spend is ever set.
    expect(state.observatory.spend).toBeNull();
  });
});

// ------------------------------- PKG-B ----------------------------------

const wire = (
  id: string,
  over: Partial<SandboxRecordWire> = {},
): SandboxRecordWire => ({
  id,
  role: 'trial-clone',
  backend: 'fork',
  state: 'warm',
  parent_id: 'ckpt',
  created_ts: 0,
  labels: {},
  isolation: null,
  exec_count: 0,
  preview_url: null,
  ...over,
});

const spend = (over: Partial<SpendWire> = {}): SpendWire => ({
  live_sandbox_seconds: 100,
  total_sandbox_seconds: 200,
  est_cost_usd: null,
  rate_per_sandbox_hour: null,
  note: 'estimate',
  ...over,
});

const snapshot = (
  sandboxes: SandboxRecordWire[],
  counts: { live: number; total_ever: number; destroyed: number },
  sp: SpendWire = spend(),
): RegistrySnapshot => ({
  type: 'registry_snapshot',
  sandboxes,
  counts,
  lineage: {},
  spend: sp,
});

describe('the 5 sandbox events (PKG-B)', () => {
  it('registered -> exec -> destroyed updates counts exactly once', () => {
    let state = reduce(initialState, {
      type: 'sandbox_registered',
      ...wire('sb-1', { state: 'creating' }),
    } as RetrialEvent);
    expect(state.observatory.counts).toEqual({ live: 1, totalEver: 1, destroyed: 0 });
    expect(state.observatory.seen).toBe(true);

    state = reduce(state, {
      type: 'sandbox_exec',
      id: 'sb-1',
      cmd: 'pytest',
      exit_code: 0,
      duration_s: 0.5,
      output_tail: 'ok',
      exec_count: 1,
    } as RetrialEvent);
    expect(state.observatory.sandboxes['sb-1'].exec_count).toBe(1);
    expect(state.observatory.sandboxes['sb-1'].lastExecSeq).toBe(1);
    expect(state.observatory.sandboxes['sb-1'].state).toBe('warm');
    // exec did not touch counts
    expect(state.observatory.counts).toEqual({ live: 1, totalEver: 1, destroyed: 0 });

    state = reduce(state, {
      type: 'sandbox_destroyed',
      id: 'sb-1',
      role: 'trial-clone',
    } as RetrialEvent);
    expect(state.observatory.counts).toEqual({ live: 0, totalEver: 1, destroyed: 1 });
    expect(state.observatory.sandboxes['sb-1'].state).toBe('destroyed');
  });

  it('state/exec/destroyed on an unknown id synthesize a stub', () => {
    let state = reduce(initialState, {
      type: 'sandbox_state',
      id: 'ghost',
      state: 'running-cmd',
      current_cmd: 'x',
    } as RetrialEvent);
    expect(state.observatory.sandboxes['ghost']).toBeDefined();
    expect(state.observatory.sandboxes['ghost'].state).toBe('running-cmd');

    state = reduce(initialState, {
      type: 'sandbox_exec',
      id: 'ghost2',
      cmd: 'c',
      exit_code: 1,
      duration_s: 0.1,
      output_tail: '',
      exec_count: 3,
    } as RetrialEvent);
    expect(state.observatory.sandboxes['ghost2']).toBeDefined();
    expect(state.observatory.sandboxes['ghost2'].exec_count).toBe(3);
  });

  it('double sandbox_destroyed does not double-count', () => {
    let state = reduce(initialState, {
      type: 'sandbox_registered',
      ...wire('sb-1'),
    } as RetrialEvent);
    const destroy = { type: 'sandbox_destroyed', id: 'sb-1', role: 'trial-clone' } as RetrialEvent;
    state = reduce(state, destroy);
    state = reduce(state, destroy);
    expect(state.observatory.counts).toEqual({ live: 0, totalEver: 1, destroyed: 1 });
  });

  it('sandbox_exec on a destroyed record leaves state destroyed', () => {
    let state = reduce(initialState, {
      type: 'sandbox_registered',
      ...wire('sb-1'),
    } as RetrialEvent);
    state = reduce(state, { type: 'sandbox_destroyed', id: 'sb-1', role: 'trial-clone' } as RetrialEvent);
    state = reduce(state, {
      type: 'sandbox_exec',
      id: 'sb-1',
      cmd: 'c',
      exit_code: 0,
      duration_s: 0.1,
      output_tail: '',
      exec_count: 9,
    } as RetrialEvent);
    expect(state.observatory.sandboxes['sb-1'].state).toBe('destroyed');
  });

  it('registry_snapshot replaces the map wholesale, preserves exec feed, stores spend', () => {
    let state = reduce(initialState, {
      type: 'sandbox_registered',
      ...wire('sb-1'),
    } as RetrialEvent);
    state = reduce(state, {
      type: 'sandbox_exec',
      id: 'sb-1',
      cmd: 'pytest',
      exit_code: 0,
      duration_s: 0.5,
      output_tail: 'ok',
      exec_count: 1,
    } as RetrialEvent);
    // A snapshot that omits sb-1's exec feed must keep it (merged by id) and
    // drop any id not in the snapshot (sb-1 present, an old sb-2 would vanish).
    state = reduce(
      state,
      snapshot(
        [wire('sb-1', { exec_count: 1 }), wire('sb-9')],
        { live: 2, total_ever: 5, destroyed: 3 },
        spend({ est_cost_usd: 0.12, rate_per_sandbox_hour: 0.1 }),
      ),
    );
    expect(Object.keys(state.observatory.sandboxes).sort()).toEqual(['sb-1', 'sb-9']);
    expect(state.observatory.sandboxes['sb-1'].recentExecs.length).toBe(1);
    expect(state.observatory.sandboxes['sb-1'].lastExecSeq).toBe(1);
    expect(state.observatory.counts).toEqual({ live: 2, totalEver: 5, destroyed: 3 });
    expect(state.observatory.spend!.est_cost_usd).toBe(0.12);
  });
});

describe('degrade banner state (PKG-B)', () => {
  const degrade = { type: 'pool_degraded', backend: 'fork', fallback: 'snapshot', reason: 'fork VM missing' } as RetrialEvent;

  it('pool_degraded sets poolDegraded', () => {
    const state = reduce(initialState, degrade);
    expect(state.poolDegraded).toEqual({ reason: 'fork VM missing' });
  });

  it('poolDegraded survives run/bisect/diagnosing resets (sticky)', () => {
    let state = reduce(initialState, degrade);
    state = reduce(state, { type: 'run_started', test_name: 't', planned_trials: 10 } as RetrialEvent);
    expect(state.poolDegraded).not.toBeNull();
    state = reduce(state, { type: 'diagnosing', test_name: 't', n: 4 } as RetrialEvent);
    expect(state.poolDegraded).not.toBeNull();
    state = reduce(state, {
      type: 'bisect_started',
      suite: 's',
      n_tests: 3,
      suspect: 'x',
    } as RetrialEvent);
    expect(state.poolDegraded).not.toBeNull();
  });

  it('is applied in the baseline_verdict terminal phase (passthrough)', () => {
    let state = reduce(initialState, {
      type: 'detect_done',
      flake_rate: 1,
      wilson_ci: [0.9, 1],
      trials: 20,
      fails: 20,
      verdict: 'ALWAYS_FAILING',
    } as RetrialEvent);
    expect(state.phase).toBe('baseline_verdict');
    state = reduce(state, degrade);
    expect(state.poolDegraded).toEqual({ reason: 'fork VM missing' });
  });
});

// The `default: return state` catch-all means a missing case SILENTLY DROPS the
// event (the hermetic_diagnosis lesson). narration_ready arrives after
// tournament_done, so it also has to prove it doesn't disturb the terminal state.
describe('narration_ready', () => {
  const narration = {
    type: 'narration_ready',
    run_id: 'run-abc',
    url: '/narration/run-abc',
    duration_s: 51.36,
    synth_s: 19.4,
    bytes: 821751,
    script: '[serious] Flake autopsy.',
    voice_id: 'XrExE9yKIg1WjnnlVkGX',
    model_id: 'eleven_v3',
  } as RetrialEvent;

  it('is stored rather than dropped', () => {
    const state = reduce(initialState, narration);
    expect(state.narration).toEqual({
      url: '/narration/run-abc',
      durationS: 51.36,
      script: '[serious] Flake autopsy.',
      voiceId: 'XrExE9yKIg1WjnnlVkGX',
      modelId: 'eleven_v3',
    });
  });

  it('does not disturb the terminal phase it arrives after', () => {
    let state = reduce(initialState, { type: 'tournament_done' } as RetrialEvent);
    const phaseBefore = state.phase;
    state = reduce(state, narration);
    expect(state.tournamentDone).toBe(true);
    expect(state.phase).toBe(phaseBefore);
  });

  it('is cleared by the next run so audio can never outlive its verdict', () => {
    // Audio describing run 1 offered on run 2's board is the stale-bleed bug
    // with a speaker attached.
    let state = reduce(initialState, narration);
    expect(state.narration).not.toBeNull();
    state = reduce(state, {
      type: 'run_started',
      test_name: 't',
      planned_trials: 10,
    } as RetrialEvent);
    expect(state.narration).toBeNull();
  });
});
