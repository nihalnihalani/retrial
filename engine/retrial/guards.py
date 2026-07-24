"""Neutering guard: stop a candidate patch from "winning" by deleting the test.

A flaky-test tournament ranks patches by how rarely they fail. That objective is
trivially gamed: a patch that drops the assertion, swallows it, or exits 0
unconditionally reads 0% flake and would sweep the tournament while detecting
nothing. This guard rejects those patches.

SCOPE — read this before trusting the result. The guard verifies that a
comparison still *governs the exit code*. It does NOT verify that the comparison
still *means what it meant*. Those are different properties and only the second
one is safety. Specifically it does NOT catch a patch that:

  - swaps the call under test for a benign one and rewrites the oracle to match
    (`rearrange(t, model.random_order)` -> `model.original_order`, then edits
    `expected`) — the shape that won the penman experiment in `seeds/real/`;
  - changes only the expected literal (`== "evt-1"` -> `== "evt-0"`);
  - keeps a real but unrelated assertion while making the exit unconditional.

Detecting those needs a semantic preservation check (same call graph / same
public surface exercised), which is not implemented. Treat this as a
DELETED-ASSERTION DETECTOR, and rely on the human promote gate for the rest. Do
not describe it as proof that the patch still tests the failure condition.

Two layers:

1. STATIC (ast): the patched file must contain at least one *real* check — an
   `assert` or a conditional `sys.exit`/`exit` whose condition compares a
   computed value (not two literals) and is not a tautology (`expected = first;
   first == expected`). A bare `sys.exit(0)` is rejected outright, and the
   patched file must carry at least as many real checks as the original (a patch
   may not delete assertions to win).

2. DYNAMIC canary: mutate the patched test so its final comparison is inverted
   (`==`->`!=`, token flip) — this forces the failure branch — and run it ONCE.
   A genuine test MUST now FAIL (exit != 0). If the mutant still passes, the
   patch's assertion is dead code (both branches exit 0, the assert is swallowed,
   etc.): the patch neutered the test and is DISQUALIFIED.
   NOTE the asymmetry that limits this layer: inverting a tautology yields a
   contradiction, which fails — so a tautology satisfies the canary *maximally*.
   Layer 1's tautology rejection exists because of that.

`neutering_check` returns a `NeuteringResult` that is truthy when the patch
survives both layers and falsy (with a `.reason`) when it is disqualified.
"""
import ast

from .trial import run_trial


def inert_seed_reason(code):
    """Return why this file would measure NOTHING if run as a script, or None.

    The engine executes a seed with `python3 /tmp/seed.py` and reads the exit
    code. A pytest-style module — `def test_x(): assert ...` with no top-level
    invocation — merely DEFINES functions and exits 0. Retrial then reruns it 40
    times, observes 0/40, computes a Wilson interval, and reports STABLE ->
    "already stable, nothing to fix".

    That is the worst failure a measurement instrument can have: a confident
    clean bill of health for an input it never measured, with full statistical
    ceremony and no error. It is also the single most likely thing a new user
    feeds it, because it is what every real Python test looks like.

    Detect it statically and refuse, rather than measure nothing and report a
    verdict. Conservative by construction: a file with ANY top-level statement
    that could run the test (a call, an exit, an assert, a loop, an if) is
    accepted, so this can only reject files that provably do nothing."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"seed does not parse as Python ({e.msg} at line {e.lineno})"

    test_defs = [n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test")]
    if not test_defs:
        return None

    # Any top-level statement that is not a pure definition/import/docstring
    # could execute the test, so the file is not provably inert.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, ast.Assign):
            continue  # module-level constant, still doesn't run anything
        return None

    names = ", ".join(n.name for n in test_defs[:3])
    return (f"seed defines test function(s) ({names}) but never calls them at "
            f"module level, so running it as a script executes no test and "
            f"exits 0 — Retrial would measure nothing and report it as stable. "
            f"Retrial runs a self-contained script, not pytest: call the test "
            f"and signal the result with sys.exit(0) or sys.exit(1). "
            f"See seeds/test_dict_order.py for the shape.")


class NeuteringResult:
    """Outcome of a neutering check. Truthy == the patch is a legitimate fix."""

    __slots__ = ("ok", "reason", "stage")

    def __init__(self, ok, reason, stage):
        self.ok = ok
        self.reason = reason
        self.stage = stage  # "static" | "dynamic" | "passed"

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"NeuteringResult(ok={self.ok!r}, stage={self.stage!r}, reason={self.reason!r})"


# Operator inversions that flip a single-comparator Compare's truth value.
_FLIP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}


def _is_exit_call(node):
    """True if `node` is a call to exit/quit/sys.exit/os._exit."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("exit", "quit")
    if isinstance(f, ast.Attribute):
        return f.attr in ("exit", "_exit")
    return False


def _contains_exit(node):
    return any(_is_exit_call(n) for n in ast.walk(node))


def _governing_compares(tree):
    """Every Compare that governs whether the test passes: the condition of a
    conditional exit (`sys.exit(0 if C else 1)` or `if C: sys.exit(...)`) or of an
    `assert`. Deduplicated, in source order."""
    seen = {}
    for node in ast.walk(tree):
        tests = []
        if _is_exit_call(node) and node.args and isinstance(node.args[0], ast.IfExp):
            tests.append(node.args[0].test)
        elif isinstance(node, ast.If) and any(
                _contains_exit(s) for s in node.body + node.orelse):
            tests.append(node.test)
        elif isinstance(node, ast.Assert):
            tests.append(node.test)
        for t in tests:
            for c in ast.walk(t):
                if isinstance(c, ast.Compare):
                    seen[id(c)] = c
    return list(seen.values())


