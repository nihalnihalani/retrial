import type { BoardState } from '../types';

// The persistent loud-degrade banner — the disaster this prevents is a SILENT
// degrade on stage (the fork pool quietly fell back to the snapshot pool, or a
// boot preflight failed, and nobody noticed until the demo lied). Mounted at the
// board root (TournamentBoard) so it covers EVERY view by construction, not just
// the Observatory. A pure function of the two pool-level sticky facts in
// BoardState; the default replay emits neither event, so the sacred path shows
// nothing (pinned by the reducer tests + the untouched mockRun deep-equal guard).
//
// Priority: an active pool degrade is the loudest fact and wins over a failed
// preflight; warn-only checks get a slim amber strip; otherwise nothing renders.
export function DegradeBanner({
  poolDegraded,
  preflight,
}: {
  poolDegraded: BoardState['poolDegraded'];
  preflight: BoardState['preflight'];
}) {
  if (poolDegraded !== null) {
    return (
      <div className="degrade-banner" role="alert">
        <span className="degrade-icon" aria-hidden="true">
          ⚠
        </span>
        <span className="degrade-text">
          <strong>FORK POOL DEGRADED</strong> — running on the snapshot fallback:{' '}
          <span className="degrade-reason mono">{poolDegraded.reason}</span>
        </span>
      </div>
    );
  }

  if (preflight && !preflight.ok) {
    const failing = preflight.checks.find((c) => c.status === 'fail');
    return (
      <div className="degrade-banner" role="alert">
        <span className="degrade-icon" aria-hidden="true">
          ⚠
        </span>
        <span className="degrade-text">
          <strong>PREFLIGHT FAILED</strong>
          {failing && (
            <>
              {' '}
              — <span className="degrade-reason mono">{failing.name}</span>: {failing.detail}
            </>
          )}
          <span className="degrade-hint">
            {' '}
            — run <code>retrial doctor</code> for the full report
          </span>
        </span>
      </div>
    );
  }

  if (preflight) {
    const warns = preflight.checks.filter((c) => c.status === 'warn');
    if (warns.length > 0) {
      return (
        <div className="degrade-banner warn" role="status">
          <span className="degrade-icon" aria-hidden="true">
            ⚠
          </span>
          <span className="degrade-text">
            preflight warnings:{' '}
            <span className="degrade-reason mono">{warns.map((w) => w.name).join(', ')}</span>
          </span>
        </div>
      );
    }
  }

  return null;
}
