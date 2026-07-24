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
  | TournamentDone;

// ---- Derived view state (built by the reducer) ----

export type Phase =
  | 'diagnosing'
  | 'detect'
  | 'tournament'
  | 'winner'
  | 'quarantine'
  | 'baseline_verdict';

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
}

export type ConnectionMode = 'live' | 'replay' | 'connecting' | 'disconnected';
