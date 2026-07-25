"""Score EVERY test in a suite run, not one.

The cost bug this fixes. `repo.build_suite_command` runs the whole suite and
scores a single node id from the junit report — but that report already contains
an outcome for every test in the suite. Measuring N tests therefore cost N full
suite runs when it should have cost one batch.

Priced at Daytona's published rates for 500 quarantined tests, 50 trials each,
against a 10-minute suite:

    one node id per suite run   250,000 sandbox-min   ~$278    ~11 days @16-way
    score every test per run           500 sandbox-min   ~$0.55   ~31 minutes

The API shape was the bug: the unit of work is not "measure a test", it is
"run the suite and score everything in it".

WHY THIS DOES NOT GO THROUGH run_trial. A trial's channel back is one exit code
plus a 500-character log tail — deliberately narrow, because the verdict must not
be forgeable by the code under test. A batch needs one row per test, which is
kilobytes, so it execs directly against a leased sandbox and parses a delimited
report. The verdict rules are unchanged and shared: skipped is not a pass, a
fixture error is not a failure, and anything that is not a verdict is excluded
from that test's denominator rather than counted against it.

STATISTICAL NOTE, stated because it is easy to get wrong. Scores from one suite
run are NOT independent across tests: a fixture that dies takes several tests
with it, and an order shuffle moves many tests at once. That is fine for the
per-test rate — each test still gets K independent suite runs, and its own
interval is honest. It is NOT fine for any statement about the suite as a whole,
so this module does not make one.
"""
import shlex

from .repo import REPO_DIR, _order_flags, _ready_marker, BOOTSTRAP_FAILED
from .verifier import wilson

# One line per test in the junit report. The delimiter is chosen so a test name
# containing spaces, colons or brackets cannot split a row.
_ROW = "\x1fRT\x1f"

_BATCH_PY = r"""
import glob, sys, xml.etree.ElementTree as E
f = glob.glob('/tmp/retrial-j.xml')
if not f:
    sys.exit(90)
r = E.parse(f[0]).getroot()
for c in r.iter('testcase'):
    cls = (c.get('classname') or '').replace('.', '/')
    name = c.get('name') or ''
    nid = (c.get('file') or cls) + '::' + name
    if c.find('error') is not None:
        st = 'ERR'
    elif c.find('skipped') is not None:
        st = 'SKIP'
    elif c.find('failure') is not None:
        st = 'FAIL'
    else:
        st = 'PASS'
    sys.stdout.write('\x1fRT\x1f' + nid + '\x1f' + st + '\n')
"""


def build_batch_command(spec):
    """Bootstrap-if-needed, run the whole suite once, print one row per test."""
    ready = _ready_marker(spec)
    url = shlex.quote(spec.tarball_url)
    suite = shlex.quote(spec.suite or ".")
    plugins = "pytest pytest-randomly" if spec.order == "shuffle" else "pytest"
    return (
        f"if [ ! -f {ready} ]; then "
        f"  rm -rf {REPO_DIR} && mkdir -p {REPO_DIR} && "
        f"  curl -sSL --fail {url} | tar xz -C {REPO_DIR} --strip-components=1 && "
        f"  cd {REPO_DIR} && "
        f"  python3 -m pip install --quiet --disable-pip-version-check "
        f"    {spec.install} {plugins} >/dev/null 2>&1 && "
        f"  touch {ready} || {{ echo BOOTSTRAP:{BOOTSTRAP_FAILED}; exit 0; }}; "
        f"fi; "
        f"cd {REPO_DIR} && rm -f /tmp/retrial-j.xml; "
        f"python3 -m pytest {suite} -q -p no:cacheprovider --tb=no "
        f"{_order_flags(spec.order)}--junit-xml=/tmp/retrial-j.xml "
        f">/dev/null 2>&1; "
        f"python3 -c {shlex.quote(_BATCH_PY)}"
    )


def _parse_rows(out):
    """{node_id: 'PASS'|'FAIL'|'SKIP'|'ERR'} from one suite run's output."""
    rows = {}
    for line in (out or "").splitlines():
        if not line.startswith(_ROW):
            continue
        parts = line.split("\x1f")
        # ['', 'RT', nid, status]
        if len(parts) >= 4:
            rows[parts[2]] = parts[3].strip()
    return rows


