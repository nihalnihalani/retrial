import { useEventStream } from '../useEventStream';
import type { ConnectionMode, Phase } from '../types';
import { DetectPhase } from './DetectPhase';
import { TournamentPhase } from './TournamentPhase';
import { WinnerCard } from './WinnerCard';
import { GenomeCard } from './GenomeCard';

const DETECT_EXPECTED = 40;

const STEPS: { key: Phase; label: string }[] = [
  { key: 'detect', label: 'Detect' },
  { key: 'tournament', label: 'Tournament' },
  { key: 'winner', label: 'Verdict' },
];

interface Props {
  onRestart: () => void;
}

// The whole board is a pure function of the event stream. It owns nothing but
// layout + the phase router.
export function TournamentBoard({ onRestart }: Props) {
  const { state, mode } = useEventStream();
  const { phase, detect, hypotheses, winner } = state;
  const winnerHyp = winner ? hypotheses.find((h) => h.id === winner.id) : undefined;

  return (
    <div className="board">
      <TopBar mode={mode} phase={phase} onRestart={onRestart} />

      <main className="board-main">
        {phase === 'detect' && (
          <DetectPhase detect={detect} expectedTrials={DETECT_EXPECTED} />
        )}
        {phase === 'tournament' && <TournamentPhase hypotheses={hypotheses} />}
        {phase === 'winner' && winner && (
          <WinnerCard winner={winner} hypothesis={winnerHyp} detect={detect} />
        )}
      </main>

      {hypotheses.length > 0 && (
        <footer className="board-footer">
          <GenomeCard hypotheses={hypotheses} />
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
  onRestart,
}: {
  mode: ConnectionMode;
  phase: Phase;
  onRestart: () => void;
}) {
  const activeIndex = STEPS.findIndex((s) => s.key === phase);
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">⚖</span>
        <span className="brand-name">RETRIAL</span>
        <span className="brand-tag">empirical flake court</span>
      </div>

      <nav className="stepper">
        {STEPS.map((s, i) => (
          <div
            key={s.key}
            className={`step ${i === activeIndex ? 'active' : ''} ${
              i < activeIndex ? 'done' : ''
            }`}
          >
            <span className="step-dot" />
            {s.label}
          </div>
        ))}
      </nav>

      <div className="topbar-right">
        <ModeBadge mode={mode} />
        <button className="restart-btn" onClick={onRestart} title="Replay the run">
          ↻ Replay
        </button>
      </div>
    </header>
  );
}

function ModeBadge({ mode }: { mode: ConnectionMode }) {
  const map: Record<ConnectionMode, { text: string; cls: string }> = {
    live: { text: 'LIVE', cls: 'live' },
    replay: { text: 'REPLAY', cls: 'replay' },
    connecting: { text: 'CONNECTING…', cls: 'connecting' },
  };
  const { text, cls } = map[mode];
  return (
    <span className={`mode-badge ${cls}`}>
      <span className="mode-dot" />
      {text}
    </span>
  );
}
