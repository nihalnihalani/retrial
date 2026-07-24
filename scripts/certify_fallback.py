"""Certify the bad-wifi FALLBACK hypotheses against the neutering guard.

If live Fireworks diagnosis fails on demo day (bad wifi), we POST a pre-baked
cached-hypotheses payload to /tournament instead. Those cached patches MUST pass
engine-fix's neutering guard (a sys.exit(0)-style stub would be disqualified and
the fallback would QUARANTINE on stage). This script runs each canonical fallback
patch through `guards.neutering_check` against LIVE Daytona (static + dynamic
canary) and prints PASS/FAIL, then writes the certified payload to
scripts/fallback_hypotheses.json for demo-day-us to POST verbatim.

Standalone: imports the engine's guard + pool as a CONSUMER; touches no engine
internals. Run from repo root or scripts/:  python scripts/certify_fallback.py
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parent.parent
load_dotenv(_REPO / ".env")
sys.path.insert(0, str(_REPO / "engine"))

from retrial.guards import neutering_check          # consumer import, no edits
from retrial.pool import SandboxPool

SEED_PATH = "seeds/test_dict_order.py"
ORIGINAL = (_REPO / SEED_PATH).read_text()

# The canonical fallback payload: one REAL deterministic-ordering fix (the winner)
# + one REAL wrong-cause decoy (stays flaky, eliminated empirically). BOTH are
# genuine patches that keep the assertion — neither is a neuter.
HYPOTHESES = [
    {
        "id": "h1",
        "cause_class": "order_dependency",
        "explanation": "Set iteration order varies with PYTHONHASHSEED; take the "
                       "deterministically smallest element instead of an arbitrary one.",
        "model": "accounts/fireworks/models/glm-5p2",
        "patched_code": (
            "import sys\n"
            "events = {f\"evt-{i}\" for i in range(8)}\n"
            "first = sorted(events)[0]\n"
            "sys.exit(0 if first == \"evt-0\" else 1)\n"
        ),
    },
    {
        "id": "h2",
        "cause_class": "timing",
        "explanation": "Hypothesis: a startup timing race. Adds a settle delay before "
                       "reading the events (a plausible but WRONG cause — the flake is "
                       "hash-order, so this stays flaky and is eliminated empirically).",
        "model": "accounts/fireworks/models/kimi-k2p6",
        "patched_code": (
            "import sys, time\n"
            "time.sleep(0.01)\n"
            "events = {f\"evt-{i}\" for i in range(8)}\n"
            "first = next(iter(events))\n"
            "sys.exit(0 if first == \"evt-0\" else 1)\n"
        ),
    },
]


def main():
    pool = SandboxPool()
    all_pass = True
    try:
        pool.warm(2)
        print(f"Certifying {len(HYPOTHESES)} fallback hypotheses vs neutering_check "
              f"(static + live-Daytona dynamic canary):\n")
        for h in HYPOTHESES:
            res = neutering_check(ORIGINAL, h["patched_code"], pool=pool)
            verdict = "PASS" if res.ok else "FAIL"
            if not res.ok:
                all_pass = False
            print(f"[{verdict}] {h['id']} ({h['cause_class']}) — stage={res.stage}: {res.reason}")
    finally:
        pool.destroy_all()

    # Write the full /tournament POST body so demo-day can `curl -d @<file>`.
    payload = {"seed_path": SEED_PATH, "hypotheses": HYPOTHESES}
    out = _REPO / "scripts" / "fallback_hypotheses.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote certified payload -> {out}")
    print("CERTIFIED: all fallback patches pass the neutering guard."
          if all_pass else
          "WARNING: at least one fallback patch FAILED the guard — do NOT use as-is.")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
