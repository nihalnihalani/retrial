import { useEffect, useState } from 'react';
import type { ConnectionMode } from './types';

const HEALTH_URL = 'http://localhost:8000/health';
const POLL_MS = 2000;

export interface PoolHealth {
  live: number;
  available: number;
}

// Polls the engine's /health for Daytona pool counts while LIVE, so the judges
// can watch their sandbox pool breathe through the whole demo. No-ops (and
// clears) outside LIVE mode so the default replay never touches the network.
export function useDaytonaHealth(mode: ConnectionMode): PoolHealth | null {
  const [health, setHealth] = useState<PoolHealth | null>(null);

  useEffect(() => {
    if (mode !== 'live') {
      setHealth(null);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const res = await fetch(HEALTH_URL, { cache: 'no-store' });
        if (!res.ok) throw new Error(String(res.status));
        const pool = (await res.json())?.pool ?? {};
        if (!cancelled && typeof pool.live === 'number') {
          setHealth({ live: pool.live, available: pool.available ?? 0 });
        }
      } catch {
        // transient failure — keep the last known counts, try again next tick
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, POLL_MS);
      }
    };
    poll();

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [mode]);

  return health;
}
