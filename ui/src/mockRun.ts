import type { RetrialEvent } from './types';
import realRun from './realRun.json';
import realRunQuarantine from './realRunQuarantine.json';

// Playback of REAL captured event streams (recorded from live runs 2026-07-23),
// not synthetic data. Each recorded frame carries a `_t` seconds timestamp; we
// schedule frames by their inter-frame gaps, compressed for a tight demo and
// with the long live-diagnosis window capped so the board isn't waiting ~25s.
//
// Shape: an ordered list of { after, event } — `after` is ms to wait BEFORE
// emitting the event (relative to the previous one). The player just walks it.

export interface ScriptedEvent {
  after: number;
  event: RetrialEvent;
}

// 'winner' = the winning-fix recording; 'quarantine' = the no-fix recording.
// 'promote' = the winner recording + a scripted promote-gate coda (the gate
// renders with buttons disabled in replay — approval is live-only).
// 'bisect' = a fully scripted time-travel bisection on the checkpoint rail.
// The two new outcomes are OPT-IN demos behind ?mock=…; the default replay
// (no params) is the untouched realRun.json path.
export type MockOutcome = 'winner' | 'quarantine' | 'promote' | 'bisect';
export const MOCK_OUTCOMES: MockOutcome[] = ['winner', 'quarantine', 'promote', 'bisect'];

// A recorded frame: a real event plus capture metadata the reducer ignores.
type RawFrame = RetrialEvent & { _t?: number; seq?: number; ts?: number };

const COMPRESS = 1.7; // 1.5-2x faster than wall-clock
const MIN_GAP_MS = 30;
const MAX_GAP_MS = 4000; // caps the ~25s live-diagnosis window for replay
const STITCH_MS = 250; // gap inserted where two segments are joined (_t resets)
const FIRST_MS = 300;

// Turn a contiguous, time-ordered list of recorded frames into a schedule.
function buildSchedule(frames: RawFrame[]): ScriptedEvent[] {
  const out: ScriptedEvent[] = [];
  let prevT: number | null = null;

  for (const frame of frames) {
    const frameT: number = typeof frame._t === 'number' ? frame._t : prevT ?? 0;
    let after: number;
    if (prevT === null) {
      after = FIRST_MS;
    } else {
      const dt = frameT - prevT;
      // a non-positive delta means we crossed a stitch boundary (per-run _t reset)
      after = dt <= 0 ? STITCH_MS : Math.min(MAX_GAP_MS, (dt * 1000) / COMPRESS);
    }
    out.push({ after: Math.max(MIN_GAP_MS, Math.round(after)), event: frame as RetrialEvent });
    prevT = frameT;
  }
  return out;
}

// The quarantine capture is a ring buffer: the tail (from the LAST run_started)
// is the real quarantine run; earlier frames replay the prior run. We take that
// tail, and re-attach the real diagnosing + genome frames captured just before
// it so the replay still opens on the diagnosis window and has genome data.
function sliceQuarantine(frames: RawFrame[]): RawFrame[] {
  let lastRun = -1;
  frames.forEach((f, i) => {
    if (f.type === 'run_started') lastRun = i;
  });
  if (lastRun < 0) return frames;

  const head = frames.slice(0, lastRun);
  const diagnosing = [...head].reverse().find((f) => f.type === 'diagnosing');
  const genome = [...head].reverse().find((f) => f.type === 'genome_updated');

  const opening: RawFrame[] = [];
  if (diagnosing) opening.push(diagnosing);
  if (genome) opening.push(genome);
  return [...opening, ...frames.slice(lastRun)];
}

export function buildMockScript(outcome: MockOutcome = 'winner'): ScriptedEvent[] {
  if (outcome === 'quarantine') {
    return buildSchedule(sliceQuarantine(realRunQuarantine as unknown as RawFrame[]));
  }
  if (outcome === 'promote') {
    // The real winner recording, plus a scripted promote-gate coda APPENDED
    // after the final recorded frame — never spliced into buildSchedule's
    // delta math, so the recorded portion plays exactly as always. Payload
    // values mirror the recording's own winner_confirmed frame.
    return [...buildSchedule(realRun as unknown as RawFrame[]), ...promoteCoda()];
  }
  if (outcome === 'bisect') {
    return bisectScript();
  }
  // The winning capture is a single clean run — play it as recorded.
  return buildSchedule(realRun as unknown as RawFrame[]);
}

