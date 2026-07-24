"""ForkSandboxPool hardening tests — Daytona SDK fully mocked (see conftest).

Ported from the Rewind fork_futures hardening suite and extended with the
degrade contract: checkpoint always re-frozen, partial clones rolled back,
degrade is sticky, spend guard trips into fallback, factory defaults to the
snapshot backend.
"""
import threading
import time

import pytest

from conftest import FakeCheckpointSandbox, FakeChild, FakeClient

from retrial import forkpool as forkpool_mod
from retrial.events import EventBus
from retrial.forkpool import ForkSandboxPool, _retry
from retrial.pool import SandboxPool, make_pool


@pytest.fixture(autouse=True)
def _stub_sdk(monkeypatch):
    """Belt-and-braces: no test may construct a real Daytona client."""
    monkeypatch.setattr(forkpool_mod, "Daytona", lambda *a, **k: object())
    monkeypatch.setattr(forkpool_mod, "DaytonaConfig", lambda *a, **k: object())
    import retrial.pool as pool_mod
    monkeypatch.setattr(pool_mod, "Daytona", lambda *a, **k: object())
    monkeypatch.setattr(pool_mod, "DaytonaConfig", lambda *a, **k: object())


def _fork_pool(client, **kwargs):
    kwargs.setdefault("fallback", SandboxPool(client=FakeClient()))
    return ForkSandboxPool(client=client, **kwargs)


def _wait_until(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# ----------------------------- _retry -----------------------------
def test_retry_returns_first_success_and_raises_last_error():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"boom {calls['n']}")
        return "ok"

    assert _retry("t", flaky) == "ok"
    assert calls["n"] == 3
    with pytest.raises(RuntimeError, match="always"):
        _retry("t", lambda: (_ for _ in ()).throw(RuntimeError("always")))


# ----------------------------- test 1: happy path -----------------------------
def test_warm_creates_one_root_one_checkpoint_n_clones():
    client = FakeClient()
    pool = _fork_pool(client)
    assert pool.warm(2) == 2

    # ONE root, created once, cold-start paid.
    assert client.create_calls == 1
    root = client.roots[0]
    assert ("echo warm", None) in [(c, t) for c, t in root.process.execs] or \
        root.process.execs[0][0] == "echo warm"
    # Checkpoint = fork-of-root, frozen at creation, woken for the batch,
    # re-frozen after it: pause #1 = freeze, pause #2 = finally re-freeze.
    assert root.fork_calls == 1
    ckpt = client.checkpoints[0]
    assert ckpt.start_calls == 1
    assert ckpt.pause_calls == 2
    # 2 byte-identical clones, tracked live and available.
    assert len(ckpt.children) == 2
    assert pool.stats()["available"] == 2
    assert pool.stats()["live"] == 2
    assert pool.stats()["backend"] == "fork"
    assert pool._degraded is False


def test_second_warm_reuses_checkpoint():
    client = FakeClient()
    pool = _fork_pool(client)
    pool.warm(1)
    pool.warm(2)
    assert client.create_calls == 1          # root created once, ever
    assert client.roots[0].fork_calls == 1   # checkpoint created once, ever
    assert len(client.checkpoints[0].children) == 3


# ------------------- test 2: partial-fork rollback + degrade -------------------
def test_fork_failure_mid_batch_rolls_back_degrades_and_falls_back():
    client = FakeClient(clone_script=[("child", "c0"), ("raise",)])
    bus = EventBus()
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client, bus=bus,
                           fallback=SandboxPool(client=fallback_client))

    # No raise to the caller: warm degrades and is served by the fallback.
    assert pool.warm(2) == 2

    # The clone spawned before the failure was rolled back — no orphans.
    assert pool._live == {}
    assert "c0" in client.deleted
    # Checkpoint re-paused exactly once by the batch (pause #1 was the freeze).
    ckpt = client.checkpoints[0]
    assert ckpt.pause_calls == 2
    # Degraded, and the warm sandboxes came from the snapshot fallback.
    assert pool._degraded is True
    assert fallback_client.create_calls == 2
    # pool_degraded emitted on the real bus with the honest payload.
    degraded = [e for e in bus.history() if e["type"] == "pool_degraded"]
    assert len(degraded) == 1
    payload = degraded[0]["payload"]
    assert payload["backend"] == "fork"
    assert payload["fallback"] == "snapshot"
    assert "fork blew up" in payload["reason"]


def test_missing_fork_primitive_degrades():
    # AttributeError from a missing _experimental_fork rides the same generic
    # except net — no dedicated hasattr probe exists (mock-verified only).
    client = FakeClient(root_fork_fails=True)
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client,
                           fallback=SandboxPool(client=fallback_client))
    assert pool.warm(1) == 1
    assert pool._degraded is True
    assert fallback_client.create_calls == 1


