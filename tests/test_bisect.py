"""FlakeBisector tests — Daytona SDK fully mocked (see conftest).

Covers the SEAM-2 contract: binary-search correctness against canned probes,
the no-flip sanity gate, probe invariants (checkpoint always re-frozen, probe
forks never leaked), leaf-first teardown order, event registration, the server
/bisect gates + shared run acceptance, seed-suite compilation, the fork-issuance
serialization stress test (the load-bearing concurrency rule), and the
full-budget confirmation pass with its honest-inconclusive contradiction path.
"""
import py_compile
import threading
import time
from collections import Counter

import pytest

from conftest import ROOT, FakeChild, FakeCheckpointSandbox, FakeClient

from retrial import bisect as bisect_mod
from retrial.bisect import FlakeBisector, _CheckpointProbePool
from retrial.events import EVENT_TYPES, EventBus


@pytest.fixture(autouse=True)
def _stub_sdk(monkeypatch):
    """Belt-and-braces: no test may construct a real Daytona client."""
    monkeypatch.setattr(bisect_mod, "Daytona", lambda *a, **k: object())
    monkeypatch.setattr(bisect_mod, "DaytonaConfig", lambda *a, **k: object())


SUITE = [
    ("test_00_smoke.py", "import sys; sys.exit(0)"),
    ("test_01_math.py", "import sys; sys.exit(0)"),
    ("test_02_strings.py", "import sys; sys.exit(0)"),
    ("test_03_cache_writer.py", "import sys; sys.exit(0)"),
    ("test_04_parse.py", "import sys; sys.exit(0)"),
    ("test_05_suspect.py", "import sys; sys.exit(0)"),
]

_BASE = {"trials": 20, "fails": 0, "errors": 0, "flake_rate": 0.0,
         "wilson_ci": [0.0, 0.05], "verdict": "STABLE"}
_FLIP = {"trials": 20, "fails": 10, "errors": 0, "flake_rate": 0.5,
         "wilson_ci": [0.3, 0.7], "verdict": "FLAKY"}


def _canned_probe(flip_at):
    """A fake FlakeBisector._probe: base below flip_at, flipped at/after it."""

    def probe(self, k, min_trials=None, max_trials=None, use_cache=True):
        return dict(_FLIP if k >= flip_at else _BASE)

    return probe


def _wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# ------------------- test 1: search correctness (pure) -------------------
def test_search_finds_polluter(monkeypatch):
    monkeypatch.setattr(FlakeBisector, "_probe", _canned_probe(flip_at=4))
    bus = EventBus()
    b = FlakeBisector(client=FakeClient(), bus=bus)
    result = b.run(SUITE, suite_name="order_pollution")

    assert result["polluter_index"] == 3
    assert result["polluter_test"] == "test_03_cache_writer.py"
    assert result["suspect"] == "test_05_suspect.py"
    assert result["checkpoints"] == 6
    assert result["confirmed"] is True

    history = bus.history()
    types = [e["type"] for e in history]
    assert types[0] == "bisect_started"
    started = history[0]["payload"]
    assert started == {"suite": "order_pollution", "n_tests": 5,
                       "suspect": "test_05_suspect.py",
                       "max_trials": b.max_trials}
    # One checkpoint per boundary: pristine + 5 prefix tests.
    created = [e["payload"] for e in history if e["type"] == "checkpoint_created"]
    assert [c["k"] for c in created] == [0, 1, 2, 3, 4, 5]
    assert created[0]["label"] == "pristine"
    assert created[4]["label"] == "test_03_cache_writer.py"
    # The search trace: lo/hi window narrows monotonically, snake_case payloads.
    narrowed = [e["payload"] for e in history if e["type"] == "bisect_narrowed"]
    assert [(n["k"], n["flipped"], n["lo"], n["hi"]) for n in narrowed] == [
        (2, False, 2, 5), (3, False, 3, 5), (4, True, 3, 4)]
    assert types[-1] == "bisect_done"
    assert history[-1]["payload"]["polluter_test"] == "test_03_cache_writer.py"


