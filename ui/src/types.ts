// The typed event contract the engine streams over ws://localhost:8000/ws.
// The entire UI is a pure function of this event stream — no other inputs.
// Payload shapes are the source of truth agreed with engine-dev; see open
// questions in the handoff if any field name drifts.

export type WilsonCI = [number, number]; // [low, high], each 0..1

// Fires the instant a run is requested, ~20-30s before run_started, while N
// Fireworks models draft competing root-cause theories in parallel.
export interface Diagnosing {
  type: 'diagnosing';
  test_name: string;
  n: number; // number of models drafting hypotheses (== hypotheses used)
  models?: string[]; // real model slugs, when the engine supplies them
}

export interface RunStarted {
  type: 'run_started';
  test_name: string;
  planned_trials: number; // detect-phase rerun budget; sizes the grid
  threshold?: number; // flake decision threshold as a 0..1 fraction, when the engine advertises it
}

export interface TrialDone {
  type: 'trial_done';
  // null => a DETECT-phase rerun of the unmodified test (the lie detector).
  // non-null => a rerun of a specific patched hypothesis in the tournament.
  hypothesis_id: string | null;
  trial_index: number;
  passed: boolean;
  duration_s: number;
}

export interface DetectDone {
  type: 'detect_done';
  flake_rate: number; // 0..1
  wilson_ci: WilsonCI;
  trials: number;
  fails: number;
  // The engine tags the detect pass: "FLAKY" (nondeterministic) or
  // "ALWAYS_FAILING" (a regression — fails every time, nothing to prove).
  verdict?: string;
}

export interface HypothesisCreated {
  type: 'hypothesis_created';
  id: string;
  cause_class: string; // e.g. "race_condition", "order_dependency"
  explanation: string;
  model?: string; // raw slug that authored this hypothesis (same format as diagnosing.models)
  // "no_valid_patch" => the model returned nothing parseable; this lane is
  // created then immediately eliminated and never races (no trials).
  status?: string;
}

export interface HypothesisVerified {
  type: 'hypothesis_verified';
  id: string;
  flake_rate: number;
  wilson_ci: WilsonCI;
  trials: number;
  model?: string; // raw slug (same format as diagnosing.models)
  // Per-lane verdict at max_trials: "STABLE" (a real fix) | "FLAKY" (still flaky)
  // | "INCONCLUSIVE" (CI straddles the threshold — ineligible to win) | "ERROR".
  verdict?: string;
}

export interface HypothesisEliminated {
  type: 'hypothesis_eliminated';
  id: string;
  // optional — reason the evidence knocked it out
  reason?: string;
  model?: string; // raw slug (same format as diagnosing.models)
  // "no_valid_patch" => the model never produced a parseable patch (no trials).
  status?: string;
  neutered?: boolean; // true => disqualified by the neutering guard
}

export interface WinnerConfirmed {
  type: 'winner_confirmed';
  id: string;
  flake_rate: number; // rate during the tournament round
  confirm_flake_rate: number; // rate during the dedicated confirmation round
  confirm_trials?: number; // trials in the confirmation round, when reported
  wilson_ci?: WilsonCI; // CI of the confirmed winner
  orig_flake_rate?: number; // baseline flake before the fix
  braintrust_url?: string; // real Braintrust experiment permalink (the receipt)
  model?: string; // real model slug credited with the winning fix — prefer over index-mapping
}

// No fix stabilized the test — the no-dead-end path: quarantine WITH evidence.
export interface QuarantineConfirmed {
  type: 'quarantine_confirmed';
  best_id: string; // the least-bad hypothesis (best effort, still not stable)
  dossier: {
    flake_rate: number;
    wilson_ci: WilsonCI;
    trials: number;
    reason: string;
  };
  braintrust_url?: string; // real Braintrust autopsy permalink
}

// Cumulative repo flake genome — the compounding flywheel.
export interface GenomeUpdated {
  type: 'genome_updated';
  runs: number;
  by_cause_class: Record<string, number>;
}

// Emitted (after tournament_done) when the fix / quarantine PR is actually opened.
export interface PrOpened {
  type: 'pr_opened';
  url: string;
  verdict?: string; // 'FIXED' | 'QUARANTINE' | …
}

export interface TournamentDone {
  type: 'tournament_done';
}

// The hermetic (network-blocked) second detect pass. Field names mirror the
// coordinator's _hermetic_detect emit EXACTLY and are REQUIRED, not optional,
// so tsc catches any future payload drift instead of silently accepting it.
// (This interface fixes an old drift: the engine emitted hermetic_diagnosis
// but the union never listed it.)
export interface HermeticDiagnosis {
  type: 'hermetic_diagnosis';
  verdict: string; // 'external_dep' | 'env_independent'
  networked_rate: number;
  hermetic_rate: number;
  networked_ci: WilsonCI;
  hermetic_ci: WilsonCI;
}

