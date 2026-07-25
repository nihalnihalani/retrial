"""Flake matrix: rerun the SAME test under deliberately varied environments and
report which axis flips the outcome.

Why this exists. Every incumbent detects flakiness by mining CI history — Trunk,
Datadog, BuildPulse, CircleCI, Buildkite. History is a record of what happened,
so it can tell you a test is unreliable but it structurally CANNOT tell you what
made it unreliable: there is no counterfactual in a log. You cannot ask a
historical record "would it still have failed with a different hash seed?"

A disposable-sandbox swarm can, because it can run the counterfactual. Varying
one axis at a time and measuring the flake rate under each turns "this test is
flaky" into "this test fails when PYTHONHASHSEED varies, and not otherwise" —
an actionable cause, not a label.

Academic precedent for the mechanism: Shaker (star-rg.github.io/shaker) injects
CPU/IO load with stress-ng and observes tests that fail 23.5% under stress and 0%
without. That validates active perturbation as a detector; no product ships it.

HONESTY RULES BAKED IN:

- An axis is only reported as implicated when its interval is DISJOINT from the
  control's. Overlapping intervals mean "not distinguishable at this budget",
  never "no effect" — absence of evidence gets said as absence of evidence.
- Every cell carries its own Wilson interval and n. There are no bare rates.
- Infra errors are excluded from each cell's denominator, exactly as elsewhere,
  and reported separately so a reader can see whether to trust the cell.
- The control cell is measured, not assumed. Comparing against an unmeasured
  baseline is how you manufacture an effect.
"""
from .config import DEFAULT_THRESHOLD
from .trial import run_trial
from .verifier import verify, wilson

# One axis = one environment perturbation applied to the interpreter that runs
# the test. Each is a real, documented cause of nondeterminism in Python.
#
# `None` for the control: no variables set, the environment the pool gives us.
# Each axis carries a PROBE: a tiny program that exits 0 only if the
# perturbation actually took effect in this sandbox. An axis that silently fails
# to apply produces an interval overlapping the control, which a reader
# correctly interprets as "this axis is not the cause" — evidence of absence
# manufactured from an experiment that never ran. MEASURED 2026-07-25 in the
# default container: tzdata IS present (TZ works, UTC -> NZST), but tr_TR is NOT
# a generated locale (only C, C.utf8, en_US.utf8, POSIX), so LC_ALL silently
# falls back and 'I'.lower() stays 'i'. The locale axis was inert and reporting
# a result. Probes turn that into an honest "unavailable" instead.
_PROBE_TZ = ("import time,sys; sys.exit(0 if time.timezone != 0 or "
             "time.tzname[0] not in ('UTC','GMT') else 1)")
_PROBE_TZ_UTC = ("import time,sys; sys.exit(0 if time.tzname[0] in "
                 "('UTC','GMT') else 1)")
_PROBE_TR = ("import sys; sys.exit(0 if 'I'.lower() != 'i' else 1)")
_PROBE_HASH = "import sys,os; sys.exit(0 if os.environ.get('PYTHONHASHSEED') else 1)"
_PROBE_UTF8 = ("import sys; sys.exit(0 if sys.flags.utf8_mode == 0 else 1)")

AXES = [
    ("control", {}, "no perturbation — the baseline this substrate produces", None),
    ("hash_seed_0", {"PYTHONHASHSEED": "0"},
     "hash randomization DISABLED — set/dict iteration order becomes stable",
     _PROBE_HASH),
    ("hash_seed_1", {"PYTHONHASHSEED": "1"},
     "hash randomization pinned to a different fixed seed", _PROBE_HASH),
    ("tz_auckland", {"TZ": "Pacific/Auckland"},
     "a timezone 12-13h from UTC, across the date line", _PROBE_TZ),
    ("tz_utc", {"TZ": "UTC"}, "timezone pinned to UTC", _PROBE_TZ_UTC),
    ("locale_tr", {"LC_ALL": "tr_TR.UTF-8", "LANG": "tr_TR.UTF-8"},
     "Turkish locale — the classic dotless-i case-folding trap", _PROBE_TR),
    ("utf8_off", {"PYTHONUTF8": "0"},
     "UTF-8 mode disabled — filesystem/stdio encoding follows the locale",
     _PROBE_UTF8),
]