def _alias_map(tree):
    """Map of plain `x = y` name aliases (single Name target, single Name value).

    Used to see through `expected = first` — the shape a tautology takes when a
    patch defines its own oracle from the value it is about to compare."""
    aliases = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Name)):
            aliases[node.targets[0].id] = node.value.id
    return aliases


def _resolve(name, aliases, _depth=0):
    """Follow an alias chain to its root name (cycle- and depth-safe)."""
    while name in aliases and _depth < 16:
        name = aliases[name]
        _depth += 1
    return name


def _is_tautology(compare, aliases):
    """True if this comparison is guaranteed by construction rather than by the
    behaviour under test — both sides are the same expression, or the same value
    reached through `x = y` aliasing.

    `expected = first; sys.exit(0 if first == expected else 1)` is the canonical
    shape: it has a non-literal operand (so it looks like a real check), it is
    load-bearing on the exit code (so the inverted canary dutifully fails), and it
    tests nothing at all."""
    if len(compare.ops) != 1 or not isinstance(compare.ops[0], (ast.Eq, ast.Is)):
        return False
    left, right = compare.left, compare.comparators[0]
    if isinstance(left, ast.Name) and isinstance(right, ast.Name):
        return _resolve(left.id, aliases) == _resolve(right.id, aliases)
    try:
        return ast.unparse(left) == ast.unparse(right)
    except Exception:
        return False


def _real_checks(tree):
    """Governing compares that actually test a computed value: at least one
    operand is not a literal, and the comparison is not a tautology.

    `"a" == "a"` is not a real check, and neither is `first == expected` where
    `expected` was just assigned from `first`."""
    aliases = _alias_map(tree)
    out = []
    for c in _governing_compares(tree):
        operands = [c.left] + list(c.comparators)
        if all(isinstance(o, ast.Constant) for o in operands):
            continue
        if _is_tautology(c, aliases):
            continue
        out.append(c)
    return out


def _is_trivial_exit(tree):
    """True if the module (minus imports) is exactly a bare `sys.exit(0)`."""
    body = [s for s in tree.body if not isinstance(s, (ast.Import, ast.ImportFrom))]
    if len(body) != 1:
        return False
    s = body[0]
    if isinstance(s, ast.Expr) and _is_exit_call(s.value):
        args = s.value.args
        if not args:
            return True
        a = args[0]
        return isinstance(a, ast.Constant) and (a.value == 0 or a.value is None)
    return False


def _mutate_for_canary(patched_code):
    """Return `patched_code` with its final governing comparison inverted (forcing
    the failure branch), or None if there is no single-op comparison to flip."""
    try:
        tree = ast.parse(patched_code)
    except SyntaxError:
        return None
    targets = _governing_compares(tree)
    if not targets:
        return None
    target = targets[-1]                      # the final comparison
    if len(target.ops) != 1:
        return None
    op = type(target.ops[0])
    if op not in _FLIP:
        return None
    target.ops = [_FLIP[op]()]
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree)
    except Exception:
        return None


def neutering_check(original_code, patched_code, pool=None, isolation="process",
                    timeout=60):
    """Decide whether `patched_code` is a legitimate fix or has neutered the test.

    Static analysis always runs. The dynamic canary runs only when a `pool` is
    supplied (it executes one mutated trial in a sandbox); without a pool the
    check is static-only (used by unit tests). Returns a `NeuteringResult`
    (truthy == legitimate). A patch is disqualified when it drops assertions,
    reduces to `sys.exit(0)`, or its mutated form still passes.
    """
    # --- STATIC ---
    try:
        ptree = ast.parse(patched_code)
    except SyntaxError:
        return NeuteringResult(False, "failed neutering guard — patched code does not parse", "static")

    if _is_trivial_exit(ptree):
        return NeuteringResult(
            False, "failed neutering guard — patch is a bare sys.exit(0) with no assertion", "static")

    patched_checks = len(_real_checks(ptree))
    if patched_checks < 1:
        return NeuteringResult(
            False, "failed neutering guard — patch contains no assertion or conditional "
            "exit that tests a value", "static")

    try:
        orig_checks = len(_real_checks(ast.parse(original_code)))
    except SyntaxError:
        orig_checks = 0
    if patched_checks < orig_checks:
        return NeuteringResult(
            False, f"failed neutering guard — patch dropped assertions "
            f"({patched_checks} check(s) < original {orig_checks})", "static")

    # --- DYNAMIC canary ---
    if pool is not None:
        mutated = _mutate_for_canary(patched_code)
        if mutated is not None:
            res = run_trial(pool, mutated, timeout=timeout, isolation=isolation)
            # Only a clean (non-infra) run is trustworthy; an infra error never
            # disqualifies a real fix.
            if res.get("error") is None and res.get("passed"):
                return NeuteringResult(
                    False, "failed neutering guard — patch no longer detects the "
                    "failure condition", "dynamic")

    return NeuteringResult(True, "ok", "passed")
