"""SandboxPool tests with the injected FakeClient — lease/release semantics,
resize arithmetic, stats shape, and background-destroy behavior. No Daytona."""
import time

from conftest import FakeClient

from retrial.pool import SandboxPool


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
