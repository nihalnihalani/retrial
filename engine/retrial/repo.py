"""Run a REAL test from a REAL repository, instead of a self-contained seed file.

Until now a "test" was one Python file that Retrial wrote to /tmp and executed.
That is the whole reason the tool could not be pointed at anybody's code. This
module makes the unit of measurement `(repo, ref, pytest node id)`.

MEASURED in the default Daytona container (2026-07-25), which is what the design
rests on:

    which git curl tar python3 pip   -> all present (git IS available)
    python3 -c 'import pytest'       -> ModuleNotFoundError (pytest is NOT)
    codeload tarball at a pinned sha -> 0.8s
    pip install -e . pytest          -> 5.3s
    pytest 'tests/test_layout.py::test_rearrange' -q -> 2.5s, exit 0

So the bootstrap is ~6s and the marginal trial is ~2.5s.

Two design decisions follow from those numbers:

1. TARBALL, NOT CLONE. `codeload.github.com/{owner}/{repo}/tar.gz/{sha}` needs no
   git, no auth for public repos, and no history — 0.8s versus a full clone. The
   ref is a pinned sha, so every trial in a run measures byte-identical source.

2. BOOTSTRAP IS IDEMPOTENT AND SELF-HEALING, NOT A SEPARATE PHASE. The trial
   command checks for a marker file and bootstraps only if it is absent. Under
   `isolation=process` a pooled sandbox pays the ~6s once and every later trial
   reuses it; a fresh or recycled sandbox heals itself with no coordination and
   no extra Daytona round-trip. There is no setup step that can drift out of
   sync with the pool.

WHAT RUNNING ONE NODE ID IN ISOLATION CAN AND CANNOT SEE — read this before
believing any verdict this module produces.

Gruber et al., ICST 2021, 7,571 flaky tests mined from 22,352 PyPI projects
(they rerun the WHOLE suite 200x in order and 200x shuffled; they never rerun a
test alone to detect flakiness):

    order-dependent            4,461  59%   <- of which 3,168 are "victims"
                                              (always PASS alone) and 738 are
                                              "brittles" (always FAIL alone)
    test-infrastructure        2,158  28%
    non-order-dependent (NOD)    952  13%

So 3,906 of 7,571 flaky tests — **51.6%** — are provably DETERMINISTIC when run
alone. Only the 952 NOD tests are plausibly reproducible by rerunning one test.

Measured directly, in Java: Lam et al., ISSRE 2020 reran each flaky test 4,000
times in isolation. **50 of 107 (46.7%) reproduced; 53.3% did not reproduce even
at 4,000 isolated reruns.**

That means isolation fails in BOTH directions, and the second one is worse:

    a "victim"  passes 100% alone  -> Retrial reports STABLE / ALREADY_STABLE
                                      ("nothing to fix") for a genuinely flaky test
    a "brittle" fails  100% alone  -> Retrial reports ALWAYS_FAILING -> REGRESSION
                                      ("fix the code, not the test") for a test
                                      that is fine when its setter runs first

Neither is a hedge; both are confident, precise, wrong answers. This is why the
CLI states the limit in --help rather than leaving it implicit, and why suite-
context measurement (running a prefix or the whole suite, which is what
`bisect.py` already does) is the next capability rather than a nice-to-have.

Also worth knowing before quoting a trial budget: Gruber measured that **≥170
reruns** are needed for 95% confidence that a test is NOT NOD-flaky (31 shuffled
suite runs for OD). Retrial's default of 50 buys a bound on the 10% threshold,
not a clean bill of health.

The one thing this design does BETTER than suite-rerun tools: a fresh sandbox
per trial is exactly the instrument FlaPy uses to separate the 28%
infrastructure bucket, which iDFlakies-style suite reordering cannot distinguish
at all.

PYTEST EXIT CODES ARE NOT BOOLEAN, and getting this wrong is how a measurement
tool lies. pytest reserves:

    0  all tests passed
    1  tests were collected and run, some FAILED     <- the only real "fail"
    2  interrupted by the user or an internal error
    3  internal error
    4  command-line usage error
    5  NO TESTS WERE COLLECTED                       <- a wrong node id!

Only 0 and 1 are verdicts. 5 in particular is the trap: a typo'd node id, a
renamed test, or a wrong ref produces "no tests collected", and any tool that
treats non-zero as failure would report that as a 100% flaky test — a confident,
precise lie about a test it never ran. Retrial already excludes non-{0,1} exits
from the flake-rate denominator; this module adds the specific diagnosis so the
operator is told *which* of those things happened.
"""
import re
import shlex

# Where the repo is unpacked inside the sandbox. /tmp is the only reliably
# writable path in the container image.
REPO_DIR = "/tmp/retrial-repo"
_READY = f"{REPO_DIR}/.retrial-ready"

