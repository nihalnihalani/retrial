import type { RetrialEvent } from './types';

// A scripted, realistic replay of a full Retrial run so the board is fully
// demoable with no engine attached. Numbers mirror calibration-results.json:
// the seeded race condition lands ~40-55% flake, the winning fix drives it to 0.
//
// Shape: an ordered list of { after, event } — `after` is ms to wait BEFORE
// emitting the event (relative to the previous one). The player just walks it.

export interface ScriptedEvent {
  after: number;
  event: RetrialEvent;
}

// deterministic PRNG so the replay looks the same every rehearsal
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(42);

const DETECT_TRIALS = 40;
const DETECT_FAIL_RATE = 0.48;
const DETECT_STEP_MS = 360; // ~14.4s of detect, matches the ~15s brief

const HYPOTHESES: { id: string; cause_class: string; explanation: string }[] = [
  {
    id: 'h1',
    cause_class: 'race_condition',
    explanation:
      'Unsynchronized counter increments across 8 threads — read-modify-write is not atomic across bytecode boundaries. Fix: guard with a lock.',
  },
  {
    id: 'h2',
    cause_class: 'shared_state',
    explanation:
      'Module-level `counter` global leaks between test runs; a stale value survives into the next case. Fix: reset in a fixture.',
  },
  {
    id: 'h3',
    cause_class: 'order_dependency',
    explanation:
      'Passes only when it runs after the fixture seeder; alphabetical collection order changes the outcome. Fix: make the test self-seed.',
  },
  {
    id: 'h4',
    cause_class: 'timing',
    explanation:
      'GIL scheduling jitter under load changes thread interleaving. Fix: pin thread count / add a barrier.',
  },
];

// Per-hypothesis tournament behaviour. h1 is the real fix (converges to 0).
// The others keep flaking at various rates and get eliminated.
const PLANS: Record<
  string,
  { trials: number; failRate: number; outcome: 'winner' | 'eliminated'; reason?: string }
> = {
  h1: { trials: 50, failRate: 0.0, outcome: 'winner' },
  h2: { trials: 22, failRate: 0.32, outcome: 'eliminated', reason: 'still 30%+ flaky — CI excludes 0' },
  h3: { trials: 18, failRate: 0.55, outcome: 'eliminated', reason: 'no improvement over baseline' },
  h4: { trials: 24, failRate: 0.21, outcome: 'eliminated', reason: 'CI still spans the threshold' },
};

const TRIAL_STEP_MS = 150; // cadence of interleaved tournament reruns

function wilson(p: number, n: number): [number, number] {
  if (n === 0) return [0, 1];
  const z = 1.96;
  const denom = 1 + (z * z) / n;
  const center = p + (z * z) / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  const lo = Math.max(0, (center - margin) / denom);
  const hi = Math.min(1, (center + margin) / denom);
  return [round3(lo), round3(hi)];
}

const round3 = (x: number) => Math.round(x * 1000) / 1000;

export function buildMockScript(): ScriptedEvent[] {
  const script: ScriptedEvent[] = [];

  // ---- DETECT: rerun the unmodified test, ~48% fail ----
  let detectFails = 0;
  for (let i = 0; i < DETECT_TRIALS; i++) {
    const passed = rand() >= DETECT_FAIL_RATE;
    if (!passed) detectFails++;
    script.push({
      after: i === 0 ? 700 : DETECT_STEP_MS,
      event: {
        type: 'trial_done',
        hypothesis_id: null,
        trial_index: i,
        passed,
        duration_s: round3(0.28 + rand() * 0.5),
      },
    });
  }
  const detectRate = detectFails / DETECT_TRIALS;
  script.push({
    after: 500,
    event: {
      type: 'detect_done',
      flake_rate: round3(detectRate),
      wilson_ci: wilson(detectRate, DETECT_TRIALS),
      trials: DETECT_TRIALS,
      fails: detectFails,
    },
  });

  // ---- DIAGNOSE: hypotheses appear as Fireworks models return ----
  for (let i = 0; i < HYPOTHESES.length; i++) {
    script.push({
      after: i === 0 ? 900 : 550,
      event: { type: 'hypothesis_created', ...HYPOTHESES[i] },
    });
  }

  // ---- TOURNAMENT: interleave reruns across all racing hypotheses ----
  const cursors: Record<string, { i: number; fails: number }> = {};
  const active = HYPOTHESES.map((h) => h.id);
  HYPOTHESES.forEach((h) => (cursors[h.id] = { i: 0, fails: 0 }));

  const remaining = new Set(active);
  const verifiedEmitted = new Set<string>();
  let first = true;

  while (remaining.size > 0) {
    for (const id of Array.from(remaining)) {
      const plan = PLANS[id];
      const cur = cursors[id];
      if (cur.i >= plan.trials) {
        remaining.delete(id);
        continue;
      }
      const passed = rand() >= plan.failRate;
      if (!passed) cur.fails++;
      script.push({
        after: first ? 700 : TRIAL_STEP_MS,
        event: {
          type: 'trial_done',
          hypothesis_id: id,
          trial_index: cur.i,
          passed,
          duration_s: round3(0.24 + rand() * 0.5),
        },
      });
      first = false;
      cur.i++;

      // When a hypothesis reaches its trial budget, emit verify/eliminate.
      if (cur.i >= plan.trials) {
        const rate = cur.fails / plan.trials;
        script.push({
          after: 120,
          event: {
            type: 'hypothesis_verified',
            id,
            flake_rate: round3(rate),
            wilson_ci: wilson(rate, plan.trials),
            trials: plan.trials,
          },
        });
        verifiedEmitted.add(id);
        if (plan.outcome === 'eliminated') {
          script.push({
            after: 250,
            event: { type: 'hypothesis_eliminated', id, reason: plan.reason },
          });
        }
        remaining.delete(id);
      }
    }
  }

  // ---- CONFIRM: winner survives a dedicated confirmation round ----
  script.push({
    after: 900,
    event: {
      type: 'winner_confirmed',
      id: 'h1',
      flake_rate: 0.0,
      confirm_flake_rate: 0.0,
    },
  });
  script.push({ after: 400, event: { type: 'tournament_done' } });

  return script;
}
