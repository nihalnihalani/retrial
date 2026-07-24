"""Server endpoint tests (FastAPI TestClient, everything mocked): seed scope
guard, run serialization, /health shape, and the SEAM-3 promote gate — pending
-> approve emits promotion_closed then the (stubbed) PRSmith runs; reject never
touches PRSmith; and the stale-promotion regression proving _accept_run wipes a
pending promotion for EVERY run type, not just /tournament."""
import threading
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


class _FakeServerPool:
    def ensure_warm(self, target):
        return target

    def resize_to(self, target):
        return target

    def stats(self):
        return {"available": 2, "live": 3}

    def destroy_all(self):
        return 0


class _RecordingPRSmith:
    """Stands in for server.PRSmith: records open_pr calls, emits pr_opened."""

    calls = []

    def __init__(self, repo=None, base="main", bus=None):
        self.bus = bus

    def open_pr(self, result, test_name):
        _RecordingPRSmith.calls.append((result.get("verdict"), test_name))
        if self.bus is not None:
            self.bus.emit("pr_opened", {"url": "https://example.test/pr/1",
                                        "verdict": result.get("verdict")})
        return "https://example.test/pr/1"


def _wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


@pytest.fixture()
def server(monkeypatch):
    from retrial import server as server_mod

    fake_pool = _FakeServerPool()
    monkeypatch.setattr(server_mod, "_get_pool", lambda: fake_pool)
    monkeypatch.setattr(server_mod, "_get_hpool", lambda: fake_pool)
    monkeypatch.setattr(server_mod, "PRSMITH", False)
    monkeypatch.setattr(server_mod, "PRSmith", _RecordingPRSmith)
    _RecordingPRSmith.calls = []
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with server_mod._run_lock:
        server_mod._running.update(active=False, test_name=None)
        server_mod._pending["promotion"] = None
    # No `with`: the lifespan (which pre-warms a real pool) must not run.
    return server_mod, TestClient(server_mod.app)


class _StubCoordinator:
    """run_tournament stub; the class attr scripts the returned result."""

    result = {"verdict": "QUARANTINE"}

    def __init__(self, *a, **k):
        pass

    def run_tournament(self, *a, **k):
        return dict(_StubCoordinator.result)


FIXED_RESULT = {
    "verdict": "FIXED",
    "orig_flake_rate": 0.44,
    "winner": {"id": "h2", "cause_class": "race_condition",
               "patched_code": "import sys\nassert 1 == 1\nsys.exit(0)\n"},
    "confirmation": {"flake_rate": 0.0, "wilson_ci": [0.0, 0.08], "trials": 40},
    "braintrust": {"detect": "https://bt.test/detect", "h2": "https://bt.test/h2"},
}


def _run_fixed_tournament(server_mod, client, monkeypatch, open_pr=True):
    """Drive a stubbed tournament to completion; returns the response."""
    _StubCoordinator.result = FIXED_RESULT
    monkeypatch.setattr(server_mod, "TournamentCoordinator", _StubCoordinator)
    r = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py",
                                         "open_pr": open_pr})
    assert r.status_code == 200
    assert _wait_until(lambda: not server_mod._running["active"])
    return r


# ----------------------------- basic gates -----------------------------
def test_health_shape_includes_pool_backend(server):
    server_mod, client = server
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert {"available", "live", "prewarming"} <= set(body["pool"])
    assert "pool_backend" in body["config"]
    assert body["config"]["pool_backend"] in ("snapshot", "fork")
    assert "promote_gate" in body["config"]


def test_tournament_seed_scope_guard(server):
    server_mod, client = server
    r = client.post("/tournament", json={"seed_path": "../../.env"})
    assert r.status_code == 400
    assert "seeds" in r.json()["detail"]
    r = client.post("/tournament", json={"seed_path": "seeds/no_such_seed.py"})
    assert r.status_code == 404


def test_tournament_409_while_running(server, monkeypatch):
    server_mod, client = server
    gate = threading.Event()
    running = threading.Event()

    class Blocking:
        def __init__(self, *a, **k):
            pass

        def run_tournament(self, *a, **k):
            running.set()
            gate.wait(timeout=5)
            return {"verdict": "QUARANTINE"}

    monkeypatch.setattr(server_mod, "TournamentCoordinator", Blocking)
    try:
        r = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py"})
        assert r.status_code == 200
        assert running.wait(timeout=5)
        r2 = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py"})
        assert r2.status_code == 409
    finally:
        gate.set()
    assert _wait_until(lambda: not server_mod._running["active"])
    time.sleep(0.3)  # let the bg pool-reset thread hit the still-patched fake