# A pinned 40-char commit sha. Deliberately not a branch or tag: every trial in a
# run must measure the same bytes, and a moving ref silently invalidates the
# statistics halfway through a 50-trial run.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Exit codes pytest reserves for "I did not produce a verdict". Mapped to human
# diagnoses so an operator sees the cause instead of a mysterious infra error.
PYTEST_NON_VERDICT = {
    2: "pytest was interrupted (exit 2) — a KeyboardInterrupt or an internal stop",
    3: "pytest hit an internal error (exit 3) — usually a broken conftest.py",
    4: "pytest usage error (exit 4) — bad arguments or an unreadable path",
    5: ("pytest collected NO TESTS (exit 5) — the node id does not exist at this "
        "ref. Check the path, the test name, and whether the test was renamed "
        "or parametrised."),
}
# Retrial's own bootstrap failures, chosen outside pytest's range.
BOOTSTRAP_FAILED = 98
TARGET_NOT_IN_REPORT = 96
# pytest exits 0 for a test that was SKIPPED or XFAILED — it never ran, but the
# exit code is indistinguishable from a pass. A Daytona container has no
# database, no credentials, no services and no $DISPLAY, so a large share of any
# real suite is skipif-gated on exactly those. Scoring those as passes would
# produce "<=7% at 95% confidence" about an experiment that never happened.
DID_NOT_RUN = 95
BOOTSTRAP_DIAGNOSIS = {
    BOOTSTRAP_FAILED: ("repo bootstrap failed — could not download the ref or "
                       "install dependencies in the sandbox"),
    TARGET_NOT_IN_REPORT: ("the target test was not in the suite's junit report "
                           "— it was never collected, or the suite died before "
                           "reaching it. Not scored as a failure."),
    DID_NOT_RUN: ("the test did not actually run — pytest exited 0 but reported "
                  "no passing test (skipped, xfailed, or deselected). A sandbox "
                  "has no database, credentials or services, so skipif-gated "
                  "tests land here. Excluded from the flake rate rather than "
                  "counted as a pass."),
}


class RepoSpec:
    """What to measure: one pytest node id, in one repo, at one pinned commit."""

    __slots__ = ("slug", "ref", "node_id", "install", "suite")

    def __init__(self, slug, ref, node_id, install=None, suite=None):
        slug = (slug or "").strip()
        # Accept a full GitHub URL as a convenience; store the slug.
        m = re.match(r"^(?:https?://github\.com/)?([^/]+/[^/]+?)(?:\.git)?/?$", slug)
        if m:
            slug = m.group(1)
        if not _SLUG_RE.match(slug):
            raise ValueError(f"repo must be owner/name (got {slug!r})")
        ref = (ref or "").strip().lower()
        if not _SHA_RE.match(ref):
            raise ValueError(
                f"ref must be a full 40-character commit sha, not a branch or "
                f"tag (got {ref!r}). A moving ref would let the source change "
                f"underneath a run and silently invalidate the statistics.")
        node_id = (node_id or "").strip()
        if not node_id or node_id.startswith("-"):
            raise ValueError(f"test must be a pytest node id (got {node_id!r})")
        self.slug, self.ref, self.node_id = slug, ref, node_id
        # How to install the project. `-e .` covers the common case; a caller can
        # override for repos that need requirements files or extras.
        self.install = install or "-e ."
        # When set, trials run the WHOLE suite at this path in a randomised
        # order and score only `node_id` — the only way to see order-dependent
        # flakiness, which isolation structurally cannot reproduce.
        self.suite = (suite or "").strip() or None

    @property
    def tarball_url(self):
        return f"https://codeload.github.com/{self.slug}/tar.gz/{self.ref}"

    def label(self):
        return f"{self.slug}@{self.ref[:7]} :: {self.node_id}"

    def as_dict(self):
        return {"repo": self.slug, "ref": self.ref, "test": self.node_id,
                "install": self.install, "suite": self.suite,
                "mode": "suite" if self.suite else "isolated"}


# Extracts ONE test's outcome from a whole-suite junit report and re-emits it as
# a 0/1 exit code, so suite-context trials flow through the same verdict channel
# as isolated ones. Without this, a suite run's exit code reflects every test in
# the suite, and a different test failing would be scored against the target.
_EXTRACT = (
    "import sys,glob,xml.etree.ElementTree as E;"
    "f=glob.glob('/tmp/retrial-r.xml');"
    "sys.exit(96) if not f else None;"
    "r=E.parse(f[0]).getroot();"
    "cs=[c for c in r.iter('testcase') "
    "if (c.get('file','')+'::'+c.get('classname','').split('.')[-1]+'::'+c.get('name','')) "
    "  .endswith(TARGET) or c.get('name')==TARGET.split('::')[-1]];"
    "sys.exit(96) if not cs else None;"
    "c=cs[0];"
    "sys.exit(1 if (c.find('failure') is not None or c.find('error') is not None) "
"     else (95 if c.find('skipped') is not None else 0))"
)