def run_suite_batch(pool, spec, runs=20, timeout=900, on_run=None):
    """Run the suite `runs` times and return a per-test report.

    Each run is one leased sandbox exec. Every test in the suite gets `runs`
    observations for the price of `runs` suite executions — not runs x tests.
    """
    observations = {}   # nid -> {"pass": n, "fail": n, "nonverdict": n}
    completed = 0
    bootstrap_failures = 0

    for i in range(runs):
        sb = pool.lease()
        ok = False
        try:
            r = sb.process.exec(build_batch_command(spec), timeout=timeout)
            out = r.result or ""
            if f"BOOTSTRAP:{BOOTSTRAP_FAILED}" in out:
                bootstrap_failures += 1
            else:
                rows = _parse_rows(out)
                if rows:
                    completed += 1
                    ok = True
                    for nid, st in rows.items():
                        o = observations.setdefault(
                            nid, {"pass": 0, "fail": 0, "nonverdict": 0})
                        if st == "PASS":
                            o["pass"] += 1
                        elif st == "FAIL":
                            o["fail"] += 1
                        else:            # SKIP / ERR are not verdicts
                            o["nonverdict"] += 1
        except Exception:
            ok = False
        finally:
            # Same rule as trial.py: a sandbox that misbehaved is not reused.
            pool.release(sb, reusable=ok)
        if on_run is not None:
            try:
                on_run(i + 1, runs, completed)
            except Exception:
                pass

    rows = []
    for nid, o in observations.items():
        n = o["pass"] + o["fail"]
        p, lo, hi = wilson(o["fail"], n)
        rows.append({
            "test": nid,
            "trials": n,
            "fails": o["fail"],
            "nonverdict": o["nonverdict"],
            "flake_rate": round(p, 4),
            "wilson_ci": [round(lo, 4), round(hi, 4)],
        })
    # Flakiest first, then by tightest interval — the triage order.
    rows.sort(key=lambda r: (-r["flake_rate"], r["wilson_ci"][1]))
    return {
        "suite": spec.suite or ".",
        "repo": spec.slug,
        "ref": spec.ref,
        "order": spec.order,
        "runs_requested": runs,
        "runs_completed": completed,
        "bootstrap_failures": bootstrap_failures,
        "tests_scored": len(rows),
        "rows": rows,
    }


def format_batch(report, top=None, min_rate=0.0):
    lines = []
    lines.append(f"suite:     {report['repo']}@{report['ref'][:7]} :: {report['suite']}")
    lines.append(f"order:     {report['order']}")
    lines.append(f"runs:      {report['runs_completed']}/{report['runs_requested']} "
                 f"completed, {report['tests_scored']} tests scored from them")
    if report["bootstrap_failures"]:
        lines.append(f"           {report['bootstrap_failures']} run(s) failed to "
                     f"bootstrap — excluded, not counted as failures")
    lines.append("")
    shown = [r for r in report["rows"] if r["flake_rate"] >= min_rate]
    if top:
        shown = shown[:top]
    lines.append(f"{'rate':>6}  {'95% CI':>14}  {'n':>4} {'nv':>3}  test")
    lines.append("-" * 88)
    for r in shown:
        lo, hi = r["wilson_ci"]
        lines.append(f"{r['flake_rate']:>5.0%}  {lo:>5.0%} - {hi:<5.0%}  "
                     f"{r['trials']:>4} {r['nonverdict']:>3}  {r['test'][:52]}")
    lines.append("")
    lines.append("nv = observations that were not a verdict (skipped, or a fixture "
                 "error). They are")
    lines.append("excluded from that test's denominator, never counted as passes "
                 "or failures.")
    lines.append("")
    lines.append("Per-test intervals are honest: each test got one observation per "
                 "suite run. Outcomes")
    lines.append("within a single run are NOT independent across tests, so no claim "
                 "is made here about")
    lines.append("the suite as a whole.")
    return "\n".join(lines)
