import { useEffect, useReducer, useState } from 'react';
import { initialState, reduce } from './reducer';
import type { BoardState, ConnectionMode, RetrialEvent } from './types';

const WS_URL = 'ws://localhost:8000/ws';
const CONNECT_TIMEOUT_MS = 4000;
const RECONNECT_DELAY_MS = 2000;

interface Stream {
  state: BoardState;
  mode: ConnectionMode;
  connectionMessage: string | null;
}

// The board is live-only. A frame is either streamed by the engine or it is an
// explicit connection state; there is no recorded or scripted fallback.
export function useEventStream(): Stream {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [mode, setMode] = useState<ConnectionMode>('connecting');
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let timeoutId: number | undefined;
    let reconnectTimer: number | undefined;

    const connect = () => {
      if (disposed) return;
      setMode('connecting');
      setConnectionMessage(null);

      try {
        ws = new WebSocket(WS_URL);
      } catch {
        setMode('disconnected');
        setConnectionMessage('The live engine could not be reached on port 8000.');
        return;
      }

      timeoutId = window.setTimeout(() => {
        if (disposed || ws?.readyState === WebSocket.OPEN) return;
        setMode('disconnected');
        setConnectionMessage('The live engine did not respond. Start the backend, then reconnect.');
        try {
          ws?.close();
        } catch {
          /* no-op */
        }
      }, CONNECT_TIMEOUT_MS);

      ws.onopen = () => {
        if (disposed) return;
        window.clearTimeout(timeoutId);
        setMode('live');
        setConnectionMessage(null);
      };

      ws.onmessage = (message) => {
        try {
          dispatch(JSON.parse(message.data) as RetrialEvent);
        } catch {
          // Ignore malformed frames. The typed reducer remains the only state path.
        }
      };

      ws.onerror = () => {
        if (disposed) return;
        setConnectionMessage('The live event stream encountered a connection error.');
      };

      ws.onclose = () => {
        if (disposed) return;
        window.clearTimeout(timeoutId);
        setMode('disconnected');
        setConnectionMessage('Live engine disconnected. Retrying automatically every two seconds.');
        reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();

    return () => {
      disposed = true;
      window.clearTimeout(timeoutId);
      window.clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        /* no-op */
      }
    };
  }, []);

  return { state, mode, connectionMessage };
}
