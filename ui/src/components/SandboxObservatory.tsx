import { useEffect, useMemo, useState } from 'react';
import { reconstructObservatory } from '../observatoryReconstruct';
import { authAware } from '../authError';
import type {
  BoardState,
  ConnectionMode,
  ObservatorySandbox,
  ObservatoryState,
  SandboxExecEntry,
  SandboxRole,
  SpendWire,
} from '../types';

const API = 'http://localhost:8000';

// Fixed render order for the role groups — the fork spine first (root ->
// checkpoint -> trial-clone), then the snapshot pool, then bisect probes.
const ROLE_ORDER: SandboxRole[] = [
  'root',
  'checkpoint',
  'trial-clone',
  'snapshot-pool',
  'bisect-probe',
];
const ROLE_LABEL: Record<SandboxRole, string> = {
  root: 'roots',
  checkpoint: 'checkpoints',
  'trial-clone': 'trial clones',
  'snapshot-pool': 'snapshot pool',
  'bisect-probe': 'bisect probes',
};

type ObsView = 'grid' | 'tree';

// The Sandbox Observatory: the backstage pass into the Daytona swarm. Every
// sandbox Retrial ever touched, its fork lineage, what it is executing now, and
// the controls to reap it. A pure function of BoardState.observatory — or, when
// no real registry event has arrived (the default replay), of a labeled
// reconstruction derived from recorded trial events (observatoryReconstruct).
export function SandboxObservatory({
  state,
  mode,
  runActive,
}: {
  state: BoardState;
  mode: ConnectionMode;
  runActive: boolean;
}) {
  const live = mode === 'live';
  const seen = state.observatory.seen;
  // Data-source rule (OBSERVATORY-PLAN F4): real feed when any registry event
  // arrived, else the reconstruction. The source label never lies about which.
  const obs: ObservatoryState = seen ? state.observatory : reconstructObservatory(state);
  const source: 'live' | 'scripted feed' | 'replay reconstruction' = seen
    ? live
      ? 'live'
      : 'scripted feed'
    : 'replay reconstruction';

  const [view, setView] = useState<ObsView>('grid');
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // Live freshness: while LIVE and this panel is mounted (open), poll
  // GET /sandboxes every 10s so the header spend chip + counters stay fresh even
  // between run-acceptance snapshots. Cards stay event-driven. Replay never
  // fetches (the effect returns early), so the sacred path issues no requests.
  const [polled, setPolled] = useState<{
    spend: SpendWire | null;
    counts: ObservatoryState['counts'];
  } | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API}/sandboxes`);
        if (!res.ok || cancelled) return;
        const body = await res.json();
        if (cancelled) return;
        setPolled({
          spend: (body.spend ?? null) as SpendWire | null,
          counts: {
            live: body.counts.live,
            totalEver: body.counts.total_ever,
            destroyed: body.counts.destroyed,
          },
        });
      } catch {
        /* offline — keep the event-driven header */
      }
    };
    poll();
    const iv = window.setInterval(poll, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(iv);
    };
  }, [live]);

  // Header uses the freshest source: the live poll when present, else the
  // event-driven state. The spend chip stays hidden unless a snapshot/poll
  // actually carried spend (default replay / reconstruction => null).
  const headerCounts = polled?.counts ?? obs.counts;
  const headerSpend = polled?.spend ?? obs.spend;

  const cards = useMemo(() => Object.values(obs.sandboxes), [obs.sandboxes]);
  const selectedCard = selected ? obs.sandboxes[selected] ?? null : null;

  // DELETE one sandbox (live only). 409 => live fork-children leaf-guard.
  const destroyOne = async (id: string) => {
    if (!live) return;
    try {
      const res = await fetch(`${API}/sandboxes/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (res.status === 409) setToast(`${shortId(id)} has live fork-children — destroy leaves first`);
      else if (!res.ok) setToast(authAware(res, `destroy failed — engine returned ${res.status}`));
      // Success streams sandbox_destroyed over the WS; no local state to sync.
    } catch {
      setToast('engine unreachable on :8000');
    }
  };

  return (
    <section className="obs-panel" aria-label="Sandbox Observatory">
      <ObsHeader
        source={source}
        counts={headerCounts}
        spend={headerSpend}
        view={view}
        onViewChange={setView}
        live={live}
        runActive={runActive}
        onDestroyAll={() => setConfirmOpen(true)}
      />

      {toast && (
        <div className="toast toast-error obs-toast" role="alert">
          {toast}
        </div>
      )}

      {cards.length === 0 ? (
        <p className="obs-empty">no sandboxes tracked yet — the swarm lights up when a run starts</p>
      ) : view === 'grid' ? (
        <ObsGrid cards={cards} onSelect={setSelected} onDestroy={destroyOne} live={live} />
      ) : (
        <ObsTree cards={cards} onSelect={setSelected} />
      )}

      <ObsDrawer
        card={selectedCard}
        live={live}
        onClose={() => setSelected(null)}
        onDestroy={destroyOne}
      />

      {confirmOpen && (
        <DestroyAllModal
          counts={obs.counts}
          runActive={runActive}
          onClose={() => setConfirmOpen(false)}
          onToast={setToast}
        />
      )}
    </section>
  );
}

