import { useEventStream } from '../useEventStream';
import { useDaytonaHealth, type PoolHealth } from '../useDaytonaHealth';
import type { ConnectionMode, Phase } from '../types';
import { DiagnosingView } from './DiagnosingView';
import { DetectPhase } from './DetectPhase';
import { TournamentPhase } from './TournamentPhase';
import { WinnerCard } from './WinnerCard';
import { QuarantineCard } from './QuarantineCard';
import { GenomeCard } from './GenomeCard';

const DETECT_EXPECTED = 40;

// Which stepper index each phase lights up. Diagnosing is a pre-phase (nothing
// lit yet); winner + quarantine share the terminal "Verdict" step.
const PHASE_STEP: Record<Phase, number> = {
  diagnosing: -1,
  detect: 0,
  tournament: 1,
  winner: 2,
  quarantine: 2,
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
    hypotheses,
    winner,
    quarantine,
    testName,
    plannedTrials,
    diagnoseModels,
    diagnoseModelNames,
    genome,
    prUrl,
  } = state;
  const winnerHyp = winner ? hypotheses.find((h) => h.id === winner.id) : undefined;
  const bestHyp = quarantine ? hypotheses.find((h) => h.id === quarantine.bestId) : undefined;

  return (
    <div className="board">
      <TopBar
        mode={mode}
        phase={phase}
        testName={testName}
        health={health}
        onRestart={onRestart}
      />

      <main className="board-main">
        {phase === 'diagnosing' && (
          <DiagnosingView
            testName={testName}
            n={diagnoseModels ?? 4}
            modelNames={diagnoseModelNames}
          />
        )}
        {phase === 'detect' && (
          <DetectPhase detect={detect} expectedTrials={plannedTrials ?? DETECT_EXPECTED} />
        )}
        {phase === 'tournament' && <TournamentPhase hypotheses={hypotheses} />}
        {phase === 'winner' && winner && (
          <WinnerCard winner={winner} hypothesis={winnerHyp} detect={detect} prUrl={prUrl} />
        )}
        {phase === 'quarantine' && quarantine && (
          <QuarantineCard
            quarantine={quarantine}
            bestHypothesis={bestHyp}
            detect={detect}
            prUrl={prUrl}
          />
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
}: {
  mode: ConnectionMode;
  phase: Phase;
  testName: string | null;
  health: PoolHealth | null;
  onRestart: () => void;
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
        <SandboxTicker mode={mode} health={health} />
        <ModeBadge mode={mode} />
        <button className="restart-btn" onClick={onRestart} title="Replay the run">
          ↻ Replay
        </button>
      </div>
    </header>
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
  };
  const { text, cls, title } = map[mode];
  return (
    <span className={`mode-badge ${cls}`} title={title}>
      <span className="mode-dot" />
      {text}
    </span>
  );
}
