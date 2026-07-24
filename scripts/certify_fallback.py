"""Certify the bad-wifi FALLBACK hypotheses for demo day.

If live Fireworks diagnosis fails on demo day (bad wifi), we POST a pre-baked
cached-hypotheses payload to /tournament instead. This certifies that payload
against LIVE Daytona so it reliably yields FIXED (never a surprise QUARANTINE).

Each fallback hypothesis is checked IN ITS ACTUAL TOURNAMENT ROLE:
- The WINNER (a real deterministic fix) must (a) pass engine-fix's neutering
  guard and (b) be empirically STABLE. The guard runs ONLY on eligible/STABLE
  candidates in the real flow, so the winner is the one that must be guard-clean
  — an eligible winner the guard rejects is exactly what causes a QUARANTINE.
- The DECOY (a plausible but WRONG hypothesis) must be empirically UNSTABLE
  (FLAKY or ALWAYS_FAILING — its high fail rate can early-stop to either), so it
  is eliminated by EVIDENCE. It is never eligible, so the tournament never
  guard-checks it — guard-checking a flaky patch in isolation is meaningless
  (its assertion-flipped canary is itself flaky). We verify it never stabilizes.

Standalone: imports the engine's guard/pool/verifier as a CONSUMER; touches no
engine internals. Run from repo root or scripts/:
    python scripts/certify_fallback.py
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parent.parent
load_dotenv(_REPO / ".env")
sys.path.insert(0, str(_REPO / "engine"))

from retrial.guards import neutering_check          # consumer imports, no edits
from retrial.pool import SandboxPool
from retrial.verifier import verify

SEED_PATH = "seeds/test_dict_order.py"
ORIGINAL = (_REPO / SEED_PATH).read_text()
THRESHOLD = 0.10
MAX_TRIALS = 40   # DEMO-CONFIG LAW: a 0-fail fix needs ~40 trials to clear 10%

# The canonical fallback payload: one REAL deterministic-ordering fix (the winner)
# + one plausible WRONG decoy (stays flaky, eliminated by evidence).
WINNER = {
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
}
DECOY = {
    "id": "h2",
    "cause_class": "timing",
    "explanation": "Hypothesis: a startup timing race. Adds a settle delay before "
                   "reading events — a plausible but WRONG cause; the flake is "
                   "hash-order, so this stays flaky and is eliminated by evidence.",
    "model": "accounts/fireworks/models/kimi-k2p6",
    "patched_code": (
        "import sys, time\n"
        "time.sleep(0.01)\n"
        "events = {f\"evt-{i}\" for i in range(8)}\n"
        "first = next(iter(events))\n"
        "sys.exit(0 if first == \"evt-0\" else 1)\n"
    ),
}
HYPOTHESES = [WINNER, DECOY]


def main():
    pool = SandboxPool()
    ok = True
    try:
        pool.warm(8)
        print("Certifying bad-wifi fallback payload vs LIVE Daytona "
              f"(threshold={THRESHOLD:.0%}, max_trials={MAX_TRIALS}):\n")

        # --- WINNER: must pass the neutering guard AND be empirically STABLE ---
        guard = neutering_check(ORIGINAL, WINNER["patched_code"], pool=pool)
        wv = verify(pool, WINNER["patched_code"], max_trials=MAX_TRIALS,
                    conc=8, threshold=THRESHOLD)
        winner_ok = bool(guard) and wv["verdict"] == "STABLE"
        ok = ok and winner_ok
        print(f"[{'PASS' if winner_ok else 'FAIL'}] {WINNER['id']} WINNER ({WINNER['cause_class']})")
        print(f"        neutering guard: {'PASS' if guard else 'FAIL'} "
              f"(stage={guard.stage}: {guard.reason})")
        print(f"        empirical: {wv['verdict']} flake={wv['flake_rate']:.0%} "
              f"CI={wv['wilson_ci']} ({wv['trials']} trials)")

        # --- DECOY: must be empirically UNSTABLE (eliminated by evidence, not guard) ---
        dv = verify(pool, DECOY["patched_code"], max_trials=MAX_TRIALS,
                    conc=8, threshold=THRESHOLD)
        # Accept elimination-by-evidence in ANY form: the decoy's high fail rate
        # often early-stops to ALWAYS_FAILING rather than FLAKY, so requiring
        # exactly FLAKY intermittently cries wolf. Both FLAKY and ALWAYS_FAILING
        # mean the decoy did NOT stabilize — it is eliminated by evidence either way.
        decoy_ok = dv["verdict"] != "STABLE"
        ok = ok and decoy_ok
        print(f"[{'PASS' if decoy_ok else 'FAIL'}] {DECOY['id']} DECOY ({DECOY['cause_class']})")
        print(f"        empirical: {dv['verdict']} flake={dv['flake_rate']:.0%} "
              f"CI={dv['wilson_ci']} ({dv['trials']} trials) — eliminated by evidence")
        print(f"        (not guard-checked: a flaky decoy is never an eligible winner)")
    finally:
        pool.destroy_all()

    payload = {"seed_path": SEED_PATH, "hypotheses": HYPOTHESES}
    out = _REPO / "scripts" / "fallback_hypotheses.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote certified payload -> {out}")
    print("CERTIFIED: fallback POST yields FIXED — winner is guard-clean + empirically "
          "stable; decoy is empirically eliminated." if ok else
          "WARNING: certification FAILED — do NOT ship this fallback as-is.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