// ---------------- header strip ----------------

function ObsHeader({
  source,
  counts,
  spend,
  view,
  onViewChange,
  live,
  runActive,
  onDestroyAll,
}: {
  source: 'live' | 'scripted feed' | 'replay reconstruction';
  counts: ObservatoryState['counts'];
  spend: SpendWire | null;
  view: ObsView;
  onViewChange: (v: ObsView) => void;
  live: boolean;
  runActive: boolean;
  onDestroyAll: () => void;
}) {
  const reconstruction = source === 'replay reconstruction';
  // Destroy-all is live-only; while a run is active it needs the force path
  // (handled in the modal), so the button opens the modal but stays disabled
  // entirely in replay.
  const destroyTitle = !live ? 'live only' : 'destroy every live sandbox';
  return (
    <header className="obs-header">
      <div className="obs-title">
        <span className="hex">⬢</span>
        <span className="obs-title-text">Sandbox Observatory</span>
        <span className={`obs-source ${reconstruction ? 'reconstruction' : ''}`} title={sourceTitle(source)}>
          {source}
        </span>
      </div>

      <div className="obs-counters mono">
        <span className="obs-count">
          <strong>{counts.live}</strong> live
        </span>
        <span className="obs-count-sep">·</span>
        <span className="obs-count">
          <strong>{counts.totalEver}</strong> total-ever
        </span>
        <span className="obs-count-sep">·</span>
        <span className="obs-count obs-count-dim">
          <strong>{counts.destroyed}</strong> destroyed
        </span>
      </div>

      <SpendChip spend={spend} />

      <div className="obs-header-right">
        <div className="view-toggle" role="group" aria-label="Observatory view">
          {(['grid', 'tree'] as const).map((v) => (
            <button
              key={v}
              className={`view-toggle-btn ${view === v ? 'active' : ''}`}
              onClick={() => onViewChange(v)}
              title={`${v} view`}
            >
              {v === 'grid' ? '▦ Grid' : '⑂ Tree'}
            </button>
          ))}
        </div>
        <button
          className="obs-destroy-all"
          onClick={onDestroyAll}
          disabled={!live}
          title={destroyTitle}
        >
          ✕ Destroy all{runActive && live ? ' (run active)' : ''}
        </button>
      </div>
    </header>
  );
}

function sourceTitle(source: string): string {
  if (source === 'live') return 'live registry over WebSocket';
  if (source === 'scripted feed') return 'scripted ?mock=observatory registry feed';
  return 'derived from recorded trial events — not recorded registry data';
}

// The always-honest spend chip: measured sandbox-lifetime hours, and — only
// when a rate is configured — a clearly-labeled "est. $" estimate. Never a bare
// price, never invented money. Hidden entirely when no spend is available
// (default replay / reconstruction), so the sacred path shows no chip.
function SpendChip({ spend }: { spend: SpendWire | null }) {
  if (spend === null) return null;
  const hasEst = spend.est_cost_usd !== null;
  return (
    <span className="obs-spend mono tabular-nums" title={spend.note}>
      ⏱ {fmtHours(spend.total_sandbox_seconds)} sandbox-h
      {hasEst ? (
        <span className="obs-spend-est"> · est. ${spend.est_cost_usd!.toFixed(2)}</span>
      ) : (
        <span className="obs-spend-hint"> · set RETRIAL_EST_RATE_PER_SANDBOX_HOUR for $ est.</span>
      )}
    </span>
  );
}

function fmtHours(seconds: number): string {
  return (seconds / 3600).toFixed(2);
}

// ---------------- grid view ----------------

