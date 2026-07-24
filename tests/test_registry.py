"""SandboxRegistry unit tests — the Sandbox Observatory ledger.

Covers the B8 contract: the full lifecycle walk with a bounded exec ring and
exact never-decremented counters, mark_destroyed idempotency, lineage +
JSON-serializability, the typed events on a real EventBus (snake_case, B6
shapes), the @_safe never-break-a-run guarantee, the preview() caching rule
(positive caches forever, paused-negative retries, destroyed returns None), a
16-thread churn stress with a concurrent snapshot reader, leaf-first
reap_orphans, and the destroyed-record retention window with exact counters.
"""
import json
import threading

from conftest import FakeChild, FakePreviewLink

from retrial.events import EventBus
from retrial.registry import SandboxRegistry


# ----------------------- test 1: lifecycle walk -----------------------
def test_register_exec_destroy_walk_and_counters():
    reg = SandboxRegistry(exec_history=5, destroyed_retain=50)
    sb = FakeChild("sb-1")
    reg.register(sb, role="snapshot-pool", backend="snapshot",
                 labels={"retrial": "pool"}, state="creating")

    rec = reg.record("sb-1")
    assert rec["role"] == "snapshot-pool"
    assert rec["backend"] == "snapshot"
    assert rec["state"] == "creating"
    assert rec["parent_id"] is None
    assert rec["exec_count"] == 0
    assert rec["recent_execs"] == []
    assert rec["preview_url"] is None
    assert reg.counts() == {"live": 1, "total_ever": 1, "destroyed": 0}

    reg.exec_started("sb-1", "echo hi")
    rec = reg.record("sb-1")
    assert rec["state"] == "running-cmd"
    assert rec["current_cmd"] == "echo hi"

    reg.exec_finished("sb-1", "echo hi", 0, "hi", 0.01)
    rec = reg.record("sb-1")
    assert rec["state"] == "warm"
    assert rec["current_cmd"] is None
    assert rec["exec_count"] == 1
    assert len(rec["recent_execs"]) == 1
    assert rec["recent_execs"][0]["exit_code"] == 0

    # Ring bounded at exec_history=5, but exec_count keeps counting past it.
    for i in range(5 + 5):
        reg.exec_finished("sb-1", f"cmd {i}", i % 2, f"out {i}", 0.01)
    rec = reg.record("sb-1")
    assert len(rec["recent_execs"]) == 5           # ring capped
    assert rec["exec_count"] == 1 + 10             # count never capped

    reg.mark_destroyed("sb-1")
    assert reg.counts() == {"live": 0, "total_ever": 1, "destroyed": 1}
    # total_ever is NEVER decremented — a new registration only bumps it.
    reg.register(FakeChild("sb-2"), role="snapshot-pool", backend="snapshot")
    assert reg.counts()["total_ever"] == 2
    assert reg.counts()["destroyed"] == 1


# ----------------------- test 2: idempotent destroy -----------------------
def test_mark_destroyed_is_idempotent():
    reg = SandboxRegistry()
    reg.register(FakeChild("sb-1"), role="trial-clone", backend="fork")
    reg.mark_destroyed("sb-1")
    reg.mark_destroyed("sb-1")   # second call: no double count
    assert reg.counts() == {"live": 0, "total_ever": 1, "destroyed": 1}


# ----------------------- test 3: lineage + JSON -----------------------
def test_lineage_tree_and_snapshot_is_json_serializable():
    reg = SandboxRegistry()
    reg.register(FakeChild("root"), role="root", backend="fork")
    reg.register(FakeChild("ckpt"), role="checkpoint", backend="fork",
                 parent_id="root")
    reg.register(FakeChild("c1"), role="trial-clone", backend="fork",
                 parent_id="ckpt")
    reg.register(FakeChild("c2"), role="trial-clone", backend="fork",
                 parent_id="ckpt")

    snap = reg.snapshot()
    assert snap["lineage"]["root"] == ["ckpt"]
    assert set(snap["lineage"]["ckpt"]) == {"c1", "c2"}
    assert snap["counts"] == {"live": 4, "total_ever": 4, "destroyed": 0}
    # The whole snapshot must survive the /ws JSON round-trip verbatim.
    assert json.loads(json.dumps(snap)) == snap