// The scripted promote-gate coda for ?mock=promote. In replay the gate's
// buttons are disabled (approval is live-engine only), so the script simply
// opens the modal and leaves it up for the demo.
function promoteCoda(): ScriptedEvent[] {
  return [
    {
      after: 1200,
      event: {
        type: 'promotion_pending',
        test_name: 'test_first_key.py',
        verdict: 'FIXED',
        winner_id: 'h1',
        flake_rate: 0.5,
        confirm_flake_rate: 0.0,
        braintrust_url:
          'https://www.braintrust.dev/app/NIHAL/p/retrial/experiments/test_first_key.py%2Fh1%2Frun2-ba1dda',
      },
    },
  ];
}

// A fully scripted time-travel bisection for ?mock=bisect: the order_pollution
// demo suite (6 tests, polluter = test_03_cache_writer.py). Synthetic and
// labeled as such by the REPLAY badge — it exercises the checkpoint rail:
// bisect_started → checkpoint_created×6 → endpoint probes → narrowing →
// confirmation probes → bisect_done.
function bisectScript(): ScriptedEvent[] {
  const suite = [
    'test_00_smoke.py',
    'test_01_math.py',
    'test_02_strings.py',
    'test_03_cache_writer.py',
    'test_04_parse.py',
  ];
  const clean = { flake_rate: 0.0, wilson_ci: [0.0, 0.08] as [number, number], trials: 24, verdict: 'STABLE' };
  const dirty = { flake_rate: 0.5, wilson_ci: [0.31, 0.69] as [number, number], trials: 24, verdict: 'FLAKY' };

  const s: ScriptedEvent[] = [
    {
      after: 400,
      event: {
        type: 'bisect_started',
        suite: 'order_pollution',
        n_tests: 5,
        suspect: 'test_05_suspect.py',
        max_trials: 24,
      },
    },
    { after: 700, event: { type: 'checkpoint_created', k: 0, label: 'pristine' } },
  ];
  suite.forEach((name, i) => {
    s.push({
      after: 550,
      event: { type: 'checkpoint_created', k: i + 1, label: name, test_passed: true },
    });
  });
  // Endpoint probes: pristine is clean, full prefix is dirty — the flip exists.
  s.push({ after: 900, event: { type: 'checkpoint_probed', k: 0, ...clean } });
  s.push({ after: 1400, event: { type: 'checkpoint_probed', k: 5, ...dirty } });
  // Binary search: 2 → clean, 3 → clean, 4 → dirty ⇒ polluter is index 3.
  // (Engine ordering: each probe lands first, then the narrowed window with
  // the post-update lo/hi — exactly what FlakeBisector._run emits.)
  s.push({ after: 1300, event: { type: 'checkpoint_probed', k: 2, ...clean } });
  s.push({ after: 400, event: { type: 'bisect_narrowed', lo: 2, hi: 5, k: 2, flipped: false } });
  s.push({ after: 1300, event: { type: 'checkpoint_probed', k: 3, ...clean } });
  s.push({ after: 400, event: { type: 'bisect_narrowed', lo: 3, hi: 5, k: 3, flipped: false } });
  s.push({ after: 1300, event: { type: 'checkpoint_probed', k: 4, ...dirty } });
  s.push({ after: 400, event: { type: 'bisect_narrowed', lo: 3, hi: 4, k: 4, flipped: true } });
  // Full-budget confirmation of both sides of the flip, then the verdict.
  s.push({ after: 1500, event: { type: 'checkpoint_probed', k: 3, ...clean, trials: 24 } });
  s.push({ after: 1500, event: { type: 'checkpoint_probed', k: 4, ...dirty, trials: 24 } });
  s.push({
    after: 900,
    event: {
      type: 'bisect_done',
      polluter_test: 'test_03_cache_writer.py',
      polluter_index: 3,
      suspect: 'test_05_suspect.py',
      checkpoints: 6,
      base_flake_rate: 0.0,
      full_flake_rate: 0.5,
      confirmed: true,
      probes: [
        { k: 0, ...clean },
        { k: 2, ...clean },
        { k: 3, ...clean },
        { k: 4, ...dirty },
        { k: 5, ...dirty },
      ],
    },
  });
  return s;
}
