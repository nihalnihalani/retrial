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

// The fork pool backend fell back to the snapshot pool (sticky, per-process).
export interface PoolDegraded {
  type: 'pool_degraded';
  backend: string;
  fallback: string;
  reason: string;
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
  | PoolDegraded
  | BisectStarted
  | CheckpointCreated
  | CheckpointProbed
  | BisectNarrowed
  | BisectDone
  | PromotionPending
  | PromotionClosed;

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
  hermetic: HermeticState | null;
}

export type ConnectionMode = 'live' | 'replay' | 'connecting' | 'disconnected';
