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
import threading

from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

from .registry import as_safe
from .settings import get_settings


class SandboxPool:
    """Thread-safe pool of fresh Daytona sandboxes for trial execution."""

    def __init__(self, client=None, target=None, labels=None, hermetic=False,
                 auto_delete_min=None, registry=None):
        self._client = client or Daytona(
            DaytonaConfig(target=target or get_settings().resolved_pool_target())
        )
        self._labels = labels or {"retrial": "pool"}
        # Sandbox Observatory feed: every create/warm/destroy is mirrored into
        # the registry (tests inject a private one; the server uses the process
        # default). All hooks are @_safe on the registry side — observability
        # can never break a run — so there is no try/except at the call sites.
        # as_safe wraps an injected registry so even a hostile one can't raise.
        self._registry = as_safe(registry)
        # Hermetic pools create every sandbox with egress blocked AT CREATE TIME
        # (never toggled at runtime — that kills the control channel). Used to
        # diagnose external-dependency flakes by infrastructure.
        self._hermetic = hermetic
        # Belt-and-braces credit protection: sandboxes auto-delete after this many
        # minutes, so a crashed run can't leave orphans burning credit.
        self._auto_delete_min = (auto_delete_min if auto_delete_min is not None
                                 else get_settings().auto_delete_min)
        self._available = []          # clean, ready-to-lease sandbox objects
        self._live = {}               # id -> sandbox, every sandbox we created and own
        self._lock = threading.Lock()
        # Sticky teardown sentinel (same lock-free-read style as ForkSandboxPool's
        # _degraded): destroy_all sets it so a still-running run cannot silently
        # re-create sandboxes against a pool the reap already tore down.
        self._torn_down = False

    # -- internals -------------------------------------------------------
    def _create_one(self):
        if self._torn_down:
            raise RuntimeError("snapshot pool torn down by destroy_all — "
                               "refusing to re-create a sandbox mid-process")
        kwargs = {"labels": self._labels}
        if self._auto_delete_min and self._auto_delete_min > 0:
            kwargs["auto_delete_interval"] = self._auto_delete_min
        if self._hermetic:
            kwargs["network_block_all"] = True
        sb = self._client.create(CreateSandboxFromSnapshotParams(**kwargs), timeout=120)
        with self._lock:
            self._live[sb.id] = sb
        # Registry hook OUTSIDE _lock (lock discipline): the destroyer the
        # registry routes DELETE /sandboxes/{id} through is this pool's _evict,
        # so destruction always goes through the owner and bookkeeping can never
        # lease a destroyed sandbox.
        self._registry.register(sb, role="snapshot-pool", backend="snapshot",
                                labels=self._labels, destroy_fn=self._evict,
                                state="creating")
        return sb

    def _evict(self, sid):
        """Per-sandbox destroyer the registry routes DELETE through. Removes the
        sandbox from _available (by id) and reads its handle from _live, then
        destroys OUTSIDE the lock. Idempotent — routing destruction through the
        owner keeps pool bookkeeping from ever leasing a destroyed sandbox."""
        with self._lock:
            self._available = [s for s in self._available
                               if getattr(s, "id", None) != sid]
            handle = self._live.get(sid)
        if handle is not None:
            self._destroy(handle)
        return None

    def _destroy(self, sb):
        try:
            self._client.delete(self._client.get(sb.id))
        except Exception:
            pass
        finally:
            with self._lock:
                self._live.pop(sb.id, None)
            # Registry hook OUTSIDE _lock: the sandbox is gone from the pool.
            self._registry.mark_destroyed(getattr(sb, "id", None))

    # -- public API ------------------------------------------------------
    def warm(self, n):
        """Pre-create n sandboxes concurrently, each pre-execed so its cold-start
        is paid now. Returns the count made ready."""
        if self._torn_down:
            raise RuntimeError("snapshot pool torn down by destroy_all — "
                               "refusing to warm mid-process")
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
                # Cold-start paid: the sandbox is warm and ready to lease.
                self._registry.set_state(getattr(sb, "id", None), "warm")
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
        if self._torn_down:
            raise RuntimeError("snapshot pool torn down by destroy_all — "
                               "refusing to create a sandbox mid-process")
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
        """Tear down every sandbox this pool owns, concurrently. Sets the sticky
        torn-down sentinel so a surviving run fails honestly instead of silently
        rebuilding sandboxes against a reaped pool (the reap resets the server's
        module pool ref to None so the NEXT run gets a fresh, un-torn pool)."""
        with self._lock:
            self._torn_down = True
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


def make_pool(bus=None, registry=None, **kwargs):
    """RETRIAL_POOL_BACKEND=fork|snapshot (default snapshot, the safe choice).

    fork = the Rewind engine: one warm root frozen as a checkpoint, trial
    sandboxes forked from it byte-identically — with automatic, sticky
    fallback to this SandboxPool on any fork-path failure (emits
    `pool_degraded`). snapshot = this SandboxPool, unchanged. `registry` (the
    Sandbox Observatory ledger) is threaded through to whichever backend is
    built; None means the process-default REGISTRY.
    """
    backend = get_settings().retrial_pool_backend.lower()
    if backend == "fork":
        from .forkpool import ForkSandboxPool  # lazy: avoids import cycle
        return ForkSandboxPool(bus=bus, registry=registry, **kwargs)
    return SandboxPool(registry=registry, **kwargs)
