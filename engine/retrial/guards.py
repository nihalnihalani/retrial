"""Neutering guard: stop a candidate patch from "winning" by deleting the test.

A flaky-test tournament ranks patches by how rarely they fail. That objective is
trivially gamed: a patch that drops the assertion, swallows it, or exits 0
unconditionally reads 0% flake and would sweep the tournament while detecting
nothing. This guard makes a patch prove it still tests the failure condition
before it is allowed to win.

Two layers:

1. STATIC (ast): the patched file must contain at least one *real* check — an
   `assert` or a conditional `sys.exit`/`exit` whose condition compares a
   computed value (not two literals). A bare `sys.exit(0)` is rejected outright,
   and the patched file must carry at least as many real checks as the original
   (a patch may not delete assertions to win).

2. DYNAMIC canary: mutate the patched test so its final comparison is inverted
   (`==`->`!=`, token flip) — this forces the failure branch — and run it several
   times. A genuine test MUST now FAIL (exit != 0), and a real detector fails the
   inverted comparison DETERMINISTICALLY, so the mutant failing even once proves
   the assertion is live. Only when the mutant still passes on EVERY clean
   (non-infra) attempt is its assertion dead code (both branches exit 0, the
   assert is swallowed, etc.): the patch neutered the test and is DISQUALIFIED.
   Infra errors are non-disqualifying and don't count as evidence.

`neutering_check` returns a `NeuteringResult` that is truthy when the patch is a
legitimate fix and falsy (with a `.reason`) when it is disqualified.
"""
import ast

from .trial import run_trial


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


def _real_checks(tree):
    """Governing compares that actually test a computed value (at least one
    operand is not a literal). `"a" == "a"` is not a real check."""
    out = []
    for c in _governing_compares(tree):
        operands = [c.left] + list(c.comparators)
        if any(not isinstance(o, ast.Constant) for o in operands):
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
    supplied (it executes several mutated trials in a sandbox); without a pool the
    check is static-only (used by unit tests). Returns a `NeuteringResult`
    (truthy == legitimate). A patch is disqualified when it drops assertions,
    reduces to `sys.exit(0)`, or its mutated form still passes on every clean
    attempt.
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
            # A SINGLE canary run can wrongly disqualify a genuine but
            # NON-deterministic winning fix that merely happens to pass its
            # inverted comparison once. A real detector fails the inverted
            # comparison DETERMINISTICALLY, so run the canary several times: the
            # mutant failing even once proves the assertion is live and the patch
            # is legitimate. Only disqualify when EVERY clean (non-infra) attempt
            # still passes. Infra errors never disqualify and don't count as
            # evidence (an all-infra run leaves the patch un-disqualified).
            canary_attempts = 5
            saw_clean_run = False
            detector_fired = False
            for _ in range(canary_attempts):
                res = run_trial(pool, mutated, timeout=timeout, isolation=isolation)
                if res.get("error") is not None:
                    continue  # infra error: not trustworthy evidence, skip
                saw_clean_run = True
                if not res.get("passed"):
                    detector_fired = True  # mutant failed -> detector is live
                    break
            if saw_clean_run and not detector_fired:
                return NeuteringResult(
                    False, "failed neutering guard — patch no longer detects the "
                    "failure condition", "dynamic")

    return NeuteringResult(True, "ok", "passed")