# ------------------- test 2: no-flip sanity gate -------------------
def test_no_flip_reports_honest_inconclusive(monkeypatch):
    monkeypatch.setattr(FlakeBisector, "_probe", _canned_probe(flip_at=999))
    bus = EventBus()
    b = FlakeBisector(client=FakeClient(), bus=bus)
    result = b.run(SUITE, suite_name="order_pollution")

    assert result["polluter_test"] is None
    assert result["reason"] == "flake rate does not flip across the suite"
    types = [e["type"] for e in bus.history()]
    assert "bisect_narrowed" not in types          # never searched
    assert types[-1] == "bisect_done"


# ------------------- test 3: probe invariants -------------------
def _bisector_with_ckpt(bus=None):
    """A bisector wired to fakes with root + checkpoint 0 already frozen."""
    client = FakeClient()
    b = FlakeBisector(client=client, bus=bus, max_trials=10, conc=4)
    b._suspect_code = "import sys; sys.exit(0)"
    b._create_root()
    b._checkpoint("pristine")
    return b, client


def test_probe_wakes_measures_and_refreezes(monkeypatch):
    b, client = _bisector_with_ckpt(bus=EventBus())
    ckpt = client.checkpoints[0]

    def fake_verify(pool, code, **kw):
        sbs = [pool.lease() for _ in range(3)]
        for sb in sbs:
            pool.release(sb, reusable=False)
        return {"trials": 3, "fails": 0, "errors": 0, "flake_rate": 0.0,
                "wilson_ci": [0.0, 0.2], "verdict": "STABLE",
                "stopped_early": False, "isolation": "sandbox", "history": []}

    monkeypatch.setattr(bisect_mod, "verify", fake_verify)
    assert ckpt.pause_calls == 1                  # frozen at creation
    res = b._probe(0)
    assert ckpt.start_calls == 1                  # woken for the probe
    assert ckpt.pause_calls == 2                  # ALWAYS re-frozen after it
    # Every probe fork deleted, none leaked live.
    assert len(ckpt.children) == 3
    assert all(c.id in client.deleted for c in ckpt.children)
    assert b._probes[0] is res
    # The probe emitted the FlakeMeter-shaped event.
    probed = [e for e in b._bus.history() if e["type"] == "checkpoint_probed"]
    assert len(probed) == 1
    assert probed[0]["payload"] == {"k": 0, "flake_rate": 0.0,
                                    "wilson_ci": [0.0, 0.2], "trials": 3,
                                    "verdict": "STABLE"}
    # Cache: a second probe of the same checkpoint is free.
    assert b._probe(0) is res
    assert ckpt.start_calls == 1


def test_probe_refreezes_and_sweeps_even_when_verify_raises(monkeypatch):
    b, client = _bisector_with_ckpt()
    ckpt = client.checkpoints[0]
    leaked = []

    def bad_verify(pool, code, **kw):
        leaked.append(pool.lease())               # a fork in flight at the crash
        raise RuntimeError("probe blew up mid-verify")

    monkeypatch.setattr(bisect_mod, "verify", bad_verify)
    with pytest.raises(RuntimeError):
        b._probe(0)
    assert ckpt.pause_calls == 2                  # re-frozen despite the crash
    assert leaked[0].id in client.deleted         # leftover fork swept
    assert 0 not in b._probes                     # no cached lie


# ------------------- test 4: leaf-first teardown order -------------------
def test_teardown_order_probes_then_checkpoints_then_root(monkeypatch):
    client = FakeClient()

    def fake_verify(pool, code, **kw):
        for sb in [pool.lease(), pool.lease()]:
            pool.release(sb, reusable=False)
        k = client.checkpoints.index(pool._ckpt)
        return dict(_FLIP if k >= 1 else _BASE)

    monkeypatch.setattr(bisect_mod, "verify", fake_verify)
    b = FlakeBisector(client=client)
    result = b.run(SUITE[:3], suite_name="mini")   # prefix of 2, suspect last
    assert result["polluter_index"] == 0

    order = client.deleted
    root_id = client.roots[0].id
    ckpt_ids = [c.id for c in client.checkpoints]
    probe_ids = [ch.id for c in client.checkpoints for ch in c.children]
    assert order[-1] == root_id                                   # root dies last
    last_probe = max(order.index(pid) for pid in probe_ids)
    first_ckpt = min(order.index(cid) for cid in ckpt_ids)
    assert last_probe < first_ckpt < order.index(root_id)         # leaf-first
    # Idempotent: everything is already gone.
    assert b.destroy_all() == 0


