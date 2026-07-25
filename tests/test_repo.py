"""Real-repo measurement: the validation and exit-code mapping that keep it honest.

The failure this file exists to prevent: pytest's exit codes are not boolean, and
a tool that treats every non-zero as "the test failed" will report a typo'd node
id as a 100% flaky test — a precise, confident lie about a test it never ran.
"""
import pytest

from retrial.repo import (BOOTSTRAP_FAILED, DID_NOT_RUN, REPO_DIR, RepoSpec,
                          build_command, build_suite_command, diagnose_exit)

SHA = "7770dfe14b3d0d197cedc6640f3ff7e3bd695726"


# ----------------------------- RepoSpec ---------------------------------
def test_accepts_slug_and_full_url_identically():
    a = RepoSpec("goodmami/penman", SHA, "tests/t.py::test_x")
    b = RepoSpec("https://github.com/goodmami/penman", SHA, "tests/t.py::test_x")
    c = RepoSpec("https://github.com/goodmami/penman.git", SHA, "tests/t.py::test_x")
    assert a.slug == b.slug == c.slug == "goodmami/penman"


@pytest.mark.parametrize("ref", ["main", "v1.2.1", "7770dfe", "", "HEAD",
                                 "7770dfe14b3d0d197cedc6640f3ff7e3bd69572"])
def test_a_moving_or_short_ref_is_refused(ref):
    """A branch would let the source change underneath a 50-trial run, so the
    trials would not be measuring the same thing. Refuse up front."""
    with pytest.raises(ValueError, match="40-character commit sha"):
        RepoSpec("owner/name", ref, "tests/t.py::test_x")


@pytest.mark.parametrize("slug", ["", "notaslug", "a/b/c", "owner/", "/name"])
def test_malformed_repo_is_refused(slug):
    with pytest.raises(ValueError):
        RepoSpec(slug, SHA, "tests/t.py::test_x")


@pytest.mark.parametrize("node", ["", "   ", "--co-me-from-flags"])
def test_malformed_node_id_is_refused(node):
    with pytest.raises(ValueError):
        RepoSpec("owner/name", SHA, node)


def test_tarball_url_pins_the_sha():
    spec = RepoSpec("goodmami/penman", SHA, "tests/t.py::test_x")
    assert spec.tarball_url == f"https://codeload.github.com/goodmami/penman/tar.gz/{SHA}"


# --------------------------- exit-code mapping --------------------------
def test_zero_and_one_are_the_only_verdicts():
    assert diagnose_exit(0) is None
    assert diagnose_exit(1) is None


def test_no_tests_collected_is_diagnosed_not_counted_as_failure():
    """pytest exit 5 = a wrong node id. The single most likely operator error,
    and the one that would otherwise read as a 100% flaky test."""
    msg = diagnose_exit(5)
    assert msg and "NO TESTS" in msg and "node id does not exist" in msg


@pytest.mark.parametrize("code,needle", [
    (2, "interrupted"), (3, "internal error"), (4, "usage error"),
    (BOOTSTRAP_FAILED, "bootstrap failed"),
])
def test_every_non_verdict_code_names_its_cause(code, needle):
    assert needle in diagnose_exit(code)


def test_unknown_code_still_reports_as_harness_failure():
    assert "not a test result" in diagnose_exit(42)


# ------------------------------ command ---------------------------------
def test_command_is_idempotent_and_self_healing():
    """Bootstrap is guarded by a marker file, so a pooled sandbox pays it once
    and a fresh one heals itself — with no separate setup phase to drift."""
    cmd = build_command(RepoSpec("owner/name", SHA, "tests/t.py::test_x"))
    assert cmd.startswith("if [ ! -f ")
    assert ".retrial-ready" in cmd
    assert cmd.count("EXIT:") == 2  # bootstrap-failure marker + the real verdict


def test_command_quotes_a_hostile_node_id():
    nasty = "tests/t.py::test_x[a b]; rm -rf /"
    cmd = build_command(RepoSpec("owner/name", SHA, nasty))
    assert "; rm -rf /" not in cmd.replace(f"'{nasty}'", "")
    assert f"'{nasty}'" in cmd or '"' + nasty + '"' in cmd


