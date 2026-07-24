"""Neutering-guard tests — pure static analysis plus the dynamic canary with a
stubbed run_trial. A patch must still genuinely test the failure condition to
be allowed to win; these pin both the acceptance and every disqualification."""
import pytest

from retrial import guards as guards_mod
from retrial.guards import NeuteringResult, _mutate_for_canary, neutering_check

ORIGINAL = """import sys
value = compute() if False else 41 + 1
assert value == 42
sys.exit(0)
"""

LEGIT_FIX = """import sys
import threading
lock = threading.Lock()
with lock:
    value = 41 + 1
assert value == 42
sys.exit(0)
"""


# ----------------------------- static layer -----------------------------
def test_legit_fix_passes_static():
    res = neutering_check(ORIGINAL, LEGIT_FIX)
    assert bool(res) is True
    assert res.stage == "passed"


def test_bare_sys_exit_zero_is_rejected():
    res = neutering_check(ORIGINAL, "import sys\nsys.exit(0)\n")
    assert not res
    assert res.stage == "static"
    assert "sys.exit(0)" in res.reason


def test_no_real_check_is_rejected():
    # A literal-vs-literal compare tests nothing computed.
    res = neutering_check(ORIGINAL, 'import sys\nassert "a" == "a"\nsys.exit(0)\n')
    assert not res
    assert "no assertion" in res.reason


def test_dropped_assertions_are_rejected():
    original = "x = 1\nassert x == 1\nassert x < 2\n"
    patched = "x = 1\nassert x == 1\n"                # 1 check < original 2
    res = neutering_check(original, patched)
    assert not res
    assert "dropped assertions" in res.reason


def test_unparseable_patch_is_rejected():
    res = neutering_check(ORIGINAL, "def broken(:\n")
    assert not res
    assert res.stage == "static"
    assert "does not parse" in res.reason


# ----------------------------- canary mutation -----------------------------
def test_mutate_flips_the_final_compare():
    mutated = _mutate_for_canary("x = 2\nassert x == 2\n")
    assert mutated is not None
    assert "x != 2" in mutated


def test_mutate_targets_the_last_governing_compare():
    code = "x = 1\nassert x == 1\nassert x < 5\n"
    mutated = _mutate_for_canary(code)
    assert "x == 1" in mutated                        # earlier check untouched
    assert "x >= 5" in mutated                        # final check inverted


def test_mutate_handles_conditional_exit():
    mutated = _mutate_for_canary("import sys\nx = 1\nsys.exit(0 if x == 1 else 1)\n")
    assert mutated is not None
    assert "x != 1" in mutated


def test_mutate_returns_none_on_garbage():
    assert _mutate_for_canary("not python(((") is None
    assert _mutate_for_canary("x = 1\n") is None      # nothing to flip


# ----------------------------- dynamic canary -----------------------------
class _PoolSentinel:
    """Only identity matters — run_trial is stubbed."""


def _stub_run_trial(monkeypatch, result):
    calls = []

    def fake_run_trial(pool, code, timeout=60, isolation="process"):
        calls.append(code)
        return dict(result)

    monkeypatch.setattr(guards_mod, "run_trial", fake_run_trial)
    return calls


def test_canary_disqualifies_patch_whose_mutant_still_passes(monkeypatch):
    calls = _stub_run_trial(monkeypatch, {"passed": True, "error": None})
    res = neutering_check(ORIGINAL, LEGIT_FIX, pool=_PoolSentinel())
    assert not res
    assert res.stage == "dynamic"
    assert "no longer detects" in res.reason
    assert "!=" in calls[0]                           # the mutant actually ran


def test_canary_accepts_patch_whose_mutant_fails(monkeypatch):
    _stub_run_trial(monkeypatch, {"passed": False, "error": None})
    res = neutering_check(ORIGINAL, LEGIT_FIX, pool=_PoolSentinel())
    assert bool(res) is True


def test_canary_infra_error_never_disqualifies(monkeypatch):
    _stub_run_trial(monkeypatch, {"passed": True, "error": "sandbox exploded"})
    res = neutering_check(ORIGINAL, LEGIT_FIX, pool=_PoolSentinel())
    assert bool(res) is True                          # untrustworthy run ignored


def test_result_repr_and_truthiness():
    ok = NeuteringResult(True, "ok", "passed")
    bad = NeuteringResult(False, "nope", "static")
    assert bool(ok) and not bool(bad)
    assert "static" in repr(bad)
