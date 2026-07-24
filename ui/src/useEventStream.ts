import { useEffect, useReducer, useState } from 'react';
import { buildMockScript } from './mockRun';
import { initialState, reduce } from './reducer';
import type { BoardState, ConnectionMode, RetrialEvent } from './types';

const WS_URL = 'ws://localhost:8000/ws';
const CONNECT_TIMEOUT_MS = 1500;

interface Stream {
  state: BoardState;
  mode: ConnectionMode;
}

// Connects to the live engine WebSocket if it answers quickly; otherwise falls
// back to the bundled scripted replay so the board is always demoable.
// A full reset is done by remounting the consumer (App keys it by run id).
export function useEventStream(): Stream {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [mode, setMode] = useState<ConnectionMode>('connecting');

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let timeoutId: number | undefined;
    let mockCancel: (() => void) | null = null;
    let wentLive = false;

    const startMock = () => {
      if (disposed || mockCancel) return;
      setMode('replay');
      mockCancel = playScript((ev) => dispatch(ev));
    };

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
  }, []);

  return { state, mode };
}

// Walks the scripted replay, emitting each event after its delay. Returns a
// cancel function that stops any pending timer.
function playScript(emit: (ev: RetrialEvent) => void): () => void {
  const script = buildMockScript();
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
