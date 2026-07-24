import { FlakeMeter } from './FlakeMeter';
import { TrialGrid } from './TrialGrid';
import { modelForHypothesis, displayModel } from '../models';
import { pct } from '../format';
import type { BoardState, BisectState, Hypothesis } from '../types';

// The tournament rendered as a tree on a vertical timeline rail (Rewind's
// visual grammar: a 1px gradient spine, status dots, strikethrough kills,
// winner glow). Root = the flaky test on trial; branches = hypotheses; leaves
// = their trial reruns. When a bisection is running, the same rail renders
// checkpoint rows instead. Pure function of BoardState — no fetching.
export function TreeTimeline({ state }: { state: BoardState }) {
  const { bisect } = state;
  return (
    <section className="phase tree-timeline">
      <header className="phase-head">
        <h2 className="phase-title">
          {bisect ? 'Time Travel · Checkpoint Rail' : 'The Tournament · Tree View'}
        </h2>
        <p className="phase-sub">
          {bisect
            ? 'The suite frozen at every test boundary; the suspect re-tried from each checkpoint until the flake rate flips.'
            : 'Root is the accused test; each branch is a hypothesis racing its fix; leaves are the reruns.'}
        </p>
      </header>
      {bisect ? <BisectRail state={state} bisect={bisect} /> : <TournamentRail state={state} />}
    </section>
  );
}

function Dot({ tone, pulse = false }: { tone: 'run' | 'pass' | 'fail' | 'gold' | 'idle'; pulse?: boolean }) {
  return <span className={`rail-dot dot-${tone} ${pulse ? 'dot-pulse' : ''}`} aria-hidden="true" />;
}

// ---------------- tournament-as-tree ----------------

function TournamentRail({ state }: { state: BoardState }) {
  const { detect, hypotheses, testName, winner, phase, threshold, diagnoseModelNames } = state;
  // The root's verdict pulse: red while the flake is proven, green once a
  // confirmed winner stabilizes it. Anything else stays neutral.
  const rootTone: 'run' | 'pass' | 'fail' =
    phase === 'winner' && winner ? 'pass' : detect.done ? 'fail' : 'run';
  const rootStatus =
    phase === 'winner' && winner
      ? '● TESTS GREEN'
      : detect.done
        ? '● TESTS RED'
        : 'detect · rerunning';

  return (
    <div className="rail">
      <div className="rail-spine" aria-hidden="true" />

      {/* root card: the accused test + its measured lie */}
      <div className="rail-row rail-root-row">
        <Dot tone={rootTone} pulse={!detect.done} />
        <div className={`rail-root tone-${rootTone}`}>
          <div className="rail-root-head">
            <span className="rail-root-name mono">{testName ?? 'awaiting test…'}</span>
            <span className={`rail-root-status status-tone-${rootTone}`}>{rootStatus}</span>
          </div>
          <FlakeMeter
            rate={detect.flakeRate}
            ci={detect.wilsonCi}
            label={detect.done ? 'baseline flake rate' : 'baseline flake (live)'}
            threshold={threshold}
          />
        </div>
      </div>

      {/* branches: one per hypothesis */}
      <div className="rail-branches">
        {hypotheses.length === 0 && (
          <p className="rail-empty">no hypotheses yet — the tree grows when the tournament starts</p>
        )}
        {hypotheses.map((h, i) => (
          <BranchRow
            key={h.id}
            h={h}
            model={displayModel(h.model, modelForHypothesis(diagnoseModelNames, i))}
          />
        ))}
      </div>
    </div>
  );
}

const BRANCH_BADGE: Record<Hypothesis['status'], { text: string; cls: string }> = {
  racing: { text: 'RACING', cls: 'badge-racing' },
  verified: { text: 'VERIFIED', cls: 'badge-verified' },
  eliminated: { text: 'KILL', cls: 'badge-kill' },
  inconclusive: { text: 'INCONCLUSIVE', cls: 'badge-inconclusive' },
  winner: { text: '◆ WINNER', cls: 'badge-winner' },
};