// The verdict autopsy finished synthesizing (ElevenLabs, output only). Arrives
// AFTER tournament_done — narration is fired off the run thread once the verdict
// is final, so like pr_opened it is a legal post-terminal event and the reducer
// must not treat it as a new run. Absent entirely when NARRATE=0 or no API key.
// `url` is server-relative (`/narration/run-<hex>`); duration_s is derived from
// the CBR mp3 payload, not promised by the API.
export interface NarrationReady {
  type: 'narration_ready';
  run_id: string;
  url: string;
  duration_s: number;
  synth_s: number;
  bytes: number;
  script: string;
  voice_id: string;
  model_id: string;
}

// The fork pool backend fell back to the snapshot pool (sticky, per-process).
export interface PoolDegraded {
  type: 'pool_degraded';
  backend: string;
  fallback: string;
  reason: string;
}

// ---- Preflight / doctor (boot-time config + optional live deep check) ----
export interface PreflightCheck {
  name: string;
  status: 'pass' | 'warn' | 'fail';
  detail: string;
}

// Emitted at server boot (config-level) and, when RETRIAL_PREFLIGHT_LIVE=1, a
// second time after the live fork smoke. Re-seeded by _accept_run so it survives
// BUS.reset(). Field names mirror the engine's run_preflight payload EXACTLY.
export interface PreflightDone {
  type: 'preflight_done';
  ok: boolean;
  live_checked: boolean;
  checks: PreflightCheck[];
  timings: Record<string, number> | null;
}

// ---- Time-travel bisection (fork backend only) ----

export interface BisectStarted {
  type: 'bisect_started';
  suite: string;
  n_tests: number; // prefix length = number of checkpointed tests
  suspect: string;
  max_trials?: number;
}

export interface CheckpointCreated {
  type: 'checkpoint_created';
  k: number; // checkpoint k = suite state after the first k tests (0 = pristine)
  label: string;
  test_passed?: boolean; // informational: the prefix test's own outcome
}

export interface CheckpointProbed {
  type: 'checkpoint_probed';
  k: number;
  flake_rate: number;
  wilson_ci: WilsonCI;
  trials: number;
  verdict?: string;
}

export interface BisectNarrowed {
  type: 'bisect_narrowed';
  lo: number;
  hi: number;
  k: number;
  flipped: boolean;
}

export interface BisectProbeSummary {
  k: number;
  flake_rate: number;
  wilson_ci: WilsonCI;
  trials: number;
  verdict?: string;
}

export interface BisectDone {
  type: 'bisect_done';
  polluter_test?: string | null;
  polluter_index?: number;
  suspect?: string;
  checkpoints?: number;
  reason?: string; // honest-inconclusive explanation when no polluter found
  error?: string; // terminal failure (fork path unavailable, etc.)
  probes?: (BisectProbeSummary | null)[];
  base_flake_rate?: number;
  full_flake_rate?: number;
  confirmed?: boolean;
}

// ---- Promote gate (human approval before PRSmith ships) ----

export interface PromotionPending {
  type: 'promotion_pending';
  test_name: string;
  verdict: string; // 'FIXED' | 'QUARANTINE'
  winner_id?: string | null;
  flake_rate?: number | null; // original (pre-fix) flake rate
  confirm_flake_rate?: number | null; // winner's confirmation-round rate
  braintrust_url?: string | null;
}

export interface PromotionClosed {
  type: 'promotion_closed';
  approved: boolean;
  test_name?: string;
}

// ---- Sandbox Observatory (Phase 2) ----
// Wire types mirror the engine's registry.py record + the B6 event-payload
// table EXACTLY (snake_case over the wire). Fields the engine ALWAYS sends are
// required, not optional, so tsc catches any future payload drift (the same
// tsc-drift rule HermeticDiagnosis follows). See OBSERVATORY-PLAN.md B6.

export type SandboxRole =
  | 'root'
  | 'checkpoint'
  | 'trial-clone'
  | 'snapshot-pool'
  | 'bisect-probe';

export type SandboxLifeState =
  | 'creating'
  | 'warm'
  | 'paused'
  | 'running-cmd'
  | 'destroyed'
  | 'degraded';

export interface SandboxExecEntry {
  cmd: string;
  exit_code: number | null; // null => infra error (no exit code parsed)
  output_tail: string;
  duration_s: number;
  ts: number;
}

