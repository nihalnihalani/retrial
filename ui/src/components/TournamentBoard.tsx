import { useEffect, useState } from 'react';
import { Boxes, GitBranch, Grid3X3, History, Play, RotateCcw, Scale, Telescope } from 'lucide-react';
import { useEventStream } from '../useEventStream';
import { useDaytonaHealth, type PoolHealth } from '../useDaytonaHealth';
import { modelForHypothesis, displayModel } from '../models';
import type { ConnectionMode, Phase } from '../types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { DiagnosingView } from './DiagnosingView';
import { BlinkingSquares } from './fx/BlinkingSquares';
import { useMagnetic } from './fx/useMagnetic';
import { DetectPhase } from './DetectPhase';
import { TournamentPhase } from './TournamentPhase';
import { WinnerCard } from './WinnerCard';
import { QuarantineCard } from './QuarantineCard';
import { TerminalVerdictCard } from './TerminalVerdictCard';
import { GenomeCard } from './GenomeCard';
import { TreeTimeline } from './TreeTimeline';
import { PromoteGate } from './PromoteGate';
import { SandboxObservatory } from './SandboxObservatory';
import { RunHistory } from './RunHistory';
import { DegradeBanner } from './DegradeBanner';
import { authAware } from '../authError';

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
  bisect: -1, // time travel has its own rail; the 3-act stepper stays dark
};
const STEPS = ['Detect', 'Tournament', 'Verdict'];

// Grid = the classic phase router (the existing star, default). Tree = the
// tournament rendered as a timeline rail; also forced whenever a bisection
// is running (the rail IS that feature's view).
type BoardView = 'grid' | 'tree';

interface Props {
  onRestart: () => void;
}