# ----------------------- test 4: events on a real bus -----------------------
def test_events_emitted_in_order_with_b6_payloads():
    bus = EventBus()
    reg = SandboxRegistry(bus=bus)
    reg.register(FakeChild("sb-1"), role="trial-clone", backend="fork",
                 parent_id="ckpt", labels={"retrial": "fork-pool"})
    reg.exec_started("sb-1", "run")
    reg.exec_finished("sb-1", "run", 1, "x" * 500, 0.5)
    reg.mark_destroyed("sb-1")

    types = [e["type"] for e in bus.history()]
    assert types == ["sandbox_registered", "sandbox_state", "sandbox_exec",
                     "sandbox_destroyed"]
    payloads = {e["type"]: e["payload"] for e in bus.history()}

    reg_p = payloads["sandbox_registered"]
    assert set(reg_p) == {"id", "role", "backend", "state", "parent_id",
                          "created_ts", "labels", "isolation", "exec_count",
                          "preview_url"}
    assert reg_p["id"] == "sb-1" and reg_p["role"] == "trial-clone"

    st_p = payloads["sandbox_state"]
    assert st_p == {"id": "sb-1", "state": "running-cmd", "current_cmd": "run"}

    ex_p = payloads["sandbox_exec"]
    assert set(ex_p) == {"id", "cmd", "exit_code", "duration_s", "output_tail",
                         "exec_count"}
    assert ex_p["exit_code"] == 1 and ex_p["exec_count"] == 1
    assert len(ex_p["output_tail"]) == 200          # tail capped at 200

    assert payloads["sandbox_destroyed"] == {"id": "sb-1", "role": "trial-clone"}


# ----------------------- test 5: never-break-a-run -----------------------
def test_hooks_never_raise_even_with_a_broken_bus():
    class BoomBus:
        def emit(self, *a, **k):
            raise RuntimeError("bus exploded")

    reg = SandboxRegistry()
    reg.attach_bus(BoomBus())
    sb = FakeChild("sb-1")
    # Every hook must return None without propagating the bus explosion.
    assert reg.register(sb, role="root", backend="fork") is None
    assert reg.set_state("sb-1", "warm") is None
    assert reg.exec_started("sb-1", "c") is None
    assert reg.exec_finished("sb-1", "c", 0, "o", 0.1) is None
    assert reg.mark_degraded("sb-1") is None
    assert reg.mark_destroyed("sb-1") is None
    assert reg.emit_snapshot() is None
    # And a hook on an unknown id is a silent no-op.
    assert reg.exec_finished("ghost", "c", 0, "o", 0.1) is None
    assert reg.set_state("ghost", "warm") is None


# ----------------------- test 6: preview() caching rule -----------------------
class _CountingHandle(FakeChild):
    def __init__(self, cid, raises=False):
        super().__init__(cid)
        self.preview_calls = 0
        self._raises = raises

    def get_preview_link(self, port):
        self.preview_calls += 1
        if self._raises:
            raise RuntimeError("paused sandbox has no preview link")
        return FakePreviewLink(f"https://preview.fake/{self.id}:{port}")


def test_preview_caches_positive_forever():
    reg = SandboxRegistry()
    h = _CountingHandle("sb-1")
    reg.register(h, role="root", backend="fork", state="warm")
    url = reg.preview("sb-1")
    assert url == "https://preview.fake/sb-1:8080"
    again = reg.preview("sb-1")
    assert again == url
    assert h.preview_calls == 1                     # cached, not re-fetched


def test_preview_negative_not_cached_while_paused():
    reg = SandboxRegistry()
    h = _CountingHandle("sb-1", raises=True)
    reg.register(h, role="checkpoint", backend="fork", state="paused")
    assert reg.preview("sb-1") is None
    assert reg.preview("sb-1") is None
    assert h.preview_calls == 2                      # retried, never poisoned


def test_preview_after_destroyed_returns_none_without_touching_handle():
    reg = SandboxRegistry()
    h = _CountingHandle("sb-1")
    reg.register(h, role="root", backend="fork", state="warm")
    reg.mark_destroyed("sb-1")
    assert reg.preview("sb-1") is None
    assert h.preview_calls == 0                      # handle dropped on destroy