# ------------------- test 5: events registered -------------------
def test_new_event_types_registered():
    for name in ("bisect_started", "checkpoint_created", "checkpoint_probed",
                 "bisect_narrowed", "bisect_done"):
        assert name in EVENT_TYPES


# ------------------- test 6: server gates -------------------
class _FakeServerPool:
    def ensure_warm(self, target):
        return target

    def resize_to(self, target):
        return target

    def stats(self):
        return {"available": 0, "live": 0}

    def destroy_all(self):
        return 0


@pytest.fixture()
def server(monkeypatch):
    from fastapi.testclient import TestClient
    from retrial import server as server_mod

    fake_pool = _FakeServerPool()
    monkeypatch.setattr(server_mod, "_get_pool", lambda: fake_pool)
    monkeypatch.setattr(server_mod, "_get_hpool", lambda: fake_pool)
    monkeypatch.setattr(server_mod, "PRSMITH", False)  # never shell out to gh
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with server_mod._run_lock:
        server_mod._running.update(active=False, test_name=None)
    # No `with`: the lifespan (which pre-warms a real pool) must not run.
    return server_mod, TestClient(server_mod.app)


def _counting_accept(server_mod, monkeypatch):
    accepts = []
    real = server_mod._accept_run

    def counted(name):
        accepts.append(name)
        return real(name)

    monkeypatch.setattr(server_mod, "_accept_run", counted)
    return accepts


def test_bisect_requires_fork_backend(server, monkeypatch):
    server_mod, client = server
    monkeypatch.delenv("RETRIAL_POOL_BACKEND", raising=False)
    r = client.post("/bisect", json={"suite_dir": "seeds/suites/order_pollution"})
    assert r.status_code == 400
    assert "fork backend" in r.json()["detail"]