def build_suite_command(spec, preview_tail=""):
    """Run the whole suite in a RANDOMISED order and score only the target test.

    This is the answer to the isolation blind spot documented at the top of this
    module: ~51.6% of flaky tests are deterministic when run alone, because their
    flakiness comes from what ran BEFORE them. Reproducing that requires suite
    context, and randomised order is how iDFlakies and FlaPy expose it.

    The suite's own exit code is useless here — it reflects every test in the
    suite, so an unrelated failure elsewhere would be scored against the target.
    Instead the suite writes a junit report and a tiny extractor re-emits ONLY
    the target's outcome as 0/1. If the target is absent from the report (never
    collected, or the suite died before reaching it) that is exit 96 — a
    non-verdict, excluded from the flake-rate denominator rather than counted as
    a failure.
    """
    url = shlex.quote(spec.tarball_url)
    suite = shlex.quote(spec.suite or ".")
    target_py = spec.node_id.replace("\\", "\\\\").replace("'", "\\'")
    extract = shlex.quote(f"TARGET='{target_py}';" + _EXTRACT)
    install = spec.install
    return (
        f"if [ ! -f {_READY} ]; then "
        f"  rm -rf {REPO_DIR} && mkdir -p {REPO_DIR} && "
        f"  curl -sSL --fail {url} | tar xz -C {REPO_DIR} --strip-components=1 && "
        f"  cd {REPO_DIR} && "
        f"  python3 -m pip install --quiet --disable-pip-version-check {install} "
        f"    pytest pytest-random-order >/dev/null 2>&1 && "
        f"  touch {_READY} || {{ echo EXIT:{BOOTSTRAP_FAILED}; exit 0; }}; "
        f"fi; "
        f"cd {REPO_DIR} && rm -f /tmp/retrial-r.xml; "
        # --random-order re-shuffles per process, so each trial is a different
        # order — which is exactly the axis an order-dependent flake lives on.
        f"python3 -m pytest {suite} -q -p no:cacheprovider "
        f"--random-order --junitxml=/tmp/retrial-r.xml >/dev/null 2>&1; "
        f"python3 -c {extract}; RC=$?; "
        f"{preview_tail}echo EXIT:$RC"
    )


def build_command(spec, preview_tail=""):
    """The single exec that bootstraps-if-needed and runs one trial.

    One round-trip, like the seed path — the bootstrap rides inside the first
    trial rather than costing a separate call. `set -e` is deliberately NOT used:
    every failure mode is mapped to an explicit exit code so nothing reaches the
    verdict parser as an ambiguous non-zero.
    """
    url = shlex.quote(spec.tarball_url)
    node = shlex.quote(spec.node_id)
    install = spec.install  # operator-supplied pip args, not user input
    return (
        f"if [ ! -f {_READY} ]; then "
        f"  rm -rf {REPO_DIR} && mkdir -p {REPO_DIR} && "
        f"  curl -sSL --fail {url} | tar xz -C {REPO_DIR} --strip-components=1 && "
        f"  cd {REPO_DIR} && "
        f"  python3 -m pip install --quiet --disable-pip-version-check {install} pytest "
        f"    >/dev/null 2>&1 && "
        f"  touch {_READY} || {{ echo EXIT:{BOOTSTRAP_FAILED}; exit 0; }}; "
        f"fi; "
        # -p no:randomly / -p no:cacheprovider: a plugin that shuffles order or
        # writes .pytest_cache would add variance Retrial did not ask for, and
        # variance is the thing being measured.
        f"cd {REPO_DIR}; "
        f"OUT=$(python3 -m pytest {node} -q -p no:randomly -p no:cacheprovider 2>&1); "
        f"RC=$?; "
        # An exit of 0 is only a PASS if pytest actually passed something. All
        # -skipped / all-xfailed / all-deselected also exit 0, and scoring those
        # as passes is how a tool reports a clean bill of health for a test that
        # never ran.
        f"if [ $RC -eq 0 ] && ! printf '%s' \"$OUT\" | grep -qE '[0-9]+ passed'; "
        f"then RC={DID_NOT_RUN}; fi; "
        f"{preview_tail}echo EXIT:$RC"
    )


def diagnose_exit(code):
    """Human diagnosis for a non-verdict exit code, or None if 0/1."""
    if code in (0, 1):
        return None
    return (PYTEST_NON_VERDICT.get(code)
            or BOOTSTRAP_DIAGNOSIS.get(code)
            or f"non-verdict exit code {code} (harness failure, not a test result)")
