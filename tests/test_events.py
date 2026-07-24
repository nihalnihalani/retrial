"""EventBus tests + the emit-site registry scan.

The EventBus half covers the replay/reset/fan-out contract the server and UI
rely on. The scan half is the ENFORCEMENT that `EVENT_TYPES` (a docs-only
tuple) otherwise lacks: it walks every module in engine/retrial/ with ast,
collects the first-arg string literal of every `*.emit("...")` / `*._emit("...")`
call, and fails if any emitted type is missing from the registry. A typo'd
event name at any emit site — which grep-the-tuple would never catch, and
which the tuple's own history proves happens (hermetic_diagnosis was emitted
for weeks without being listed) — fails the suite instead of silently
breaking the UI.
"""
import ast
from pathlib import Path

from conftest import ROOT

from retrial.events import EVENT_TYPES, EventBus


# ----------------------------- bus contract -----------------------------
def test_emit_shape_and_fanout():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    ev = bus.emit("trial_done", {"passed": True})
    assert set(ev) == {"seq", "type", "ts", "payload"}
    assert ev["seq"] == 1
    assert ev["type"] == "trial_done"
    assert isinstance(ev["ts"], float)
    assert ev["payload"] == {"passed": True}
    assert seen == [ev]


def test_subscribe_replays_backlog_then_streams():
    bus = EventBus()
    first = bus.emit("detect_done", {"flake_rate": 0.5})
    late = []
    bus.subscribe(late.append)
    assert late == [first]              # backlog replayed on subscribe
    second = bus.emit("tournament_done", {})
    assert late == [first, second]      # then live streaming


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen = []
    unsubscribe = bus.subscribe(seen.append)
    bus.emit("trial_done", {})
    unsubscribe()
    bus.emit("trial_done", {})
    assert len(seen) == 1


def test_reset_clears_buffer_but_seq_stays_monotonic():
    bus = EventBus()
    bus.emit("trial_done", {"trial_index": 0})
    bus.emit("trial_done", {"trial_index": 1})
    bus.reset()
    assert bus.history() == []          # a late subscriber sees no stale tail
    ev = bus.emit("run_started", {})
    assert ev["seq"] == 3               # never rewound — ordering undisturbed
    assert [e["seq"] for e in bus.history()] == [3]


def test_ring_buffer_caps_backlog():
    bus = EventBus(buffer_size=3)
    for i in range(5):
        bus.emit("trial_done", {"trial_index": i})
    idx = [e["payload"]["trial_index"] for e in bus.history()]
    assert idx == [2, 3, 4]             # oldest evicted first


def test_subscriber_exceptions_are_swallowed():
    bus = EventBus()

    def bad(ev):
        raise RuntimeError("subscriber blew up")

    seen = []
    bus.subscribe(bad)
    bus.subscribe(seen.append)
    ev = bus.emit("trial_done", {})     # must not raise
    assert seen == [ev]                 # later subscribers still get the event
    # And backlog replay to a broken subscriber is swallowed too.
    bus.subscribe(bad)


# ----------------------------- emit-site scan -----------------------------
def _emitted_literals():
    """Every string-literal event type passed to a *.emit / *._emit call in
    engine/retrial/*.py, with its file:line for a readable failure message."""
    found = []
    for path in sorted((ROOT / "engine" / "retrial").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if name not in ("emit", "_emit"):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((first.value, f"{path.name}:{node.lineno}"))
    return found


def test_every_emit_site_is_registered_in_event_types():
    emitted = _emitted_literals()
    assert emitted, "scan found no emit sites — the walker is broken"
    unregistered = [(name, loc) for name, loc in emitted
                    if name not in EVENT_TYPES]
    assert unregistered == [], (
        f"emit sites use event types missing from events.EVENT_TYPES: "
        f"{unregistered}")


def test_scan_covers_the_previously_drifted_event():
    # hermetic_diagnosis is the precedent: emitted by coordinator.py but
    # missing from the tuple until SEAM-3. Pin both halves of the fix.
    assert "hermetic_diagnosis" in EVENT_TYPES
    assert any(name == "hermetic_diagnosis" for name, _ in _emitted_literals())


def test_promotion_events_registered():
    assert "promotion_pending" in EVENT_TYPES
    assert "promotion_closed" in EVENT_TYPES


def test_observatory_events_registered():
    # The 5 Sandbox Observatory event names (the ast emit-site scan above already
    # binds registry.py's emit sites; this documents intent cheaply).
    for name in ("sandbox_registered", "sandbox_state", "sandbox_exec",
                 "sandbox_destroyed", "registry_snapshot"):
        assert name in EVENT_TYPES
