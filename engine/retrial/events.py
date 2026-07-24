"""EventBus: thread-safe publish/subscribe with monotonic timestamps.

Every emitted event is a typed JSON-serializable dict fanned out to all
subscribers. A ring buffer of the last N events is replayed to any subscriber
that joins late (the UI stream), so a tab opened mid-tournament still sees the
race so far. Event types: trial_done, detect_done, hypothesis_created,
hypothesis_verified, hypothesis_eliminated, winner_confirmed, tournament_done.
"""
import threading
import time
from collections import deque

# The canonical event types this system emits. Documentation, not enforcement.
EVENT_TYPES = (
    "trial_done",
    "detect_done",
    "hypothesis_created",
    "hypothesis_verified",
    "hypothesis_eliminated",
    "winner_confirmed",
    "tournament_done",
)


class EventBus:
    """Thread-safe event fan-out with a replay buffer for late subscribers."""

    def __init__(self, buffer_size=500):
        self._subs = []
        self._buffer = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._seq = 0

    def subscribe(self, fn):
        """Register fn(event) and immediately replay the buffered backlog to it.

        Returns an unsubscribe callable.
        """
        with self._lock:
            self._subs.append(fn)
            backlog = list(self._buffer)
        for ev in backlog:
            try:
                fn(ev)
            except Exception:
                pass
        return lambda: self._unsubscribe(fn)

    def _unsubscribe(self, fn):
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def emit(self, event_type, payload):
        """Publish an event to every subscriber and append it to the buffer."""
        with self._lock:
            self._seq += 1
            ev = {
                "seq": self._seq,
                "type": event_type,
                "ts": round(time.monotonic() - self._t0, 4),
                "payload": payload,
            }
            self._buffer.append(ev)
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def reset(self):
        """Clear the replay buffer so the next run starts with a clean backlog.

        The buffer is shared and long-lived (one bus per server process). Without
        this, a late WS subscriber replays a PRIOR run's surviving tail — and
        because the buffer is capped, an earlier event (e.g. that run's
        detect_done) may already be evicted while its tournament_done survives,
        so the board shows a stale verdict with no matching detect. Call at the
        start of every run. `seq` stays monotonic (never rewound) so any
        already-connected client's event ordering is undisturbed."""
        with self._lock:
            self._buffer.clear()

    def history(self):
        """Return a snapshot of the buffered events (oldest first)."""
        with self._lock:
            return list(self._buffer)
