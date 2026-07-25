"""Local execution backend: same statistics, no cloud, no egress.

This is the backend that makes Retrial adoptable. Every incumbent is ingest-only
— you upload JUnit XML and no source leaves your CI — so a hosted service that
EXECUTES your tests is a categorically harder sell. Running the measurement on
the customer's own runner sidesteps that instead of trying to engineer past it.
"""
import sys

from retrial.localpool import LocalPool, build_local_command
from retrial.repo import DID_NOT_RUN, FIXTURE_ERROR, TARGET_NOT_IN_REPORT


def test_defaults_to_the_interpreter_running_retrial():
    """`python3` on PATH is usually NOT the venv, and the PATH one has no
    pytest — which surfaces as every trial being a non-verdict."""
    assert sys.executable in build_local_command(node_id="t.py::x")


def test_scores_from_the_junit_report_not_the_exit_code():
    cmd = build_local_command(node_id="t.py::x")
    assert "--junit-xml=" in cmd
    for code in (TARGET_NOT_IN_REPORT, FIXTURE_ERROR, DID_NOT_RUN):
        assert str(code) in cmd
    assert cmd.rstrip().endswith("echo EXIT:$RC")


def test_suite_plus_test_means_order_context():
    """The suite runs; only the target is scored. That is the only way to see
    order-dependent flakiness, which running a node id alone cannot reproduce."""
    cmd = build_local_command(node_id="t.py::x", suite="tests")
    assert "-m pytest tests " in cmd   # the SUITE is what pytest runs
    assert "t.py::x" in cmd            # the target is what gets scored
    cmd_alone = build_local_command(node_id="t.py::x")
    assert "-m pytest t.py::x " in cmd_alone  # no suite -> pytest runs the node id


def test_order_policy_reaches_the_command():
    assert "-p randomly " in build_local_command(node_id="t::x", order="shuffle")
    assert "-p no:randomly" in build_local_command(node_id="t::x", order="fixed")


# ------------------------- the pool surface ------------------------------
def test_implements_the_same_four_methods_the_engine_leases_against():
    p = LocalPool(size=2)
    assert p.warm(2) == 2
    a = p.lease()
    b = p.lease()
    assert a is not b
    p.release(a, reusable=True)
    p.release(b, reusable=True)
    assert p.destroy_all() == 2


def test_a_bad_sandbox_is_not_returned_to_the_pool():
    """Same rule as the cloud pools: a sandbox that misbehaved never serves
    another trial."""
    p = LocalPool(size=4)
    sb = p.lease()
    p.release(sb, reusable=False)
    assert p.destroy_all() == 0


def test_torn_down_pool_refuses_to_rebuild():
    p = LocalPool(size=1)
    p.destroy_all()
    for call in (lambda: p.lease(), lambda: p.warm(1)):
        try:
            call()
        except RuntimeError:
            continue
        raise AssertionError("torn-down pool must refuse")


def test_env_is_inherited_not_read():
    """This repo routes every env READ through settings.py and enforces it with
    a test. Inheriting the parent environment for a subprocess is not a read."""
    assert LocalPool()._env is None
