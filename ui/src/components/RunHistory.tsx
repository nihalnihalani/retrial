import { useCallback, useEffect, useState } from 'react';
import type { ConnectionMode } from '../types';
import { pct } from '../format';

const API = 'http://localhost:8000';

// One recorded run from GET /runs. Deliberately a LOCAL type, not in types.ts:
// run history is fetch-on-open and lives entirely outside the event
// stream/reducer (the sacred replay cannot be affected by construction).
interface RunRow {
  id: string;
  kind: string;
  test_name: string | null;
  verdict: string | null;
  orig_flake_rate: number | null;
  final_flake_rate: number | null;
  winner_model: string | null;
  braintrust_url: string | null;
  started_at: number | null;
  finished_at: number | null;
}

// Verdict -> badge modifier. POLLUTER carries the culprit name, so match by
// prefix; everything unrecognized falls to the neutral grey.
function verdictClass(verdict: string | null): string {
  const v = verdict ?? '';
  // `is-` prefixes, NOT bare state words: Tailwind v4 ships a `.fixed
  // { position: fixed }` utility, so 'runs-verdict fixed' pinned every FIXED
  // badge to the viewport and dropped it on top of the row's kind badge.
  if (v === 'FIXED') return 'runs-verdict is-fixed';
  if (v === 'QUARANTINE') return 'runs-verdict is-quarantine';
  if (v.startsWith('POLLUTER')) return 'runs-verdict is-polluter';
  if (v === 'ERROR') return 'runs-verdict is-error';
  return 'runs-verdict is-inconclusive';
}

function whenText(finished_at: number | null): string {
  if (finished_at == null) return '—';
  try {
    return new Date(finished_at * 1000).toLocaleString();
  } catch {
    return '—';
  }
}

// A read-only history panel, same grammar as the Sandbox Observatory: fetched
// once on open (live only — the replay has no database), refreshed manually.
// No polling: history doesn't move on its own.
export function RunHistory({ mode }: { mode: ConnectionMode }) {
  const live = mode === 'live';
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!live) return; // replay/reconstruction never fetches — honest empty state
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/runs`);
      if (!res.ok) {
        setError(`engine returned ${res.status}`);
        return;
      }
      const body = await res.json();
      setRuns((body.runs ?? []) as RunRow[]);
    } catch {
      setError('engine unreachable on :8000 — is it running?');
    } finally {
      setLoading(false);
    }
  }, [live]);

  useEffect(() => {
    if (live) load();
  }, [live, load]);

  return (
    <section className="runs-panel" aria-label="Run history">
      <header className="runs-header">
        <div className="runs-title">
          <span className="hex">☰</span>
          <span className="runs-title-text">Run history</span>
          <span className="runs-source" title="recent completed runs from the engine's SQLite history">
            {live ? 'live' : 'live-only'}
          </span>
        </div>
        {live && (
          <button
            className="runs-refresh"
            onClick={load}
            disabled={loading}
            title="Reload recent runs"
          >
            ↻ {loading ? 'loading…' : 'Refresh'}
          </button>
        )}
      </header>

      <div className="runs-body">
        {!live ? (
          <p className="runs-empty">run history is live-only (the replay has no database)</p>
        ) : error ? (
          <p className="runs-empty runs-error">{error}</p>
        ) : loading && runs === null ? (
          <p className="runs-empty">loading recent runs…</p>
        ) : runs && runs.length === 0 ? (
          <p className="runs-empty">no completed runs yet — history starts with your first run</p>
        ) : (
          <ul className="runs-list">
            {(runs ?? []).map((r) => (
              <li key={r.id} className="runs-row">
                <span className={`runs-kind ${r.kind}`}>{r.kind}</span>
                <span className="runs-test mono" title={r.test_name ?? ''}>
                  {r.test_name ?? '—'}
                </span>
                <span className={verdictClass(r.verdict)}>{r.verdict ?? '—'}</span>
                {r.orig_flake_rate != null && r.final_flake_rate != null && (
                  <span className="runs-rates mono">
                    {pct(r.orig_flake_rate)} → {pct(r.final_flake_rate)}
                  </span>
                )}
                {r.winner_model && <span className="runs-model mono">{r.winner_model}</span>}
                {r.braintrust_url && (
                  <a
                    className="runs-bt"
                    href={r.braintrust_url}
                    target="_blank"
                    rel="noreferrer"
                    title="Braintrust experiment"
                  >
                    ↗ bt
                  </a>
                )}
                <span className="runs-when">{whenText(r.finished_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
