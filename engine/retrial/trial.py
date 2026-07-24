"""TrialRunner: one test execution in one fresh sandbox. Dumb on purpose.

Leases a sandbox, writes the test via the verified heredoc-exec pattern
(scripts/calibrate_seeds.py), runs it once with a timeout, parses the exit
code, and always releases the sandbox as dirty. All statistical intelligence
lives above this layer in the Verifier.
"""
import re
import time

_EXIT_RE = re.compile(r"EXIT:(-?\d+)")


def run_trial(pool, test_code, timeout=60):
    """Run test_code once in a fresh sandbox.

    Returns {"passed": bool, "duration_s": float, "log_tail": str,
             "exit_code": int|None, "error": str|None}. `error` is non-None only
    for infrastructure failures (the trial did not yield a real pass/fail).
    """
    sb = pool.lease()
    t0 = time.monotonic()
    try:
        # Write the test file, then run it echoing the exit code (verified pattern).
        sb.process.exec("cat > /tmp/seed.py << 'PYEOF'\n" + test_code + "\nPYEOF")
        r = sb.process.exec("python3 /tmp/seed.py; echo EXIT:$?", timeout=timeout)
        out = r.result or ""
        duration = time.monotonic() - t0
        m = _EXIT_RE.search(out)
        if m is None:
            # Ran but produced no parseable exit marker -> treat as infra error.
            return {
                "passed": False,
                "duration_s": round(duration, 3),
                "log_tail": out.strip()[-500:],
                "exit_code": None,
                "error": "no EXIT marker in output",
            }
        code = int(m.group(1))
        return {
            "passed": code == 0,
            "duration_s": round(duration, 3),
            "log_tail": out.strip()[-500:],
            "exit_code": code,
            "error": None,
        }
    except Exception as e:
        return {
            "passed": False,
            "duration_s": round(time.monotonic() - t0, 3),
            "log_tail": str(e)[-500:],
            "exit_code": None,
            "error": str(e)[:200],
        }
    finally:
        pool.release(sb, dirty=True)


class TrialRunner:
    """Thin object wrapper around run_trial for callers that prefer an instance."""

    def __init__(self, timeout=60):
        self.timeout = timeout

    def run_trial(self, pool, test_code):
        return run_trial(pool, test_code, timeout=self.timeout)
