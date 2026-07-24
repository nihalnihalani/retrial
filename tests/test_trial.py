"""Regression cover for run_trial's lease/exit accounting (no external deps)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from retrial.trial import run_trial
from retrial import verifier


class _LeaseFailPool:
    def lease(self):
        raise Exception("Total disk limit exceeded. Maximum allowed: 30GiB.")

    def release(self, sb, reusable=False):
        raise AssertionError("release must not run when lease failed")


class _SpoofExitPool:
    class _SB:
        class process:
            @staticmethod
            def exec(cmd, timeout=60):
                class R:
                    result = "app logged EXIT:0 itself\n...\nEXIT:1"
                return R()

    def lease(self):
        return self._SB()

    def release(self, sb, reusable=False):
        pass


def test_lease_failure_is_counted_infra_error_not_silent_drop():
    r = run_trial(_LeaseFailPool(), "print('x')")
    assert r is not None and r["error"] is not None and r["exit_code"] is None
    res = verifier.verify(_LeaseFailPool(), "print('x')", max_trials=6, conc=3, min_trials=2)
    assert res["errors"] == 6 and res["trials"] == 0 and res["verdict"] == "ERROR"


def test_exit_code_uses_trailing_marker():
    r = run_trial(_SpoofExitPool(), "print('x')")
    assert r["exit_code"] == 1 and r["passed"] is False
