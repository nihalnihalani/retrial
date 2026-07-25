"""A seed that executes no test must be REFUSED, not measured.

The engine runs `python3 /tmp/seed.py` and reads the exit code. A pytest-style
module defines functions and exits 0, so Retrial would rerun it 40 times, observe
0/40, compute a Wilson interval and report STABLE -> "already stable, nothing to
fix": a confident clean bill of health for an input it never executed. It is also
the single most likely thing a new user feeds it, since it is what every real
Python test looks like.
"""
import pytest

from retrial.guards import inert_seed_reason

PYTEST_STYLE = "def test_thing():\n    assert 1 == 2\n"


def test_pytest_style_module_is_refused():
    reason = inert_seed_reason(PYTEST_STYLE)
    assert reason is not None
    assert "nothing at module level runs them" in reason
    assert "sys.exit" in reason  # the message must say how to fix it


def test_docstring_imports_and_constants_do_not_rescue_it():
    code = ('"""doc"""\nimport os\nX = 1\n'
            'def test_a():\n    assert os\n')
    assert inert_seed_reason(code) is not None


def test_unparseable_seed_is_refused_with_the_syntax_error():
    reason = inert_seed_reason("def test_a(:\n")
    assert reason is not None and "does not parse" in reason


@pytest.mark.parametrize("code", [
    "def test_a():\n    assert 1 == 1\ntest_a()\n",          # explicit call
    "import sys\ndef test_a():\n    assert 1 == 2\n"
    "if True:\n    test_a()\n",                              # guarded call
    "import sys\nsys.exit(0)\n",                             # no test defs
    # An Assign's value is an arbitrary expression. Skipping Assign as an
    # "inert constant" refused these with a message insisting the file never
    # calls its tests, on the line that calls them.
    "def test_x():\n    assert 1 == 2\n_r = test_x()\n",
    "def test_x():\n    assert 1 == 2\ny = [test_x()]\n",
    # Decorators run at import and may invoke what they wrap.
    "def runner(f):\n    f()\n    return f\n@runner\ndef test_x():\n"
    "    assert 1 == 2\n",
])
def test_anything_that_could_execute_is_accepted(code):
    """Conservative by construction — only provably inert files are refused."""
    assert inert_seed_reason(code) is None


def test_the_repo_s_own_seeds_are_accepted():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "seeds"
    for p in list(root.glob("test_*.py")) + [root / "real" / "penman_live.py"]:
        assert inert_seed_reason(p.read_text()) is None, p.name


@pytest.mark.parametrize("code", [
    # Class-based tests are ~half of real pytest suites and were invisible to a
    # scan that only walked top-level FunctionDef — they hit the exact silent
    # "already stable" failure this guard exists to prevent.
    "class TestOrder:\n    def test_first(self):\n        assert 1 == 2\n",
    # Statement kinds that are not calls and still do not run the test.
    "def test_a():\n    assert 1 == 2\nx: int = 1\n",
    "x = 0\ndef test_a():\n    assert 1 == 2\nx += 1\n",
])
def test_inert_shapes_that_previously_slipped_through(code):
    assert inert_seed_reason(code) is not None
