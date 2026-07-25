"""Flake matrix: an axis is implicated only on DISJOINT intervals.

The whole value of this feature is that it makes a causal claim. A causal claim
from overlapping confidence intervals is exactly the "coincidence dressed as a
result" this product exists to refuse, so the separation rule is the thing worth
pinning down.
"""
from retrial.matrix import AXES, run_matrix


class _FakePool:
    """Pool whose trial outcome is a pure function of the env it is handed."""

    def __init__(self, by_env):
        self.by_env = by_env

    def lease(self):
        return self

    def release(self, sb, reusable=True):
        pass


def _fake_run_trial(outcomes):
    calls = {"n": 0}

    def run(pool, code, timeout=60, isolation="process", env=None):
        key = tuple(sorted((env or {}).items()))
        seq = outcomes[key]
        passed = seq[calls["n"] % len(seq)]
        calls["n"] += 1
        return {"passed": passed, "duration_s": 0.0, "log_tail": "",
                "exit_code": 0 if passed else 1, "error": None}
    return run


def _run(monkeypatch, control_seq, axis_seq, axis_name):
    axes = [("control", {}, "baseline"),
            (axis_name, dict(next(a[1] for a in AXES if a[0] == axis_name)), "d")]
    outcomes = {(): control_seq, tuple(sorted(axes[1][1].items())): axis_seq}
    import retrial.verifier as V
    monkeypatch.setattr(V, "run_trial", _fake_run_trial(outcomes))
    return run_matrix(_FakePool({}), "x", axes=axes, trials=40, conc=40)


def test_disjoint_intervals_implicate_the_axis(monkeypatch):
    # control ~50% flaky; axis never fails -> intervals cannot overlap.
    res = _run(monkeypatch, [True, False], [True], "hash_seed_0")
    assert [i["axis"] for i in res["implicated"]] == ["hash_seed_0"]
    assert res["implicated"][0]["direction"] == "stabilises"


def test_axis_that_makes_it_worse_is_reported_as_destabilising(monkeypatch):
    res = _run(monkeypatch, [True, False], [False], "hash_seed_1")
    assert res["implicated"][0]["direction"] == "destabilises"


def test_overlapping_intervals_implicate_nothing(monkeypatch):
    """Not separated by this budget is NOT 'no effect' — and must not be
    reported as one."""
    res = _run(monkeypatch, [True, False], [True, False], "tz_utc")
    assert res["implicated"] == []
    assert "did not separate" in res["note"]


def test_every_cell_carries_its_own_interval_and_n(monkeypatch):
    res = _run(monkeypatch, [True, False], [True], "hash_seed_0")
    for cell in res["cells"]:
        assert len(cell["wilson_ci"]) == 2
        assert cell["trials"] > 0
        assert "flake_rate" in cell and "errors" in cell
