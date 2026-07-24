import type { BoardState, ObservatorySandbox, ObservatoryState } from './types';

// The replay-reconstruction derivation: a READ-ONLY Observatory view synthesized
// from the recorded trial events already in BoardState, so the panel is never
// empty in the default replay (which emits NO registry events) — WITHOUT
// touching the sacred realRun.json flow or the reducer. It is a pure function
// of BoardState (deterministic: the same replay renders the same cards every
// pass), and the panel labels this source "replay reconstruction" — an honest
// derived visualization of recorded trial events, NOT recorded registry data.
//
// Called only from SandboxObservatory when state.observatory.seen === false.

const REPLAY_POOL = 16; // matches the recorded SandboxTicker's "16 sandboxes"

export function reconstructObservatory(state: BoardState): ObservatoryState {
  // A running bisection reconstructs its checkpoint spine; everything else
  // reconstructs the 16-sandbox snapshot pool that ran the tournament.
  if (state.bisect) return reconstructBisect(state);
  return reconstructTournament(state);
}

function reconstructTournament(state: BoardState): ObservatoryState {
  const detectTrials = state.detect.trials.length;
  const hypoTrials = state.hypotheses.reduce((n, h) => n + h.trials.length, 0);
  const totalExecs = detectTrials + hypoTrials;

  // Round-robin the observed trial count across the pool so the cards show
  // honest, proportional exec load (not fabricated numbers).
  const base = Math.floor(totalExecs / REPLAY_POOL);
  const rem = totalExecs % REPLAY_POOL;

  // The run is "in flight" while we're still in detect/tournament and no
  // terminal card has landed — then one card shows a live running-cmd.
  const inFlight = state.phase === 'detect' || state.phase === 'tournament';
  const activeCard = totalExecs > 0 ? (totalExecs - 1) % REPLAY_POOL : 0;

  const sandboxes: Record<string, ObservatorySandbox> = {};
  for (let i = 0; i < REPLAY_POOL; i++) {
    const id = `sb-replay-${String(i + 1).padStart(2, '0')}`;
    const execCount = base + (i < rem ? 1 : 0);
    const running = inFlight && i === activeCard && execCount > 0;
    sandboxes[id] = {
      id,
      role: 'snapshot-pool',
      backend: 'snapshot',
      state: running ? 'running-cmd' : 'warm',
      parent_id: null,
      created_ts: 0,
      labels: {},
      isolation: null,
      exec_count: execCount,
      preview_url: null,
      currentCmd: running ? 'pytest -x (rerun)' : null,
      recentExecs: [],
      lastExecSeq: 0,
    };
  }
  return finalize(sandboxes);
}

function reconstructBisect(state: BoardState): ObservatoryState {
  const bisect = state.bisect!;
  const sandboxes: Record<string, ObservatorySandbox> = {};

  const rootId = 'sb-bisect-root';
  sandboxes[rootId] = {
    id: rootId,
    role: 'root',
    backend: 'fork',
    state: bisect.done ? 'paused' : 'warm',
    parent_id: null,
    created_ts: 0,
    labels: { suite: bisect.suite },
    isolation: 'sandbox',
    exec_count: 0,
    preview_url: null,
    currentCmd: null,
    recentExecs: [],
    lastExecSeq: 0,
  };

  for (const c of bisect.checkpoints) {
    const ckptId = `sb-ckpt-${c.k}`;
    const probed = c.probe != null;
    sandboxes[ckptId] = {
      id: ckptId,
      role: 'checkpoint',
      backend: 'fork',
      state: 'paused',
      parent_id: rootId,
      created_ts: 0,
      labels: { k: String(c.k), label: c.label },
      isolation: 'sandbox',
      exec_count: 0,
      preview_url: null,
      currentCmd: null,
      recentExecs: [],
      lastExecSeq: 0,
    };
    // A probed checkpoint spun a bisect-probe fork; once the bisection is done
    // the probes have been reaped (leaf-first), so show them destroyed.
    if (probed) {
      const probeId = `sb-probe-${c.k}`;
      sandboxes[probeId] = {
        id: probeId,
        role: 'bisect-probe',
        backend: 'fork',
        state: bisect.done ? 'destroyed' : 'warm',
        parent_id: ckptId,
        created_ts: 0,
        labels: { k: String(c.k) },
        isolation: 'sandbox',
        exec_count: c.probe!.trials,
        preview_url: null,
        currentCmd: null,
        recentExecs: [],
        lastExecSeq: 0,
      };
    }
  }
  return finalize(sandboxes);
}

// Compute counts from the synthesized set and mark the view unseen (it is a
// reconstruction, not real registry traffic — the panel's source label depends
// on state.observatory.seen, never on this flag).
function finalize(sandboxes: Record<string, ObservatorySandbox>): ObservatoryState {
  const all = Object.values(sandboxes);
  const destroyed = all.filter((s) => s.state === 'destroyed').length;
  return {
    sandboxes,
    counts: { live: all.length - destroyed, totalEver: all.length, destroyed },
    seen: false,
    // A reconstruction has no recorded lifetimes — it never invents money.
    spend: null,
  };
}
