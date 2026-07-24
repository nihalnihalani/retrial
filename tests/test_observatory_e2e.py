"""End-to-end binding proof that the REAL registry singleton is wired.

Every other suite deliberately injects a private registry or monkeypatches
server_mod.REGISTRY, so none of them would catch a regression where the pool
the server runs and the registry the server reads diverge (e.g. an accidental
`registry=SandboxRegistry()` at a construction site). This file closes that
hole: NO registry monkeypatching anywhere. Fakes are injected ONLY at the
Daytona-client boundary (make_pool builds a real SandboxPool over a FakeClient),
so the entire coordinator -> _get_pool() -> pool -> trial.py -> registry-hook
chain runs real production code into the server's own process-default REGISTRY,
which the /sandboxes endpoint then reads. Assertions are DELTAS (the process
REGISTRY is shared across the session and must not be reset)."""
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import FakeClient  # noqa: E402


def _wait_until(cond, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture()
def e2e(monkeypatch):
    from retrial import server as server_mod
    from retrial.pool import SandboxPool

    # Inject the fake ONLY at the client boundary: a REAL SandboxPool (real
    # warm/lease/_create_one/_destroy -> real registry hooks) over a FakeClient.
    # No registry argument -> the process-default REGISTRY, exactly what the
    # endpoint reads. This is the whole point of the test.
    def fake_make_pool(bus=None, registry=None, **kwargs):
        return SandboxPool(client=FakeClient(), auto_delete_min=0)

    monkeypatch.setattr(server_mod, "make_pool", fake_make_pool)
    monkeypatch.setattr(server_mod, "_POOL", None)
    monkeypatch.setattr(server_mod, "_HPOOL", None)
    monkeypatch.setattr(server_mod, "PRSMITH", False)
    monkeypatch.setattr(server_mod, "HERMETIC", False)
    monkeypatch.setattr(server_mod, "MAX_TRIALS", 4)
    monkeypatch.setattr(server_mod, "CONC", 2)
    monkeypatch.setattr(server_mod, "PREWARM", 0)
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with server_mod._run_lock:
        server_mod._running.update(active=False, test_name=None)
        server_mod._reaping["now"] = False
        server_mod._active["bisector"] = None
    return server_mod, TestClient(server_mod.app)


def test_real_pool_trial_chain_feeds_the_endpoints_singleton(e2e):
    server_mod, client = e2e
    before = server_mod.REGISTRY.counts()["total_ever"]

    # Drive a real tournament (no hypotheses -> detect/quarantine path) to
    # completion through the real coordinator/pool/trial stack.
    r = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py"})
    assert r.status_code == 200
    assert _wait_until(lambda: not server_mod._running["active"])

    body = client.get("/sandboxes").json()
    after = body["counts"]["total_ever"]
    # total_ever increased — records the test never inserted (delta, not absolute).
    assert after > before

    # At least one record arrived via the REAL pool/trial hooks: a snapshot-pool
    # (or trial-clone) sandbox that actually executed a trial. It could only have
    # gotten there through the same singleton the endpoint reads.
    hooked = [s for s in body["sandboxes"]
              if s["role"] in ("snapshot-pool", "trial-clone")
              and s["exec_count"] > 0]
    assert hooked, "no sandbox with exec_count>0 — the real chain is not wired"
