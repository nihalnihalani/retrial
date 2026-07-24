// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { SandboxObservatory } from './SandboxObservatory';
import { initialState } from '../reducer';
import type { BoardState, ObservatorySandbox, ObservatoryState, SpendWire } from '../types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const sb = (id: string, over: Partial<ObservatorySandbox> = {}): ObservatorySandbox => ({
  id,
  role: 'trial-clone',
  backend: 'fork',
  state: 'warm',
  parent_id: null,
  created_ts: 0,
  labels: {},
  isolation: null,
  exec_count: 0,
  preview_url: null,
  currentCmd: null,
  recentExecs: [],
  lastExecSeq: 0,
  ...over,
});

const obs = (over: Partial<ObservatoryState> = {}): ObservatoryState => ({
  sandboxes: {},
  counts: { live: 0, totalEver: 0, destroyed: 0 },
  seen: true,
  spend: null,
  ...over,
});

const board = (observatory: ObservatoryState, over: Partial<BoardState> = {}): BoardState => ({
  ...initialState,
  observatory,
  ...over,
});

const spend = (over: Partial<SpendWire> = {}): SpendWire => ({
  live_sandbox_seconds: 3600,
  total_sandbox_seconds: 7200,
  est_cost_usd: null,
  rate_per_sandbox_hour: null,
  note: 'estimate',
  ...over,
});

// A poll-answering fetch stub for LIVE-mode renders (the panel polls
// GET /sandboxes on mount). Individual tests override method-specific routes.
const pollOk = () =>
  Promise.resolve({
    ok: true,
    json: async () => ({ counts: { live: 0, total_ever: 0, destroyed: 0 }, spend: null }),
  } as Response);

describe('SandboxObservatory — reconstruction & empty state', () => {
  it('renders reconstruction cards + the honest source label, no spend chip', () => {
    const state = board(obs({ seen: false }), {
      phase: 'detect',
      detect: {
        ...initialState.detect,
        trials: [
          { index: 0, passed: true },
          { index: 1, passed: false },
        ],
      },
    });
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    expect(screen.getByText('replay reconstruction')).toBeInTheDocument();
    // Reconstruction never invents money => no spend chip.
    expect(document.querySelector('.obs-spend')).toBeNull();
  });

  it('empty live registry shows the honest empty state, live 0, no cards', () => {
    vi.stubGlobal('fetch', vi.fn(pollOk));
    const state = board(obs({ seen: true, counts: { live: 0, totalEver: 0, destroyed: 0 } }));
    render(<SandboxObservatory state={state} mode="live" runActive={false} />);
    expect(screen.getByText(/no sandboxes tracked yet/i)).toBeInTheDocument();
    expect(document.querySelector('.obs-card')).toBeNull();
  });
});

describe('SandboxObservatory — preview link', () => {
  it('shows the "no preview link" note and no button when preview_url is null', () => {
    const state = board(obs({ sandboxes: { 'sb-a': sb('sb-a', { preview_url: null }) } }));
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    fireEvent.click(screen.getByTitle('open detail'));
    expect(screen.getByText(/no preview link/i)).toBeInTheDocument();
    expect(screen.queryByText(/Open Daytona preview/i)).toBeNull();
  });

  it('shows the preview button when preview_url is present', () => {
    const state = board(obs({ sandboxes: { 'sb-b': sb('sb-b', { preview_url: 'http://x/preview' }) } }));
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    fireEvent.click(screen.getByTitle('open detail'));
    expect(screen.getByText(/Open Daytona preview/i)).toBeInTheDocument();
  });
});

