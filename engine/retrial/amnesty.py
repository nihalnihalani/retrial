"""Quarantine amnesty: re-measure a LIST of tests and rank them for triage.

The business case, and the one place "measure it now" beats every incumbent on a
claim they cannot make.

Quarantine lists grow monotonically. GitLab's public numbers: 480 quarantined
tests, up 119% year over year, 159-day average age, 38 with no owner. The only
automatic exits on the market are PASSIVE clocks — BuildPulse un-quarantines
after 7 clean days, Datadog marks "Fixed" after 30 days of not flaking, Trunk
ships no automatic exit at all. Nobody actively re-measures, so the list is a
coverage-debt ledger that only ever increases.

Retrial can re-measure the whole list in one batch and rank it. That is the
product: not a fix, a **verdict** — the thing none of them publish a confidence
bound for.

DELIBERATE OUTPUT DISCIPLINE:

- The report NEVER says "safe to un-quarantine". It ranks by evidence and says
  what was measured. Reinstating a test is a human decision with consequences
  this tool cannot see (does it gate a deploy? is it load-bearing for a
  compliance claim?).
- A test that could not be measured is its OWN category, never folded into
  "clean". `errors` and the reason are carried per test. A batch that quietly
  reports 300 clean tests when 120 of them never executed is the exact failure
  this codebase keeps finding in other people's tools.
- Ranking is by the CI UPPER BOUND, not the point estimate. Two tests that both
  read 0% are not equally evidenced if one ran 50 trials and the other ran 8.
"""
from .verifier import verify

# Verdicts that mean "we got a real measurement", in the order a triage reader
# should work through them.
_TRIAGE_ORDER = {
    "STABLE": 0,          # strongest candidates for reinstatement
    "INCONCLUSIVE": 1,    # needs more trials, not a conclusion
    "FLAKY": 2,           # still flaky — leave quarantined
    "ALWAYS_FAILING": 3,  # not flaky: broken
    "ERROR": 4,           # never measured
}


def run_amnesty(pool, specs, trials=50, conc=16, threshold=0.10,
                timeout=180, isolation="process", bus=None, on_result=None):
    """Measure every spec in `specs` and return a ranked triage report.

    `specs` is a list of RepoSpec (repo mode) — one per test. `on_result` is an
    optional callback(index, total, row) for progress.
    """
    rows = []
    total = len(specs)
    for i, spec in enumerate(specs):
        v = verify(pool, "", max_trials=trials, conc=conc, threshold=threshold,
                   bus=bus, timeout=timeout, isolation=isolation,
                   emit_trials=False, repo_spec=spec)
        # Distinct infra causes, deduped, so a reader sees WHY something could
        # not be measured rather than a bare count.
        causes = []
        for h in v.get("history", []):
            e = h.get("error")
            if e and e not in causes:
                causes.append(e)
        row = {
            "test": spec.node_id,
            "repo": spec.slug,
            "ref": spec.ref,
            "order": spec.order,
            "trials": v["trials"],
            "fails": v["fails"],
            "errors": v["errors"],
            "flake_rate": v["flake_rate"],
            "wilson_ci": v["wilson_ci"],
            "verdict": v["verdict"],
            "infra_causes": causes[:3],
        }
        rows.append(row)
        if on_result is not None:
            try:
                on_result(i + 1, total, row)
            except Exception:
                pass

    rows.sort(key=lambda r: (_TRIAGE_ORDER.get(r["verdict"], 9),
                             r["wilson_ci"][1], -r["trials"]))
    measured = [r for r in rows if r["verdict"] != "ERROR"]
    unmeasured = [r for r in rows if r["verdict"] == "ERROR"]
    return {
        "rows": rows,
        "counts": {
            "total": total,
            "measured": len(measured),
            "unmeasured": len(unmeasured),
            "stable": sum(1 for r in rows if r["verdict"] == "STABLE"),
            "still_flaky": sum(1 for r in rows if r["verdict"] == "FLAKY"),
            "always_failing": sum(1 for r in rows if r["verdict"] == "ALWAYS_FAILING"),
            "inconclusive": sum(1 for r in rows if r["verdict"] == "INCONCLUSIVE"),
        },
        "trials_per_test": trials,
        "threshold": threshold,
    }


def format_amnesty(report):
    """A triage table. Every rate carries its interval; nothing is called safe."""
    c = report["counts"]
    out = []
    out.append(f"{'verdict':<15} {'test':<44} {'rate':>6}  {'95% CI':>14}  {'n':>4} {'err':>4}")
    out.append("-" * 96)
    for r in report["rows"]:
        lo, hi = r["wilson_ci"]
        test = r["test"] if len(r["test"]) <= 44 else "…" + r["test"][-43:]
        out.append(f"{r['verdict']:<15} {test:<44} {r['flake_rate']:>5.0%}  "
                   f"{lo:>5.0%} - {hi:<5.0%}  {r['trials']:>4} {r['errors']:>4}")
    out.append("")
    out.append(f"measured {c['measured']}/{c['total']} at {report['trials_per_test']} "
               f"trials each — {c['stable']} did not fail once, "
               f"{c['inconclusive']} inconclusive, {c['still_flaky']} still flaky, "
               f"{c['always_failing']} always failing.")
    if c["unmeasured"]:
        out.append(f"{c['unmeasured']} could NOT be measured — listed as ERROR "
                   f"above with the cause. They are not 'clean'.")
    out.append("")
    out.append("This is triage, not permission. A STABLE row means the test did "
               "not fail in this many reruns,")
    out.append("bounded by the interval shown — not that it is safe to "
               "reinstate. That call needs a human who")
    out.append("knows what the test gates.")
    return "\n".join(out)
