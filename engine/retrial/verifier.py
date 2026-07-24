"""Verifier: batches trials, computes an empirical flake rate with a Wilson 95%
confidence interval, and stops early once the interval provably clears the
decision threshold.

A green run proves nothing at 40% flake — verification, not generation, is the
bottleneck for flaky tests. So we rerun the test in fresh sandboxes many times
and reason about the interval, not a single outcome. Adaptive early-stop halts
as soon as the CI fully excludes `threshold` on either side, saving sandboxes
and wall-clock. `confirm()` is a fresh, independent verify run used to guard the
tournament winner against selection bias.
"""
import math
import threading

from .trial import run_trial


def wilson(fails, n, z=1.96):
    """Wilson score interval for a binomial proportion. Returns (p, lo, hi)."""
    if n == 0:
        return (0.0, 0.0, 1.0)
    p = fails / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - m), min(1.0, c + m))


def _verdict(p, lo, hi, threshold):
    if hi < threshold:
        return "STABLE"
    if lo > threshold:
        return "FLAKY"
    # Interval straddles the threshold (inconclusive) — fall back to point estimate.
    return "FLAKY" if p > threshold else "STABLE"


def verify(pool, test_code, max_trials=50, conc=16, threshold=0.05,
           min_trials=8, bus=None, label=None, timeout=60, isolation="process",
           hypothesis_id=None, emit_trials=True):
    """Rerun test_code up to max_trials times (conc at a time) and classify it.

    Emits a `trial_done` per valid trial (when emit_trials) carrying the UI
    contract: hypothesis_id (None for the detect series), a per-context 0-based
    trial_index, passed, duration_s.

    Returns {"trials", "fails", "errors", "flake_rate", "wilson_ci":[lo,hi],
             "verdict": "STABLE"|"FLAKY", "stopped_early": bool, "history": [...]}.
    """
    history = []
    fails = 0
    n = 0            # valid trials (pass/fail; excludes infra errors)
    errors = 0
    done = 0
    stopped_early = False
    trial_index = 0  # per-context 0-based index over valid trials (for the UI grid)

    while done < max_trials:
        batch = min(conc, max_trials - done)
        results = [None] * batch

        def worker(i):
            results[i] = run_trial(pool, test_code, timeout=timeout, isolation=isolation)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(batch)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        done += batch

        for res in results:
            if res is None:
                continue
            history.append(res)
            if res["error"] is not None:
                errors += 1
                continue  # infra errors are not real pass/fail — keep the grid honest
            n += 1
            if not res["passed"]:
                fails += 1
            if bus is not None and emit_trials:
                bus.emit("trial_done", {
                    "hypothesis_id": hypothesis_id,
                    "trial_index": trial_index,
                    "passed": res["passed"],
                    "duration_s": res["duration_s"],
                })
            trial_index += 1

        # Adaptive early-stop: the CI provably excludes the threshold either way.
        p, lo, hi = wilson(fails, n)
        if n >= min_trials and (hi < threshold or lo > threshold):
            stopped_early = True
            break

    p, lo, hi = wilson(fails, n)
    return {
        "trials": n,
        "fails": fails,
        "errors": errors,
        "flake_rate": round(p, 4),
        "wilson_ci": [round(lo, 4), round(hi, 4)],
        "verdict": _verdict(p, lo, hi, threshold),
        "stopped_early": stopped_early,
        "isolation": isolation,
        "history": history,
    }


def confirm(pool, test_code, max_trials=50, conc=16, threshold=0.05,
            min_trials=8, bus=None, label=None, timeout=60, isolation="process",
            emit_trials=False):
    """A fresh, independent verify run to confirm a tournament winner.

    Per-trial events are suppressed by default: the confirmation round reports a
    single summary number (confirm_flake_rate on winner_confirmed) rather than
    re-flooding the winner's grid.
    """
    return verify(pool, test_code, max_trials=max_trials, conc=conc,
                  threshold=threshold, min_trials=min_trials, bus=bus,
                  label=label or "confirm", timeout=timeout, isolation=isolation,
                  emit_trials=emit_trials)


def verify_hermetic(hermetic_pool, test_code, max_trials=50, conc=16, threshold=0.05,
                    min_trials=8, bus=None, timeout=60, isolation="process"):
    """Measure a test's flake rate in NETWORK-BLOCKED sandboxes (a hermetic pool
    created with network_block_all=True). Compared against the networked detect
    rate, this diagnoses external-dependency flakes by infrastructure: if the two
    rates' CIs don't overlap, the flake is network/external-dependent. Per-trial
    events are suppressed (this is a diagnostic pass, not the main board)."""
    return verify(hermetic_pool, test_code, max_trials=max_trials, conc=conc,
                  threshold=threshold, min_trials=min_trials, bus=bus,
                  label="hermetic", timeout=timeout, isolation=isolation,
                  emit_trials=False)


class Verifier:
    """Object wrapper bundling default verify/confirm parameters."""

    def __init__(self, max_trials=50, conc=16, threshold=0.05, min_trials=8,
                 bus=None, timeout=60, isolation="process"):
        self.max_trials = max_trials
        self.conc = conc
        self.threshold = threshold
        self.min_trials = min_trials
        self.bus = bus
        self.timeout = timeout
        self.isolation = isolation

    def verify(self, pool, test_code, label=None):
        return verify(pool, test_code, self.max_trials, self.conc, self.threshold,
                      self.min_trials, self.bus, label, self.timeout, self.isolation)

    def confirm(self, pool, test_code, label=None):
        return confirm(pool, test_code, self.max_trials, self.conc, self.threshold,
                       self.min_trials, self.bus, label, self.timeout, self.isolation)