def run_matrix(pool, test_code, axes=None, trials=24, conc=16,
               threshold=DEFAULT_THRESHOLD, timeout=60, isolation="process",
               bus=None, repo_spec=None):
    """Measure `test_code` once per axis. Returns a result dict.

    Every cell is a full `verify()` — same Wilson math, same verdict table, same
    infra-error exclusion as a detect run. `trials` is per cell, so the total
    cost is len(axes) * trials.

    `repo_spec` points the matrix at a REAL repository instead of a seed file.
    Without it the most differentiated capability in the product only worked on
    hand-written seeds — the axes could never be applied to anybody's actual
    test. `run_trial` already accepts `env` and `repo_spec` together and
    `verify()` forwards both, so this is a pass-through, not a new mechanism.
    """
    axes = axes if axes is not None else AXES
    cells = []
    for entry in axes:
        name, env, description = entry[0], entry[1], entry[2]
        probe = entry[3] if len(entry) > 3 else None

        # Prove the perturbation actually applied BEFORE measuring under it.
        # One trial, and it saves `trials` wasted ones on a dead axis.
        applied, why = True, None
        if probe:
            r = run_trial(pool, probe, timeout=timeout, isolation=isolation,
                          env=env or None)
            if r.get("error") is not None:
                applied, why = False, f"probe could not run: {r['error']}"
            elif not r.get("passed"):
                applied, why = False, (
                    "the perturbation did not take effect in this sandbox "
                    "(the variable was set but nothing changed) — most often a "
                    "locale that is not generated or missing tzdata")

        if not applied:
            cells.append({
                "axis": name, "env": dict(env), "description": description,
                "trials": 0, "fails": 0, "errors": 0, "flake_rate": 0.0,
                "wilson_ci": [0.0, 1.0], "verdict": "UNAVAILABLE",
                "unavailable_reason": why,
            })
            continue

        v = verify(pool, test_code, max_trials=trials, conc=conc,
                   threshold=threshold, bus=bus, timeout=timeout,
                   isolation=isolation, emit_trials=False, env=env or None,
                   repo_spec=repo_spec)
        cells.append({
            "axis": name,
            "env": dict(env),
            "description": description,
            "trials": v["trials"],
            "fails": v["fails"],
            "errors": v["errors"],
            "flake_rate": v["flake_rate"],
            "wilson_ci": v["wilson_ci"],
            "verdict": v["verdict"],
        })

    control = next((c for c in cells if c["axis"] == "control"), None)
    implicated = []
    if control is not None and control["trials"] > 0:
        lo_c, hi_c = control["wilson_ci"]
        for c in cells:
            if (c["axis"] == "control" or c["trials"] == 0
                    or c["verdict"] == "UNAVAILABLE"):
                continue
            lo, hi = c["wilson_ci"]
            # Disjoint intervals only. Two proportions whose 95% intervals do not
            # overlap differ at this evidence level; overlapping ones are simply
            # not separated by the budget we spent, which is a different
            # statement from "they are the same".
            if hi < lo_c or lo > hi_c:
                implicated.append({
                    "axis": c["axis"],
                    "direction": "stabilises" if c["flake_rate"] < control["flake_rate"]
                                 else "destabilises",
                    "control_rate": control["flake_rate"],
                    "axis_rate": c["flake_rate"],
                    "description": c["description"],
                })

    return {
        "cells": cells,
        "control": control,
        "implicated": implicated,
        "trials_per_axis": trials,
        "note": ("An axis is listed only when its 95% interval is disjoint from "
                 "the control's. Overlapping intervals mean this budget did not "
                 "separate them — not that the axis has no effect."),
    }


def format_matrix(result):
    """Human-readable table. Rates always carry their interval."""
    lines = []
    lines.append(f"{'axis':<14} {'flake':>7}  {'95% CI':>15}  {'n':>4} {'err':>4}  verdict")
    lines.append("-" * 74)
    for c in result["cells"]:
        lo, hi = c["wilson_ci"]
        ci = f"{lo:.0%} - {hi:.0%}"
        if c["verdict"] == "UNAVAILABLE":
            lines.append(f"{c['axis']:<14} {'—':>6}  {'—':>15}  {'—':>4} {'—':>4}  UNAVAILABLE")
            continue
        lines.append(f"{c['axis']:<14} {c['flake_rate']:>6.0%}  {ci:>15}  "
                     f"{c['trials']:>4} {c['errors']:>4}  {c['verdict']}")
    lines.append("")
    dead = [c for c in result["cells"] if c["verdict"] == "UNAVAILABLE"]
    if dead:
        lines.append("UNAVAILABLE — not measured, and NOT evidence of no effect:")
        for c in dead:
            lines.append(f"  {c['axis']}: {c.get('unavailable_reason', 'perturbation did not apply')}")
        lines.append("")
    if not result["implicated"]:
        lines.append("no axis separated from the control at this budget.")
        lines.append("that is NOT 'no environmental cause' — it is 'this many")
        lines.append("trials did not distinguish one'. Raise --trials to narrow.")
    else:
        for imp in result["implicated"]:
            lines.append(f"IMPLICATED: {imp['axis']} {imp['direction']} the test "
                         f"({imp['control_rate']:.0%} -> {imp['axis_rate']:.0%})")
            lines.append(f"            {imp['description']}")
    return "\n".join(lines)
