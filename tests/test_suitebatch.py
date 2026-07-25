"""Suite sweep: every test scored from one run, and non-verdicts kept separate.

The bug this fixes: scoring ONE node id per suite run, when the junit report the
run already produced holds an outcome for every test. For 500 tests x 50 trials
against a 10-minute suite that is ~250,000 sandbox-minutes instead of ~500.
"""
from retrial.suitebatch import _parse_rows, build_batch_command, run_suite_batch


class _Spec:
    slug, ref, install, order = "o/n", "a" * 40, "-e .", "shuffle"
    suite = "tests"
    node_id = "sweep::all"

    @property
    def tarball_url(self):
        return f"https://codeload.github.com/{self.slug}/tar.gz/{self.ref}"


def _row(nid, st):
    return f"\x1fRT\x1f{nid}\x1f{st}"


def test_parses_one_row_per_test():
    out = "\n".join([_row("t.py::a", "PASS"), _row("t.py::b", "FAIL"),
                     _row("t.py::c", "SKIP"), "noise line"])
    assert _parse_rows(out) == {"t.py::a": "PASS", "t.py::b": "FAIL",
                               "t.py::c": "SKIP"}


def test_delimiter_survives_a_hostile_test_name():
    """Node ids contain colons, brackets and spaces. A naive delimiter would
    split the row and mis-attribute an outcome to the wrong test."""
    nid = "tests/t.py::TestX::test_y[a b: c-1]"
    assert _parse_rows(_row(nid, "FAIL")) == {nid: "FAIL"}


class _FakeSandbox:
    def __init__(self, outs):
        self.process = self
        self._outs = outs
        self.id = "sb"

    def exec(self, cmd, timeout=None):
        class R:
            result = self._outs.pop(0)
        return R()


class _FakePool:
    def __init__(self, outs):
        self.sb = _FakeSandbox(outs)
        self.released = []

    def lease(self):
        return self.sb

    def release(self, sb, reusable=True):
        self.released.append(reusable)


def test_every_test_gets_one_observation_per_suite_run():
    runs = ["\n".join([_row("t::a", "PASS"), _row("t::b", "FAIL")]),
            "\n".join([_row("t::a", "FAIL"), _row("t::b", "FAIL")]),
            "\n".join([_row("t::a", "PASS"), _row("t::b", "FAIL")])]
    rep = run_suite_batch(_FakePool(runs), _Spec(), runs=3)
    assert rep["runs_completed"] == 3 and rep["tests_scored"] == 2
    by = {r["test"]: r for r in rep["rows"]}
    assert by["t::b"]["fails"] == 3 and by["t::b"]["trials"] == 3
    assert by["t::a"]["fails"] == 1 and by["t::a"]["trials"] == 3
    # flakiest first
    assert rep["rows"][0]["test"] == "t::b"


def test_skips_and_fixture_errors_leave_the_denominator():
    runs = ["\n".join([_row("t::a", "SKIP"), _row("t::b", "ERR")]),
            "\n".join([_row("t::a", "PASS"), _row("t::b", "FAIL")])]
    rep = run_suite_batch(_FakePool(runs), _Spec(), runs=2)
    by = {r["test"]: r for r in rep["rows"]}
    assert by["t::a"]["trials"] == 1 and by["t::a"]["nonverdict"] == 1
    assert by["t::b"]["trials"] == 1 and by["t::b"]["fails"] == 1
    assert by["t::b"]["nonverdict"] == 1


def test_a_bootstrap_failure_is_counted_and_not_scored():
    rep = run_suite_batch(_FakePool(["BOOTSTRAP:98", _row("t::a", "PASS")]),
                          _Spec(), runs=2)
    assert rep["bootstrap_failures"] == 1
    assert rep["runs_completed"] == 1


def test_a_sandbox_that_produced_nothing_is_not_reused():
    pool = _FakePool(["", _row("t::a", "PASS")])
    run_suite_batch(pool, _Spec(), runs=2)
    assert pool.released == [False, True]


def test_command_bootstraps_once_and_writes_a_junit_report():
    cmd = build_batch_command(_Spec())
    assert cmd.startswith("if [ ! -f ")
    assert "--junit-xml=" in cmd and "-p randomly " in cmd