# ----------------------------- test 3: factory -----------------------------
def test_make_pool_defaults_to_snapshot(monkeypatch):
    monkeypatch.delenv("RETRIAL_POOL_BACKEND", raising=False)
    pool = make_pool(client=FakeClient())
    assert isinstance(pool, SandboxPool)
    assert not isinstance(pool, ForkSandboxPool)


def test_make_pool_fork_backend(monkeypatch):
    monkeypatch.setenv("RETRIAL_POOL_BACKEND", "fork")
    bus = EventBus()
    pool = make_pool(bus=bus, client=FakeClient())
    assert isinstance(pool, ForkSandboxPool)
    assert pool._bus is bus


def test_make_pool_unknown_backend_is_snapshot(monkeypatch):
    monkeypatch.setenv("RETRIAL_POOL_BACKEND", "warp-drive")
    assert isinstance(make_pool(client=FakeClient()), SandboxPool)


# ------------------------- test 4: leaf-first teardown -------------------------
def test_destroy_all_order_forks_then_checkpoint_then_root():
    client = FakeClient()
    pool = _fork_pool(client)
    pool.warm(3)
    ckpt = client.checkpoints[0]
    clone_ids = {c.id for c in ckpt.children}

    assert pool.destroy_all() == 5  # 3 forks + checkpoint + root
    order = client.deleted
    assert set(order[:3]) == clone_ids          # leaves first (concurrent batch)
    assert order[3] == ckpt.id                  # then the checkpoint
    assert order[4] == client.roots[0].id       # root last
    assert pool._live == {}
    assert pool.stats()["available"] == 0
    # Idempotent: nothing left to destroy.
    assert pool.destroy_all() == 0


# ------------------------- test 5: release semantics -------------------------
def test_release_reusable_returns_clone_to_available():
    client = FakeClient()
    pool = _fork_pool(client)
    pool.warm(1)
    sb = pool.lease()
    assert pool.stats()["available"] == 0
    pool.release(sb, reusable=True)
    stats = pool.stats()
    assert stats["available"] == 1 and stats["live"] == 1
    assert pool.lease() is sb  # same warm clone handed back out


def test_release_not_reusable_destroys_in_background():
    client = FakeClient()
    pool = _fork_pool(client)
    pool.warm(1)
    sb = pool.lease()
    pool.release(sb, reusable=False)
    assert _wait_until(lambda: sb.id in client.deleted)
    assert _wait_until(lambda: pool.stats()["live"] == 0)


def test_lease_forks_on_demand_when_pool_empty():
    client = FakeClient()
    pool = _fork_pool(client)
    sb = pool.lease()  # no warm() first: creates root+ckpt, forks one clone
    assert sb is not None
    assert client.create_calls == 1
    assert len(client.checkpoints[0].children) == 1


# ----------------------------- test 6: spend guard -----------------------------
def test_spend_guard_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setenv("RETRIAL_MAX_FORKS", "1")
    client = FakeClient()
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client,
                           fallback=SandboxPool(client=fallback_client))
    assert pool.warm(2) == 2  # served by the fallback, no raise to the caller
    assert pool._degraded is True
    assert fallback_client.create_calls == 2
    # The guard tripped before waking the checkpoint or forking anything.
    assert client.checkpoints[0].start_calls == 0
    assert client.checkpoints[0].fork_calls == 0


# ------------------------- test 7: degrade stickiness -------------------------
def test_degrade_is_sticky_no_fork_machinery_reinvoked():
    client = FakeClient(clone_script=[("raise",)])
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client,
                           fallback=SandboxPool(client=fallback_client))

    assert pool.warm(1) == 1  # induced degrade, served by fallback
    creates_after_first = client.create_calls
    root_forks_after_first = client.roots[0].fork_calls
    ckpt_forks_after_first = client.checkpoints[0].fork_calls

    assert pool.warm(1) == 1  # second call: straight to _fallback.warm
    assert client.create_calls == creates_after_first        # no new root
    assert client.roots[0].fork_calls == root_forks_after_first    # no new ckpt
    assert client.checkpoints[0].fork_calls == ckpt_forks_after_first  # no clone forks
    assert fallback_client.create_calls == 2

    # The other entry points short-circuit too.
    pool.ensure_warm(3)
    pool.resize_to(2)
    assert pool.lease() is not None
    assert client.checkpoints[0].fork_calls == ckpt_forks_after_first


def test_degraded_stats_merge_fallback_and_report_backend():
    client = FakeClient(clone_script=[("raise",)])
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client,
                           fallback=SandboxPool(client=fallback_client))
    pool.warm(2)
    stats = pool.stats()
    assert stats["backend"] == "fork-degraded"
    assert stats["available"] == 2 and stats["live"] == 2  # fallback's sandboxes