describe('SandboxObservatory — destroy-all modal gating', () => {
  it('disables Destroy all in replay mode', () => {
    const state = board(obs());
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    expect(screen.getByRole('button', { name: /destroy all/i })).toBeDisabled();
  });

  it('force checkbox appears only when a run is active', () => {
    vi.stubGlobal('fetch', vi.fn(pollOk));
    const state = board(obs());
    render(<SandboxObservatory state={state} mode="live" runActive={false} />);
    fireEvent.click(screen.getByRole('button', { name: /destroy all/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('force-confirm posts ?force=1 and surfaces a 409 toast', async () => {
    const fetchMock = vi.fn((url: string, _opts?: RequestInit) => {
      if (String(url).includes('destroy_all')) {
        return Promise.resolve({ ok: false, status: 409 } as Response);
      }
      return pollOk();
    });
    vi.stubGlobal('fetch', fetchMock);
    const state = board(obs({ counts: { live: 2, totalEver: 2, destroyed: 0 } }));
    render(<SandboxObservatory state={state} mode="live" runActive={true} />);

    fireEvent.click(screen.getByRole('button', { name: /destroy all/i }));
    // runActive => force checkbox present; check it to enable FORCE DESTROY ALL.
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /force destroy all/i }));

    await screen.findByText(/run is active/i);
    const posted = fetchMock.mock.calls.find((c) => String(c[0]).includes('destroy_all'));
    expect(posted).toBeTruthy();
    expect(String(posted![0])).toContain('force=1');
  });
});

describe('SandboxObservatory — spend chip', () => {
  it('shows "est. $0.12" when a cost estimate is present', () => {
    const state = board(
      obs({ spend: spend({ est_cost_usd: 0.12, rate_per_sandbox_hour: 0.1 }) }),
    );
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    expect(document.querySelector('.obs-spend')!.textContent).toContain('est. $0.12');
  });

  it('nudges to set the rate env var when no rate is configured', () => {
    const state = board(obs({ spend: spend({ est_cost_usd: null, rate_per_sandbox_hour: null }) }));
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    expect(document.querySelector('.obs-spend')!.textContent).toContain(
      'RETRIAL_EST_RATE_PER_SANDBOX_HOUR',
    );
  });

  it('renders no chip when spend is null', () => {
    const state = board(obs({ spend: null }));
    render(<SandboxObservatory state={state} mode="replay" runActive={false} />);
    expect(document.querySelector('.obs-spend')).toBeNull();
  });
});

describe('SandboxObservatory — auth-401 surfacing (B4b)', () => {
  it('a 401 on the destroy path shows the auth-gate message', async () => {
    const fetchMock = vi.fn((_url: string, opts?: RequestInit) => {
      if ((opts?.method ?? 'GET') === 'DELETE') {
        return Promise.resolve({ ok: false, status: 401 } as Response);
      }
      return pollOk();
    });
    vi.stubGlobal('fetch', fetchMock);
    const state = board(obs({ sandboxes: { 'sb-1': sb('sb-1') }, counts: { live: 1, totalEver: 1, destroyed: 0 } }));
    render(<SandboxObservatory state={state} mode="live" runActive={false} />);

    fireEvent.click(screen.getByRole('button', { name: /destroy sb-1/i }));
    const toast = await screen.findByRole('alert');
    expect(toast.textContent).toContain('RETRIAL_AUTH_TOKEN');
    expect(toast.textContent).toContain('unauthenticated');
  });

  it('a 409 on the destroy path still shows the conflict message (authAware falls through)', async () => {
    const fetchMock = vi.fn((_url: string, opts?: RequestInit) => {
      if ((opts?.method ?? 'GET') === 'DELETE') {
        return Promise.resolve({ ok: false, status: 409 } as Response);
      }
      return pollOk();
    });
    vi.stubGlobal('fetch', fetchMock);
    const state = board(obs({ sandboxes: { 'sb-1': sb('sb-1') }, counts: { live: 1, totalEver: 1, destroyed: 0 } }));
    render(<SandboxObservatory state={state} mode="live" runActive={false} />);

    fireEvent.click(screen.getByRole('button', { name: /destroy sb-1/i }));
    const toast = await screen.findByRole('alert');
    expect(toast.textContent).toContain('fork-children');
  });
});
