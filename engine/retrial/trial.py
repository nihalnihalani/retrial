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
import re
import time

_EXIT_RE = re.compile(r"EXIT:(-?\d+)")


def run_trial(pool, test_code, timeout=60, isolation="process"):
    """Run test_code once in a leased sandbox.

    Returns {"passed": bool, "duration_s": float, "log_tail": str,
             "exit_code": int|None, "error": str|None}. `error` is non-None only
    for infrastructure failures (the trial did not yield a real pass/fail).
    """
    sb = pool.lease()
    t0 = time.monotonic()
    infra_error = False
    try:
        # Write the test file AND run it in a single exec round-trip (the exec
        # round-trip, not create, is the per-trial cost — one call, not two).
        cmd = ("cat > /tmp/seed.py << 'PYEOF'\n" + test_code
               + "\nPYEOF\npython3 /tmp/seed.py; echo EXIT:$?")
        r = sb.process.exec(cmd, timeout=timeout)
        out = r.result or ""
        duration = time.monotonic() - t0
        m = _EXIT_RE.search(out)
        if m is None:
            # Ran but produced no parseable exit marker -> treat as infra error.
            infra_error = True
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
        infra_error = True
        return {
            "passed": False,
            "duration_s": round(time.monotonic() - t0, 3),
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
