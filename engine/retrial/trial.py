"""TrialRunner: one test execution in one leased sandbox. Dumb on purpose.

Leases a sandbox, writes the test via the verified heredoc-exec pattern
(scripts/calibrate_seeds.py), runs it once with a timeout, parses the exit code,
and releases the sandbox according to the isolation level. All statistical
intelligence lives above this layer in the Verifier.

Isolation levels (chosen per seed by the flake class):
- "process": reuse the warm sandbox; a fresh `python3` process already gives a
  fresh PYTHONHASHSEED and fresh scheduling. Exec-bound throughput.
- "sandbox": destroy the sandbox after the trial; required for state-polluting
  flakes (filesystem/port/env). Create-bound throughput.
A trial that hits an infra error never returns its sandbox to the pool,
regardless of isolation — a broken sandbox must not serve another trial.
"""
import base64
import re
import time

from .registry import REGISTRY
from .settings import get_settings

_EXIT_RE = re.compile(r"EXIT:(-?\d+)")

# The only exit codes that carry a test verdict: 0 = passed, 1 = failed.
#
# SCOPE — this does NOT rescue every harness failure, and an earlier version of
# this comment wrongly implied it did. CPython exits **1** on an uncaught
# ImportError (verified), which is indistinguishable from an assertion failure at
# this layer. A seed whose dependency is missing therefore still reads as 40/40
# genuine failures, not infra errors. Only a seed that *catches* its own setup
# failure and exits with a non-verdict code (as seeds/real/penman_live.py does
# with exit 3) is protected, plus pytest's reserved 2-5 once real pytest runs
# here. Distinguishing a bare ImportError from a real failure needs the harness
# to own the import, not a wider exit-code table.
_VERDICT_EXIT_CODES = (0, 1)


_PREVIEW_DIR = "/tmp/retrial-preview"


def _preview_tail():
    """Shell fragment that refreshes this sandbox's preview page with the trial
    that just ran, or "" when previews are off (the default).

    Three properties this must not break, in order of importance:

    1. The EXIT marker must still carry the SEED's exit code. The caller captures
       it into `RC` BEFORE this fragment runs and echoes `$RC`, so anything in
       here may clobber `$?` freely.
    2. It must never fail a trial. The write is `2>/dev/null || true` — a sandbox
       warmed before previews were enabled has no preview dir, and that must be
       invisible to the measurement.
    3. It must not add a round-trip. This rides inside the SAME exec as the
       trial, so previews cost no extra Daytona calls on the hot path.

    The page states only what was measured: this sandbox's own last exit code."""
    if not get_settings().preview_on:
        return ""
    return (
        f"{{ printf '%s' "
        f"'<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=2>"
        f"<title>retrial sandbox</title>"
        f"<body style=\"font:14px/1.6 ui-monospace,monospace;background:#0b0d12;"
        f"color:#e6e9ef;padding:2rem\">"
        f"<h1 style=\"font-size:15px;letter-spacing:.12em\">RETRIAL &middot; SANDBOX</h1>"
        f"<p>last trial exit code: '\"$RC\"'</p>"
        f"<p style=\"color:#8b93a7\">0 = the test passed &middot; 1 = it failed. "
        f"This sandbox is one of a swarm rerunning the same test to measure how "
        f"often it lies.</p>' "
        f"> {_PREVIEW_DIR}/index.html; }} 2>/dev/null || true; "
    )


def run_trial(pool, test_code, timeout=60, isolation="process"):
    """Run test_code once in a leased sandbox.

    Returns {"passed": bool, "duration_s": float, "log_tail": str,
             "exit_code": int|None, "error": str|None}. `error` is non-None only
    for infrastructure failures (the trial did not yield a real pass/fail).
    """
    sb = pool.lease()
    sid = getattr(sb, "id", None)
    t0 = time.monotonic()
    infra_error = False
    cmd = None
    try:
        # Write the test file AND run it in a single exec round-trip (the exec
        # round-trip, not create, is the per-trial cost — one call, not two).
        # The seed is shipped base64-encoded, not via a heredoc: candidate/patched
        # code is untrusted and could otherwise contain the heredoc sentinel (or
        # shell metacharacters) and break out of the write. base64 has no shell-
        # special chars, so the payload can never escape its own decode.
        b64 = base64.b64encode(test_code.encode("utf-8")).decode("ascii")
        # RC is captured immediately, because `$?` is clobbered by whatever runs
        # next — and with previews on, something does run next. The EXIT marker
        # the verifier parses always reports the SEED's code, never the preview
        # write's.
        cmd = (f"echo '{b64}' | base64 -d > /tmp/seed.py && "
               f"python3 /tmp/seed.py; RC=$?; {_preview_tail()}echo EXIT:$RC")
        # Observatory hook: this pooled sandbox is now running a command. The
        # registry's @_safe guarantees this never affects the trial (a
        # test-constructed pool with a private registry merely double-books into
        # the inert process default — harmless).
        REGISTRY.exec_started(sid, cmd, isolation=isolation)
        r = sb.process.exec(cmd, timeout=timeout)
        out = r.result or ""
        duration = time.monotonic() - t0
        m = _EXIT_RE.search(out)
        if m is None:
            # Ran but produced no parseable exit marker -> treat as infra error.
            infra_error = True
            log_tail = out.strip()[-500:]
            REGISTRY.exec_finished(sid, cmd, None, log_tail, round(duration, 3))
            return {
                "passed": False,
                "duration_s": round(duration, 3),
                "log_tail": log_tail,
                "exit_code": None,
                "error": "no EXIT marker in output",
            }
        code = int(m.group(1))
        log_tail = out.strip()[-500:]
        REGISTRY.exec_finished(sid, cmd, code, log_tail, round(duration, 3))
        if code not in _VERDICT_EXIT_CODES:
            # Not a test verdict — the harness itself failed (dependency install,
            # import error, interpreter usage error, pytest's 2-5 range). Counting
            # these as failures is how a measurement tool lies: a seed whose
            # dependency is missing would read as a confident 100% flake rate.
            # Excluded from the flake-rate denominator like any other infra error.
            infra_error = True
            return {
                "passed": False,
                "duration_s": round(duration, 3),
                "log_tail": log_tail,
                "exit_code": code,
                "error": f"non-verdict exit code {code} (harness/bootstrap failure)",
            }
        return {
            "passed": code == 0,
            "duration_s": round(duration, 3),
            "log_tail": log_tail,
            "exit_code": code,
            "error": None,
        }
    except Exception as e:
        infra_error = True
        duration = round(time.monotonic() - t0, 3)
        REGISTRY.exec_finished(sid, cmd, None, str(e)[-500:], duration)
        return {
            "passed": False,
            "duration_s": duration,
            "log_tail": str(e)[-500:],
            "exit_code": None,
            "error": str(e)[:200],
        }
    finally:
        # Reuse only a healthy sandbox under process isolation; otherwise destroy.
        reusable = (isolation == "process") and not infra_error
        pool.release(sb, reusable=reusable)


class TrialRunner:
    """Thin object wrapper around run_trial for callers that prefer an instance."""

    def __init__(self, timeout=60, isolation="process"):
        self.timeout = timeout
        self.isolation = isolation

    def run_trial(self, pool, test_code):
        return run_trial(pool, test_code, timeout=self.timeout, isolation=self.isolation)
