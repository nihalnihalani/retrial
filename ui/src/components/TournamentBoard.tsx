import { useEffect, useState } from 'react';
import { useEventStream } from '../useEventStream';
import { useDaytonaHealth, type PoolHealth } from '../useDaytonaHealth';
import { modelForHypothesis, displayModel } from '../models';
import type { ConnectionMode, Phase } from '../types';
import { DiagnosingView } from './DiagnosingView';
import { DetectPhase } from './DetectPhase';
import { TournamentPhase } from './TournamentPhase';
import { WinnerCard } from './WinnerCard';
import { QuarantineCard } from './QuarantineCard';
import { TerminalVerdictCard } from './TerminalVerdictCard';
import { VoiceAutopsy } from './VoiceAutopsy';
import { GenomeCard } from './GenomeCard';

const DETECT_EXPECTED = 40;
const TOURNAMENT_URL = 'http://localhost:8000/tournament';

// The two calibrated seeds the GO button can kick off. `label` is what the demo
// shows; `paths` are `seed_path` candidates tried in order until one isn't a 404.
// The task's target style is repo-root-relative `seeds/...`; the live engine
// today resolves relative to its CWD (engine/), so `../seeds/...` is what
// currently validates (verified 200). Sending the intended style first means the
// button keeps working across the engine's move to repo-root resolution with no
// edit here — the fallback simply stops being needed.
const SEEDS: { label: string; paths: string[] }[] = [
  { label: 'test_dict_order.py', paths: ['seeds/test_dict_order.py', '../seeds/test_dict_order.py'] },
  { label: 'test_first_key.py', paths: ['seeds/test_first_key.py', '../seeds/test_first_key.py'] },
  // NOTE: the regression seed test_always_fails.py is intentionally NOT here — a
  // stage misclick would stall the demo ~2min in diagnosing. Drive it by curl
  // (POST /tournament {"seed_path":"seeds/test_always_fails.py"}) for the ALWAYS_FAILING
  // card; the TerminalVerdictCard renders it identically however the run is triggered.
];

// Which stepper index each phase lights up. Diagnosing is a pre-phase (nothing
// lit yet); every terminal verdict shares the final "Verdict" step. A
// baseline_verdict short-circuits from detect, so it lights the Verdict step too.
const PHASE_STEP: Record<Phase, number> = {
  diagnosing: -1,
  detect: 0,
  tournament: 1,
  winner: 2,
  quarantine: 2,
  baseline_verdict: 2,
};
const STEPS = ['Detect', 'Tournament', 'Verdict'];

interface Props {
  onRestart: () => void;
}

