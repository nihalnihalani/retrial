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

    def run(pool, code, timeout=60, isolation="process", env=None, **_):
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


def test_an_axis_that_does_not_apply_is_UNAVAILABLE_not_no_effect(monkeypatch):
    """MEASURED 2026-07-25: tr_TR is not a generated locale in the default
    container, so LC_ALL silently falls back and 'I'.lower() stays 'i'. Without a
    probe the axis produced an interval overlapping the control, which a reader
    correctly reads as "locale is not the cause" — evidence of absence
    manufactured from an experiment that never ran."""
    import retrial.matrix as M

    def probe_fails(pool, code, timeout=60, isolation="process", env=None, **_):
        # the probe program itself: exits non-zero => perturbation inert
        return {"passed": False, "duration_s": 0.0, "log_tail": "",
                "exit_code": 1, "error": None}

    monkeypatch.setattr(M, "run_trial", probe_fails)
    axes = [("control", {}, "baseline", None),
            ("locale_tr", {"LC_ALL": "tr_TR.UTF-8"}, "turkish", "probe")]
    monkeypatch.setattr(M, "verify", lambda *a, **k: {
        "trials": 20, "fails": 10, "errors": 0, "flake_rate": 0.5,
        "wilson_ci": [0.3, 0.7], "verdict": "FLAKY"})
    res = M.run_matrix(_FakePool({}), "x", axes=axes, trials=20)

    dead = [c for c in res["cells"] if c["axis"] == "locale_tr"][0]
    assert dead["verdict"] == "UNAVAILABLE"
    assert dead["trials"] == 0
    assert "did not take effect" in dead["unavailable_reason"]
    # and it must never be implicated, in either direction
    assert all(i["axis"] != "locale_tr" for i in res["implicated"])
    text = M.format_matrix(res)
    assert "NOT evidence of no effect" in text
