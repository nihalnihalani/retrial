import { describe, it, expect } from 'vitest';
import { buildMockScript, buildSchedule, mergeSchedules, type ScriptedEvent } from './mockRun';
import { initialState, reduce } from './reducer';
import realRun from './realRun.json';
import type { RetrialEvent } from './types';

// F7b — the AUTOMATED guard on the single most sacred guarantee in the codebase:
// the default replay (no ?mock param) must stay byte-for-byte the untouched
// realRun.json path, and must be incapable of lighting up the Observatory. This
// team already shipped one regression against this invariant (the stale-bleed
// bug) on the strength of manual smoke checks; structural protection (a pure
// reconstruction, new reducer arms only) is necessary but not sufficient.

const REGISTRY_TYPES = [
  'sandbox_registered',
  'sandbox_state',
  'sandbox_exec',
  'sandbox_destroyed',
  'registry_snapshot',
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const asFrames = realRun as any;

describe('default replay is the untouched realRun baseline', () => {
  it('buildMockScript() with no outcome deep-equals buildSchedule(realRun)', () => {
    // Proves the default branch is a pure pass-through of the pre-Observatory
    // baseline — never merged with any new track.
    expect(buildMockScript()).toEqual(buildSchedule(asFrames));
  });

  it('the default schedule emits no registry event types', () => {
    // Also guards realRun.json itself against an accidental hand-edit that
    // would sneak a registry event into the sacred capture.
    const schedule = buildMockScript();
    for (const { event } of schedule) {
      expect(REGISTRY_TYPES).not.toContain(event.type);
    }
  });

  it('the default schedule cannot light up the Observatory', () => {
    // Fold the whole default schedule through the reducer from initialState:
    // the Observatory must stay untouched (seen === false, no sandboxes).
    let state = initialState;
    for (const { event } of buildMockScript()) {
      state = reduce(state, event);
    }
    expect(state.observatory.seen).toBe(false);
    expect(Object.keys(state.observatory.sandboxes)).toHaveLength(0);
  });
});

describe('mergeSchedules — the one new pure helper in the mock path', () => {
  const ev = (type: string): RetrialEvent => ({ type } as unknown as RetrialEvent);

  it('preserves absolute-time ordering across two tracks', () => {
    const a: ScriptedEvent[] = [
      { after: 100, event: ev('a1') }, // abs 100
      { after: 100, event: ev('a2') }, // abs 200
    ];
    const b: ScriptedEvent[] = [
      { after: 150, event: ev('b1') }, // abs 150
      { after: 100, event: ev('b2') }, // abs 250
    ];
    const merged = mergeSchedules(a, b);
    expect(merged.map((m) => m.event.type)).toEqual(['a1', 'b1', 'a2', 'b2']);
    // Deltas roll back up to the same absolute clock.
    let t = 0;
    const abs = merged.map((m) => (t += m.after));
    expect(abs).toEqual([100, 150, 200, 250]);
  });

  it('breaks ties with track A before track B, stably', () => {
    const a: ScriptedEvent[] = [{ after: 100, event: ev('a1') }];
    const b: ScriptedEvent[] = [{ after: 100, event: ev('b1') }];
    const merged = mergeSchedules(a, b);
    expect(merged.map((m) => m.event.type)).toEqual(['a1', 'b1']);
  });

  it('round-trips a single track unchanged in absolute time', () => {
    const a: ScriptedEvent[] = [
      { after: 30, event: ev('x') },
      { after: 70, event: ev('y') },
    ];
    const merged = mergeSchedules(a, []);
    expect(merged.map((m) => m.after)).toEqual([30, 70]);
    expect(merged.map((m) => m.event.type)).toEqual(['x', 'y']);
  });
});
