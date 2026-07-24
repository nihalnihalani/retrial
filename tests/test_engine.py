"""Engine unit tests — regression cover for the trial/verifier/voice paths.

Run: .venv/bin/python -m pytest tests/ -q   (from repo root)
These are pure/faked-dependency tests — no Daytona, no network — so they run in
CI in milliseconds and guard the invariants the live demo depends on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from retrial.trial import run_trial
from retrial import verifier
from retrial.voice import build_script


# --------------------------------------------------------------------------
# trial.py — a failed lease must be a COUNTED infra error, never a silent drop.
# (Regression: pool.lease() used to sit outside the try, so a lease failure
# under Daytona disk pressure killed the verify() worker thread, results[i]
# stayed None, and the trial was counted as neither valid nor error — starving
# a genuine fix of the trials it needed to clear the threshold.)
# --------------------------------------------------------------------------
class _LeaseFailPool:
    def lease(self):
        raise Exception("Total disk limit exceeded. Maximum allowed: 30GiB.")

    def release(self, sb, reusable=False):
        raise AssertionError("release must not be called when lease failed")


class _SpoofExitPool:
    """Sandbox whose output contains a spoofed early EXIT:0 then the real EXIT:1."""
    class _SB:
        class process:
            @staticmethod
            def exec(cmd, timeout=60):
                class R:
                    result = "app said EXIT:0 in its own log\n...\nEXIT:1"
                return R()

    def lease(self):
        return self._SB()

    def release(self, sb, reusable=False):
        pass


def test_lease_failure_is_infra_error_not_exception():
    r = run_trial(_LeaseFailPool(), "print('x')")
    assert r is not None
    assert r["error"] is not None
    assert r["exit_code"] is None
    assert r["passed"] is False


def test_verify_counts_lease_failures_as_errors():
    res = verifier.verify(_LeaseFailPool(), "print('x')", max_trials=6, conc=3, min_trials=2)
    assert res["errors"] == 6
    assert res["trials"] == 0
    assert res["verdict"] == "ERROR"


def test_exit_code_uses_trailing_marker_not_first_match():
    # The authoritative marker is the trailing `echo EXIT:$?`; an earlier spoofed
    # EXIT:0 in test stdout must not shadow it.
    r = run_trial(_SpoofExitPool(), "print('x')")
    assert r["exit_code"] == 1
    assert r["passed"] is False


# --------------------------------------------------------------------------
# verifier.py — verdicts are classified from EVIDENCE (Wilson CI), not points.
# --------------------------------------------------------------------------
def test_wilson_bounds_and_verdicts():
    # 0/40 clears the 10% threshold at 95% (upper < 0.10) -> STABLE.
    p, lo, hi = verifier.wilson(0, 40)
    assert p == 0.0 and hi < 0.10
    assert verifier._verdict(0, 40, lo, hi, 0.10, 8) == "STABLE"
    # A confidently-flaky sample (both passes and fails, whole CI above threshold).
    p, lo, hi = verifier.wilson(20, 40)
    assert verifier._verdict(20, 40, lo, hi, 0.10, 8) == "FLAKY"
    # 100% failing with enough trials is a regression, not a flake.
    p, lo, hi = verifier.wilson(12, 12)
    assert verifier._verdict(12, 12, lo, hi, 0.10, 8) == "ALWAYS_FAILING"
    # No valid trials is ERROR, never STABLE-by-default.
    assert verifier._verdict(0, 0, 0.0, 1.0, 0.10, 8) == "ERROR"


# --------------------------------------------------------------------------
# voice.py — narration is honest: every number comes from the real result.
# --------------------------------------------------------------------------
def test_build_script_fixed_uses_real_numbers():
    result = {
        "verdict": "FIXED", "orig_flake_rate": 0.48,
        "winner": {"model": "accounts/fireworks/models/glm-5p2",
                   "cause_class": "order_dependency"},
        "confirmation": {"flake_rate": 0.0},
    }
    s = build_script(result, "test_dict_order.py")
    assert "48 percent" in s
    assert "glm-5p2" in s          # model slug, not a placeholder
    assert "0 percent" in s
    assert "fixed" in s.lower()


def test_build_script_regression_is_honest():
    result = {"verdict": "REGRESSION", "orig_verdict": "ALWAYS_FAILING", "orig_flake_rate": 1.0}
    s = build_script(result, "always_fails.py")
    assert "regression" in s.lower()
    assert "fix the code" in s.lower()