function ObsGrid({
  cards,
  onSelect,
  onDestroy,
  live,
}: {
  cards: ObservatorySandbox[];
  onSelect: (id: string) => void;
  onDestroy: (id: string) => void;
  live: boolean;
}) {
  return (
    <div className="obs-grid-groups">
      {ROLE_ORDER.map((role) => {
        const group = cards.filter((c) => c.role === role);
        if (group.length === 0) return null;
        // Destroyed cards sink to the group tail.
        const sorted = [...group].sort((a, b) => rank(a) - rank(b) || a.id.localeCompare(b.id));
        return (
          <div key={role} className="obs-group">
            <div className="obs-group-label">
              {ROLE_LABEL[role]}
              <span className="obs-group-count">{group.length}</span>
            </div>
            <div className="obs-grid">
              {sorted.map((c) => (
                <ObsCard
                  key={`${c.id}-${c.lastExecSeq}`}
                  card={c}
                  onSelect={onSelect}
                  onDestroy={onDestroy}
                  live={live}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function rank(c: ObservatorySandbox): number {
  return c.state === 'destroyed' ? 1 : 0;
}

function ObsCard({
  card,
  onSelect,
  onDestroy,
  live,
}: {
  card: ObservatorySandbox;
  onSelect: (id: string) => void;
  onDestroy: (id: string) => void;
  live: boolean;
}) {
  const dead = card.state === 'destroyed';
  const canDestroy = live && !dead;
  return (
    <div
      className={`obs-card ${dead ? 'destroyed' : ''} ${card.state === 'degraded' ? 'degraded' : ''}`}
      onClick={() => onSelect(card.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(card.id)}
      title="open detail"
    >
      <div className="obs-card-top">
        <span className={`obs-id mono ${dead ? 'strike' : ''}`}>{shortId(card.id)}</span>
        {canDestroy && (
          <button
            className="obs-destroy-btn"
            onClick={(e) => {
              e.stopPropagation();
              onDestroy(card.id);
            }}
            title="destroy this sandbox"
            aria-label={`destroy ${card.id}`}
          >
            ✕
          </button>
        )}
      </div>
      <div className="obs-card-chips">
        <span className={`obs-chip role-${card.role}`}>{card.role}</span>
        <span className="obs-chip backend">{card.backend}</span>
      </div>
      <div className="obs-card-state">
        <StateBadge state={card.state} pulseKey={card.lastExecSeq} />
        <span className="obs-execs mono" title="executions on this sandbox">
          {card.exec_count}×
        </span>
      </div>
      {card.state === 'running-cmd' && card.currentCmd && (
        <div className="obs-current-cmd mono" title={card.currentCmd}>
          {card.currentCmd}
        </div>
      )}
    </div>
  );
}

function StateBadge({ state, pulseKey }: { state: ObservatorySandbox['state']; pulseKey: number }) {
  // The pulse retriggers on exec via the card's key remount; the class simply
  // carries the state color. running-cmd/creating breathe on their own.
  return (
    <span
      key={pulseKey}
      className={`obs-state ${state} ${state === 'running-cmd' ? 'obs-pulse' : ''}`}
    >
      {state}
    </span>
  );
}

// ---------------- tree view (fork lineage) ----------------

function ObsTree({
  cards,
  onSelect,
}: {
  cards: ObservatorySandbox[];
  onSelect: (id: string) => void;
}) {
  const byId = useMemo(() => {
    const m: Record<string, ObservatorySandbox> = {};
    for (const c of cards) m[c.id] = c;
    return m;
  }, [cards]);

  const children = useMemo(() => {
    const m: Record<string, string[]> = {};
    for (const c of cards) {
      if (c.parent_id && byId[c.parent_id]) {
        (m[c.parent_id] ??= []).push(c.id);
      }
    }
    for (const k of Object.keys(m)) m[k].sort();
    return m;
  }, [cards, byId]);

  // Roots of the fork forest: no parent (or parent pruned), and NOT a bare
  // snapshot-pool sandbox — those group under a synthetic header rather than
  // pretending to be lineage roots.
  const treeRoots = cards
    .filter((c) => (!c.parent_id || !byId[c.parent_id]) && c.role !== 'snapshot-pool')
    .sort((a, b) => a.id.localeCompare(b.id));
  const snapshotPool = cards
    .filter((c) => c.role === 'snapshot-pool')
    .sort((a, b) => a.id.localeCompare(b.id));

  return (
    <div className="obs-tree">
      {treeRoots.length === 0 && snapshotPool.length === 0 && (
        <p className="obs-empty">no fork lineage yet</p>
      )}
      {treeRoots.map((r) => (
        <TreeNode key={r.id} card={r} childIndex={children} byId={byId} depth={0} onSelect={onSelect} />
      ))}
      {snapshotPool.length > 0 && (
        <div className="obs-tree-pool">
          <div className="obs-group-label">
            snapshot pool<span className="obs-group-count">{snapshotPool.length}</span>
            <span className="obs-tree-pool-note">flat — no fork lineage</span>
          </div>
          <div className="obs-grid">
            {snapshotPool.map((c) => (
              <ObsCard key={`${c.id}-${c.lastExecSeq}`} card={c} onSelect={onSelect} onDestroy={() => {}} live={false} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TreeNode({
  card,
  childIndex,
  byId,
  depth,
  onSelect,
}: {
  card: ObservatorySandbox;
  childIndex: Record<string, string[]>;
  byId: Record<string, ObservatorySandbox>;
  depth: number;
  onSelect: (id: string) => void;
}) {
  const kids = childIndex[card.id] ?? [];
  const dead = card.state === 'destroyed';
  return (
    <div className="obs-tree-node" style={{ marginLeft: depth === 0 ? 0 : 20 }}>
      <div
        className={`obs-tree-row ${dead ? 'destroyed' : ''}`}
        onClick={() => onSelect(card.id)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(card.id)}
      >
        {depth > 0 && <span className="obs-tree-spine" aria-hidden="true" />}
        <span className={`obs-tree-dot state-${card.state}`} aria-hidden="true" />
        <span className={`obs-id mono ${dead ? 'strike' : ''}`}>{shortId(card.id)}</span>
        <span className={`obs-chip role-${card.role}`}>{card.role}</span>
        <span className={`obs-state ${card.state}`}>{card.state}</span>
        {card.exec_count > 0 && <span className="obs-execs mono">{card.exec_count}×</span>}
      </div>
      {kids.map((kid) => (
        <TreeNode
          key={kid}
          card={byId[kid]}
          childIndex={childIndex}
          byId={byId}
          depth={depth + 1}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

// ---------------- detail drawer ----------------

function ObsDrawer({
  card,
  live,
  onClose,
  onDestroy,
}: {
  card: ObservatorySandbox | null;
  live: boolean;
  onClose: () => void;
  onDestroy: (id: string) => void;
}) {
  // In LIVE mode the drawer fetches the full server-side record on open (the
  // complete exec history + a lazily-resolved preview_url the WS never carries).
  // Replay/reconstruction uses the in-state record only.
  const [detail, setDetail] = useState<{
    recentExecs: SandboxExecEntry[];
    previewUrl: string | null;
  } | null>(null);

  const id = card?.id ?? null;
  useEffect(() => {
    setDetail(null);
    if (!id || !live) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API}/sandboxes/${encodeURIComponent(id)}`);
        if (!res.ok || cancelled) return;
        const rec = await res.json();
        if (cancelled) return;
        setDetail({
          recentExecs: (rec.recent_execs ?? []) as SandboxExecEntry[],
          previewUrl: (rec.preview_url ?? null) as string | null,
        });
      } catch {
        /* offline — fall back to in-state data */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, live]);

  const open = card !== null;
  if (!card) return <aside className="obs-drawer" aria-hidden="true" />;

  const execs = detail?.recentExecs ?? card.recentExecs;
  const previewUrl = detail?.previewUrl ?? card.preview_url ?? null;
  const dead = card.state === 'destroyed';

  return (
    <aside className={`obs-drawer ${open ? 'open' : ''}`} aria-label="Sandbox detail">
      <div className="obs-drawer-head">
        <div>
          <div className="obs-drawer-id mono">{card.id}</div>
          <div className="obs-drawer-sub">
            <span className={`obs-chip role-${card.role}`}>{card.role}</span>
            <span className="obs-chip backend">{card.backend}</span>
            <span className={`obs-state ${card.state}`}>{card.state}</span>
          </div>
        </div>
        <button className="obs-drawer-close" onClick={onClose} aria-label="close">
          ✕
        </button>
      </div>

      <div className="obs-drawer-body">
        <dl className="obs-record">
          <Field label="parent" value={card.parent_id ? shortId(card.parent_id) : '— (root)'} mono />
          <Field label="isolation" value={card.isolation ?? '—'} />
          <Field label="exec count" value={`${card.exec_count}`} mono />
          <Field label="created" value={fmtTs(card.created_ts)} mono />
        </dl>

        {card.labels && Object.keys(card.labels).length > 0 && (
          <div className="obs-labels">
            {Object.entries(card.labels).map(([k, v]) => (
              <span key={k} className="obs-label-chip mono">
                {k}={v}
              </span>
            ))}
          </div>
        )}

        <div className="obs-preview-row">
          {previewUrl ? (
            <button
              className="obs-preview-btn"
              onClick={() => window.open(previewUrl, '_blank', 'noopener')}
              title="open the Daytona preview in a new tab"
            >
              ↗ Open Daytona preview
            </button>
          ) : (
            <span className="obs-preview-none" title="Daytona did not expose a preview link">
              no preview link (Daytona did not expose one)
            </span>
          )}
        </div>

        <div className="obs-exec-head">
          exec feed
          <span className="obs-exec-head-count">{execs.length}</span>
        </div>
        <div className="obs-exec-feed">
          {execs.length === 0 ? (
            <p className="obs-exec-empty">
              no exec detail{live ? '' : ' in this replay reconstruction'}
            </p>
          ) : (
            [...execs]
              .slice()
              .reverse()
              .map((e, i) => <ExecRow key={i} entry={e} />)
          )}
        </div>

        {live && !dead && (
          <button className="obs-drawer-destroy" onClick={() => onDestroy(card.id)}>
            ✕ Destroy this sandbox
          </button>
        )}
      </div>
    </aside>
  );
}

function ExecRow({ entry }: { entry: SandboxExecEntry }) {
  const badge =
    entry.exit_code === 0
      ? { cls: 'ok', text: '0' }
      : entry.exit_code == null
        ? { cls: 'infra', text: 'infra' }
        : { cls: 'fail', text: `${entry.exit_code}` };
  return (
    <div className="obs-exec-row">
      <div className="obs-exec-line">
        <span className={`exit-badge ${badge.cls}`}>{badge.text}</span>
        <span className="obs-exec-cmd mono" title={entry.cmd}>
          {entry.cmd || '—'}
        </span>
        <span className="obs-exec-dur mono">{entry.duration_s.toFixed(2)}s</span>
      </div>
      {entry.output_tail && <pre className="obs-exec-tail mono">{entry.output_tail}</pre>}
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="obs-field">
      <dt className="obs-field-label">{label}</dt>
      <dd className={`obs-field-value ${mono ? 'mono' : ''}`}>{value}</dd>
    </div>
  );
}

// ---------------- destroy-all confirm modal ----------------

function DestroyAllModal({
  counts,
  runActive,
  onClose,
  onToast,
}: {
  counts: ObservatoryState['counts'];
  runActive: boolean;
  onClose: () => void;
  onToast: (msg: string) => void;
}) {
  const [force, setForce] = useState(false);
  const [posting, setPosting] = useState(false);

  const confirm = async () => {
    if (posting) return;
    setPosting(true);
    try {
      const res = await fetch(`${API}/sandboxes/destroy_all${force ? '?force=1' : ''}`, {
        method: 'POST',
      });
      if (res.status === 409) {
        onToast('a run is active — check force to cancel it and reap');
      } else if (!res.ok) {
        onToast(authAware(res, `reap failed — engine returned ${res.status}`));
      } else {
        onClose();
      }
    } catch {
      onToast('engine unreachable on :8000');
    } finally {
      setPosting(false);
    }
  };

  return (
    <div className="promote-overlay obs-modal-overlay" role="dialog" aria-modal="true" aria-label="Destroy all sandboxes">
      <div className="promote-modal obs-modal">
        <div className="promote-head obs-modal-head">
          <span className="promote-head-label">Destroy All Sandboxes</span>
          <span className="promote-head-blink">◆ irreversible</span>
        </div>
        <div className="promote-body">
          <p className="promote-lead">
            This tears down <strong>every live sandbox</strong> across all pools and bisectors,
            leaf-first. Currently <strong>{counts.live}</strong> live ·{' '}
            <strong>{counts.destroyed}</strong> already destroyed.
          </p>

          {runActive && (
            <label className="obs-force-row">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              <span className="obs-force-label">
                <strong>Force:</strong> CANCELS the active run — a bisect stops at its next probe; a
                tournament&apos;s remaining trials fail as infra-excluded. Sandboxes are not rebuilt
                mid-run.
              </span>
            </label>
          )}

          <div className="promote-actions">
            <button className="promote-btn reject" onClick={onClose} disabled={posting}>
              CANCEL
            </button>
            <button
              className="promote-btn approve obs-modal-confirm"
              onClick={confirm}
              disabled={posting || (runActive && !force)}
              title={runActive && !force ? 'a run is active — check force to proceed' : 'destroy all'}
            >
              {posting ? 'REAPING…' : force ? 'FORCE DESTROY ALL' : 'DESTROY ALL'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------- helpers ----------------

function shortId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 12)}…` : id;
}

function fmtTs(ts: number): string {
  return ts > 0 ? `${ts.toFixed(1)}s` : '—';
}
