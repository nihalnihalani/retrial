import { useEventStream } from '../useEventStream';
import type { ConnectionMode, Phase } from '../types';
import { DetectPhase } from './DetectPhase';
import { TournamentPhase } from './TournamentPhase';
import { WinnerCard } from './WinnerCard';
import { QuarantineCard } from './QuarantineCard';
import { GenomeCard } from './GenomeCard';

const DETECT_EXPECTED = 40;

// Which stepper index each phase lights up. winner + quarantine share the
// terminal "Verdict" step.
const PHASE_STEP: Record<Phase, number> = {
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
  const { phase, detect, hypotheses, winner, quarantine, testName, plannedTrials } = state;
  const winnerHyp = winner ? hypotheses.find((h) => h.id === winner.id) : undefined;
  const bestHyp = quarantine ? hypotheses.find((h) => h.id === quarantine.bestId) : undefined;

  return (
    <div className="board">
      <TopBar mode={mode} phase={phase} testName={testName} onRestart={onRestart} />

      <main className="board-main">
        {phase === 'detect' && (
          <DetectPhase detect={detect} expectedTrials={plannedTrials ?? DETECT_EXPECTED} />
        )}
        {phase === 'tournament' && <TournamentPhase hypotheses={hypotheses} />}
        {phase === 'winner' && winner && (
          <WinnerCard winner={winner} hypothesis={winnerHyp} detect={detect} />
        )}
        {phase === 'quarantine' && quarantine && (
          <QuarantineCard quarantine={quarantine} bestHypothesis={bestHyp} detect={detect} />
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
  testName,
  onRestart,
}: {
  mode: ConnectionMode;
  phase: Phase;
  testName: string | null;
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
        <ModeBadge mode={mode} />
        <button className="restart-btn" onClick={onRestart} title="Replay the run">
          ↻ Replay
        </button>
      </div>
    </header>
  );
}

// Keep the file::test tail; drop deep directories so the header stays tidy.
function shortTestName(name: string): string {
  const [path, ...rest] = name.split('::');
  const file = path.split('/').pop() ?? path;
  return [file, ...rest].join('::');
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
