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
BOOTSTRAP_DIAGNOSIS = {
    BOOTSTRAP_FAILED: ("repo bootstrap failed — could not download the ref or "
                       "install dependencies in the sandbox"),
}


class RepoSpec:
    """What to measure: one pytest node id, in one repo, at one pinned commit."""

    __slots__ = ("slug", "ref", "node_id", "install")

    def __init__(self, slug, ref, node_id, install=None):
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

    @property
    def tarball_url(self):
        return f"https://codeload.github.com/{self.slug}/tar.gz/{self.ref}"

    def label(self):
        return f"{self.slug}@{self.ref[:7]} :: {self.node_id}"

    def as_dict(self):
        return {"repo": self.slug, "ref": self.ref, "test": self.node_id,
                "install": self.install}


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
        f"cd {REPO_DIR} && python3 -m pytest {node} -q "
        f"-p no:randomly -p no:cacheprovider >/dev/null 2>&1; RC=$?; "
        f"{preview_tail}echo EXIT:$RC"
    )


def diagnose_exit(code):
    """Human diagnosis for a non-verdict exit code, or None if 0/1."""
    if code in (0, 1):
        return None
    return (PYTEST_NON_VERDICT.get(code)
            or BOOTSTRAP_DIAGNOSIS.get(code)
            or f"non-verdict exit code {code} (harness failure, not a test result)")
