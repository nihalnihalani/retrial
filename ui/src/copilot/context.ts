import type { BoardState, ConnectionMode } from '../types';

export type CopilotBoardView = 'grid' | 'tree';

function basename(value: string | null) {
  if (!value) return null;
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1]?.slice(0, 120) ?? null;
}

function finiteRate(value: number | null) {
  return value !== null && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : null;
}

function sanitizeExplanation(value: string) {
  const unsafeLine =
    /(^|\s)(curl|wget|pytest|python|bash|zsh|sh|npm|pnpm|yarn|git)\s|(^|\s)\$\s/i;
  return value
    .replace(/```[\s\S]*?```/g, '[code omitted]')
    .split(/\r?\n/)
    .filter((line) => !unsafeLine.test(line))
    .join(' ')
    .replace(/https?:\/\/\S+/gi, '[url omitted]')
    .replace(/\b[A-Z][A-Z0-9_]{2,}\s*=\s*\S+/g, '[configuration omitted]')
    .replace(/\b(?:sk|ghp|github_pat|fw)_[A-Za-z0-9_-]{12,}\b/g, '[secret omitted]')
    .replace(/(?:^|\s)(?:~\/|\/Users\/|\/home\/)\S+/g, ' [path omitted]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 240);
}

function terminalStatus(state: BoardState) {
  if (state.phase === 'winner') return 'fixed';
  if (state.phase === 'quarantine') return 'quarantined';
  if (state.phase === 'baseline_verdict') {
    return state.baselineVerdict?.verdict.toLowerCase().slice(0, 40) ?? 'baseline_verdict';
  }
  if (state.phase === 'bisect' && state.bisect?.done) return 'bisect_complete';
  if (state.testName) return 'active';
  return 'idle';
}

/**
 * Builds the only client state sent to the copilot. It deliberately excludes
 * source text, patches, command output, environment data, sandbox identifiers,
 * and URLs. Hypothesis explanations are aggressively sanitized and truncated.
 */
export function buildCopilotBoardContext(
  state: BoardState,
  mode: ConnectionMode,
  view: CopilotBoardView,
) {
  const summary = {
    product: 'Retrial tournament board',
    mode,
    phase: state.phase,
    runStatus: terminalStatus(state),
    test: basename(state.testName),
    view,
    evidence: {
      plannedTrials: state.plannedTrials,
      completedTrials: state.detect.trials.length,
      failures: state.detect.fails,
      flakeRate: finiteRate(state.detect.flakeRate),
      wilsonInterval: state.detect.wilsonCi,
      threshold: finiteRate(state.threshold),
      detectComplete: state.detect.done,
    },
    hypotheses: state.hypotheses.slice(0, 4).map((hypothesis) => ({
      id: hypothesis.id.slice(0, 80),
      causeClass: hypothesis.causeClass.slice(0, 80),
      explanation: sanitizeExplanation(hypothesis.explanation),
      status: hypothesis.status,
      model: hypothesis.model?.slice(0, 120) ?? null,
      completedTrials: hypothesis.trials.length,
      flakeRate: finiteRate(hypothesis.flakeRate),
      wilsonInterval: hypothesis.wilsonCi,
      verdict: hypothesis.verdict?.slice(0, 40) ?? null,
      hasValidPatch: !hypothesis.noValidPatch,
    })),
    outcome: {
      winnerId: state.winner?.id.slice(0, 80) ?? null,
      originalFlakeRate: finiteRate(state.winner?.origFlakeRate ?? null),
      confirmedFlakeRate: finiteRate(state.winner?.confirmFlakeRate ?? null),
      quarantineReason: state.quarantine?.reason.slice(0, 160) ?? null,
      pullRequestOpened: state.prUrl !== null,
    },
    promotion: {
      pending: state.promotion?.open ?? false,
      verdict: state.promotion?.verdict.slice(0, 40) ?? null,
      decision: state.promotion?.approved ?? null,
    },
    infrastructure: {
      preflightOk: state.preflight?.ok ?? null,
      preflightLiveChecked: state.preflight?.liveChecked ?? false,
      poolDegraded: state.poolDegraded !== null,
      liveSandboxes: state.observatory.seen ? state.observatory.counts.live : null,
    },
    controls: {
      safeActionsOnly: true,
      assistantCannotStartRuns: true,
      assistantCannotApprovePromotion: true,
      assistantCannotMutateSandboxes: true,
    },
  };

  // This is a defensive invariant as the board evolves. The current payload is
  // far below the limit; fail closed if future fields accidentally make it huge.
  const encoded = JSON.stringify(summary);
  if (encoded.length > 8_192) {
    throw new Error('Copilot board context exceeded its 8 KB safety budget.');
  }
  return summary;
}