# ----------------------------- promote gate -----------------------------
def test_promote_404_when_nothing_pending(server):
    server_mod, client = server
    r = client.post("/promote", json={"approve": True})
    assert r.status_code == 404


def test_gated_run_parks_promotion_and_emits_pending(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setattr(server_mod, "PROMOTE_GATE", True)
    _run_fixed_tournament(server_mod, client, monkeypatch)

    with server_mod._run_lock:
        pending = server_mod._pending["promotion"]
    assert pending is not None
    assert pending["test_name"] == "test_dict_order.py"
    assert _RecordingPRSmith.calls == []              # gated: NO auto-PR
    events = {e["type"]: e["payload"] for e in server_mod.BUS.history()}
    assert "promotion_pending" in events
    p = events["promotion_pending"]
    assert p["test_name"] == "test_dict_order.py"
    assert p["verdict"] == "FIXED"
    assert p["winner_id"] == "h2"
    assert p["flake_rate"] == 0.44
    assert p["confirm_flake_rate"] == 0.0
    assert p["braintrust_url"] == "https://bt.test/h2"


def test_promote_approve_emits_closed_then_ships(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setattr(server_mod, "PROMOTE_GATE", True)
    _run_fixed_tournament(server_mod, client, monkeypatch)

    r = client.post("/promote", json={"approve": True})
    assert r.status_code == 200
    assert r.json() == {"status": "approved"}
    assert _wait_until(lambda: len(_RecordingPRSmith.calls) == 1)
    assert _RecordingPRSmith.calls == [("FIXED", "test_dict_order.py")]
    types = [e["type"] for e in server_mod.BUS.history()]
    # Honest-state ordering: promotion_closed before the (stubbed) pr_opened.
    assert types.index("promotion_closed") < types.index("pr_opened")
    closed = next(e["payload"] for e in server_mod.BUS.history()
                  if e["type"] == "promotion_closed")
    assert closed == {"approved": True, "test_name": "test_dict_order.py"}
    # The pending slot is consumed: a second approval finds nothing.
    r2 = client.post("/promote", json={"approve": True})
    assert r2.status_code == 404


def test_promote_reject_never_calls_prsmith(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setattr(server_mod, "PROMOTE_GATE", True)
    _run_fixed_tournament(server_mod, client, monkeypatch)

    r = client.post("/promote", json={"approve": False})
    assert r.status_code == 200
    assert r.json() == {"status": "rejected"}
    time.sleep(0.2)
    assert _RecordingPRSmith.calls == []
    types = [e["type"] for e in server_mod.BUS.history()]
    assert "promotion_closed" in types
    assert "pr_opened" not in types
    closed = next(e["payload"] for e in server_mod.BUS.history()
                  if e["type"] == "promotion_closed")
    assert closed["approved"] is False


def test_gate_off_restores_auto_pr(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setattr(server_mod, "PROMOTE_GATE", False)
    _run_fixed_tournament(server_mod, client, monkeypatch)
    assert _wait_until(lambda: len(_RecordingPRSmith.calls) == 1)
    with server_mod._run_lock:
        assert server_mod._pending["promotion"] is None
    types = [e["type"] for e in server_mod.BUS.history()]
    assert "promotion_pending" not in types


# ------------------- stale-promotion regression (the bug class) -------------------
def test_accept_run_wipes_pending_promotion_for_every_run_type(server, monkeypatch):
    """A promotion left unclicked by a finished tournament must not survive
    into a subsequent /bisect run and feed /promote stale result data. This is
    the same bug class as the ring-buffer stale bleed: it only stays fixed
    because acceptance is centralized in _accept_run."""
    server_mod, client = server
    monkeypatch.setattr(server_mod, "PROMOTE_GATE", True)
    _run_fixed_tournament(server_mod, client, monkeypatch)
    with server_mod._run_lock:
        assert server_mod._pending["promotion"] is not None  # left unclicked

    # Now start a bisect run (backend gate satisfied, bisector stubbed).
    monkeypatch.setenv("RETRIAL_POOL_BACKEND", "fork")

    class StubBisector:
        def __init__(self, *a, **k):
            pass

        def run(self, suite, suspect_index=None, suite_name=""):
            return {"polluter_test": None}

    monkeypatch.setattr(server_mod, "FlakeBisector", StubBisector)
    r = client.post("/bisect", json={"suite_dir": "seeds/suites/order_pollution"})
    assert r.status_code == 200
    assert _wait_until(lambda: not server_mod._running["active"])

    with server_mod._run_lock:
        assert server_mod._pending["promotion"] is None      # wiped at acceptance
    r2 = client.post("/promote", json={"approve": True})
    assert r2.status_code == 404                             # nothing stale served
    assert _RecordingPRSmith.calls == []


# ============================ Sandbox Observatory ============================
from conftest import FakeChild                              # noqa: E402
from retrial.registry import SandboxRegistry                # noqa: E402


@pytest.fixture()
def obs_server(server, monkeypatch):
    """The `server` fixture + a fresh registry attached to the test bus, so the
    Observatory endpoints read a clean, isolated ledger (no cross-test bleed)."""
    server_mod, client = server
    reg = SandboxRegistry()
    reg.attach_bus(server_mod.BUS)
    monkeypatch.setattr(server_mod, "REGISTRY", reg)
    # Start every observatory test from a quiescent, un-reaping, no-pool state.
    monkeypatch.setattr(server_mod, "_POOL", None)
    monkeypatch.setattr(server_mod, "_HPOOL", None)
    with server_mod._run_lock:
        server_mod._reaping["now"] = False
        server_mod._active["bisector"] = None
    return server_mod, client, reg


class _SpyPool:
    def __init__(self):
        self.destroyed = 0

    def destroy_all(self):
        self.destroyed += 1
        return 3


def test_get_sandboxes_shape(obs_server):
    server_mod, client, reg = obs_server
    reg.register(FakeChild("sb-1"), role="snapshot-pool", backend="snapshot",
                 state="warm")
    body = client.get("/sandboxes").json()
    assert {"sandboxes", "counts", "lineage", "est_resources"} <= set(body)
    for k in ("live", "total_ever", "destroyed"):
        assert isinstance(body["counts"][k], int)
    assert body["est_resources"]["live_sandboxes"] == body["counts"]["live"]


def test_get_sandbox_detail_404_and_history_and_preview(obs_server):
    server_mod, client, reg = obs_server
    assert client.get("/sandboxes/ghost").status_code == 404
    reg.register(FakeChild("sb-1"), role="root", backend="fork", state="warm")
    reg.exec_started("sb-1", "run")
    reg.exec_finished("sb-1", "run", 0, "ok", 0.1)
    body = client.get("/sandboxes/sb-1").json()
    assert body["id"] == "sb-1"
    assert len(body["recent_execs"]) == 1
    # Preview link resolved from the fake handle (warm sandbox).
    assert body["preview_url"] == "https://preview.fake/sb-1:8080"


def test_delete_sandbox_404_409_and_owner_route(obs_server):
    server_mod, client, reg = obs_server
    assert client.delete("/sandboxes/ghost").status_code == 404

    # 409 when the record has live fork-children.
    reg.register(FakeChild("root"), role="root", backend="fork", state="warm")
    reg.register(FakeChild("clone"), role="trial-clone", backend="fork",
                 parent_id="root", state="warm")
    assert client.delete("/sandboxes/root").status_code == 409

    # 200 routes through the owner's destroy_fn (which marks it destroyed).
    evicted = []
    reg.register(FakeChild("leaf"), role="snapshot-pool", backend="snapshot",
                 state="warm",
                 destroy_fn=lambda sid: (evicted.append(sid),
                                         reg.mark_destroyed(sid)))
    r = client.delete("/sandboxes/leaf")
    assert r.status_code == 200 and r.json()["status"] == "destroying"
    assert evicted == ["leaf"]
    assert reg.record("leaf")["state"] == "destroyed"

    # A now-destroyed sandbox is a 404.
    assert client.delete("/sandboxes/leaf").status_code == 404


def test_destroy_all_409_while_running_unless_forced(obs_server, monkeypatch):
    server_mod, client, reg = obs_server
    spy = _SpyPool()
    monkeypatch.setattr(server_mod, "_POOL", spy)

    with server_mod._run_lock:
        server_mod._running["active"] = True
    try:
        assert client.post("/sandboxes/destroy_all").status_code == 409
        r = client.post("/sandboxes/destroy_all?force=1")
        assert r.status_code == 200
        assert r.json()["forced"] is True
        assert spy.destroyed == 1                       # stubbed pool torn down
    finally:
        with server_mod._run_lock:
            server_mod._running["active"] = False
    # Idle: 200 without force.
    monkeypatch.setattr(server_mod, "_POOL", _SpyPool())
    assert client.post("/sandboxes/destroy_all").status_code == 200


def test_force_cancels_active_bisector_not_yanked(obs_server, monkeypatch):
    server_mod, client, reg = obs_server

    class SpyBisector:
        def __init__(self):
            self.cancelled = 0
            self.destroyed = 0

        def cancel(self):
            self.cancelled += 1

        def destroy_all(self):
            self.destroyed += 1
            return 0

    bis = SpyBisector()
    reap_orphans_calls = []
    monkeypatch.setattr(reg, "reap_orphans",
                        lambda: reap_orphans_calls.append(1))
    spy_pool = _SpyPool()
    monkeypatch.setattr(server_mod, "_POOL", spy_pool)
    with server_mod._run_lock:
        server_mod._running["active"] = True
        server_mod._active["bisector"] = bis
    try:
        r = client.post("/sandboxes/destroy_all?force=1")
        assert r.status_code == 200
        body = r.json()
        assert body["bisector_cancelled"] is True
        assert bis.cancelled == 1
        assert bis.destroyed == 0                    # server never destroy_all'd it
        assert reap_orphans_calls == []              # skip_orphans while cancelling
        # Module pool refs reset to None so the NEXT run builds a fresh pool.
        assert server_mod._POOL is None
    finally:
        with server_mod._run_lock:
            server_mod._running["active"] = False
            server_mod._active["bisector"] = None


def test_accept_reap_toctou_sentinel_blocks_new_run(obs_server, monkeypatch):
    server_mod, client, reg = obs_server
    with server_mod._run_lock:
        server_mod._reaping["now"] = True
    try:
        r = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py"})
        assert r.status_code == 409
        assert "reap in progress" in r.json()["detail"]
    finally:
        with server_mod._run_lock:
            server_mod._reaping["now"] = False


def test_destroy_all_clears_sentinel_even_when_reap_raises(obs_server, monkeypatch):
    server_mod, client, reg = obs_server

    def boom(*a, **k):
        raise RuntimeError("reap exploded")

    monkeypatch.setattr(server_mod, "_reap_everything", boom)
    with pytest.raises(RuntimeError):
        client.post("/sandboxes/destroy_all")
    assert server_mod._reaping["now"] is False       # finally cleared it


def test_reap_everything_is_idempotent(obs_server, monkeypatch):
    server_mod, client, reg = obs_server
    spy = _SpyPool()
    monkeypatch.setattr(server_mod, "_POOL", spy)
    first = server_mod._reap_everything()
    assert first == 3                                # the stub pool's 3
    second = server_mod._reap_everything()           # pools already None
    assert second == 0                               # nothing extra destroyed


def test_snapshot_survives_acceptance_with_totals_intact(obs_server, monkeypatch):
    server_mod, client, reg = obs_server
    # A live sandbox that predates the run — it must survive acceptance.
    reg.register(FakeChild("pre-1"), role="snapshot-pool", backend="snapshot",
                 state="warm")
    total_before = reg.counts()["total_ever"]
    assert total_before >= 1

    class StubCoordinator:
        def __init__(self, *a, **k):
            pass

        def run_tournament(self, *a, **k):
            return {"verdict": "QUARANTINE"}

    monkeypatch.setattr(server_mod, "TournamentCoordinator", StubCoordinator)
    r = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py"})
    assert r.status_code == 200
    assert _wait_until(lambda: not server_mod._running["active"])

    hist = server_mod.BUS.history()
    # The fresh buffer opens with the re-seeded snapshot (registry NOT wiped).
    assert hist[0]["type"] == "registry_snapshot"
    snap = hist[0]["payload"]
    assert snap["counts"]["total_ever"] >= total_before   # pre-run sandbox intact
    # And it precedes every run-thread event by seq (inside the accept lock).
    later = [e for e in hist if e["type"] != "registry_snapshot"]
    if later:
        assert hist[0]["seq"] < min(e["seq"] for e in later)
    time.sleep(0.3)  # let the bg pool-reset thread settle before unwind