def test_release_pre_degrade_clone_routed_by_ownership():
    # Lease a clone while healthy, then degrade: releasing that clone must be
    # handled by the fork pool (it owns it), not the fallback.
    client = FakeClient(clone_script=[("child", "c0"), ("raise",)])
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client,
                           fallback=SandboxPool(client=fallback_client))
    pool.warm(1)
    sb = pool.lease()
    assert sb.id == "c0"
    pool.warm(1)  # second batch fails -> degrade
    assert pool._degraded is True
    pool.release(sb, reusable=True)  # fork clone post-degrade: destroyed, not stranded
    assert _wait_until(lambda: sb.id in client.deleted)
    assert _wait_until(lambda: sb.id not in pool._live)
    assert sb.id not in [getattr(s, "id", None) for s in fallback_client.registry.values()]


def test_concurrent_warms_degrade_once():
    # Two threads racing into a failing fork path: exactly one pool_degraded
    # event, both calls served by the fallback, no exception escapes.
    client = FakeClient(clone_script=[("raise",)])
    bus = EventBus()
    fallback_client = FakeClient()
    pool = ForkSandboxPool(client=client, bus=bus,
                           fallback=SandboxPool(client=fallback_client))
    results = []
    threads = [threading.Thread(target=lambda: results.append(pool.warm(1)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [1, 1]
    assert len([e for e in bus.history() if e["type"] == "pool_degraded"]) == 1


# ------- test: fork-batch serialization under thread storm (fatal-risk) -------
class _SlowForkCheckpoint(FakeCheckpointSandbox):
    """~1ms inside the fork critical section, so UNSERIALIZED concurrent
    batches would overlap (max_in_flight > 1) and a concurrent batch's
    re-freezing pause() could land mid-fork (pauses_during_fork > 0)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pauses_during_fork = 0

    def pause(self):
        with self._mutex:
            if self.in_flight > 0:
                self.pauses_during_fork += 1
        super().pause()

    def _experimental_fork(self, name=None):
        with self._mutex:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self._auto_n += 1
            cid = f"{self.id}-clone-{self._auto_n}"
        try:
            time.sleep(0.001)
            child = FakeChild(cid)
            with self._mutex:
                self.fork_calls += 1
                self.children.append(child)
                self._registry[child.id] = child
            return child
        finally:
            with self._mutex:
                self.in_flight -= 1


def test_fork_pool_serializes_fork_batches_under_thread_storm():
    # Coordinator load shape: many worker threads lease() on-demand at once
    # against one checkpoint handle. The _fork_lock discipline must issue
    # forks one batch at a time and never pause() the handle mid-fork.
    client = FakeClient()
    pool = _fork_pool(client)
    ckpt = _SlowForkCheckpoint(cid="ckpt-stress", registry=client.registry)
    pool._ckpt = ckpt          # checkpoint pre-built; lease() goes straight to
    pool._root = object()      # _fork_clones (the code under stress)
    n = 16
    barrier = threading.Barrier(n)
    leased = [None] * n

    def worker(i):
        barrier.wait()
        leased[i] = pool.lease()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert pool._degraded is False                # nothing fell back
    assert ckpt.max_in_flight == 1                # forks actually serialized
    assert ckpt.pauses_during_fork == 0           # no re-freeze mid-fork
    assert ckpt.start_calls == n                  # each batch: start ...
    assert ckpt.pause_calls == n                  # ... then re-freeze
    ids = {sb.id for sb in leased}
    assert len(ids) == n                          # 16 distinct clones
    assert set(pool._live) == ids                 # no corruption/duplicates


def test_spend_guard_check_is_inside_fork_lock(monkeypatch):
    # Two racing single-clone batches with room for only ONE more fork: the
    # loser of the lock must re-read the live count and trip the guard —
    # never jointly overshoot RETRIAL_MAX_FORKS.
    monkeypatch.setenv("RETRIAL_MAX_FORKS", "1")
    client = FakeClient()
    pool = _fork_pool(client)
    ckpt = _SlowForkCheckpoint(cid="ckpt-guard", registry=client.registry)
    pool._ckpt = ckpt
    pool._root = object()
    barrier = threading.Barrier(2)
    results = [None, None]

    def worker(i):
        barrier.wait()
        results[i] = pool.lease()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert ckpt.fork_calls == 1                   # exactly one fork went out
    assert len(pool._live) == 1
    # The guard trip degrades that caller to the snapshot fallback (the
    # degrade contract) — but the cap itself was never overshot.
    assert pool._degraded is True
