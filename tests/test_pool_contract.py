"""Backend contract parity: every pool backend honors the exact external
surface server.py / /health / the UI SandboxTicker rely on.

Parametrized over the snapshot pool, the fork pool, and a fork pool pre-forced
into its degraded state — this is what stops a stub ForkSandboxPool passing
its own tests while drifting from the drop-in surface.
"""
import time

import pytest

from conftest import FakeChild, FakeClient

from retrial.forkpool import ForkSandboxPool
from retrial.pool import SandboxPool


def _snapshot_pool():
    return SandboxPool(client=FakeClient())


def _fork_pool():
    return ForkSandboxPool(client=FakeClient(),
                           fallback=SandboxPool(client=FakeClient()))


def _degraded_fork_pool():
    pool = _fork_pool()
    pool._degrade(RuntimeError("forced degrade for contract test"))
    assert pool._degraded is True
    return pool


@pytest.fixture(params=["snapshot", "fork", "fork-degraded"])
def pool(request):
    return {"snapshot": _snapshot_pool,
            "fork": _fork_pool,
            "fork-degraded": _degraded_fork_pool}[request.param]()


def test_stats_always_has_int_available_and_live(pool):
    stats = pool.stats()
    assert {"available", "live"} <= set(stats)
    assert isinstance(stats["available"], int)
    assert isinstance(stats["live"], int)
    # ... and stays well-typed after activity.
    pool.warm(1)
    sb = pool.lease()
    stats = pool.stats()
    assert isinstance(stats["available"], int)
    assert isinstance(stats["live"], int)
    pool.release(sb, reusable=True)


def test_lease_never_none_when_provisionable(pool):
    assert pool.lease() is not None          # cold lease (creates/forks on demand)
    pool.warm(2)
    assert pool.lease() is not None          # warm lease


def test_release_none_and_unknown_are_noops(pool):
    pool.release(None)                       # must not raise
    pool.release(None, reusable=True)
    stranger = FakeChild("never-leased-from-this-pool")
    pool.release(stranger)                   # must not raise
    pool.release(stranger, reusable=True)
    time.sleep(0.02)                         # let background destroy threads run


def test_warm_zero_and_resizes_return_without_raising(pool):
    assert isinstance(pool.warm(0), int)
    assert isinstance(pool.ensure_warm(2), int)
    assert isinstance(pool.resize_to(1), int)
    assert isinstance(pool.resize_to(0), int)


def test_destroy_all_returns_int_and_is_idempotent(pool):
    pool.warm(2)
    first = pool.destroy_all()
    assert isinstance(first, int)
    again = pool.destroy_all()
    assert isinstance(again, int)
    assert again == 0
