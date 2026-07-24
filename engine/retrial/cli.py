"""Retrial CLI — the lie detector.

    python -m retrial.cli check <path-to-test> [--json] [--max-trials N]
                                               [--conc N] [--threshold T]

`check` runs detect-only: it reruns the unmodified test many times in fresh
sandboxes and reports the empirical flake rate + Wilson 95% CI + verdict. This
is the "your CI just lied to you" moment, headless.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import braintrust

from .config import DEFAULT_THRESHOLD
from .pool import SandboxPool
from .verifier import verify
from .diagnosis import diagnose


def _cmd_check(args):
    test_path = Path(args.test)
    if not test_path.exists():
        print(f"error: no such file: {test_path}", file=sys.stderr)
        return 2
    test_code = test_path.read_text()

    max_trials = args.max_trials or int(os.environ.get("MAX_TRIALS", "50"))
    conc = args.conc or int(os.environ.get("CONC", "16"))

    pool = SandboxPool()
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
    if not os.environ.get("FIREWORKS_API_KEY"):
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
    return p


def main(argv=None):
    # Braintrust tracing: auto-instruments supported AI clients (e.g. openai).
    # Conditional + fail-silent: logging must never break the CLI.
    if os.environ.get("BRAINTRUST_API_KEY"):
        try:
            braintrust.init_logger(project="retrial")
            braintrust.auto_instrument()
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