// The whole board is a pure function of the event stream. It owns nothing but
// layout + the phase router.
export function TournamentBoard({ onRestart }: Props) {
  const { state, mode } = useEventStream();
  const health = useDaytonaHealth(mode);
  const {
    phase,
    detect,
    hermetic,
    hypotheses,
    winner,
    quarantine,
    baselineVerdict,
    testName,
    plannedTrials,
    threshold,
    diagnoseModels,
    diagnoseModelNames,
    genome,
    prUrl,
    voice,
    tournamentDone,
  } = state;
  const winnerIdx = winner ? hypotheses.findIndex((h) => h.id === winner.id) : -1;
  const bestIdx = quarantine ? hypotheses.findIndex((h) => h.id === quarantine.bestId) : -1;
  const winnerHyp = winnerIdx >= 0 ? hypotheses[winnerIdx] : undefined;
  const bestHyp = bestIdx >= 0 ? hypotheses[bestIdx] : undefined;
  // Prefer the model the engine explicitly credits (winner_confirmed.model, or
  // the lane's own model); fall back to positional index-mapping only when none.
  const winnerModel = displayModel(
    winner?.model ?? winnerHyp?.model ?? null,
    modelForHypothesis(diagnoseModelNames, winnerIdx),
  );
  const bestModel = displayModel(
    bestHyp?.model ?? null,
    modelForHypothesis(diagnoseModelNames, bestIdx),
  );

  const [toast, setToast] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const [seedLabel, setSeedLabel] = useState(SEEDS[0].label);

  // A run is "active" from the moment it names a test until it reaches a
  // terminal verdict; the GO button disables itself for that whole window.
  const terminal =
    phase === 'winner' || phase === 'quarantine' || phase === 'baseline_verdict' || tournamentDone;
  const runActive = testName !== null && !terminal;

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  const startRun = async () => {
    if (posting || runActive) return;
    const chosen = SEEDS.find((s) => s.label === seedLabel) ?? SEEDS[0];
    setPosting(true);
    setToast(null);
    try {
      let lastStatus = 0;
      // Try each candidate path; a 404 means "wrong path shape for this engine",
      // so fall through to the next. Any other status (200, 409, 5xx) is final.
      for (const path of chosen.paths) {
        const res = await fetch(TOURNAMENT_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ seed_path: path }),
        });
        lastStatus = res.status;
        if (res.ok) return; // engine now streams diagnosing/run_started over the WS
        if (res.status !== 404) break; // 409 already-running, 5xx, etc. — stop
      }
      const extra =
        lastStatus === 409
          ? ' — a run is already in progress'
          : lastStatus === 404
            ? ' — seed not found by the engine'
            : lastStatus === 400
              ? ' — seed path rejected by the engine'
              : ` — engine returned ${lastStatus}`;
      setToast(`Couldn't start run${extra}`);
    } catch {
      setToast('Engine unreachable on :8000 — is it running?');
    } finally {
      setPosting(false);
    }
  };

  return (
    <div className="board">
      <TopBar
        mode={mode}
        phase={phase}
        testName={testName}
        health={health}
        onRestart={onRestart}
        canGo={mode === 'live'}
        goDisabled={runActive || posting}
        posting={posting}
        seedLabel={seedLabel}
        onSeedChange={setSeedLabel}
        onGo={startRun}
      />

      {toast && (
        <div className="toast toast-error" role="alert">
          {toast}
        </div>
      )}

      <main className="board-main">
        {phase === 'diagnosing' && (
          <DiagnosingView
            testName={testName}
            n={diagnoseModels ?? 4}
            modelNames={diagnoseModelNames}
            hypotheses={hypotheses}
          />
        )}
        {phase === 'detect' && (
          <DetectPhase
            detect={detect}
            expectedTrials={plannedTrials ?? DETECT_EXPECTED}
            threshold={threshold}
            hermetic={hermetic}
          />
        )}
        {phase === 'tournament' && (
          <TournamentPhase
            hypotheses={hypotheses}
            modelNames={diagnoseModelNames}
            plannedTrials={plannedTrials}
            threshold={threshold}
          />
        )}
        {phase === 'winner' && winner && (
          <WinnerCard
            winner={winner}
            hypothesis={winnerHyp}
            detect={detect}
            prUrl={prUrl}
            model={winnerModel}
          />
        )}
        {phase === 'quarantine' && quarantine && (
          <QuarantineCard
            quarantine={quarantine}
            bestHypothesis={bestHyp}
            detect={detect}
            prUrl={prUrl}
            model={bestModel}
          />
        )}
        {phase === 'baseline_verdict' && baselineVerdict && (
          <TerminalVerdictCard state={baselineVerdict} />
        )}
        {voice && (phase === 'winner' || phase === 'quarantine' || phase === 'baseline_verdict') && (
          <VoiceAutopsy url={voice.url} text={voice.text} />
        )}
      </main>

      {(hypotheses.length > 0 || genome) && (
        <footer className="board-footer">
          <GenomeCard hypotheses={hypotheses} genome={genome} />
          <p className="tagline">
            Every flaky test deserves a retrial. <strong>Fifty of them, actually.</strong>
          </p>
        </footer>
      )}
    </div>
  );
}

