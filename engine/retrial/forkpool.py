"""ForkSandboxPool: fork-clone provisioning with automatic snapshot fallback.

The Rewind engine inside Retrial. Instead of creating N independent sandboxes
(each with its own cold-start history), this pool warms ONE root sandbox
(repo + deps + hot caches), freezes it as a checkpoint — a paused fork-child
capturing full filesystem + RAM while the root keeps running — and
`_experimental_fork`s that checkpoint into N byte-identical trial clones.

Why this matters statistically: every trial clone starts from the SAME initial
fs+RAM state, so trial-to-trial variance is purely the flake under test, not
provisioning noise (different cache states, different first-exec timings).
Tighter trial distribution -> tighter Wilson CI for the same trial budget.

The degrade contract: the fork/pause/start APIs are experimental and cannot be
live-verified in this environment (mock-verified only). ANY failure on the
fork path — including the AttributeError a missing `_experimental_fork` raises —
degrades this pool, permanently for its lifetime, to a plain `SandboxPool`
(the proven snapshot pool) and emits a `pool_degraded` event. Degrade is
STICKY: once degraded, every entry point short-circuits straight to the
fallback so we never re-pay a root-create + failed-fork round-trip (seconds,
live, on every growth call) re-discovering the same missing primitive.

Drop-in guarantee: same 7-method public surface as `SandboxPool`
(warm/ensure_warm/resize_to/lease/release/destroy_all/stats) — consumers
never know which backend served them. Select via RETRIAL_POOL_BACKEND=fork
(see `pool.make_pool`; the default is snapshot, the safe choice).
"""
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

from .pool import SandboxPool

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _retry(op_name, fn, attempts=3, base_delay=1.0):
    """Retry an _experimental_ Daytona call with linear backoff.

    The fork/pause/start APIs are still experimental — a transient failure
    should cost a retry, not the demo. Raises the last error after exhausting
    attempts.
    """
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # SDK raises broad DaytonaError subclasses
            last = exc
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    raise last


