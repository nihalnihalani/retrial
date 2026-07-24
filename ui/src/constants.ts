// Single source of truth for the flake decision threshold. The engine is
// standardizing on 10%; if a run advertises its own threshold (run_started
// carries `threshold` as a 0..1 fraction) that value wins, and this is the
// fallback when it doesn't.
export const DEFAULT_THRESHOLD = 0.1; // 10%, as a fraction 0..1