def test_bisect_scope_guard_and_missing_suite(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setenv("RETRIAL_POOL_BACKEND", "fork")
    r = client.post("/bisect", json={"suite_dir": "../.."})
    assert r.status_code == 400
    assert "seeds" in r.json()["detail"]
    r = client.post("/bisect", json={"suite_dir": "seeds/suites/no_such_suite"})
    assert r.status_code == 404
    r = client.post("/bisect", json={"suite_dir": "seeds/suites/order_pollution",
                                     "suspect": "test_99_ghost.py"})
    assert r.status_code == 404


def test_bisect_starts_409s_and_routes_through_accept_run(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setenv("RETRIAL_POOL_BACKEND", "fork")
    accepts = _counting_accept(server_mod, monkeypatch)
    gate = threading.Event()
    running = threading.Event()

    class StubBisector:
        def __init__(self, *a, **k):
            pass

        def run(self, suite, suspect_index=None, suite_name=""):
            running.set()
            gate.wait(timeout=5)
            return {"polluter_test": None}

    monkeypatch.setattr(server_mod, "FlakeBisector", StubBisector)
    try:
        r = client.post("/bisect", json={"suite_dir": "seeds/suites/order_pollution"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "started"
        assert body["suite"] == "order_pollution"
        assert body["n_tests"] == 5
        assert body["suspect"] == "test_05_suspect.py"
        assert running.wait(timeout=5)
        # Second POST while active: 409, and it never re-accepted the run.
        r2 = client.post("/bisect", json={"suite_dir": "seeds/suites/order_pollution"})
        assert r2.status_code == 409
    finally:
        gate.set()
    assert _wait_until(lambda: not server_mod._running["active"])
    assert accepts == ["order_pollution"]


def test_tournament_routes_through_accept_run(server, monkeypatch):
    server_mod, client = server
    accepts = _counting_accept(server_mod, monkeypatch)

    class StubCoordinator:
        def __init__(self, *a, **k):
            pass

        def run_tournament(self, *a, **k):
            return {"verdict": "QUARANTINE"}

    monkeypatch.setattr(server_mod, "TournamentCoordinator", StubCoordinator)
    r = client.post("/tournament", json={"seed_path": "seeds/test_dict_order.py"})
    assert r.status_code == 200
    assert _wait_until(lambda: not server_mod._running["active"])
    assert accepts == ["test_dict_order.py"]
    # Let the background pool-reset thread hit the still-patched fake pool
    # before monkeypatch unwinds (it starts after _running clears).
    time.sleep(0.3)


# ------------------- test 7: demo suite seeds compile -------------------
def test_order_pollution_seeds_compile():
    suite_dir = ROOT / "seeds" / "suites" / "order_pollution"
    files = sorted(suite_dir.glob("test_*.py"))
    assert [f.name for f in files] == [
        "test_00_smoke.py", "test_01_math.py", "test_02_strings.py",
        "test_03_cache_writer.py", "test_04_parse.py", "test_05_suspect.py"]
    for f in files:
        py_compile.compile(str(f), doraise=True)


# ------------- test 8: probe-pool fork serialization (fatal-risk) -------------
class SlowForkCheckpoint(FakeCheckpointSandbox):
    """~1ms inside the fork critical section, so UNSERIALIZED concurrent forks
    would overlap and drive max_in_flight above 1."""

    def _experimental_fork(self, name=None):
        with self._mutex:
            self.fork_calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self._auto_n += 1
            cid = f"{self.id}-clone-{self._auto_n}"
        try:
            time.sleep(0.001)
            child = FakeChild(cid)
            with self._mutex:
                self.children.append(child)
                self._registry[child.id] = child
            return child
        finally:
            with self._mutex:
                self.in_flight -= 1


def test_probe_pool_serializes_fork_issuance_under_thread_storm():
    client = FakeClient()
    ckpt = SlowForkCheckpoint(cid="ckpt-stress", registry=client.registry)
    pool = _CheckpointProbePool(ckpt, client)
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

    # The _fork_lock discipline actually serialized fork issuance.
    assert ckpt.max_in_flight == 1
    ids = {sb.id for sb in leased}
    assert len(ids) == n                          # 16 distinct sandboxes
    assert set(pool._live) == ids                 # no corruption/duplicates
    for sb in leased:
        pool.release(sb)
    pool.destroy_all()                            # joins bg deletes, sweeps rest
    assert pool._live == {}
    counts = Counter(client.deleted)
    assert set(counts) == ids
    assert all(v == 1 for v in counts.values())   # deleted exactly once each


# ------------------- test 9: confirmation pass -------------------
def test_confirmation_pass_uses_full_budget(monkeypatch):
    client = FakeClient()
    bus = EventBus()
    calls = []

    def fake_verify(pool, code, max_trials=None, conc=None, threshold=None,
                    min_trials=None, bus=None, timeout=None, isolation=None,
                    emit_trials=True, **kw):
        k = client.checkpoints.index(pool._ckpt)
        calls.append({"k": k, "min_trials": min_trials, "max_trials": max_trials,
                      "isolation": isolation, "emit_trials": emit_trials})
        return dict(_FLIP if k >= 4 else _BASE)

    monkeypatch.setattr(bisect_mod, "verify", fake_verify)
    b = FlakeBisector(client=client, bus=bus, max_trials=24, min_trials=8)
    result = b.run(SUITE, suite_name="order_pollution")

    assert result["polluter_index"] == 3
    # Search probes (cached, adaptive budget), then the confirmed pair.
    assert [c["k"] for c in calls] == [0, 5, 2, 3, 4, 3, 4]
    for c in calls[:5]:
        assert c["min_trials"] == 8 and c["max_trials"] == 24
    for c in calls[-2:]:
        # Full budget, early-stop defeated: min_trials == max_trials.
        assert c["min_trials"] == 24 and c["max_trials"] == 24
    for c in calls:
        assert c["isolation"] == "sandbox" and c["emit_trials"] is False
    probed = [e["payload"] for e in bus.history() if e["type"] == "checkpoint_probed"]
    assert len(probed) == 7                       # 5 search + 2 confirmation
    assert {"k", "flake_rate", "wilson_ci", "trials", "verdict"} <= set(probed[0])
    # The bisect_done probe table reflects the confirmed (full-budget) numbers.
    assert {p["k"] for p in result["probes"]} == {0, 2, 3, 4, 5}


def test_confirmation_contradiction_reports_inconclusive(monkeypatch):
    def probe(self, k, min_trials=None, max_trials=None, use_cache=True):
        if min_trials is not None:
            # Full-budget confirmation disagrees with the noisy search probes.
            return dict(_BASE)
        return dict(_FLIP if k >= 4 else _BASE)

    monkeypatch.setattr(FlakeBisector, "_probe", probe)
    bus = EventBus()
    b = FlakeBisector(client=FakeClient(), bus=bus)
    result = b.run(SUITE, suite_name="order_pollution")

    assert result["polluter_test"] is None
    assert "confirmation pass contradicts" in result["reason"]
    done = [e["payload"] for e in bus.history() if e["type"] == "bisect_done"]
    assert len(done) == 1
    assert done[0]["polluter_test"] is None


# ================= Sandbox Observatory wiring (SEAM-Phase-2) =================
from retrial.registry import SandboxRegistry  # noqa: E402


def test_probe_forks_are_bisect_probe_children_and_end_destroyed(monkeypatch):
    bus = EventBus()
    reg = SandboxRegistry(bus=bus)
    client = FakeClient()
    b = FlakeBisector(client=client, bus=bus, registry=reg, max_trials=10, conc=4)
    b._suspect_code = "import sys; sys.exit(0)"
    b._create_root()
    b._checkpoint("pristine")
    ckpt = client.checkpoints[0]

    def fake_verify(pool, code, **kw):
        sbs = [pool.lease() for _ in range(3)]
        for sb in sbs:
            pool.release(sb, reusable=False)
        return {"trials": 3, "fails": 0, "errors": 0, "flake_rate": 0.0,
                "wilson_ci": [0.0, 0.2], "verdict": "STABLE",
                "stopped_early": False, "isolation": "sandbox", "history": []}

    monkeypatch.setattr(bisect_mod, "verify", fake_verify)
    b._probe(0)

    probes = [s for s in reg.snapshot()["sandboxes"] if s["role"] == "bisect-probe"]
    assert len(probes) == 3
    assert all(p["parent_id"] == ckpt.id for p in probes)   # children of the ckpt
    assert all(p["state"] == "destroyed" for p in probes)   # single-use, reaped
    # The checkpoint itself is registered as a checkpoint, child of the root.
    ckpt_rec = reg.record(ckpt.id)
    assert ckpt_rec["role"] == "checkpoint"
    assert ckpt_rec["parent_id"] == client.roots[0].id


def test_exec_test_emits_one_sandbox_exec_with_parsed_exit_code():
    bus = EventBus()
    reg = SandboxRegistry(bus=bus)
    client = FakeClient()
    b = FlakeBisector(client=client, bus=bus, registry=reg)
    b._create_root()
    b._root.process.result = "some output\nEXIT:1\n"   # scripted non-zero exit

    res = b._exec_test(b._root, "import sys; sys.exit(1)")
    assert res["exit_code"] == 1

    exec_events = [e for e in bus.history() if e["type"] == "sandbox_exec"]
    assert len(exec_events) == 1                        # exactly one per exec
    assert exec_events[0]["payload"]["exit_code"] == 1
    assert exec_events[0]["payload"]["id"] == b._root.id


def test_cooperative_cancel_stops_before_next_probe_and_reaps_once(monkeypatch):
    client = FakeClient()
    bus = EventBus()
    b = FlakeBisector(client=client, bus=bus)

    # Cancel right after the pristine checkpoint (checkpoint 0) is built.
    orig_ckpt = b._checkpoint

    def hooked(label, test_passed=None):
        orig_ckpt(label, test_passed)
        if label == "pristine":
            b.cancel()

    monkeypatch.setattr(b, "_checkpoint", hooked)
    result = b.run(SUITE, suite_name="order_pollution")

    # Honest terminal error mentioning cancellation; no polluter guessed.
    assert result.get("polluter_test") is None
    assert "cancel" in str(result.get("error", "")).lower()
    # No probe ever started after the cancel point.
    types = [e["type"] for e in bus.history()]
    assert "checkpoint_probed" not in types
    assert "bisect_narrowed" not in types
    # destroy_all ran exactly once from the run thread's finally, leaf-first:
    # the single checkpoint before the root, and the root deleted exactly once.
    root_id = client.roots[0].id
    assert client.deleted.count(root_id) == 1
    ckpt_id = client.checkpoints[0].id
    assert client.deleted.index(ckpt_id) < client.deleted.index(root_id)
