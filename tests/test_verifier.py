"""Verifier tests — Wilson interval known values, the verdict table, and
verify()'s adaptive early-stop / infra-error accounting against a scripted
pool (no Daytona; the pool hands out fake sandboxes whose exec output is a
canned EXIT line, so run_trial's real parsing path is exercised)."""
import threading

import pytest

from retrial.verifier import (ALWAYS_FAILING_MIN_TRIALS, _verdict, verify,
                              wilson)


# ----------------------------- wilson -----------------------------
def test_wilson_zero_trials_is_maximally_uncertain():
    assert wilson(0, 0) == (0.0, 0.0, 1.0)


def test_wilson_half_brackets_half():
    p, lo, hi = wilson(5, 10)
    assert p == 0.5
    assert lo < 0.5 < hi
    assert 0.0 < lo < hi < 1.0


def test_wilson_tightens_with_n():
    _, lo_small, hi_small = wilson(5, 10)
    _, lo_big, hi_big = wilson(50, 100)
    assert (hi_big - lo_big) < (hi_small - lo_small)


# ----------------------------- verdict table -----------------------------
@pytest.mark.parametrize(
    "fails,n,threshold,expected",
    [
        (0, 0, 0.1, "ERROR"),             # no valid trials
        (24, 24, 0.1, "ALWAYS_FAILING"),  # 100% failing AT the terminal floor
        (40, 40, 0.1, "ALWAYS_FAILING"),  # 100% failing well past the floor
        (0, 40, 0.1, "STABLE"),           # upper bound below threshold
        (20, 40, 0.1, "FLAKY"),           # mixed, whole CI above threshold
        (1, 10, 0.1, "INCONCLUSIVE"),     # CI straddles the threshold
        (2, 2, 0.1, "INCONCLUSIVE"),      # all-fail but below min_trials
        (10, 10, 0.1, "INCONCLUSIVE"),    # all-fail past min_trials, under floor
        (16, 16, 0.1, "INCONCLUSIVE"),    # a p=.88 flake's all-fail opening batch
    ],
)
def test_verdict_table(fails, n, threshold, expected):
    _, lo, hi = wilson(fails, n)
    assert _verdict(fails, n, lo, hi, threshold, min_trials=8) == expected


def test_always_failing_needs_more_evidence_than_min_trials():
    """A terminal ALWAYS_FAILING must outrank an ordinary early-stop.

    Regression test for a live failure: the real penman specimen (measured 81%,
    CI 57-93%) opened with 16/16 failures on its first tournament, tripped
    ALWAYS_FAILING, and the detect-gate ended the run as "REGRESSION" without
    ever diagnosing a genuinely flaky test."""
    for n in range(8, ALWAYS_FAILING_MIN_TRIALS):
        _, lo, hi = wilson(n, n)
        assert _verdict(n, n, lo, hi, 0.1, min_trials=8) == "INCONCLUSIVE", n
    _, lo, hi = wilson(ALWAYS_FAILING_MIN_TRIALS, ALWAYS_FAILING_MIN_TRIALS)
    assert _verdict(ALWAYS_FAILING_MIN_TRIALS, ALWAYS_FAILING_MIN_TRIALS,
                    lo, hi, 0.1, min_trials=8) == "ALWAYS_FAILING"


# ----------------------------- scripted pool -----------------------------
class _ScriptedSandbox:
    """Sandbox whose exec returns the next scripted outcome from a shared list.

    "pass" -> EXIT:0, "fail" -> EXIT:1, "error" -> no EXIT marker (infra)."""

    def __init__(self, script, lock):
        self._script = script
        self._lock = lock
        self.id = f"fake-{id(self)}"

        outer = self

        class _P:
            def exec(self, cmd, timeout=None):
                with outer._lock:
                    kind = outer._script.pop(0) if outer._script else "pass"

                class _R:
                    result = {"pass": "EXIT:0\n", "fail": "EXIT:1\n",
                              "error": "boom, no marker\n"}[kind]

                return _R()

        self.process = _P()


class ScriptedPool:
    """lease/release pool that replays a scripted outcome sequence in order."""

    def __init__(self, script):
        self._lock = threading.Lock()
        self._script = list(script)
        self.released = []

    def lease(self):
        return _ScriptedSandbox(self._script, self._lock)

    def release(self, sb, reusable=False):
        self.released.append((sb.id, reusable))


# ----------------------------- verify -----------------------------
def test_verify_early_stops_stable_below_threshold():
    pool = ScriptedPool(["pass"] * 50)
    res = verify(pool, "code", max_trials=50, conc=8, threshold=0.1, min_trials=8)
    assert res["verdict"] == "STABLE"
    assert res["stopped_early"] is True
    assert res["trials"] < 50                 # the CI cleared 10% before budget
    assert res["fails"] == 0
    assert res["wilson_ci"][1] < 0.1


def test_verify_early_stops_flaky_above_threshold():
    # Alternate pass/fail; conc=1 keeps consumption deterministic per trial.
    pool = ScriptedPool(["pass", "fail"] * 25)
    res = verify(pool, "code", max_trials=50, conc=1, threshold=0.1, min_trials=8)
    assert res["verdict"] == "FLAKY"
    assert res["stopped_early"] is True
    assert res["trials"] == 8                 # 4/8 fails: CI already above 10%
    assert res["wilson_ci"][0] > 0.1


def test_verify_all_failing_is_a_regression_not_flake():
    pool = ScriptedPool(["fail"] * 50)
    res = verify(pool, "code", max_trials=50, conc=8, threshold=0.1, min_trials=8)
    assert res["verdict"] == "ALWAYS_FAILING"
    assert res["fails"] == res["trials"]


def test_verify_excludes_infra_errors_from_trials():
    pool = ScriptedPool(["error"] * 4 + ["pass"] * 46)
    res = verify(pool, "code", max_trials=50, conc=1, threshold=0.1, min_trials=8)
    assert res["errors"] == 4
    assert res["trials"] + res["errors"] <= 50
    assert res["fails"] == 0                  # errors are not failures
    # An errored sandbox is never released reusable (must not serve again).
    errored = [r for r in pool.released[:4]]
    assert all(reusable is False for _, reusable in errored)


def test_verify_emits_trial_done_per_valid_trial():
    from retrial.events import EventBus

    bus = EventBus()
    pool = ScriptedPool(["pass", "error", "fail"] + ["pass"] * 47)
    verify(pool, "code", max_trials=50, conc=1, threshold=0.5, min_trials=4,
           bus=bus, hypothesis_id="h1")
    trials = [e["payload"] for e in bus.history() if e["type"] == "trial_done"]
    # The infra error emitted nothing; indexes are contiguous over valid trials.
    assert [t["trial_index"] for t in trials] == list(range(len(trials)))
    assert all(t["hypothesis_id"] == "h1" for t in trials)
    assert {"passed", "duration_s"} <= set(trials[0])


def test_verify_no_valid_trials_is_error():
    pool = ScriptedPool(["error"] * 10)
    res = verify(pool, "code", max_trials=10, conc=2, threshold=0.1, min_trials=8)
    assert res["verdict"] == "ERROR"
    assert res["trials"] == 0
    assert res["errors"] == 10