function TopBar({
  mode,
  phase,
  testName,
  health,
  onRestart,
  canGo,
  goDisabled,
  posting,
  seedLabel,
  onSeedChange,
  onGo,
}: {
  mode: ConnectionMode;
  phase: Phase;
  testName: string | null;
  health: PoolHealth | null;
  onRestart: () => void;
  canGo: boolean;
  goDisabled: boolean;
  posting: boolean;
  seedLabel: string;
  onSeedChange: (label: string) => void;
  onGo: () => void;
}) {
  const activeIndex = PHASE_STEP[phase];
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">⚖</span>
        <span className="brand-name">RETRIAL</span>
        {testName ? (
          <span className="brand-test mono" title={testName}>
            {shortTestName(testName)}
          </span>
        ) : (
          <span className="brand-tag">empirical flake court</span>
        )}
      </div>

      <nav className="stepper">
        {STEPS.map((label, i) => (
          <div
            key={label}
            className={`step ${i === activeIndex ? 'active' : ''} ${
              i < activeIndex ? 'done' : ''
            }`}
          >
            <span className="step-dot" />
            {label}
          </div>
        ))}
      </nav>

      <div className="topbar-right">
        {canGo && (
          <GoControl
            seedLabel={seedLabel}
            onSeedChange={onSeedChange}
            onGo={onGo}
            disabled={goDisabled}
            posting={posting}
          />
        )}
        <SandboxTicker mode={mode} health={health} />
        {mode === 'disconnected' && (
          <span className="stale-note">connection lost — data may be stale</span>
        )}
        <ModeBadge mode={mode} />
        <button className="restart-btn" onClick={onRestart} title="Replay the run">
          ↻ Replay
        </button>
      </div>
    </header>
  );
}

// The live "hit GO on the board" control: pick a seed, POST /tournament, let the
// WebSocket stream drive the board. Disabled while a run is in flight.
function GoControl({
  seedLabel,
  onSeedChange,
  onGo,
  disabled,
  posting,
}: {
  seedLabel: string;
  onSeedChange: (label: string) => void;
  onGo: () => void;
  disabled: boolean;
  posting: boolean;
}) {
  return (
    <div className="go-control">
      <select
        className="go-seed mono"
        value={seedLabel}
        onChange={(e) => onSeedChange(e.target.value)}
        disabled={disabled}
        title="Seed to prosecute"
      >
        {SEEDS.map((s) => (
          <option key={s.label} value={s.label}>
            {s.label}
          </option>
        ))}
      </select>
      <button
        className="go-btn"
        onClick={onGo}
        disabled={disabled}
        title="Start a live run on the engine"
      >
        {posting ? 'STARTING…' : '▶ GO'}
      </button>
    </div>
  );
}

// Daytona presence: live pool counts while LIVE, a static badge in replay.
function SandboxTicker({ mode, health }: { mode: ConnectionMode; health: PoolHealth | null }) {
  if (mode === 'connecting') return null;

  if (mode === 'replay') {
    return (
      <span
        className="sandbox-ticker replay"
        title="Daytona sandbox pool — recorded from a live run, 2026-07-23"
      >
        <span className="hex">⬢</span>
        <span className="sandbox-text">16 sandboxes</span>
        <span className="sandbox-dim">(recorded)</span>
      </span>
    );
  }

  // LIVE — counts appear once the first /health poll lands.
  return (
    <span className="sandbox-ticker live" title="Daytona sandbox pool (live)">
      <span className="hex live-hex">⬢</span>
      {health ? (
        <>
          <span className="sandbox-text">
            <strong>{health.live}</strong> sandboxes live
          </span>
          <span className="sandbox-dim">·</span>
          <span className="sandbox-text">
            <strong>{health.available}</strong> warm
          </span>
        </>
      ) : (
        <span className="sandbox-dim">connecting to pool…</span>
      )}
    </span>
  );
}

// Keep the file::test tail; drop deep directories so the header stays tidy.
function shortTestName(name: string): string {
  const [path, ...rest] = name.split('::');
  const file = path.split('/').pop() ?? path;
  return [file, ...rest].join('::');
}

function ModeBadge({ mode }: { mode: ConnectionMode }) {
  const map: Record<ConnectionMode, { text: string; cls: string; title: string }> = {
    live: { text: 'LIVE', cls: 'live', title: 'Live engine over WebSocket' },
    replay: {
      text: 'RECORDED RUN',
      cls: 'replay',
      title: 'Replay of a real captured run — recorded from a live run, 2026-07-23',
    },
    connecting: { text: 'CONNECTING…', cls: 'connecting', title: 'Connecting to the engine' },
    disconnected: {
      text: 'DISCONNECTED',
      cls: 'disconnected',
      title: 'Connection lost — data may be stale. Attempting to reconnect…',
    },
  };
  const { text, cls, title } = map[mode];
  return (
    <span className={`mode-badge ${cls}`} title={title}>
      <span className="mode-dot" />
      {text}
    </span>
  );
}