def test_command_disables_plugins_that_would_add_variance():
    """pytest-randomly shuffles order; cacheprovider writes state between runs.
    Both inject variance into the exact thing being measured."""
    cmd = build_command(RepoSpec("owner/name", SHA, "tests/t.py::test_x"))
    assert "-p no:randomly" in cmd and "-p no:cacheprovider" in cmd


def test_command_runs_inside_the_repo_dir():
    cmd = build_command(RepoSpec("owner/name", SHA, "tests/t.py::test_x"))
    assert f"cd {REPO_DIR}" in cmd and "python3 -m pytest" in cmd


def test_a_skipped_test_is_not_a_pass():
    """pytest exits 0 for skipped and xfailed too. A sandbox has no database,
    credentials or services, so skipif-gated tests are common — and scoring them
    as passes reports a clean bill of health for a test that never ran. The
    junit report distinguishes them; the exit code cannot."""
    cmd = build_command(RepoSpec("owner/name", SHA, "tests/t.py::test_x"))
    assert "skipped" in cmd
    assert str(DID_NOT_RUN) in cmd


def test_did_not_run_is_diagnosed_and_excluded():
    msg = diagnose_exit(DID_NOT_RUN)
    assert msg and "did not actually run" in msg
    assert "rather than counted as a pass" in msg


def test_suite_mode_treats_a_skipped_target_as_a_non_verdict():
    assert "skipped" in build_suite_command(
        RepoSpec("owner/name", SHA, "tests/t.py::test_x", suite="tests"))


# ---------------------------- suite mode --------------------------------
from retrial.repo import TARGET_NOT_IN_REPORT, build_suite_command


def _suite_spec():
    return RepoSpec("owner/name", SHA, "tests/t.py::test_x", suite="tests")


def test_suite_mode_is_recorded_in_the_spec():
    assert _suite_spec().as_dict()["mode"] == "suite"
    assert RepoSpec("owner/name", SHA, "tests/t.py::test_x").as_dict()["mode"] == "isolated"


def test_shuffle_uses_pytest_randomly_not_pytest_random_order():
    """NOT interchangeable. pytest-randomly also RESEEDS the global RNG before
    each test, and shared-RNG state is the mechanism behind the largest real
    order-dependency class. Measured on penman's real test_rearrange: 0/40 fail
    in fixed order, 39/40 fail under pytest-randomly. A tool that only shuffled
    ORDER would report that flake as stable."""
    shuffled = build_command(RepoSpec("o/n", SHA, "t.py::x", order="shuffle"))
    assert "pytest-randomly" in shuffled and "-p randomly " in shuffled
    fixed = build_command(RepoSpec("o/n", SHA, "t.py::x", order="fixed"))
    assert "-p no:randomly" in fixed and "pytest-randomly" not in fixed


def test_order_is_part_of_the_spec_and_validated():
    assert RepoSpec("o/n", SHA, "t.py::x").order == "fixed"
    assert RepoSpec("o/n", SHA, "t.py::x", order="shuffle").as_dict()["order"] == "shuffle"
    with pytest.raises(ValueError, match="fixed"):
        RepoSpec("o/n", SHA, "t.py::x", order="random")


def test_suite_command_scores_only_the_target_not_the_suite_exit_code():
    """A suite's exit code reflects EVERY test in it, so an unrelated failure
    elsewhere would be scored against the target."""
    cmd = build_suite_command(_suite_spec())
    assert "--junit-xml=" in cmd
    assert "testcase" in cmd  # the extractor reads per-test outcomes


def test_target_missing_from_the_report_is_a_non_verdict():
    """Never collected, or the suite died first — excluded from the denominator
    rather than counted as a failure."""
    msg = diagnose_exit(TARGET_NOT_IN_REPORT)
    assert msg and "not scored as a failure" in msg.lower()


def test_fixture_error_is_separated_from_a_real_failure():
    """pytest exits 1 for BOTH "the test failed" and "a fixture raised in setup".
    Measured: assert 1==2 -> failures=1 errors=0; fixture raises -> failures=0
    errors=1. Scoring the second as a failure is how a tool says "REGRESSION,
    fix the code" about its own broken bootstrap."""
    from retrial.repo import FIXTURE_ERROR
    msg = diagnose_exit(FIXTURE_ERROR)
    assert msg and "fixture" in msg and "not a flaky test" in msg
    assert "error" in build_command(RepoSpec("o/n", SHA, "t.py::x"))
