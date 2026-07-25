"""Amnesty triage: ranking discipline and the refusal to overclaim.

The failure this guards against: a batch report that quietly folds tests it
could not measure into the "clean" pile. On a 480-test quarantine list that turns
"120 never ran" into "312 look fine", which is the exact shape of lie this
product exists to detect.
"""
from retrial.amnesty import format_amnesty, run_amnesty


class _Spec:
    def __init__(self, node, order="fixed"):
        self.node_id, self.slug, self.ref, self.order = node, "o/n", "a" * 40, order


def _fake_verify(outcomes):
    """verify() stub: outcomes maps node_id -> (verdict, fails, trials, ci, errors)."""
    def fake(pool, code, **kw):
        v, fails, trials, ci, errors = outcomes[kw["repo_spec"].node_id]
        return {"trials": trials, "fails": fails, "errors": errors,
                "flake_rate": (fails / trials) if trials else 0.0,
                "wilson_ci": ci, "verdict": v,
                "history": ([{"error": "pytest collected NO TESTS (exit 5)"}] * errors)}
    return fake


def _run(monkeypatch, outcomes):
    import retrial.amnesty as A
    monkeypatch.setattr(A, "verify", _fake_verify(outcomes))
    return run_amnesty(None, [_Spec(n) for n in outcomes], trials=50)


OUTCOMES = {
    "t.py::wide":   ("STABLE", 0, 20, [0.0, 0.16], 0),
    "t.py::tight":  ("STABLE", 0, 50, [0.0, 0.07], 0),
    "t.py::flaky":  ("FLAKY", 22, 50, [0.30, 0.58], 0),
    "t.py::broken": ("ERROR", 0, 0, [0.0, 1.0], 50),
    "t.py::unsure": ("INCONCLUSIVE", 1, 20, [0.0, 0.24], 0),
}


def test_unmeasured_tests_are_their_own_category_never_clean(monkeypatch):
    rep = _run(monkeypatch, OUTCOMES)
    assert rep["counts"]["unmeasured"] == 1
    assert rep["counts"]["measured"] == 4
    # the ERROR row must NOT be counted as stable
    assert rep["counts"]["stable"] == 2


def test_ranking_is_by_evidence_not_by_point_estimate(monkeypatch):
    """Two tests both read 0%. The one with 50 trials outranks the one with 20,
    because a bound is evidence and a point estimate is not."""
    rep = _run(monkeypatch, OUTCOMES)
    order = [r["test"] for r in rep["rows"]]
    assert order.index("t.py::tight") < order.index("t.py::wide")
    assert order.index("t.py::wide") < order.index("t.py::flaky")
    assert order[-1] == "t.py::broken"  # unmeasured sinks to the bottom


def test_report_states_the_limit_instead_of_granting_permission(monkeypatch):
    """The word "safe" may appear ONLY inside the negation. What must never
    appear is an affirmative recommendation to reinstate."""
    text = format_amnesty(_run(monkeypatch, OUTCOMES)).lower()
    assert "not that it is safe to reinstate" in text
    assert "triage, not permission" in text
    assert "not 'clean'" in text
    for affirmative in ("safe to un-quarantine", "recommend reinstating",
                        "ok to re-enable", "these are clean"):
        assert affirmative not in text


def test_infra_causes_are_carried_per_test(monkeypatch):
    rep = _run(monkeypatch, OUTCOMES)
    broken = [r for r in rep["rows"] if r["test"] == "t.py::broken"][0]
    assert broken["errors"] == 50
    assert broken["infra_causes"] and "NO TESTS" in broken["infra_causes"][0]


def test_every_row_carries_its_interval(monkeypatch):
    for r in _run(monkeypatch, OUTCOMES)["rows"]:
        assert len(r["wilson_ci"]) == 2
