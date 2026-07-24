import { useState } from 'react';
import { FlakeMeter } from './FlakeMeter';
import { pct } from '../format';
import type { ConnectionMode, PromotionState } from '../types';

const PROMOTE_URL = 'http://localhost:8000/promote';

// The human promote gate (Rewind's PromoteCard, in retrial idiom): a
// full-screen modal shown while a promotion is pending. The AI recommends —
// the human decides. Approving only HANDS the result to PRSmith; the board
// must not claim "shipped" until pr_opened actually lands (honest-state rule),
// so the buttons speak of opening a PR, never of it being open.
export function PromoteGate({
  promotion,
  mode,
}: {
  promotion: PromotionState | null;
  mode: ConnectionMode;
}) {
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!promotion?.open) return null;

  const live = mode === 'live';
  const fixed = promotion.verdict === 'FIXED';

  const decide = async (approve: boolean) => {
    if (posting || !live) return;
    setPosting(true);
    setError(null);
    try {
      const res = await fetch(PROMOTE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approve }),
      });
      if (!res.ok) setError(`engine returned ${res.status}`);
      // On success the engine emits promotion_closed, which flips
      // promotion.open and unmounts this modal — no local state to sync.
    } catch {
      setError('engine unreachable on :8000');
    } finally {
      setPosting(false);
    }
  };

  return (
    <div className="promote-overlay" role="dialog" aria-modal="true" aria-label="Promote gate">
      <div className="promote-modal">
        <div className="promote-head">
          <span className="promote-head-label">Human Promote Gate</span>
          <span className="promote-head-blink">◆ approval required</span>
        </div>

        <div className="promote-body">
          <p className="promote-lead">
            The verdict can&apos;t ship on its own. The evidence <strong>recommends</strong> — you{' '}
            <em>decide</em>. Approving hands the result to PRSmith, which opens the{' '}
            {fixed ? 'fix' : 'quarantine'} PR.
          </p>

          <div className="promote-summary">
            <div className="promote-summary-main">
              <div className="promote-summary-label">
                {fixed ? 'winning fix' : 'quarantine dossier'}
              </div>
              <div className="promote-test mono">{promotion.testName}</div>
              {promotion.winnerId && (
                <div className="promote-summary-sub mono">hypothesis {promotion.winnerId}</div>
              )}
            </div>
            <div className="promote-verdict">
              <span className={`promote-verdict-badge ${fixed ? 'fixed' : 'quarantine'}`}>
                {promotion.verdict}
              </span>
            </div>
          </div>

          <div className="promote-evidence">
            <FlakeMeter
              rate={promotion.flakeRate}
              ci={null}
              label="original flake rate"
            />
            <FlakeMeter
              rate={promotion.confirmFlakeRate}
              ci={null}
              label="confirmation round"
            />
          </div>
          {promotion.flakeRate != null && promotion.confirmFlakeRate != null && (
            <p className="promote-delta mono">
              {pct(promotion.flakeRate)} → {pct(promotion.confirmFlakeRate)}, measured — not vibes.
            </p>
          )}

          {promotion.braintrustUrl && (
            <a
              className="promote-receipt"
              href={promotion.braintrustUrl}
              target="_blank"
              rel="noreferrer"
            >
              ⛁ Braintrust receipt — every trial logged
            </a>
          )}

          {error && <p className="promote-error">{error}</p>}
          {!live && <p className="promote-replay-hint">approval is live-engine only — this is a replay</p>}

          <div className="promote-actions">
            <button
              className="promote-btn reject"
              onClick={() => decide(false)}
              disabled={posting || !live}
            >
              REJECT
            </button>
            <button
              className="promote-btn approve"
              onClick={() => decide(true)}
              disabled={posting || !live}
            >
              {posting ? 'SENDING…' : `APPROVE → OPEN ${fixed ? 'FIX' : 'QUARANTINE'} PR`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
