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

_EXIT_RE = re.compile(r"EXIT:(-?\d+)")


def run_trial(pool, test_code, timeout=60, isolation="process"):
    """Run test_code once in a leased sandbox.

    Returns {"passed": bool, "duration_s": float, "log_tail": str,
             "exit_code": int|None, "error": str|None}. `error` is non-None only
    for infrastructure failures (the trial did not yield a real pass/fail).
    """
    t0 = time.monotonic()
    infra_error = False
    sb = None
    try:
        # lease() is INSIDE the try: under disk/quota pressure pool.lease() falls
        # through to create-on-demand, which can raise (e.g. Daytona "Total disk
        # limit exceeded"). If that escaped run_trial it would kill the verify()
        # worker thread and leave results[i]=None — silently dropped, counted as
        # neither a valid trial nor an infra error, so the batch reports FEWER
        # valid trials than planned and a genuine fix can miss the trial count it
        # needs to clear the threshold (INCONCLUSIVE -> wrongful QUARANTINE). A
        # failed lease is an infrastructure error like any other; surface it.
        sb = pool.lease()
        # Write the test file AND run it in a single exec round-trip (the exec
        # round-trip, not create, is the per-trial cost — one call, not two).
        # The seed is shipped base64-encoded, not via a heredoc: candidate/patched
        # code is untrusted and could otherwise contain the heredoc sentinel (or
        # shell metacharacters) and break out of the write. base64 has no shell-
        # special chars, so the payload can never escape its own decode.
        b64 = base64.b64encode(test_code.encode("utf-8")).decode("ascii")
        cmd = (f"echo '{b64}' | base64 -d > /tmp/seed.py && "
               f"python3 /tmp/seed.py; echo EXIT:$?")
        r = sb.process.exec(cmd, timeout=timeout)
        out = r.result or ""
        duration = time.monotonic() - t0
        # The authoritative marker is the TRAILING `echo EXIT:$?`, so take the LAST
        # match: untrusted test/patch stdout could otherwise print its own
        # "EXIT:<n>" line earlier and shadow the real exit code.
        matches = _EXIT_RE.findall(out)
        if not matches:
            # Ran but produced no parseable exit marker -> treat as infra error.
            infra_error = True
            return {
                "passed": False,
                "duration_s": round(duration, 3),
                "log_tail": out.strip()[-500:],
                "exit_code": None,
                "error": "no EXIT marker in output",
            }
        code = int(matches[-1])
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
        # Only release a sandbox we actually leased (sb is None on a lease
        # failure). Reuse only a healthy sandbox under process isolation;
        # otherwise destroy it so a broken sandbox never serves another trial.
        if sb is not None:
            reusable = (isolation == "process") and not infra_error
            pool.release(sb, reusable=reusable)


class TrialRunner:
    """Thin object wrapper around run_trial for callers that prefer an instance."""

    def __init__(self, timeout=60, isolation="process"):
        self.timeout = timeout
        self.isolation = isolation

    def run_trial(self, pool, test_code):
        return run_trial(pool, test_code, timeout=self.timeout, isolation=self.isolation)
