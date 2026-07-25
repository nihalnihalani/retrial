"""Retrial CLI — the lie detector.

    python -m retrial.cli check <path-to-test> [--json] [--max-trials N]
                                               [--conc N] [--threshold T]
    python -m retrial.cli bisect <suite-dir>   [--suspect NAME] [--json] ...

`check` runs detect-only: it reruns the unmodified test many times in fresh
sandboxes and reports the empirical flake rate + Wilson 95% CI + verdict. This
is the "your CI just lied to you" moment, headless.

`bisect` is time travel: checkpoint the suite at every test boundary
(fork+pause of one live root sandbox), rerun the suspect from each checkpoint
with the Wilson-CI oracle, and binary-search to the exact test that poisons it.
"""
import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import braintrust

from .bisect import FlakeBisector
from .config import DEFAULT_THRESHOLD
from .pool import make_pool
from .preflight import run_preflight
from .settings import get_settings
from .guards import inert_seed_reason
from .amnesty import format_amnesty, run_amnesty
from .matrix import format_matrix, run_matrix
from .repo import RepoSpec
from .verifier import verify
from .diagnosis import diagnose

# Live fork smoke budget (s); the doctor --live outer timeout is this + grace.
_LIVE_SMOKE_BUDGET_S = 180


def _cmd_check(args):
    test_path = Path(args.test)
    if not test_path.exists():
        print(f"error: no such file: {test_path}", file=sys.stderr)
        return 2
    test_code = test_path.read_text()
    # Same refusal as POST /tournament: a file that executes no test would be
    # measured 40 times and reported "already stable". Fail loudly instead.
    inert = inert_seed_reason(test_code)
    if inert:
        print(f"error: {inert}", file=sys.stderr)
        return 2

    _s = get_settings()
    max_trials = args.max_trials or _s.max_trials or 50
    conc = args.conc or _s.conc or 16

    pool = make_pool()
    t0 = time.monotonic()
    try:
        pool.warm(min(conc, max_trials))
        result = verify(pool, test_code, max_trials=max_trials, conc=conc,
                        threshold=args.threshold, isolation=args.isolation)
    finally:
        pool.destroy_all()
    wall = round(time.monotonic() - t0, 1)

    if args.json:
        print(json.dumps({"test": str(test_path), "wallclock_s": wall, **result}))
        return 0

    p = result["flake_rate"]
    lo, hi = result["wilson_ci"]
    print(f"test:      {test_path.name}")
    print(f"trials:    {result['trials']} valid"
          + (f" (+{result['errors']} infra errors)" if result["errors"] else "")
          + (f", early-stopped" if result["stopped_early"] else ""))
    print(f"flake:     {result['fails']}/{result['trials']} fail = {p:.0%}")
    print(f"95% CI:    {lo:.0%} - {hi:.0%}")
    print(f"threshold: {args.threshold:.0%}")
    print(f"isolation: {result['isolation']}")
    print(f"verdict:   {result['verdict']}"
          + ("  <- your CI is lying to you" if result["verdict"] == "FLAKY" else ""))
    print(f"wallclock: {wall}s")
    return 0


def _cmd_diagnose(args):
    test_path = Path(args.test)
    if not test_path.exists():
        print(f"error: no such file: {test_path}", file=sys.stderr)
        return 2
    test_code = test_path.read_text()
    if not get_settings().fireworks_api_key:
        print("error: FIREWORKS_API_KEY not set — cannot run diagnosis", file=sys.stderr)
        return 3
    try:
        hyps = diagnose(test_code, test_path.name, log_tail="", n=args.n)
    except Exception as e:
        print(f"error: diagnosis failed: {e}", file=sys.stderr)
        return 4

    if args.json:
        print(json.dumps({"test": str(test_path), "hypotheses": hyps}))
        return 0
    print(f"test: {test_path.name}  ->  {len(hyps)} competing hypotheses\n")
    for h in hyps:
        print(f"[{h['id']}] {h['cause_class']}: {h['explanation']}")
    return 0


