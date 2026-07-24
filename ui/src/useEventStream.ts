import { useEffect, useReducer, useState } from 'react';
import { buildMockScript, type MockOutcome } from './mockRun';
import { initialState, reduce } from './reducer';
import type { BoardState, ConnectionMode, RetrialEvent } from './types';

const WS_URL = 'ws://localhost:8000/ws';
const CONNECT_TIMEOUT_MS = 1500;

interface Stream {
  state: BoardState;
  mode: ConnectionMode;
}

// Live wiring is opt-in via `?live=1` so the default (replay) demo keeps a
// spotless console — no failed WebSocket attempts on projector. With `?live=1`
// we try the engine, show LIVE only once connected, and fall back to the
// bundled scripted replay if it never opens.
// `?mock=quarantine` replays the no-winner path instead of the winning one.
// A full reset is done by remounting the consumer (App keys it by run id).
export function useEventStream(): Stream {
  const [state, dispatch] = useReducer(reduce, initialState);
  const params = readParams();
  const [mode, setMode] = useState<ConnectionMode>(params.live ? 'connecting' : 'replay');

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let timeoutId: number | undefined;
    let mockCancel: (() => void) | null = null;
    let wentLive = false;

    const startMock = () => {
      if (disposed || mockCancel) return;
      setMode('replay');
      mockCancel = playScript(params.mock, (ev) => dispatch(ev));
    };

    // Default mode: straight to replay, never touch the network.
    if (!params.live) {
      startMock();
      return () => {
        disposed = true;
        mockCancel?.();
      };
    }

    try {
      ws = new WebSocket(WS_URL);
      timeoutId = window.setTimeout(() => {
        if (disposed || wentLive) return;
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
        startMock();
      }, CONNECT_TIMEOUT_MS);

      ws.onopen = () => {
        if (disposed) return;
        wentLive = true;
        window.clearTimeout(timeoutId);
        setMode('live');
      };
      ws.onmessage = (msg) => {
        try {
          dispatch(JSON.parse(msg.data) as RetrialEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (disposed || wentLive) return;
        window.clearTimeout(timeoutId);
        startMock();
      };
    } catch {
      startMock();
    }

    return () => {
      disposed = true;
      window.clearTimeout(timeoutId);
      mockCancel?.();
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { state, mode };
}

function readParams(): { live: boolean; mock: MockOutcome } {
  if (typeof window === 'undefined') return { live: false, mock: 'winner' };
  const q = new URLSearchParams(window.location.search);
  return {
    live: q.get('live') === '1',
    mock: q.get('mock') === 'quarantine' ? 'quarantine' : 'winner',
  };
}

// Walks the scripted replay, emitting each event after its delay. Returns a
// cancel function that stops any pending timer.
function playScript(outcome: MockOutcome, emit: (ev: RetrialEvent) => void): () => void {
  const script = buildMockScript(outcome);
  let i = 0;
  let timer: number | undefined;
  let cancelled = false;

  const step = () => {
    if (cancelled || i >= script.length) return;
    const { after, event } = script[i];
    timer = window.setTimeout(() => {
      if (cancelled) return;
      emit(event);
      i++;
      step();
    }, after);
  };
  step();

  return () => {
    cancelled = true;
    window.clearTimeout(timer);
  };
}
