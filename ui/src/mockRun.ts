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
export type MockOutcome = 'winner' | 'quarantine';

// A recorded frame: a real event plus capture metadata the reducer ignores.
type RawFrame = RetrialEvent & { _t?: number; seq?: number; ts?: number };

const COMPRESS = 0.5; // 1.5-2x faster than wall-clock
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
  // The winning capture is a single clean run — play it as recorded.
  const frames = (realRun as unknown as RawFrame[]).map((f) =>
    f.type === 'diagnosing'
      ? { ...f, models: ['accounts/fireworks/models/glm-5p2', 'glm-5p1', 'kimi-k2p6', 'deepseek-v4-pro'] }
      : f,
  );
  return buildSchedule(frames);
}
