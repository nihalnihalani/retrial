"""Run trials HERE — on this machine, against the checkout that already exists.

Why this exists, and why it is arguably the most important backend.

Every incumbent in this market is ingest-only: you upload JUnit XML and no source
code leaves your infrastructure. Their security review is a questionnaire. A
hosted service that EXECUTES your tests is a categorically harder sell — it wants
your private source, a repo-scoped token, and trust in someone else's sandbox
isolation. Retrial's whole value is that it executes, so that gap is structural
and cannot be engineered away. It can only be sidestepped: run the measurement
where the code already is.

That is this module. In a GitHub Action, `actions/checkout` has already put the
source on the runner and the customer already pays for the compute. What leaves
their infrastructure is a rate and an interval.

IT IS A POOL, NOT A SPECIAL CASE. `LocalPool` implements the same four methods
the rest of the engine leases against — `warm`, `lease`, `release`, `destroy_all`
— so `verify()`, the Wilson maths, the adaptive early-stop, the verdict table,
the infra-error exclusion and the junit verdict rules are all reused unchanged.
Nothing here re-implements statistics, which is exactly the property that keeps
two backends from drifting apart.

WHAT IT DOES NOT GIVE YOU, stated plainly:

- **No isolation.** A Daytona sandbox is a fresh filesystem per trial (or per
  pool). Here, trials share one machine and one filesystem. A test that writes
  `/tmp/cache.json` pollutes the next trial exactly as it would in CI — which is
  faithful to what the customer's CI does, and is NOT the controlled environment
  `isolation="sandbox"` provides.
- **No environment axes worth trusting.** `matrix` perturbs PYTHONHASHSEED, TZ
  and locale. Locally those hit whatever this machine happens to have installed,
  so an axis can be inert for reasons that have nothing to do with the test. The
  probe mechanism in `matrix.py` catches that and reports UNAVAILABLE — but the
  sandbox backend is the one to trust for attribution.
- **Concurrency is the machine's, not 16 disposable VMs.** Parallel trials on one
  host contend for CPU, and CPU contention is itself a flake mechanism. Default
  concurrency here is deliberately low.
"""
import os.path
import shlex
import sys
import subprocess
import threading

from .repo import DID_NOT_RUN, FIXTURE_ERROR, TARGET_NOT_IN_REPORT, _order_flags

_JUNIT = "retrial-local-junit.xml"

# Same extractor contract as repo.py: read the junit report, not the exit code,
# because pytest's exit 1 means BOTH "the test failed" and "a fixture errored",
# and its exit 0 includes skipped and xfailed.
_VERDICT_PY = r"""
import sys, os, xml.etree.ElementTree as E
p = %(xml)r
if not os.path.exists(p):
    sys.exit(%(no_report)d)
r = E.parse(p).getroot()
cases = list(r.iter('testcase'))
t = %(target)r
if t:
    leaf = t.split('::')[-1]
    cases = [c for c in cases
             if c.get('name') == leaf or (c.get('name') or '').startswith(leaf + '[')]
if not cases:
    sys.exit(%(no_report)d)
c = cases[0]
if c.find('error') is not None:
    sys.exit(%(infra)d)
if c.find('skipped') is not None:
    sys.exit(%(didnt_run)d)
if c.find('failure') is not None:
    sys.exit(1)
sys.exit(0)
"""


def build_local_command(node_id=None, suite=None, order="fixed", python=None):
    """One shell command: run pytest here, score from the junit report.

    `suite` with `node_id` gives ORDER CONTEXT — the suite runs, only the target
    is scored. That is the only way to see order-dependent flakiness, which is
    roughly half of real flaky tests and which running a node id alone
    structurally cannot reproduce.
    """
    # Default to the interpreter running Retrial, not whatever `python3` resolves
    # to on PATH. In a venv or an Action those differ, and the PATH one usually
    # has no pytest — which surfaces as every trial being a non-verdict.
    python = python or sys.executable or "python3"
    target = node_id if (suite and node_id) else None
    what = shlex.quote(suite) if suite else shlex.quote(node_id or ".")
    body = _VERDICT_PY % {
        "xml": _JUNIT,
        "no_report": TARGET_NOT_IN_REPORT,
        "infra": FIXTURE_ERROR,
        "didnt_run": DID_NOT_RUN,
        "target": (target or ""),
    }
    return (
        f"rm -f {_JUNIT}; "
        f"{python} -m pytest {what} -q -p no:cacheprovider --tb=no "
        f"{_order_flags(order)}--junit-xml={_JUNIT} >/dev/null 2>&1; "
        f"{python} -c {shlex.quote(body)}; RC=$?; echo EXIT:$RC"
    )


class _LocalProcess:
    """Duck-types Daytona's `sandbox.process`, so `run_trial` cannot tell the
    difference and no statistics code needs a branch for local execution."""

    def __init__(self, cwd, env):
        self._cwd, self._env = cwd, env

    def exec(self, cmd, timeout=60):
        r = subprocess.run(["/bin/sh", "-c", cmd], cwd=self._cwd, env=self._env,
                           capture_output=True, text=True, timeout=timeout)

        class _R:
            # stdout carries the EXIT marker; stderr is folded in so a crash is
            # visible in log_tail rather than silently discarded.
            result = (r.stdout or "") + (r.stderr or "")
        return _R()


class _LocalSandbox:
    def __init__(self, idx, cwd, env):
        self.id = f"local-{idx}"
        self.process = _LocalProcess(cwd, env)


class LocalPool:
    """The pool surface, backed by this machine.

    Deliberately no `public`, no auto-delete, no preview: those are properties of
    disposable cloud sandboxes and pretending to have them here would be a lie in
    the Observatory.
    """

    def __init__(self, cwd=".", size=4, env=None):
        self._cwd = os.path.abspath(cwd)
        self._size = max(1, int(size))
        # None => the subprocess inherits this process's environment. Passing
        # os.environ explicitly would be an env READ, and this repo routes every
        # one of those through settings.py (there is a test that enforces it).
        # Inheritance is not configuration.
        self._env = dict(env) if env else None
        self._lock = threading.Lock()
        self._available = []
        self._n = 0
        self._torn_down = False

    def warm(self, n):
        """No cold start to pay — the machine is already warm. Kept so callers
        need no branch."""
        if self._torn_down:
            raise RuntimeError("local pool torn down")
        with self._lock:
            want = min(int(n), self._size)
            while len(self._available) < want:
                self._n += 1
                self._available.append(_LocalSandbox(self._n, self._cwd, self._env))
            return len(self._available)

    def lease(self):
        if self._torn_down:
            raise RuntimeError("local pool torn down")
        with self._lock:
            if self._available:
                return self._available.pop()
            self._n += 1
            return _LocalSandbox(self._n, self._cwd, self._env)

    def release(self, sb, reusable=True):
        if sb is None:
            return
        with self._lock:
            if reusable and len(self._available) < self._size:
                self._available.append(sb)

    def destroy_all(self):
        with self._lock:
            n = len(self._available)
            self._available.clear()
            self._torn_down = True
        return n
