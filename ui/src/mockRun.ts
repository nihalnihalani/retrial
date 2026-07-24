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
// 'observatory' = the recorded winner run with a PARALLEL scripted registry
// track interleaved (the fork story: root → checkpoint → clones, exec pulses, a
// mid-run destroy wave) so the Sandbox Observatory panel churns live alongside
// the tournament. The recorded frames keep their exact relative timing.
export type MockOutcome = 'winner' | 'quarantine' | 'promote' | 'bisect' | 'observatory';
export const MOCK_OUTCOMES: MockOutcome[] = [
  'winner',
  'quarantine',
  'promote',
  'bisect',
  'observatory',
];

// A recorded frame: a real event plus capture metadata the reducer ignores.
type RawFrame = RetrialEvent & { _t?: number; seq?: number; ts?: number };

const COMPRESS = 1.7; // 1.5-2x faster than wall-clock
const MIN_GAP_MS = 30;
const MAX_GAP_MS = 4000; // caps the ~25s live-diagnosis window for replay
const STITCH_MS = 250; // gap inserted where two segments are joined (_t resets)
const FIRST_MS = 300;

// Turn a contiguous, time-ordered list of recorded frames into a schedule.
// Exported for the F7b sacred-default-replay guard (mockRun.test.ts).
export function buildSchedule(frames: RawFrame[]): ScriptedEvent[] {
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
  if (outcome === 'observatory') {
    // The recorded winner run with the scripted registry track merged in by
    // absolute time — the recorded frames keep their EXACT relative timing;
    // only the parallel observatory track is interleaved. All other branches,
    // and the default (no-arg) branch below, are untouched.
    return mergeSchedules(buildSchedule(realRun as unknown as RawFrame[]), observatoryTrack());
  }
  // The winning capture is a single clean run — play it as recorded.
  return buildSchedule(realRun as unknown as RawFrame[]);
}

// Merge two delta-encoded schedules into one, stably, by ABSOLUTE time. Each
// schedule's `after` is a gap relative to its own previous event; we roll each
// up to an absolute clock, concatenate, stable-sort by that clock (ties keep
// a-before-b so the recorded frame leads its co-timed registry frame), then
// convert back to deltas. buildSchedule's own math is never touched — this only
// interleaves an already-built schedule with a parallel track. Pure and
// unit-testable (F7b).
export function mergeSchedules(a: ScriptedEvent[], b: ScriptedEvent[]): ScriptedEvent[] {
  const abs = (list: ScriptedEvent[], bias: number) => {
    let t = 0;
    return list.map((e, i) => {
      t += e.after;
      // `order` breaks ties deterministically: track A (bias 0) before track B
      // (bias 1), then by original index within a track.
      return { at: t, order: bias, idx: i, event: e.event };
    });
  };
  const merged = [...abs(a, 0), ...abs(b, 1)].sort(
    (x, y) => x.at - y.at || x.order - y.order || x.idx - y.idx,
  );
  const out: ScriptedEvent[] = [];
  let prev = 0;
  for (const m of merged) {
    out.push({ after: Math.max(0, m.at - prev), event: m.event });
    prev = m.at;
  }
  return out;
}

// A scripted registry feed that tells the fork story alongside the recorded
// winner run: an opening registry_snapshot (16 snapshot-pool sandboxes, matching
// the recorded ticker), then a fork spine (root → checkpoint → 8 trial-clones)
// with interleaved sandbox_exec pulses (exit_code 0/1 mix mirroring the recorded
// pass/fail rhythm), then a mid-run destroy wave on the non-reusable clones.
// Closing arithmetic is honest: total_ever = 26 (16 pool + root + checkpoint +
// 8 trial-clones), destroyed = 8, live = 18; the reducer recomputes counts from
// the stream, so these are the numbers a live registry would report.
function observatoryTrack(): ScriptedEvent[] {
  const s: ScriptedEvent[] = [];
  const pool = Array.from({ length: 16 }, (_, i) => ({
    id: `sb-pool-${String(i + 1).padStart(2, '0')}`,
    role: 'snapshot-pool' as const,
    backend: 'snapshot' as const,
    state: 'warm' as const,
    parent_id: null,
    created_ts: 0,
    labels: {},
    isolation: null,
    exec_count: 0,
    preview_url: null,
  }));

  // Opening snapshot: the 16 warm pool sandboxes are already provisioned.
  s.push({
    after: 350,
    event: {
      type: 'registry_snapshot',
      sandboxes: pool,
      counts: { live: 16, total_ever: 16, destroyed: 0 },
      lineage: {},
    },
  });

  // The fork spine comes up: a root, then a paused checkpoint forked from it.
  const rootId = 'sb-fork-root';
  const ckptId = 'sb-fork-ckpt';
  s.push({
    after: 900,
    event: {
      type: 'sandbox_registered',
      id: rootId,
      role: 'root',
      backend: 'fork',
      state: 'creating',
      parent_id: null,
      created_ts: 0,
      labels: { flake_class: 'order' },
      isolation: 'sandbox',
      exec_count: 0,
      preview_url: null,
    },
  });
  s.push({ after: 500, event: { type: 'sandbox_state', id: rootId, state: 'warm', current_cmd: null } });
  s.push({
    after: 600,
    event: {
      type: 'sandbox_registered',
      id: ckptId,
      role: 'checkpoint',
      backend: 'fork',
      state: 'paused',
      parent_id: rootId,
      created_ts: 0,
      labels: { flake_class: 'order' },
      isolation: 'sandbox',
      exec_count: 0,
      preview_url: null,
    },
  });

  // Eight trial-clones fork off the checkpoint and each runs a rerun. The
  // exit-code mix (mostly 0, a couple of 1s) mirrors the recorded flake rhythm.
  const cloneIds: string[] = [];
  const exitPattern = [0, 1, 0, 0, 1, 0, 0, 0];
  for (let i = 0; i < 8; i++) {
    const id = `sb-clone-${String(i + 1).padStart(2, '0')}`;
    cloneIds.push(id);
    s.push({
      after: 260,
      event: {
        type: 'sandbox_registered',
        id,
        role: 'trial-clone',
        backend: 'fork',
        state: 'warm',
        parent_id: ckptId,
        created_ts: 0,
        labels: { flake_class: 'order' },
        isolation: 'sandbox',
        exec_count: 0,
        preview_url: null,
      },
    });
    s.push({
      after: 420,
      event: {
        type: 'sandbox_exec',
        id,
        cmd: 'pytest -x tests/test_first_key.py',
        exit_code: exitPattern[i],
        duration_s: 0.6 + i * 0.05,
        output_tail: exitPattern[i] === 0 ? '1 passed in 0.5s' : '1 failed in 0.6s',
        exec_count: 1,
      },
    });
  }

  // A mid-run destroy wave: the non-reusable clones are reaped leaf-first.
  for (const id of cloneIds) {
    s.push({ after: 180, event: { type: 'sandbox_destroyed', id, role: 'trial-clone' } });
  }

  return s;
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
