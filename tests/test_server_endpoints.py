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