// One sandbox record as it rides the wire (registry_snapshot entry +
// sandbox_registered payload). `recent_execs` never rides here — it lives only
// on GET /sandboxes/{id}; the client grows its own copy from sandbox_exec.
export interface SandboxRecordWire {
  id: string;
  role: SandboxRole;
  backend: 'fork' | 'snapshot';
  state: SandboxLifeState;
  parent_id: string | null;
  created_ts: number;
  labels?: Record<string, string>;
  isolation?: string | null;
  exec_count: number;
  preview_url?: string | null;
}

export interface SandboxRegistered extends SandboxRecordWire {
  type: 'sandbox_registered';
}

// Named SandboxStateEvent (not SandboxState) to avoid colliding with the
// SandboxLifeState string-literal type.
export interface SandboxStateEvent {
  type: 'sandbox_state';
  id: string;
  state: SandboxLifeState;
  current_cmd: string | null; // null unless the sandbox is running-cmd
}

export interface SandboxExec {
  type: 'sandbox_exec';
  id: string;
  cmd: string; // <=160
  exit_code: number | null;
  duration_s: number;
  output_tail: string; // <=200
  exec_count: number;
}

export interface SandboxDestroyed {
  type: 'sandbox_destroyed';
  id: string;
  role: SandboxRole;
}

// The spend meter riding on every registry_snapshot (and GET /sandboxes). These
// are OUR monotonic sandbox-lifetime seconds (created->destroyed), NOT Daytona
// billing data; est_cost_usd is a clearly-labeled estimate and is null unless a
// rate is configured (RETRIAL_EST_RATE_PER_SANDBOX_HOUR). Required — the engine
// always sends it after B1, so tsc catches any payload drift.
export interface SpendWire {
  live_sandbox_seconds: number;
  total_sandbox_seconds: number;
  est_cost_usd: number | null;
  rate_per_sandbox_hour: number | null;
  note: string;
}

export interface RegistrySnapshot {
  type: 'registry_snapshot';
  sandboxes: SandboxRecordWire[];
  counts: { live: number; total_ever: number; destroyed: number };
  lineage: Record<string, string[]>;
  spend: SpendWire;
}

export type RetrialEvent =
  | Diagnosing
  | RunStarted
  | TrialDone
  | DetectDone
  | HypothesisCreated
  | HypothesisVerified
  | HypothesisEliminated
  | WinnerConfirmed
  | QuarantineConfirmed
  | GenomeUpdated
  | PrOpened
  | TournamentDone
  | HermeticDiagnosis
  | NarrationReady
  | PoolDegraded
  | PreflightDone
  | BisectStarted
  | CheckpointCreated
  | CheckpointProbed
  | BisectNarrowed
  | BisectDone
  | PromotionPending
  | PromotionClosed
  | SandboxRegistered
  | SandboxStateEvent
  | SandboxExec
  | SandboxDestroyed
  | RegistrySnapshot;

// ---- Derived view state (built by the reducer) ----

export type Phase =
  | 'diagnosing'
  | 'detect'
  | 'tournament'
  | 'winner'
  | 'quarantine'
  | 'baseline_verdict'
  | 'bisect';

export interface TrialCell {
  index: number;
  passed: boolean;
}

export interface DetectState {
  trials: TrialCell[];
  flakeRate: number | null;
  wilsonCi: WilsonCI | null;
  totalTrials: number | null;
  fails: number | null;
  done: boolean;
}

export type HypothesisStatus =
  | 'racing'
  | 'verified'
  | 'eliminated'
  | 'inconclusive'
  | 'winner';

export interface Hypothesis {
  id: string;
  causeClass: string;
  explanation: string;
  trials: TrialCell[];
  flakeRate: number | null;
  wilsonCi: WilsonCI | null;
  status: HypothesisStatus;
  eliminatedReason?: string;
  model: string | null; // raw slug of the authoring model, when the engine supplies it
  verdict?: string; // per-lane verdict from hypothesis_verified (STABLE/FLAKY/INCONCLUSIVE/…)
  noValidPatch?: boolean; // the model produced nothing parseable — this lane never raced
}

export interface WinnerState {
  id: string;
  flakeRate: number;
  confirmFlakeRate: number;
  confirmTrials: number | null;
  wilsonCi: WilsonCI | null;
  origFlakeRate: number | null;
  braintrustUrl: string | null;
  model: string | null; // real winning-model slug when the engine credits one
}