class ForkSandboxPool:
    """Drop-in for SandboxPool: same 7-method surface, fork-clone provisioning."""

    def __init__(self, client=None, target=None, labels=None, hermetic=False,
                 auto_delete_min=None, bus=None, fallback=None):
        self._client = client or Daytona(
            DaytonaConfig(target=target or os.environ.get("DAYTONA_TARGET", "us"))
        )
        # Remember ctor args so the lazy fallback SandboxPool is built with the
        # same shape (same client if one was injected — the mocked tests rely
        # on that; a fresh client otherwise).
        self._injected_client = client
        self._target = target
        self._labels = labels or {"retrial": "fork-pool"}
        self._hermetic = hermetic
        self._auto_delete_min = auto_delete_min
        self._root = None             # the one warm root sandbox (running)
        self._ckpt = None             # frozen checkpoint: paused fork-child of root
        self._available = []          # clean, ready-to-lease trial clones
        self._live = {}               # id -> sandbox, trial forks only (not root/ckpt)
        self._lock = threading.Lock()
        self._ckpt_lock = threading.Lock()     # serializes checkpoint creation
        # CONCURRENCY RULE (load-bearing — do not "optimize" this lock away):
        # the coordinator leases from many worker threads at once (one thread
        # per hypothesis x tournament_conc workers each), but the ONLY proven
        # usage of _experimental_fork is strictly sequential from a single
        # thread on one handle (Rewind's fork_futures). No code anywhere
        # demonstrates concurrent forks on one sandbox handle, and the real SDK
        # cannot be exercised here. So the whole start -> fork xN -> pause
        # batch is serialized: without this, one thread's re-freezing pause()
        # (the `finally` in _fork_clones) could land mid-fork of another
        # thread's batch on the SAME checkpoint handle. Trial EXECUTION
        # afterwards still runs fully parallel — the cost is only the (small)
        # serial fork latency, not trial serialization. Mirrors
        # _CheckpointProbePool._fork_lock in bisect.py (MERGE-PLAN caveat).
        self._fork_lock = threading.Lock()
        self._degrade_lock = threading.Lock()  # serializes the one-shot degrade
        self._degraded = False
        self._fallback = fallback     # SandboxPool, built lazily on first degrade
        self._bus = bus
        # Spend guard: hard cap on simultaneously live trial forks. Every live
        # fork bills real compute — no bug should be able to fork-bomb the wallet.
        self._max_forks = int(os.environ.get("RETRIAL_MAX_FORKS", "64"))

    # -- internals -------------------------------------------------------
    def _ensure_checkpoint(self):
        """Warm the root sandbox once and freeze it as the checkpoint.

        Freeze = fork the live root (the root never stops — the Rewind
        invariant), then pause the fork-child, capturing fs + RAM.
        """
        with self._ckpt_lock:
            if self._ckpt is not None:
                return
            kwargs = {"labels": self._labels}
            if self._auto_delete_min is None:
                # Same belt-and-braces credit protection as SandboxPool.
                self._auto_delete_min = int(os.environ.get("AUTO_DELETE_MIN", "60"))
            if self._auto_delete_min and self._auto_delete_min > 0:
                kwargs["auto_delete_interval"] = self._auto_delete_min
            if self._hermetic:
                kwargs["network_block_all"] = True
            root = self._client.create(CreateSandboxFromSnapshotParams(**kwargs),
                                       timeout=120)
            self._root = root
            # Pay the root's first-exec cold-start now so every clone inherits
            # a hot sandbox (same rationale as SandboxPool.warm).
            try:
                root.process.exec("echo warm")
            except Exception:
                pass
            # Optional bootstrap (repo clone / deps / cache warm) baked into the
            # checkpoint so every clone starts past it. Default empty — retrial
            # seeds only need python3, which the snapshot image already has.
            bootstrap = os.environ.get("RETRIAL_FORK_BOOTSTRAP_CMD", "").strip()
            if bootstrap:
                root.process.exec(bootstrap, timeout=180)
            fork = _retry("ckpt.fork", lambda: root._experimental_fork(
                name=f"retrial-ckpt-{uuid4().hex[:6]}"))
            _retry("ckpt.pause", fork.pause)
            self._ckpt = fork

    def _fork_clones(self, n):
        """Wake the checkpoint, fork n byte-identical trial clones, re-freeze.

        Hardened (the Rewind fork_futures invariants): the checkpoint is ALWAYS
        re-paused (finally), even if a fork raises mid-loop; clones spawned
        before the failure are cleaned up so a partial batch never leaks
        orphan sandboxes.

        The whole start -> fork xN -> pause batch holds `_fork_lock` (see the
        rationale on the lock in __init__): concurrent batches on one handle
        are an unproven SDK pattern, and an unserialized re-freeze pause()
        would land mid-fork of a concurrent batch. The spend-guard check sits
        inside the lock too, so two racing batches cannot both read a stale
        `live` count and jointly overshoot RETRIAL_MAX_FORKS.
        """
        with self._fork_lock:
            with self._lock:
                live = len(self._live)
            if live + n > self._max_forks:
                raise RuntimeError(
                    f"spend guard: {live} trial forks live; forking {n} more would "
                    f"exceed RETRIAL_MAX_FORKS={self._max_forks}")
            _retry("clones.start", self._ckpt.start)
            clones = []
            try:
                for _ in range(n):
                    fork = _retry("clones.fork", lambda: self._ckpt._experimental_fork(
                        name=f"retrial-trial-{uuid4().hex[:6]}"))
                    with self._lock:
                        self._live[fork.id] = fork
                    clones.append(fork)
            except Exception:
                # Roll back partial clones so no orphan sandboxes leak (best-effort).
                for sb in clones:
                    self._destroy(sb)
                raise
            finally:
                # The checkpoint MUST return to frozen so it stays a reusable fork
                # source and stops burning CPU quota.
                try:
                    self._ckpt.pause()
                except Exception:
                    pass
            return clones

    def _destroy(self, sb):
        try:
            self._client.delete(self._client.get(sb.id))
        except Exception:
            pass
        finally:
            with self._lock:
                self._live.pop(sb.id, None)

    def _degrade(self, exc):
        """One-shot, sticky switch to the snapshot fallback. Never raises.

        Held under its own lock so a second failing thread blocks until the
        fallback exists, then finds `_degraded` already set.
        """
        first = False
        with self._degrade_lock:
            if not self._degraded:
                self._degraded = True
                first = True
            if self._fallback is None:
                try:
                    self._fallback = SandboxPool(
                        client=self._injected_client, target=self._target,
                        labels=self._labels, hermetic=self._hermetic,
                        auto_delete_min=self._auto_delete_min)
                except Exception:
                    # Last resort: a client-less pool ctor failing means the
                    # SDK itself is unusable; leave fallback for the next call.
                    self._degraded = True
        if first and self._bus is not None:
            try:
                self._bus.emit("pool_degraded", {
                    "backend": "fork",
                    "fallback": "snapshot",
                    "reason": str(exc)[:200],
                })
            except Exception:
                pass

    # -- public API ------------------------------------------------------
    def warm(self, n):
        """Fork n byte-identical clones from the checkpoint (creating root +
        checkpoint on first call). Returns the count made ready."""
        if self._degraded:
            # Sticky short-circuit: never re-pay a root-create + failed-fork
            # round-trip re-discovering the same missing primitive.
            return self._fallback.warm(n)
        if n <= 0:
            return 0
        try:
            self._ensure_checkpoint()
            clones = self._fork_clones(n)
            with self._lock:
                self._available.extend(clones)
            return len(clones)
        except Exception as exc:
            self._degrade(exc)
            return self._fallback.warm(n)

    def ensure_warm(self, target):
        """Warm up to at least `target` ready clones (never trims). Returns the
        available count after topping up."""
        if self._degraded:
            return self._fallback.ensure_warm(target)
        try:
            with self._lock:
                deficit = target - len(self._available)
            if deficit > 0:
                self.warm(deficit)
            if self._degraded:  # warm() may have degraded mid-call
                return self._fallback.ensure_warm(target)
            with self._lock:
                return len(self._available)
        except Exception as exc:
            self._degrade(exc)
            return self._fallback.ensure_warm(target)

    def resize_to(self, target):
        """Bring the pool to exactly `target` ready clones: fork more if short,
        destroy the surplus if over. Returns the available count afterward."""
        if self._degraded:
            return self._fallback.resize_to(target)
        try:
            with self._lock:
                surplus = len(self._available) - target
                extra = [self._available.pop() for _ in range(surplus)] if surplus > 0 else []
            for sb in extra:
                threading.Thread(target=self._destroy, args=(sb,), daemon=True).start()
            if surplus < 0:
                self.warm(-surplus)
            if self._degraded:
                return self._fallback.resize_to(target)
            with self._lock:
                return len(self._available)
        except Exception as exc:
            self._degrade(exc)
            return self._fallback.resize_to(target)

    def lease(self):
        """Hand out a trial clone, popping a warm one or forking on demand."""
        if self._degraded:
            return self._fallback.lease()
        try:
            with self._lock:
                if self._available:
                    return self._available.pop()
            self._ensure_checkpoint()
            return self._fork_clones(1)[0]
        except Exception as exc:
            self._degrade(exc)
            return self._fallback.lease()

    def release(self, sb, reusable=False):
        """Return a trial clone to the pool.

        Identical semantics to SandboxPool.release: reusable=True (process
        isolation) returns the clone to the available pool — each trial is a
        fresh `python3` process, so a fork clone is reused exactly like a
        snapshot sandbox; reusable=False destroys it in the background.
        A sandbox leased pre-degrade is still tracked in `_live`, so route by
        ownership when degraded.
        """
        if sb is None:
            return
        if self._degraded:
            with self._lock:
                mine = getattr(sb, "id", None) in self._live
            if not mine:
                return self._fallback.release(sb, reusable=reusable)
            # A fork clone released after degrade is never re-leased (leases go
            # to the fallback now) — destroy it instead of stranding it.
            threading.Thread(target=self._destroy, args=(sb,), daemon=True).start()
            return
        if reusable:
            with self._lock:
                self._available.append(sb)
            return
        threading.Thread(target=self._destroy, args=(sb,), daemon=True).start()

    def destroy_all(self):
        """Tear down everything this pool owns — leaf-first, mandatory order:
        trial forks, then the checkpoint, then the root (Daytona refuses to
        delete a parent while fork-children live). Then the fallback's
        sandboxes, if one was ever built."""
        with self._lock:
            forks = list(self._live.values())
            self._available.clear()
        threads = [threading.Thread(target=self._destroy, args=(sb,)) for sb in forks]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        count = len(forks)
        for sb in (self._ckpt, self._root):
            if sb is None:
                continue
            try:
                self._client.delete(self._client.get(sb.id))
            except Exception:
                pass
            count += 1
        self._ckpt = None
        self._root = None
        if self._fallback is not None:
            count += self._fallback.destroy_all()
        return count

    def stats(self):
        """Snapshot of pool occupancy (for /health and the UI SandboxTicker).

        Same {"available", "live"} contract as SandboxPool (extra keys are
        ignored by today's consumers); counts merge the fallback's once
        degraded so the ticker never under-reports."""
        with self._lock:
            available = len(self._available)
            live = len(self._live)
        if self._degraded and self._fallback is not None:
            fstats = self._fallback.stats()
            available += fstats["available"]
            live += fstats["live"]
        return {"available": available, "live": live,
                "backend": "fork-degraded" if self._degraded else "fork"}