function BranchRow({ h, model }: { h: Hypothesis; model: string | null }) {
  const dead = h.status === 'eliminated';
  const badge = h.noValidPatch ? { text: 'NO PATCH', cls: 'badge-nopatch' } : BRANCH_BADGE[h.status];
  const tone = h.status === 'winner' ? 'gold' : dead ? 'fail' : h.status === 'racing' ? 'run' : 'idle';
  return (
    <div className={`branch-row ${h.status} ${h.noValidPatch ? 'no-valid-patch' : ''}`}>
      <span className="branch-tick" aria-hidden="true" />
      <Dot tone={tone} pulse={h.status === 'racing'} />
      <div className="branch-main">
        <div className="branch-head">
          <span className={`cause-tag cause-${h.causeClass}`}>{h.causeClass.replace(/_/g, ' ')}</span>
          {model && <span className="model-chip">· {model}</span>}
          <span className={`branch-badge ${badge.cls}`}>{badge.text}</span>
          {h.flakeRate != null && (
            <span className={`branch-score mono ${dead ? 'strike' : ''}`}>{pct(h.flakeRate)}</span>
          )}
        </div>
        <p className={`branch-label ${dead ? 'strike' : ''}`}>{h.explanation}</p>
        {!h.noValidPatch && <TrialGrid trials={h.trials} size="sm" muted={dead} />}
      </div>
    </div>
  );
}

// ---------------- bisection checkpoint rail ----------------

function BisectRail({ state, bisect }: { state: BoardState; bisect: BisectState }) {
  const { threshold } = state;
  return (
    <div className="rail">
      <div className="rail-spine" aria-hidden="true" />

      <div className="rail-row rail-root-row">
        <Dot tone={bisect.done ? (bisect.polluter ? 'fail' : 'idle') : 'run'} pulse={!bisect.done} />
        <div className={`rail-root tone-${bisect.done && bisect.polluter ? 'fail' : 'run'}`}>
          <div className="rail-root-head">
            <span className="rail-root-name mono">
              {bisect.suite || 'suite'} · suspect {bisect.suspect || '…'}
            </span>
            <span className="rail-root-status">
              {bisect.error
                ? '● ERROR'
                : bisect.done
                  ? bisect.polluter
                    ? '● POLLUTER FOUND'
                    : '● INCONCLUSIVE'
                  : `bisecting ${bisect.nTests} tests…`}
            </span>
          </div>
          {bisect.error && <p className="rail-note rail-error mono">{bisect.error}</p>}
          {!bisect.error && bisect.done && !bisect.polluter && bisect.reason && (
            <p className="rail-note mono">{bisect.reason}</p>
          )}
          {bisect.polluter && (
            <p className="rail-note mono">
              <strong>{bisect.polluter}</strong> — run before the suspect, this test poisons it
            </p>
          )}
        </div>
      </div>

      <div className="rail-branches">
        {bisect.checkpoints.map((c) => {
          // Checkpoint k+1 captures the state right AFTER prefix test k ran,
          // so the polluter's own row is the checkpoint labeled with its name.
          const isPolluterRow = bisect.polluter != null && c.label === bisect.polluter;
          return (
            <div
              key={c.k}
              className={`branch-row ckpt-row ${c.inWindow ? 'in-window' : ''} ${
                isPolluterRow ? 'polluter-row' : ''
              }`}
            >
              <span className="branch-tick" aria-hidden="true" />
              <Dot
                tone={isPolluterRow ? 'fail' : c.probe ? 'pass' : 'idle'}
                pulse={!bisect.done && c.inWindow}
              />
              <div className="branch-main">
                <div className="branch-head">
                  <span className="ckpt-k mono">k={c.k}</span>
                  <span className="branch-label mono ckpt-label">{c.label}</span>
                  {isPolluterRow && <span className="branch-badge badge-kill">POLLUTER</span>}
                  {c.inWindow && !bisect.done && (
                    <span className="branch-badge badge-window">SEARCH WINDOW</span>
                  )}
                </div>
                {c.probe ? (
                  <FlakeMeter
                    rate={c.probe.flakeRate}
                    ci={c.probe.wilsonCi}
                    label={`suspect from ckpt ${c.k} · ${c.probe.trials} trials`}
                    threshold={threshold}
                  />
                ) : (
                  <p className="rail-note dim">checkpoint frozen · not probed</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