// A terminal verdict reached at the detect pass, before any tournament runs:
// the original test is NOT flaky, so there's nothing to race. `verdict` is the
// engine's detect verdict — "ALWAYS_FAILING" (regression), "STABLE" (already
// green), "INCONCLUSIVE" (CI straddles the threshold) or "ERROR".
export interface BaselineVerdictState {
  verdict: string;
  flakeRate: number | null;
  wilsonCi: WilsonCI | null;
  trials: number | null;
  fails: number | null;
}

export interface QuarantineState {
  bestId: string;
  flakeRate: number;
  wilsonCi: WilsonCI;
  trials: number;
  reason: string;
  braintrustUrl: string | null;
}

export interface GenomeState {
  runs: number;
  byCauseClass: Record<string, number>;
}

// One rail row in the time-travel view: checkpoint k, its probe result (once
// measured), and whether it sits inside the current binary-search window.
export interface BisectCheckpoint {
  k: number;
  label: string;
  testPassed: boolean | null;
  probe: { flakeRate: number; wilsonCi: WilsonCI; trials: number; verdict: string | null } | null;
  inWindow: boolean;
}

export interface BisectState {
  suite: string;
  suspect: string;
  nTests: number;
  checkpoints: BisectCheckpoint[];
  window: [number, number] | null;
  polluter: string | null;
  polluterIndex: number | null;
  reason: string | null;
  done: boolean;
  error: string | null;
}

// The parked human-approval promotion. `open` drives the modal; the record is
// kept after closing so the board can show "awaiting PR…" until pr_opened
// fills prUrl (the honest-state rule: approval is not shipping).
export interface PromotionState {
  testName: string;
  verdict: string;
  winnerId: string | null;
  flakeRate: number | null;
  confirmFlakeRate: number | null;
  braintrustUrl: string | null;
  open: boolean;
  approved: boolean | null; // null until promotion_closed lands
}

export interface HermeticState {
  verdict: string;
  networkedRate: number;
  hermeticRate: number;
  networkedCi: WilsonCI;
  hermeticCi: WilsonCI;
}

// The spoken verdict autopsy, once synthesis finished. null for the entire run
// when narration is off — the board renders no audio affordance at all rather
// than a dead play button.
export interface NarrationState {
  url: string;
  durationS: number;
  script: string;
  voiceId: string;
  modelId: string;
}

// ---- Derived Observatory view state (built by the reducer) ----

// One sandbox as the Observatory renders it: the wire record plus the
// client-grown exec feed and a pulse counter. `recentExecs`/`lastExecSeq` are
// grown from sandbox_exec events (the wire snapshot omits recent_execs), so a
// registry_snapshot re-seed must preserve them by id (see reducer.ts).
export interface ObservatorySandbox extends SandboxRecordWire {
  currentCmd: string | null;
  recentExecs: SandboxExecEntry[]; // grown client-side, capped 20
  lastExecSeq: number; // bumps per exec — retriggers the card pulse
}

export interface ObservatoryState {
  sandboxes: Record<string, ObservatorySandbox>;
  counts: { live: number; totalEver: number; destroyed: number };
  seen: boolean; // any real registry event arrived (live or ?mock=observatory)
  // The spend meter from the latest registry_snapshot. null until a snapshot
  // carrying it arrives (default replay / reconstruction never set it — no
  // recorded lifetimes, so the chip stays hidden and no money is invented).
  spend: SpendWire | null;
}

export interface BoardState {
  phase: Phase;
  testName: string | null;
  diagnoseModels: number | null; // N models drafting during the diagnosing pre-phase
  diagnoseModelNames: string[] | null; // real model slugs, only when supplied
  plannedTrials: number | null;
  threshold: number | null; // flake decision threshold (0..1), from run_started when advertised
  detect: DetectState;
  hypotheses: Hypothesis[];
  winner: WinnerState | null;
  quarantine: QuarantineState | null;
  baselineVerdict: BaselineVerdictState | null;
  genome: GenomeState | null;
  prUrl: string | null;
  tournamentDone: boolean;
  bisect: BisectState | null;
  promotion: PromotionState | null;
  poolDegraded: { reason: string } | null; // honest badge, not an error
  // Boot preflight result (config-level, + optional live deep check). Like
  // poolDegraded it OUTLIVES a single run (a pool-level fact), so resetPerRun
  // leaves it be. Drives the persistent degrade banner (rendered in PKG-B).
  preflight: { ok: boolean; liveChecked: boolean; checks: PreflightCheck[] } | null;
  hermetic: HermeticState | null;
  narration: NarrationState | null;
  // The sandbox observatory feed. Like genome/poolDegraded, it OUTLIVES a
  // single run (the pool is shared across runs), so resetPerRun leaves it be.
  observatory: ObservatoryState;
}

export type ConnectionMode = 'live' | 'replay' | 'connecting' | 'disconnected';