def _cmd_bisect(args):
    suite_dir = Path(args.suite)
    if not suite_dir.is_dir():
        print(f"error: no such suite directory: {suite_dir}", file=sys.stderr)
        return 2
    files = sorted(suite_dir.glob("test_*.py"))
    if not files:
        print(f"error: no test_*.py files in {suite_dir}", file=sys.stderr)
        return 2
    suite = [(f.name, f.read_text()) for f in files]
    names = [n for n, _ in suite]
    suspect_index = None
    if args.suspect:
        if args.suspect not in names:
            print(f"error: --suspect {args.suspect} not in suite "
                  f"({', '.join(names)})", file=sys.stderr)
            return 2
        suspect_index = names.index(args.suspect)
    suspect_name = names[suspect_index if suspect_index is not None else -1]

    # bisect keeps its OWN defaults (30/8), distinct from check's 50/16.
    _s = get_settings()
    max_trials = args.max_trials or _s.max_trials or 30
    conc = args.conc or _s.conc or 8
    b = FlakeBisector(max_trials=max_trials, conc=conc, threshold=args.threshold)
    t0 = time.monotonic()
    result = b.run(suite, suspect_index=suspect_index, suite_name=suite_dir.name)
    wall = round(time.monotonic() - t0, 1)

    if args.json:
        print(json.dumps({"suite": str(suite_dir), "wallclock_s": wall, **result}))
        return 1 if result.get("error") else 0

    if result.get("error"):
        print(f"error: bisection failed: {result['error']}", file=sys.stderr)
        return 1
    print(f"suite:     {suite_dir.name} ({len(suite)} tests)")
    print(f"suspect:   {suspect_name}")
    for p in result.get("probes") or []:
        lo, hi = p["wilson_ci"]
        print(f"  ckpt {p['k']:>2}:  flake {p['flake_rate']:.0%}  "
              f"CI {lo:.0%}-{hi:.0%}  ({p['trials']} trials, {p['verdict']})")
    if result.get("polluter_test"):
        print(f"polluter:  {result['polluter_test']}"
              "  <- run before the suspect, this test poisons it")
    else:
        print(f"polluter:  none found — {result.get('reason', 'inconclusive')}")
    print(f"wallclock: {wall}s")
    return 0


# ------------------------ Sandbox Observatory (HTTP) ------------------------
# The registry lives in the SERVER process; a CLI in another process cannot see
# it, so `sandboxes` and `reap` are thin HTTP clients of the running server
# (stdlib urllib — no new deps). `fetch` is injectable so the tests exercise the
# formatting/exit-code logic without a live server.
def _http_json(method, url, timeout=10):
    """GET/POST `url`, returning (status_code, parsed_json). Raises URLError-
    family (connection refused, DNS, timeout) which callers map to exit 2; an
    HTTP 4xx/5xx is returned as (code, body) so callers can read the detail."""
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:      # 4xx/5xx: read the detail body
        body = e.read().decode("utf-8")
        return e.code, (json.loads(body) if body else {})


def _unreachable(url, exc):
    print(f"error: engine not reachable at {url} — is "
          f"`uvicorn retrial.server:app` running? ({exc})", file=sys.stderr)
    return 2


