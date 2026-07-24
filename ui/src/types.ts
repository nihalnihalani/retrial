// The typed event contract the engine streams over ws://localhost:8000/ws.
// The entire UI is a pure function of this event stream — no other inputs.
// Payload shapes are the source of truth agreed with engine-dev; see open
// questions in the handoff if any field name drifts.

export type WilsonCI = [number, number]; // [low, high], each 0..1

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
}

export interface HypothesisCreated {
  type: 'hypothesis_created';
  id: string;
  cause_class: string; // e.g. "race_condition", "order_dependency"
  explanation: string;
}

export interface HypothesisVerified {
  type: 'hypothesis_verified';
  id: string;
  flake_rate: number;
  wilson_ci: WilsonCI;
  trials: number;
}

export interface HypothesisEliminated {
  type: 'hypothesis_eliminated';
  id: string;
  // optional — reason the evidence knocked it out
  reason?: string;
}

export interface WinnerConfirmed {
  type: 'winner_confirmed';
  id: string;
  flake_rate: number; // rate during the tournament round
  confirm_flake_rate: number; // rate during the dedicated confirmation round
}

export interface TournamentDone {
  type: 'tournament_done';
}

export type RetrialEvent =
  | TrialDone
  | DetectDone
  | HypothesisCreated
  | HypothesisVerified
  | HypothesisEliminated
  | WinnerConfirmed
  | TournamentDone;

// ---- Derived view state (built by the reducer) ----

export type Phase = 'detect' | 'tournament' | 'winner';

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

export type HypothesisStatus = 'racing' | 'verified' | 'eliminated' | 'winner';

export interface Hypothesis {
  id: string;
  causeClass: string;
  explanation: string;
  trials: TrialCell[];
  flakeRate: number | null;
  wilsonCi: WilsonCI | null;
  status: HypothesisStatus;
  eliminatedReason?: string;
}

export interface WinnerState {
  id: string;
  flakeRate: number;
  confirmFlakeRate: number;
}

export interface BoardState {
  phase: Phase;
  detect: DetectState;
  hypotheses: Hypothesis[];
  winner: WinnerState | null;
  tournamentDone: boolean;
}

export type ConnectionMode = 'live' | 'replay' | 'connecting';