# ----------------------- test 7: thread-safety stress -----------------------
def test_thread_safety_stress_16_threads():
    reg = SandboxRegistry(exec_history=5)
    errors = []
    n_threads, per = 16, 20
    barrier = threading.Barrier(n_threads + 1)   # +1 for the reader

    def worker(t):
        try:
            barrier.wait()
            for i in range(per):
                sid = f"t{t}-{i}"
                reg.register(FakeChild(sid), role="trial-clone", backend="fork")
                reg.exec_started(sid, "run")
                reg.exec_finished(sid, "run", i % 2, "o", 0.01)
                if i % 2 == 0:
                    reg.mark_destroyed(sid)
        except Exception as e:          # pragma: no cover - failure path
            errors.append(e)

    def reader():
        try:
            barrier.wait()
            for _ in range(400):
                reg.snapshot()          # must never raise mid-churn
        except Exception as e:          # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    rt = threading.Thread(target=reader)
    for t in threads:
        t.start()
    rt.start()
    for t in threads:
        t.join()
    rt.join()

    assert errors == []
    counts = reg.counts()
    assert counts["total_ever"] == n_threads * per          # 320
    assert counts["destroyed"] == n_threads * (per // 2)    # 160
    assert counts["live"] == n_threads * (per // 2)         # 160


# ----------------------- test 8: reap_orphans leaf-first -----------------------
def test_reap_orphans_is_leaf_first():
    reg = SandboxRegistry()
    order = []

    def make_destroyer():
        return lambda sid: order.append(sid)

    reg.register(FakeChild("root"), role="root", backend="fork",
                 destroy_fn=make_destroyer())
    reg.register(FakeChild("ckpt"), role="checkpoint", backend="fork",
                 parent_id="root", destroy_fn=make_destroyer())
    reg.register(FakeChild("clone"), role="trial-clone", backend="fork",
                 parent_id="ckpt", destroy_fn=make_destroyer())

    assert reg.reap_orphans() == 3
    assert order == ["clone", "ckpt", "root"]       # deepest first


# ----------------------- test 9: retention window -----------------------
def test_destroyed_retention_window_bounds_records_not_counters():
    reg = SandboxRegistry(destroyed_retain=50)
    for i in range(60):
        sid = f"sb-{i}"
        reg.register(FakeChild(sid), role="trial-clone", backend="fork",
                     parent_id="ckpt")
        reg.mark_destroyed(sid)

    snap = reg.snapshot()
    # Exactly the 50 most-recently-destroyed retained; oldest 10 pruned.
    assert len(snap["sandboxes"]) == 50
    assert all(s["state"] == "destroyed" for s in snap["sandboxes"])
    for i in range(10):
        assert reg.record(f"sb-{i}") is None        # pruned
    for i in range(10, 60):
        assert reg.record(f"sb-{i}") is not None    # retained
    # Counters stay EXACT despite pruning.
    assert snap["counts"]["total_ever"] == 60
    assert snap["counts"]["destroyed"] == 60
    # Lineage lists contain no pruned ids.
    kids = snap["lineage"].get("ckpt", [])
    assert all(k not in {f"sb-{i}" for i in range(10)} for k in kids)


# ----------------------- test 10: spend meter (T1-4) -----------------------
def test_spend_meter_determinism():
    # Drive a deterministic clock through _now so lifetime seconds are exact
    # (no wall-clock flakiness). register at t=0, destroy at t=10, add a second
    # live sandbox, then a double-destroy that must not double-bill.
    reg = SandboxRegistry(destroyed_retain=50)
    clock = {"t": 0.0}
    reg._now = lambda: clock["t"]

    clock["t"] = 0.0
    reg.register(FakeChild("a"), role="root", backend="fork")
    clock["t"] = 10.0
    reg.mark_destroyed("a")
    sp = reg.spend()
    assert sp["total_sandbox_seconds"] == 10
    assert sp["live_sandbox_seconds"] == 0

    clock["t"] = 15.0
    reg.register(FakeChild("b"), role="root", backend="fork")
    clock["t"] = 20.0
    sp = reg.spend()
    assert sp["live_sandbox_seconds"] == 5           # b: 20-15
    assert sp["total_sandbox_seconds"] == 15         # 10 destroyed + 5 live

    # A double mark_destroyed on the already-dead "a" adds nothing.
    clock["t"] = 25.0
    reg.mark_destroyed("a")
    sp = reg.spend()
    # a stays billed at 10; b is still live (25-15=10) => total 20.
    assert sp["live_sandbox_seconds"] == 10
    assert sp["total_sandbox_seconds"] == 20


def test_spend_seconds_survive_pruning():
    # The exact-forever accumulator must not lose seconds when the destroyed
    # record itself is pruned past the retention window.
    reg = SandboxRegistry(destroyed_retain=1)
    clock = {"t": 0.0}
    reg._now = lambda: clock["t"]

    lifetimes = [5.0, 7.0, 3.0]
    t = 0.0
    for i, life in enumerate(lifetimes):
        clock["t"] = t
        reg.register(FakeChild(f"s{i}"), role="trial-clone", backend="fork",
                     parent_id="ckpt")
        clock["t"] = t + life
        reg.mark_destroyed(f"s{i}")
        t += life + 1.0

    # Only 1 destroyed record retained — but the seconds are exact.
    assert len(reg.snapshot()["sandboxes"]) == 1
    assert reg._destroyed_seconds == sum(lifetimes)
    assert reg.spend()["total_sandbox_seconds"] == int(sum(lifetimes))


def test_spend_cost_estimate_and_snapshot_serializable():
    reg = SandboxRegistry()
    clock = {"t": 0.0}
    reg._now = lambda: clock["t"]
    reg.register(FakeChild("a"), role="root", backend="fork")
    clock["t"] = 3600.0                                   # one sandbox-hour live

    assert reg.spend()["est_cost_usd"] is None            # no rate => no price
    sp = reg.spend(rate_per_hour=0.10)
    assert sp["est_cost_usd"] == 0.10                     # 1h * $0.10
    assert sp["rate_per_sandbox_hour"] == 0.10

    snap = reg.snapshot()
    assert "spend" in snap
    for k in ("live_sandbox_seconds", "total_sandbox_seconds", "est_cost_usd",
              "rate_per_sandbox_hour", "note"):
        assert k in snap["spend"]
    json.dumps(snap)                                       # must not raise


# --------------- test 7: preview auth token + listener bootstrap -------------
# Both regressions were found against the LIVE Daytona proxy, not in review:
# the bare preview URL answers 401 (the token was being dropped) and, once
# authorized, 502 (nothing in a Retrial sandbox listens on the port).
class _TokenHandle(FakeChild):
    """A preview link that carries a token, like the real SDK's."""

    def get_preview_link(self, port):
        link = FakePreviewLink(f"https://preview.fake/{self.id}:{port}")
        link.token = "tok-abc"
        return link


def test_preview_url_carries_the_auth_token_as_a_query_param():
    reg = SandboxRegistry()
    reg.register(_TokenHandle("sb-1"), role="trial", backend="snapshot", state="warm")
    # Query param, not a header: a browser <a href> cannot send a header, and
    # ?token= is not a synonym (the live proxy 401s on it).
    assert reg.preview("sb-1") == (
        "https://preview.fake/sb-1:8080?DAYTONA_SANDBOX_AUTH_KEY=tok-abc")


def test_preview_still_works_when_the_link_has_no_token():
    """Older/other SDK shapes expose only `.url` — degrade to the bare URL
    rather than emitting a broken `?DAYTONA_SANDBOX_AUTH_KEY=None`."""
    reg = SandboxRegistry()
    reg.register(FakeChild("sb-2"), role="trial", backend="snapshot", state="warm")
    assert reg.preview("sb-2") == "https://preview.fake/sb-2:8080"


def test_preview_starts_a_listener_so_the_proxy_has_an_upstream():
    reg = SandboxRegistry()
    h = _TokenHandle("sb-3")
    reg.register(h, role="trial", backend="snapshot", state="warm")
    reg.preview("sb-3")
    cmds = [c for c, _ in h.process.execs]
    assert any("http.server 8080" in c for c in cmds), cmds
    # Idempotent: re-running must not stack a second server on the port.
    assert any("pgrep" in c for c in cmds), cmds
    # Serves the trial's own working directory, not a placeholder.
    assert any("--directory /tmp" in c for c in cmds), cmds


def test_preview_listener_failure_never_breaks_preview():
    """A pool is never worth a nicety: if the exec fails, still return the link
    (worst case is the 502 that existed before this feature)."""
    class _Boom(_TokenHandle):
        def __init__(self, cid):
            super().__init__(cid)

            class _P:
                def exec(self, cmd, timeout=None):
                    raise RuntimeError("daemon unreachable")
            self.process = _P()

    reg = SandboxRegistry()
    reg.register(_Boom("sb-4"), role="trial", backend="snapshot", state="warm")
    assert reg.preview("sb-4").startswith("https://preview.fake/sb-4:8080?")


def test_preview_does_not_touch_a_sandbox_on_the_trial_path():
    """The listener is started lazily on drawer-open only. Registering and
    running trials must never pay for it."""
    reg = SandboxRegistry()
    h = _TokenHandle("sb-5")
    reg.register(h, role="trial", backend="snapshot", state="warm")
    reg.exec_finished("sb-5", "python3 seed.py", 0, "EXIT:0", 0.4)
    assert h.process.execs == []          # nothing until preview() is called
