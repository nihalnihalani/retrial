"""SandboxPool tests with the injected FakeClient — lease/release semantics,
resize arithmetic, stats shape, and background-destroy behavior. No Daytona.

Plus the Sandbox Observatory wiring: an injected private registry sees
snapshot-pool records go warm then destroyed, _evict routes DELETE through the
owner, and a RaisingRegistry can never break the pool (never-break-a-run)."""
import time

import pytest

from conftest import FakeClient, RaisingRegistry

from retrial.pool import SandboxPool
from retrial.registry import SandboxRegistry


def _make_pool():
    return SandboxPool(client=FakeClient(), auto_delete_min=0)


def _wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def test_warm_precreates_and_pays_cold_start():
    pool = _make_pool()
    assert pool.warm(3) == 3
    assert pool.stats() == {"available": 3, "live": 3}
    # Each warm sandbox got its cold-start exec paid up front.
    for sb in pool._available:
        assert ("echo warm", None) in sb.process.execs


def test_lease_pops_warm_else_creates():
    pool = _make_pool()
    pool.warm(1)
    client = pool._client
    creates_before = client.create_calls
    warm_sb = pool.lease()
    assert client.create_calls == creates_before      # popped, not created
    fresh_sb = pool.lease()
    assert client.create_calls == creates_before + 1  # empty pool -> on-demand
    assert warm_sb.id != fresh_sb.id


def test_release_reusable_returns_to_pool():
    pool = _make_pool()
    pool.warm(1)
    sb = pool.lease()
    pool.release(sb, reusable=True)
    assert pool.stats() == {"available": 1, "live": 1}
    assert pool.lease() is sb


def test_release_not_reusable_destroys_in_background():
    pool = _make_pool()
    sb = pool.lease()
    pool.release(sb, reusable=False)
    client = pool._client
    assert _wait_until(lambda: sb.id in client.deleted)
    assert _wait_until(lambda: pool.stats()["live"] == 0)


def test_release_none_is_a_noop():
    pool = _make_pool()
    pool.release(None)                                # must not raise


def test_resize_to_grows_and_trims():
    pool = _make_pool()
    assert pool.resize_to(3) == 3
    assert pool.stats()["available"] == 3
    assert pool.resize_to(1) == 1
    client = pool._client
    assert _wait_until(lambda: len(client.deleted) == 2)
    assert _wait_until(lambda: pool.stats() == {"available": 1, "live": 1})


def test_ensure_warm_tops_up_but_never_trims():
    pool = _make_pool()
    pool.warm(3)
    assert pool.ensure_warm(1) == 3                   # never trims
    assert pool.ensure_warm(5) == 5                   # tops up the deficit


def test_destroy_all_and_idempotent():
    pool = _make_pool()
    pool.warm(2)
    leased = pool.lease()      # pops a warm one; still owned by the pool
    assert pool.destroy_all() == 2
    client = pool._client
    assert len(client.deleted) == 2
    assert leased.id in client.deleted                # leased sandboxes die too
    assert pool.stats() == {"available": 0, "live": 0}
    assert pool.destroy_all() == 0                    # idempotent


# ----------------------- Sandbox Observatory wiring -----------------------
def _wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def test_registry_records_snapshot_pool_sandboxes_warm():
    reg = SandboxRegistry()
    pool = SandboxPool(client=FakeClient(), auto_delete_min=0, registry=reg)
    pool.warm(2)
    snap = reg.snapshot()
    assert reg.counts() == {"live": 2, "total_ever": 2, "destroyed": 0}
    assert all(s["role"] == "snapshot-pool" and s["backend"] == "snapshot"
               and s["state"] == "warm" for s in snap["sandboxes"])


def test_registry_marks_destroyed_on_non_reusable_release():
    reg = SandboxRegistry()
    pool = SandboxPool(client=FakeClient(), auto_delete_min=0, registry=reg)
    pool.warm(1)
    sb = pool.lease()
    pool.release(sb, reusable=False)          # background destroy
    assert _wait_until(lambda: reg.counts()["destroyed"] == 1)
    assert reg.record(sb.id)["state"] == "destroyed"


def test_evict_removes_from_available_and_destroys():
    reg = SandboxRegistry()
    pool = SandboxPool(client=FakeClient(), auto_delete_min=0, registry=reg)
    pool.warm(2)
    sid = pool._available[0].id
    pool._evict(sid)                          # the DELETE /sandboxes/{id} path
    assert sid not in [s.id for s in pool._available]
    assert sid in pool._client.deleted
    assert reg.record(sid)["state"] == "destroyed"


def test_raising_registry_never_breaks_the_pool():
    pool = SandboxPool(client=FakeClient(), auto_delete_min=0,
                       registry=RaisingRegistry())
    assert pool.warm(2) == 2                  # register/set_state exploded: no-op
    sb = pool.lease()
    pool.release(sb, reusable=True)
    assert pool.stats() == {"available": 2, "live": 2}
    assert pool.destroy_all() == 2            # mark_destroyed exploded: still fine