def _cmd_sandboxes(args, fetch=_http_json):
    url = args.url.rstrip("/") + "/sandboxes"
    try:
        status, data = fetch("GET", url)
    except urllib.error.URLError as e:
        return _unreachable(args.url, e)
    if status != 200:
        print(f"error: {data.get('detail', data)}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data))
        return 0
    boxes = data.get("sandboxes", [])
    counts = data.get("counts", {})
    # A pseudo-"now" from the freshest timestamp in the snapshot: the CLI runs in
    # a different process and cannot share the server's monotonic clock, so age
    # is computed RELATIVE to the newest activity in this snapshot (honest and
    # deterministic for the given payload).
    now = max((sb.get("updated_ts") or sb.get("created_ts") or 0.0)
              for sb in boxes) if boxes else 0.0
    print(f"{'ID':<12}  {'ROLE':<13}  {'BACKEND':<8}  {'STATE':<11}  "
          f"{'PARENT':<12}  {'EXECS':<5}  {'AGE':<7}  CURRENT_CMD")
    for sb in boxes:
        age = round(now - (sb.get("created_ts") or 0.0), 1)
        cmd = sb.get("current_cmd") or ""
        print(f"{str(sb.get('id',''))[:12]:<12}  {str(sb.get('role',''))[:13]:<13}  "
              f"{str(sb.get('backend',''))[:8]:<8}  {str(sb.get('state',''))[:11]:<11}  "
              f"{str(sb.get('parent_id') or '-')[:12]:<12}  "
              f"{sb.get('exec_count', 0):<5}  {age:<7}  {cmd[:60]}")
    print(f"live {counts.get('live', 0)} · total-ever "
          f"{counts.get('total_ever', 0)} · destroyed {counts.get('destroyed', 0)}")
    return 0


def _cmd_reap(args, fetch=_http_json):
    url = args.url.rstrip("/") + "/sandboxes/destroy_all"
    if args.force:
        url += "?force=1"
    try:
        status, data = fetch("POST", url)
    except urllib.error.URLError as e:
        return _unreachable(args.url, e)
    if status == 409:
        print(f"refused: {data.get('detail', 'a run is active')}", file=sys.stderr)
        return 1
    if status != 200:
        print(f"error: {data.get('detail', data)}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data))
        return 0
    msg = f"destroyed {data.get('count', 0)} sandboxes"
    if data.get("bisector_cancelled"):
        msg += " (active bisect run cancelled)"
    print(msg)
    return 0


# ------------------------------- doctor -------------------------------
def _fmt_timings(timings):
    """One-line 'create 4.2s · fork 0.7s · … · total 8.3s' from the smoke's
    timings dict, in the natural fork-cycle order, only the steps reached."""
    order = [("create_s", "create"), ("checkpoint_s", "checkpoint"),
             ("fork_s", "fork"), ("exec_s", "exec"),
             ("teardown_s", "teardown"), ("total_s", "total")]
    parts = [f"{label} {timings[key]}s" for key, label in order if key in timings]
    return " · ".join(parts)


def _cmd_doctor(args, preflight_fn=run_preflight):
    """Validate config end-to-end (PASS/WARN/FAIL per check). `--live` runs the
    real budget-capped fork smoke behind a HARD outer timeout so a wedged SDK
    call can never hang the operator's terminal. Exit 0 iff no check failed
    (warns never fail); errors from the preflight function itself → exit 1."""
    try:
        if args.live:
            # Hard external timeout: the smoke's internal budget is cooperative
            # (see preflight.live_fork_smoke residual-risk note) and a pre-demo
            # `doctor --live` on venue wifi is exactly when a hang costs most.
            box = {}
            t = threading.Thread(
                target=lambda: box.update(res=preflight_fn(live=True)),
                daemon=True)
            t.start()
            t.join(_LIVE_SMOKE_BUDGET_S + 30)   # smoke budget + grace
            if "res" not in box:
                print("FAIL  live_smoke         timed out after "
                      f"{_LIVE_SMOKE_BUDGET_S + 30}s — wedged SDK call; sandbox "
                      "auto-deletes in <=10 min (auto_delete_interval)")
                return 1                          # daemon thread: process exits clean
            res = box["res"]
        else:
            res = preflight_fn(live=False)
    except Exception as e:
        print(f"error: preflight failed: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(res))
        return 0 if res.get("ok") else 1

    for c in res.get("checks", []):
        print(f"{c['status'].upper():<4}  {c['name']:<18}  {c['detail']}")
    if res.get("timings"):
        print(f"timings:  {_fmt_timings(res['timings'])}")
    fails = [c for c in res.get("checks", []) if c["status"] == "fail"]
    if res.get("ok"):
        print("doctor: OK")
    else:
        print(f"doctor: FAILED ({len(fails)} failing checks)")
    return 0 if res.get("ok") else 1


def _cmd_matrix(args):
    test_path = Path(args.test)
    if not test_path.exists():
        print(f"error: no such file: {test_path}", file=sys.stderr)
        return 2
    test_code = test_path.read_text()
    inert = inert_seed_reason(test_code)
    if inert:
        print(f"error: {inert}", file=sys.stderr)
        return 2
    pool = make_pool()
    try:
        pool.warm(min(args.conc, 16))
        result = run_matrix(pool, test_code, trials=args.trials, conc=args.conc)
    finally:
        pool.destroy_all()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"test:      {test_path.name}")
        print(f"axes:      {len(result['cells'])} x {result['trials_per_axis']} trials")
        print()
        print(format_matrix(result))
    return 0


def _cmd_repo(args):
    """Measure a REAL pytest test in a REAL repository at a pinned commit."""
    try:
        spec = RepoSpec(args.repo, args.ref, args.test, install=args.install,
                        suite=args.suite, order=args.order)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    _s = get_settings()
    max_trials = args.runs or _s.max_trials or 50
    conc = args.conc or _s.conc or 16

    pool = make_pool()
    t0 = time.monotonic()
    try:
        pool.warm(min(conc, max_trials))
        result = verify(pool, "", max_trials=max_trials, conc=conc,
                        threshold=args.threshold, isolation=args.isolation,
                        timeout=args.timeout, repo_spec=spec)
    finally:
        pool.destroy_all()
    wall = round(time.monotonic() - t0, 1)

    if args.json:
        print(json.dumps({**spec.as_dict(), "wallclock_s": wall, **result}))
        return 0

    lo, hi = result["wilson_ci"]
    print(f"repo:      {spec.slug}@{spec.ref[:7]}")
    print(f"mode:      {'suite' if spec.suite else 'isolated node id'}"
          + (f' — {spec.suite}' if spec.suite else '')
          + f"  |  order: {spec.order}")
    print(f"test:      {spec.node_id}")
    print(f"trials:    {result['trials']} valid"
          + (f" (+{result['errors']} infra errors)" if result["errors"] else "")
          + (", early-stopped" if result["stopped_early"] else ""))
    print(f"flake:     {result['fails']}/{result['trials']} fail = "
          f"{result['flake_rate']:.0%}")
    print(f"95% CI:    {lo:.0%} - {hi:.0%}")
    print(f"verdict:   {result['verdict']}"
          + ("  <- your CI is lying to you" if result["verdict"] == "FLAKY" else ""))
    print(f"wallclock: {wall}s")
    # Surface WHY, not just that it failed: a wrong node id is exit 5, and that
    # must never be mistaken for a flaky test.
    errs = [h.get("error") for h in result.get("history", []) if h.get("error")]
    if errs:
        seen = []
        for e in errs:
            if e not in seen:
                seen.append(e)
        print()
        print(f"infra errors ({len(errs)}), distinct causes:")
        for e in seen[:3]:
            print(f"  - {e}")
    return 0


def _cmd_amnesty(args):
    """Re-measure a LIST of tests and rank them for triage."""
    tests = []
    if args.tests_file:
        p = Path(args.tests_file)
        if not p.exists():
            print(f"error: no such file: {p}", file=sys.stderr)
            return 2
        tests = [ln.strip() for ln in p.read_text().splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
    tests += list(args.test or [])
    if not tests:
        print("error: no tests given (--tests-file or --test)", file=sys.stderr)
        return 2

    try:
        specs = [RepoSpec(args.repo, args.ref, t, install=args.install,
                          suite=args.suite, order=args.order) for t in tests]
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    _s = get_settings()
    conc = args.conc or _s.conc or 16
    pool = make_pool()
    t0 = time.monotonic()
    try:
        pool.warm(min(conc, 16))

        def progress(i, total, row):
            if not args.json:
                lo, hi = row["wilson_ci"]
                print(f"  [{i}/{total}] {row['verdict']:<15} {row['test'][:52]:<52} "
                      f"{row['flake_rate']:.0%} (CI {lo:.0%}-{hi:.0%}, n={row['trials']})",
                      flush=True)

        report = run_amnesty(pool, specs, trials=args.runs, conc=conc,
                             threshold=args.threshold, timeout=args.timeout,
                             on_result=progress)
    finally:
        pool.destroy_all()
    wall = round(time.monotonic() - t0, 1)

    if args.json:
        print(json.dumps({"repo": args.repo, "ref": args.ref,
                          "wallclock_s": wall, **report}, indent=2))
        return 0
    print()
    print(format_amnesty(report))
    print(f"\nwallclock: {wall}s")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="retrial", description="Flaky-test lie detector.")
    sub = p.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check", help="detect-only: measure a test's flake rate")
    chk.add_argument("test", help="path to the test file to rerun")
    chk.add_argument("--json", action="store_true", help="machine-readable output")
    chk.add_argument("--max-trials", type=int, default=0, help="max reruns (env MAX_TRIALS)")
    chk.add_argument("--conc", type=int, default=0, help="concurrent sandboxes (env CONC)")
    chk.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="flake-rate decision threshold (matches the UI's 10%% marker)")
    chk.add_argument("--isolation", choices=("process", "sandbox"), default="process",
                     help="process=reuse warm sandboxes (fast, fresh interpreter per trial); "
                          "sandbox=fresh sandbox per trial (state-polluting flakes)")
    chk.set_defaults(func=_cmd_check)

    dia = sub.add_parser("diagnose", help="Fireworks: generate N competing root-cause hypotheses")
    dia.add_argument("test", help="path to the flaky test file")
    dia.add_argument("-n", type=int, default=4, help="number of competing hypotheses")
    dia.add_argument("--json", action="store_true", help="machine-readable output")
    dia.set_defaults(func=_cmd_diagnose)

    bis = sub.add_parser(
        "bisect",
        help="time-travel bisection: find the test that pollutes a flaky suite",
        epilog=(
            "Honest limitations: (a) requires fork-capable Daytona — bisection has "
            "NO snapshot fallback, the capability IS the fork; without it the run "
            "reports an honest error instead of a fake result. (b) assumes the "
            "suspect's flake rate is a monotonic step function across checkpoints; "
            "noisy probes are mitigated by a full-budget confirmation pass on the "
            "converged pair, and a contradicted confirmation reports inconclusive "
            "rather than guessing."))
    bis.add_argument("suite", help="directory of seed tests, run in filename order "
                                   "(last = suspect unless --suspect)")
    bis.add_argument("--suspect", help="filename of the suspect test within the suite")
    bis.add_argument("--json", action="store_true", help="machine-readable output")
    bis.add_argument("--max-trials", type=int, default=0,
                     help="max suspect reruns per checkpoint probe (env MAX_TRIALS)")
    bis.add_argument("--conc", type=int, default=0,
                     help="concurrent probe trials per checkpoint (env CONC)")
    bis.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                     help="flake-rate decision threshold (matches the UI's 10%% marker)")
    bis.set_defaults(func=_cmd_bisect)

    rp2 = sub.add_parser(
        "repo",
        help="measure a real pytest test in a real GitHub repo at a pinned commit",
        description=(
            "Point Retrial at somebody's actual code. Downloads the repo at a "
            "PINNED 40-char commit sha (a branch would let the source move "
            "mid-run and silently invalidate the statistics), installs it plus "
            "pytest into each pooled sandbox once, then reruns ONE pytest node "
            "id across the swarm and reports the flake rate with a Wilson 95% "
            "interval.\n\n"
            "SCOPE — this runs the node id in ISOLATION, and roughly HALF of real "
            "flaky tests are deterministic when run alone. Gruber et al. (ICST 2021, n=7,571): 3,168 'victims' always PASS alone and 738 'brittles' always "
            "FAIL alone — 51.6% of flaky tests. Lam et al. (ISSRE 2020) reran "
            "each flaky test 4,000 times in isolation and reproduced only 47%.\n\n"
            "So isolation fails BOTH ways: a victim reads STABLE ('nothing to fix') and a brittle reads ALWAYS_FAILING -> REGRESSION ('fix the code, not the "
            "test'). Both are confident and wrong. A STABLE verdict here means "
            "'not flaky when run alone', never 'not flaky'. For order-dependent "
            "flakes you need suite context — see `retrial bisect`.\n\n"
            "Budget note: 50 trials bounds the 10% threshold; Gruber measured "
            "~170 reruns for 95% confidence a test is NOT flaky."))
    rp2.add_argument("--repo", required=True, help="owner/name or a GitHub URL")
    rp2.add_argument("--ref", required=True, help="full 40-char commit sha")
    rp2.add_argument("--test", required=True, help="pytest node id, e.g. tests/test_x.py::test_y")
    rp2.add_argument("--runs", type=int, default=None, help="max trials (default MAX_TRIALS)")
    rp2.add_argument("--conc", type=int, default=None)
    rp2.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    rp2.add_argument("--isolation", choices=["process", "sandbox"], default="process")
    rp2.add_argument("--timeout", type=int, default=180,
                     help="per-trial seconds; the first trial in each sandbox pays the ~6s bootstrap")
    rp2.add_argument("--order", choices=["fixed", "shuffle"], default="fixed",
                     help=("order/RNG policy, which is part of the test's IDENTITY, "
                           "not a tuning knob. fixed = the repo's natural order. "
                           "shuffle = pytest-randomly, which reshuffles AND reseeds "
                           "the global RNG per test. Measured on penman's real "
                           "test_rearrange: 0/40 fail fixed, 39/40 fail shuffled. "
                           "Run both and report which one flips the verdict."))
    rp2.add_argument("--suite", default=None,
                     help=("run the WHOLE suite at this path in a RANDOMISED order "
                           "each trial and score only --test. This is the only way "
                           "to see order-dependent flakiness, which isolation "
                           "structurally cannot reproduce (~51.6%% of flaky tests "
                           "are deterministic when run alone). Slower per trial."))
    rp2.add_argument("--install", default=None,
                     help="pip args for the project (default '-e .')")
    rp2.add_argument("--json", action="store_true")
    rp2.set_defaults(func=_cmd_repo)

    am = sub.add_parser(
        "amnesty",
        help="re-measure a list of quarantined tests and rank them for triage",
        description=(
            "Quarantine lists only ever grow: the automatic exits on the market "
            "are PASSIVE clocks (BuildPulse 7 days clean, Datadog 30 days, Trunk "
            "none). This actively re-measures every test on the list and ranks "
            "them by evidence.\n\n"
            "It reports what was measured. It never says a test is safe to "
            "reinstate — that needs a human who knows what the test gates. Tests "
            "that could NOT be measured are their own category, never folded "
            "into 'clean'."))
    am.add_argument("--repo", required=True)
    am.add_argument("--ref", required=True, help="full 40-char commit sha")
    am.add_argument("--tests-file", default=None,
                    help="file with one pytest node id per line (# comments ok)")
    am.add_argument("--test", action="append",
                    help="a node id; repeatable, combines with --tests-file")
    am.add_argument("--runs", type=int, default=50, help="trials PER TEST")
    am.add_argument("--conc", type=int, default=None)
    am.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    am.add_argument("--order", choices=["fixed", "shuffle"], default="fixed")
    am.add_argument("--suite", default=None)
    am.add_argument("--install", default=None)
    am.add_argument("--timeout", type=int, default=180)
    am.add_argument("--json", action="store_true")
    am.set_defaults(func=_cmd_amnesty)

    mx = sub.add_parser(
        "matrix",
        help="rerun a test across varied environments; report which axis flips it",
        description=(
            "Vary one environment axis at a time (hash seed, timezone, locale, "
            "UTF-8 mode) and measure the flake rate under each with its own "
            "Wilson interval. History-mining tools structurally cannot answer "
            "this — a log has no counterfactual. An axis is reported only when "
            "its interval is DISJOINT from the control's; overlap means this "
            "budget did not separate them, NOT that the axis has no effect."))
    mx.add_argument("test")
    mx.add_argument("--trials", type=int, default=24, help="trials PER AXIS (default 24)")
    mx.add_argument("--conc", type=int, default=16)
    mx.add_argument("--json", action="store_true")
    mx.set_defaults(func=_cmd_matrix)

    sb = sub.add_parser(
        "sandboxes",
        help="observatory: list every sandbox the engine tracks")
    sb.add_argument("--url", default="http://localhost:8000",
                    help="engine base URL (default http://localhost:8000)")
    sb.add_argument("--json", action="store_true", help="machine-readable output")
    sb.set_defaults(func=_cmd_sandboxes)

    rp = sub.add_parser(
        "reap",
        help="destroy every live sandbox (409-guarded while a run is active; "
             "--force cancels the run and reaps)")
    rp.add_argument("--url", default="http://localhost:8000",
                    help="engine base URL (default http://localhost:8000)")
    rp.add_argument("--force", action="store_true",
                    help="cancel an active run and reap anyway")
    rp.add_argument("--json", action="store_true", help="machine-readable output")
    rp.set_defaults(func=_cmd_reap)

    doc = sub.add_parser(
        "doctor",
        help="validate config end-to-end: PASS/FAIL per check; --live runs a "
             "real budget-capped fork smoke",
        epilog=(
            "Config checks are OFFLINE (pure, instant, no key needed). --live "
            "performs REAL Daytona API calls (~1 sandbox-minute, budget-capped) "
            "and is the SAME code path as the server's RETRIAL_PREFLIGHT_LIVE=1 "
            "deep check and scripts/live_smoke.py's SDK sequence."))
    doc.add_argument("--live", action="store_true",
                     help="create+fork+exec+destroy ONE real sandbox and report "
                          "timings (requires DAYTONA_API_KEY; costs ~1 sandbox-minute)")
    doc.add_argument("--json", action="store_true", help="machine-readable output")
    doc.set_defaults(func=_cmd_doctor)
    return p


def main(argv=None):
    # Braintrust tracing: auto-instruments supported AI clients (e.g. openai).
    # Conditional + fail-silent: logging must never break the CLI.
    if get_settings().braintrust_api_key:
        try:
            braintrust.init_logger(project="retrial")
            braintrust.auto_instrument()
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
