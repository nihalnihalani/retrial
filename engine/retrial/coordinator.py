"""TournamentCoordinator: the DAG that turns hypotheses into a verified verdict.

detect (verify the unmodified test = the lie detector) -> verify every patched
hypothesis in parallel -> pick the winner (lowest flake rate whose CI excludes
the original's rate) -> confirm the winner with a fresh run -> emit typed events
at every transition. If no hypothesis statistically beats the original, the
verdict is QUARANTINE (no dead-end: the evidence dossier is still produced).
"""
import threading

from .verifier import verify, confirm


class TournamentCoordinator:
    """Runs the detect -> diagnose-verify -> confirm tournament over hypotheses."""

    def __init__(self, pool, bus=None, max_trials=50, conc=16, threshold=0.05,
                 min_trials=8, timeout=60):
        self.pool = pool
        self.bus = bus
        self.max_trials = max_trials
        self.conc = conc
        self.threshold = threshold
        self.min_trials = min_trials
        self.timeout = timeout

    def _emit(self, event_type, payload):
        if self.bus is not None:
            self.bus.emit(event_type, payload)

    def _verify(self, test_code, label):
        return verify(self.pool, test_code, self.max_trials, self.conc,
                      self.threshold, self.min_trials, self.bus, label, self.timeout)

    def run_tournament(self, test_code, hypotheses):
        """Execute the full tournament and return the complete result dict."""
        # --- DETECT: is the original test actually flaky? ---
        detect = self._verify(test_code, label="detect")
        orig_rate = detect["flake_rate"]
        self._emit("detect_done", {
            "flake_rate": orig_rate,
            "wilson_ci": detect["wilson_ci"],
            "verdict": detect["verdict"],
            "trials": detect["trials"],
        })

        # --- VERIFY each hypothesis in parallel (one thread per lane) ---
        results = {}
        results_lock = threading.Lock()

        def race(h):
            self._emit("hypothesis_created", {
                "id": h["id"],
                "cause_class": h.get("cause_class"),
                "explanation": h.get("explanation"),
            })
            v = self._verify(h["patched_code"], label=h["id"])
            record = {
                "id": h["id"],
                "cause_class": h.get("cause_class"),
                "explanation": h.get("explanation"),
                "patched_code": h["patched_code"],
                **v,
            }
            with results_lock:
                results[h["id"]] = record
            self._emit("hypothesis_verified", {
                "id": h["id"],
                "cause_class": h.get("cause_class"),
                "flake_rate": v["flake_rate"],
                "wilson_ci": v["wilson_ci"],
                "verdict": v["verdict"],
                "trials": v["trials"],
            })

        threads = [threading.Thread(target=race, args=(h,)) for h in hypotheses]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # --- PICK WINNER: CI upper bound below the original rate, lowest flake ---
        eligible = [
            r for r in results.values()
            if r["wilson_ci"][1] < orig_rate and r["verdict"] == "STABLE"
        ]
        eligible.sort(key=lambda r: (r["flake_rate"], r["wilson_ci"][1]))
        winner = eligible[0] if eligible else None

        for r in results.values():
            if winner is None or r["id"] != winner["id"]:
                self._emit("hypothesis_eliminated", {
                    "id": r["id"],
                    "cause_class": r["cause_class"],
                    "flake_rate": r["flake_rate"],
                    "wilson_ci": r["wilson_ci"],
                })

        # --- CONFIRM the winner with a fresh, independent run ---
        confirmation = None
        if winner is not None:
            confirmation = confirm(self.pool, winner["patched_code"],
                                   self.max_trials, self.conc, self.threshold,
                                   self.min_trials, self.bus,
                                   label=f"confirm:{winner['id']}", timeout=self.timeout)
            self._emit("winner_confirmed", {
                "id": winner["id"],
                "cause_class": winner["cause_class"],
                "flake_rate": confirmation["flake_rate"],
                "wilson_ci": confirmation["wilson_ci"],
                "verdict": confirmation["verdict"],
                "orig_flake_rate": orig_rate,
            })

        # A winner only stands if the confirmation run also reads STABLE.
        if winner is not None and confirmation["verdict"] == "STABLE":
            verdict = "FIXED"
        else:
            verdict = "QUARANTINE"
            winner = None

        result = {
            "verdict": verdict,
            "orig_flake_rate": orig_rate,
            "detect": detect,
            "hypotheses": list(results.values()),
            "winner": winner,
            "confirmation": confirmation if verdict == "FIXED" else None,
        }
        self._emit("tournament_done", {
            "verdict": verdict,
            "orig_flake_rate": orig_rate,
            "winner_id": winner["id"] if winner else None,
            "winner_flake_rate": winner["flake_rate"] if winner else None,
            "num_hypotheses": len(results),
        })
        return result
