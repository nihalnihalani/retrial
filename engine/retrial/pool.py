"""SandboxPool: a thread-safe warm pool of disposable Daytona container sandboxes.

Isolation is matched to the flake class (set by the Verifier per seed):

- process isolation (the common case): the sandbox is REUSED across trials.
  Each trial runs a fresh `python3 /tmp/seed.py` process, and a fresh
  interpreter means a fresh PYTHONHASHSEED and fresh scheduling — the correct
  isolation for hash-order and scheduling-race flakes. `release(reusable=True)`
  returns the warm sandbox to the pool, so throughput is exec-bound, not
  create-bound (crucial for a live ~200-trial demo).
- sandbox isolation: the sandbox is DESTROYED after one trial (in the
  background) and replaced lazily on the next `lease()`. Required only for
  state-polluting flakes (filesystem/port/env pollution) where a reused process
  would leak state between trials.

Verified pattern (scripts/calibrate_seeds.py, DAYTONA-COOKBOOK.md): container
create ~0.7s, 16 concurrent creates ~2.0s, region "us".
"""
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class SandboxPool:
    """Thread-safe pool of fresh Daytona sandboxes for trial execution."""

    def __init__(self, client=None, target=None, labels=None):
        self._client = client or Daytona(
            DaytonaConfig(target=target or os.environ.get("DAYTONA_TARGET", "us"))
        )
        self._labels = labels or {"retrial": "pool"}
        self._available = []          # clean, ready-to-lease sandbox objects
        self._live = {}               # id -> sandbox, every sandbox we created and own
        self._lock = threading.Lock()

    # -- internals -------------------------------------------------------
    def _create_one(self):
        sb = self._client.create(
            CreateSandboxFromSnapshotParams(labels=self._labels), timeout=120
        )
        with self._lock:
            self._live[sb.id] = sb
        return sb

    def _destroy(self, sb):
        try:
            self._client.delete(self._client.get(sb.id))
        except Exception:
            pass
        finally:
            with self._lock:
                self._live.pop(sb.id, None)

    # -- public API ------------------------------------------------------
    def warm(self, n):
        """Pre-create n sandboxes concurrently, each pre-execed so its cold-start
        is paid now. Returns the count made ready."""
        made = [None] * n

        def mk(i):
            try:
                sb = self._create_one()
                # Pay the sandbox's first-exec cold-start now (concurrently, as part
                # of warm) so the first REAL trial lands instantly instead of after
                # a multi-second stall. A freshly created container's first exec is
                # slow; every exec after it is fast.
                try:
                    sb.process.exec("echo warm")
                except Exception:
                    pass
                made[i] = sb
            except Exception as e:
                made[i] = e

        threads = [threading.Thread(target=mk, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ready = [sb for sb in made if not isinstance(sb, Exception) and sb is not None]
        with self._lock:
            self._available.extend(ready)
        return len(ready)

    def ensure_warm(self, target):
        """Warm up to at least `target` ready sandboxes (never trims). Returns the
        available count after topping up. Used before a run so it starts demo-ready
        even if boot pre-warm was off or incomplete."""
        with self._lock:
            deficit = target - len(self._available)
        if deficit > 0:
            self.warm(deficit)
        with self._lock:
            return len(self._available)

    def resize_to(self, target):
        """Bring the warm pool to exactly `target` ready sandboxes: warm more if
        short, destroy the surplus if over. Keeps the pool bounded and demo-ready
        between runs. Returns the available count afterward."""
        with self._lock:
            surplus = len(self._available) - target
            extra = [self._available.pop() for _ in range(surplus)] if surplus > 0 else []
        for sb in extra:
            threading.Thread(target=self._destroy, args=(sb,), daemon=True).start()
        if surplus < 0:
            self.warm(-surplus)
        with self._lock:
            return len(self._available)

    def lease(self):
        """Hand out a fresh sandbox, popping a warm one or creating on demand."""
        with self._lock:
            if self._available:
                return self._available.pop()
        return self._create_one()

    def release(self, sb, reusable=False):
        """Return a sandbox to the pool.

        reusable=True (process isolation): the sandbox stays warm and goes back
        to the available pool for the next trial. reusable=False (sandbox
        isolation, or a sandbox that hit an infra error): destroy it in the
        background; it is replaced lazily on the next lease().
        """
        if sb is None:
            return
        if reusable:
            with self._lock:
                self._available.append(sb)
            return
        threading.Thread(target=self._destroy, args=(sb,), daemon=True).start()

    def destroy_all(self):
        """Tear down every sandbox this pool owns, concurrently."""
        with self._lock:
            sandboxes = list(self._live.values())
            self._available.clear()
        threads = [threading.Thread(target=self._destroy, args=(sb,)) for sb in sandboxes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return len(sandboxes)

    def stats(self):
        """Snapshot of pool occupancy (for the UI / debugging)."""
        with self._lock:
            return {"available": len(self._available), "live": len(self._live)}