// The whole board is a pure function of the event stream. It owns nothing but
// layout + the phase router.
export function TournamentBoard({ onRestart }: Props) {
  const { state, mode, connectionMessage } = useEventStream();
  const health = useDaytonaHealth(mode);
  const {
    phase,
    detect,
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
    tournamentDone,
    bisect,
    promotion,
    poolDegraded,
    preflight,
    observatory,
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
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const treeRequested =
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('tree') === '1';
  const [view, setView] = useState<BoardView>(treeRequested ? 'tree' : 'grid');
  // The Observatory is a backstage panel: opt-in, collapsed by default, never
  // replacing the phase router (the tournament stays the star).
  const [obsOpen, setObsOpen] = useState(false);
  // Run history: the same backstage grammar — opt-in, collapsed, read-only.
  const [runsOpen, setRunsOpen] = useState(false);

  // A run is "active" from the moment it names a test until it reaches a
  // terminal verdict; the GO button disables itself for that whole window.
  const terminal =
    phase === 'winner' ||
    phase === 'quarantine' ||
    phase === 'baseline_verdict' ||
    tournamentDone ||
    (phase === 'bisect' && (bisect?.done ?? false));
  const runActive = testName !== null && !terminal;
  const terminalPhase =
    phase === 'winner' || phase === 'quarantine' || phase === 'baseline_verdict';
  const waitingForRun = testName === null && !bisect;
  const showViewToggle = phase === 'bisect' || treeRequested;

  // A running bisection IS the tree view — the checkpoint rail is its board.
  const showTree = phase === 'bisect' || (treeRequested && view === 'tree');

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  // Stage clock: starts on diagnosing, ticks until a terminal verdict, clears when idle.
  useEffect(() => {
    if (waitingForRun) {
      setRunStartedAt(null);
      setElapsedSec(0);
      return;
    }
    if (phase === 'diagnosing') {
      setRunStartedAt(Date.now());
      setElapsedSec(0);
    }
  }, [phase, waitingForRun]);

  useEffect(() => {
    if (runStartedAt == null || terminal) return;
    const tick = () => setElapsedSec(Math.floor((Date.now() - runStartedAt) / 1000));
    tick();
    const id = window.setInterval(tick, 250);
    return () => window.clearInterval(id);
  }, [runStartedAt, terminal]);

  const startRun = async () => {
    if (posting || runActive || mode !== 'live') return;
    const chosen = SEEDS.find((s) => s.label === seedLabel) ?? SEEDS[0];
    setPosting(true);
    setToast(null);
    try {
      let lastStatus = 0;
      let lastRes: Response | null = null;
      // Try each candidate path; a 404 means "wrong path shape for this engine",
      // so fall through to the next. Any other status (200, 409, 5xx) is final.
      for (const path of chosen.paths) {
        const res = await fetch(TOURNAMENT_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ seed_path: path }),
        });
        lastStatus = res.status;
        lastRes = res;
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
      const fallback = `Couldn't start run${extra}`;
      setToast(lastRes ? authAware(lastRes, fallback) : fallback);
    } catch {
      setToast('Engine unreachable on :8000 — is it running?');
    } finally {
      setPosting(false);
    }
  };

  // Stage operator shortcut: Enter starts a live run when the board is idle.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'INPUT') return;
      if (mode !== 'live' || runActive || posting) return;
      e.preventDefault();
      void startRun();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return (
    <div className="board">
      <DegradeBanner poolDegraded={poolDegraded} preflight={preflight} />
      <TopBar
        mode={mode}
        phase={phase}
        testName={testName}
        health={health}
        degraded={poolDegraded !== null}
        onRestart={onRestart}
        goDisabled={mode !== 'live' || runActive || posting}
        posting={posting}
        seedLabel={seedLabel}
        onSeedChange={setSeedLabel}
        onGo={startRun}
        view={view}
        onViewChange={setView}
        viewLocked={phase === 'bisect'}
        showViewToggle={showViewToggle}
        obsOpen={obsOpen}
        onObsToggle={() => setObsOpen((o) => !o)}
        obsLiveCount={observatory.seen ? observatory.counts.live : null}
        elapsedSec={runStartedAt != null ? elapsedSec : null}
        runActive={runActive}
        terminal={terminal}
        runsOpen={runsOpen}
        onRunsToggle={() => setRunsOpen((o) => !o)}
      />

      {toast && (
        <div className="toast toast-error" role="alert">
          {toast}
        </div>
      )}

      <main className="board-main" id="evidence-board" aria-busy={runActive}>
        {waitingForRun ? (
          <LiveEmptyState mode={mode} message={connectionMessage} />
        ) : phase === 'diagnosing' ? (
          <DiagnosingView
            testName={testName}
            n={diagnoseModels ?? 4}
            modelNames={diagnoseModelNames}
          />
        ) : showTree ? (
          <TreeTimeline state={state} />
        ) : (
          <>
            {phase === 'detect' && (
              <DetectPhase
                detect={detect}
                expectedTrials={plannedTrials ?? DETECT_EXPECTED}
                threshold={threshold}
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
          </>
        )}
      </main>

      {obsOpen && (
        <div id="sandbox-observatory">
          <SandboxObservatory state={state} mode={mode} runActive={runActive} />
        </div>
      )}

      {runsOpen && <RunHistory mode={mode} />}

      <PromoteGate promotion={promotion} mode={mode} />

      {!terminalPhase && (hypotheses.length > 0 || genome) && (
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
  degraded,
  onRestart,
  goDisabled,
  posting,
  seedLabel,
  onSeedChange,
  onGo,
  view,
  onViewChange,
  viewLocked,
  showViewToggle,
  obsOpen,
  onObsToggle,
  obsLiveCount,
  elapsedSec,
  runActive,
  terminal,
  runsOpen,
  onRunsToggle,
}: {
  mode: ConnectionMode;
  phase: Phase;
  testName: string | null;
  health: PoolHealth | null;
  degraded: boolean;
  onRestart: () => void;
  goDisabled: boolean;
  posting: boolean;
  seedLabel: string;
  onSeedChange: (label: string) => void;
  onGo: () => void;
  view: BoardView;
  onViewChange: (view: BoardView) => void;
  viewLocked: boolean;
  showViewToggle: boolean;
  obsOpen: boolean;
  onObsToggle: () => void;
  obsLiveCount: number | null;
  elapsedSec: number | null;
  runActive: boolean;
  terminal: boolean;
  runsOpen: boolean;
  onRunsToggle: () => void;
}) {
  const activeIndex = PHASE_STEP[phase];
  return (
    <header className="topbar">
      <div className="topbar-primary">
        <div className="brand">
          <Scale className="brand-mark" aria-hidden="true" />
          <span className="brand-name">RETRIAL</span>
          {testName ? (
            <span className="brand-test mono" title={testName}>
              {shortTestName(testName)}
            </span>
          ) : (
            <span className="brand-tag">evidence before confidence</span>
          )}
        </div>

        <nav className="stepper" aria-label="Tournament progress">
          {STEPS.map((label, i) => (
            <div
              key={label}
              className={`step ${i === activeIndex ? 'active' : ''} ${
                i < activeIndex ? 'done' : ''
              }`}
              aria-current={i === activeIndex ? 'step' : undefined}
            >
              <span className="step-index mono">0{i + 1}</span>
              <span className="step-label">{label}</span>
            </div>
          ))}
        </nav>

        <div className="topbar-status">
          {elapsedSec != null && (
            <span
              className={`run-clock mono ${runActive ? 'is-live' : ''} ${terminal ? 'is-done' : ''}`}
              title={terminal ? 'Total run time' : 'Elapsed since GO'}
              aria-label={terminal ? `Finished in ${formatClock(elapsedSec)}` : `Elapsed ${formatClock(elapsedSec)}`}
            >
              {formatClock(elapsedSec)}
            </span>
          )}
          <ModeBadge mode={mode} />
          <Button
            variant="outline"
            size="sm"
            onClick={onRestart}
            title="Clear the board and reconnect"
            aria-label="Clear the board and reconnect"
            className="mobile-touch-target h-8 gap-1.5 border-border/80 bg-transparent text-muted-foreground hover:text-foreground"
          >
            <RotateCcw aria-hidden="true" /> Reset
          </Button>
        </div>
      </div>

      <div className="topbar-secondary">
        <div className="operator-controls">
          <GoControl
            seedLabel={seedLabel}
            onSeedChange={onSeedChange}
            onGo={onGo}
            disabled={goDisabled}
            posting={posting}
          />
          {showViewToggle && (
            <ViewToggle view={view} onViewChange={onViewChange} locked={viewLocked} />
          )}
        </div>
        <div className="telemetry-controls">
          <SandboxTicker mode={mode} health={health} degraded={degraded} />
        <Button
          variant={obsOpen ? 'secondary' : 'outline'}
          size="sm"
          className="mobile-touch-target h-8 gap-1.5"
          onClick={onObsToggle}
          title="Sandbox Observatory — every sandbox the engine tracks"
          aria-expanded={obsOpen}
          aria-controls="sandbox-observatory"
        >
          <Telescope aria-hidden="true" /> Observatory
          {obsLiveCount !== null && (
            <Badge variant="muted" className="ml-0.5 h-5 px-1.5 font-mono text-[10px]">
              {obsLiveCount}
            </Badge>
          )}
        </Button>
        <Button
          variant={runsOpen ? 'secondary' : 'outline'}
          size="sm"
          className="mobile-touch-target h-8 gap-1.5"
          onClick={onRunsToggle}
          title="Run history — recent completed runs from the engine"
          aria-expanded={runsOpen}
          aria-controls="run-history"
        >
          <History aria-hidden="true" /> Runs
        </Button>
        {mode === 'disconnected' && (
          <span className="stale-note">stream paused · reconnecting</span>
        )}
        </div>
      </div>
    </header>
  );
}

function LiveEmptyState({
  mode,
  message,
}: {
  mode: ConnectionMode;
  message: string | null;
}) {
  const disconnected = mode === 'disconnected';
  const connecting = mode === 'connecting';
  return (
    <Card
      className={cn(
        'live-empty relative overflow-hidden border-border/60 bg-card/40 shadow-none',
        disconnected && 'is-error border-destructive/40',
      )}
      role="status"
      aria-live="polite"
    >
      {!disconnected && <BlinkingSquares aria-hidden opacity={connecting ? 0.18 : 0.34} />}
      <CardContent className="relative flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="live-empty-orbit" aria-hidden="true">
        <span />
      </div>
      <Badge variant={disconnected ? 'destructive' : connecting ? 'muted' : 'live'} className="mb-4 tracking-[0.18em]">
        {connecting ? 'CONNECTING' : disconnected ? 'ENGINE OFFLINE' : 'LIVE · READY'}
      </Badge>
      <h1 className="max-w-3xl text-balance text-4xl font-semibold tracking-tight md:text-5xl lg:text-6xl">
        {connecting
          ? 'Connecting to the evidence stream.'
          : disconnected
            ? 'No evidence without an engine.'
            : 'Put the test on trial.'}
      </h1>
      <p className="mt-5 max-w-xl text-pretty text-base text-muted-foreground">
        {message ??
          (connecting
            ? 'Waiting for the live WebSocket on port 8000.'
            : 'Choose a calibrated seed and press GO — or hit Enter. Every cell and rate comes from the engine.')}
      </p>
      <div className="live-empty-proof mt-8">
        <span><i className="proof-dot pass" /> Daytona sandboxes</span>
        <span><i className="proof-dot accent" /> Fireworks diagnosis</span>
        <span><i className="proof-dot gold" /> Braintrust receipts</span>
      </div>
      </CardContent>
    </Card>
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
  const goRef = useMagnetic<HTMLButtonElement>(8);
  return (
    <div className="go-control flex items-center gap-0 rounded-[calc(var(--radius)+2px)] border border-border bg-secondary p-[3px] shadow-[inset_0_1px_0_hsl(0_0%_100%/0.03)]">
      <select
        className="mobile-touch-target go-seed mono h-9 min-w-[190px] border-0 bg-transparent px-2.5 text-[13px] text-foreground outline-none disabled:opacity-50"
        value={seedLabel}
        onChange={(e) => onSeedChange(e.target.value)}
        disabled={disabled}
        title="Seed to prosecute"
        aria-label="Choose a flaky test"
      >
        {SEEDS.map((s) => (
          <option key={s.label} value={s.label}>
            {s.label}
          </option>
        ))}
      </select>
      <Button
        ref={goRef}
        size="sm"
        className="mobile-touch-target h-9 px-5 tracking-[0.08em]"
        onClick={onGo}
        disabled={disabled}
        title="Start a live run on the engine"
        aria-label={posting ? 'Starting live tournament' : 'Start live tournament'}
      >
        {posting ? 'STARTING…' : <><Play aria-hidden="true" /> GO</>}
      </Button>
    </div>
  );
}

// Grid | Tree view toggle. Grid (the existing star) stays default; while a
// bisection runs the rail is the only sensible view, so the toggle locks.
function ViewToggle({
  view,
  onViewChange,
  locked,
}: {
  view: BoardView;
  onViewChange: (view: BoardView) => void;
  locked: boolean;
}) {
  return (
    <div className="view-toggle inline-flex overflow-hidden rounded-md border border-border" role="group" aria-label="Board view">
      {(['grid', 'tree'] as const).map((v) => (
        <Button
          key={v}
          type="button"
          variant={(locked ? 'tree' : view) === v ? 'secondary' : 'ghost'}
          size="sm"
          className="h-8 rounded-none border-0 px-3"
          onClick={() => onViewChange(v)}
          disabled={locked}
          title={locked ? 'bisection always shows the timeline rail' : `${v} view`}
        >
          {v === 'grid' ? <><Grid3X3 aria-hidden="true" /> Grid</> : <><GitBranch aria-hidden="true" /> Tree</>}
        </Button>
      ))}
    </div>
  );
}

// Daytona presence is always live. `degraded` renders the honest snapshot
// fallback tag once the fork backend has fallen back.
function SandboxTicker({
  mode,
  health,
  degraded,
}: {
  mode: ConnectionMode;
  health: PoolHealth | null;
  degraded: boolean;
}) {
  const degradedTag = degraded ? (
    <span className="sandbox-degraded" title="fork backend degraded — running on the snapshot pool">
      snapshot fallback
    </span>
  ) : null;

  return (
    <span
      className={`sandbox-ticker ${mode === 'live' ? 'live' : ''}`}
      title="Daytona sandbox pool"
    >
      <Boxes className="live-hex" aria-hidden="true" />
      {mode === 'live' && health ? (
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
        <span className="sandbox-dim">
          {mode === 'connecting' ? 'pool connecting…' : 'pool unavailable'}
        </span>
      )}
      {degradedTag}
    </span>
  );
}

// Keep the file::test tail; drop deep directories so the header stays tidy.
function shortTestName(name: string): string {
  const [path, ...rest] = name.split('::');
  const file = path.split('/').pop() ?? path;
  return [file, ...rest].join('::');
}

function formatClock(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function ModeBadge({ mode }: { mode: ConnectionMode }) {
  const map: Record<
    ConnectionMode,
    { text: string; variant: 'live' | 'warning' | 'muted' | 'destructive'; title: string }
  > = {
    live: { text: 'LIVE', variant: 'live', title: 'Live engine over WebSocket' },
    replay: {
      text: 'DISCONNECTED',
      variant: 'warning',
      title: 'The live engine is not connected',
    },
    connecting: { text: 'CONNECTING…', variant: 'muted', title: 'Connecting to the engine' },
    disconnected: {
      text: 'DISCONNECTED',
      variant: 'warning',
      title: 'Connection lost — data may be stale. Attempting to reconnect…',
    },
  };
  const { text, variant, title } = map[mode];
  return (
    <Badge variant={variant} className="h-8 gap-2 px-3 tracking-[0.14em]" title={title}>
      <span className={`mode-dot ${mode === 'live' || mode === 'connecting' || mode === 'disconnected' ? mode : 'disconnected'}`} />
      {text}
    </Badge>
  );
}
