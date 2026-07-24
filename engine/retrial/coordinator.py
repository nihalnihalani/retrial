"""TournamentCoordinator: the DAG that turns hypotheses into a verified verdict.

detect (verify the unmodified test = the lie detector) -> verify every patched
hypothesis in parallel -> pick the winner (lowest flake rate whose CI excludes
the original's rate) -> confirm the winner with a fresh run -> emit typed events
at every transition. If no hypothesis statistically beats the original, the
verdict is QUARANTINE (no dead-end: the evidence dossier is still produced).
"""
import threading

from .verifier import verify, confirm
from .ledger import EvidenceLedger


class TournamentCoordinator:
    """Runs the detect -> diagnose-verify -> confirm tournament over hypotheses."""

    def __init__(self, pool, bus=None, max_trials=50, conc=16, threshold=0.05,
                 min_trials=8, timeout=60, isolation="process", ledger=None,
                 tournament_conc=None):
        self.pool = pool
        self.bus = bus
        self.max_trials = max_trials
        self.conc = conc
        # Per-lane concurrency during the PARALLEL hypothesis phase. N lanes each
        # run this many trials at once, so peak sandboxes = N * tournament_conc;
        # capping it (default 8) bounds that peak (~32 for 4 hypotheses) instead
        # of N * conc. Detect/confirm are single-lane and use the full conc.
        self.tournament_conc = tournament_conc or conc
        self.threshold = threshold
        self.min_trials = min_trials
        self.timeout = timeout
        self.isolation = isolation
        # Braintrust ledger: default on when the key is present; a disabled ledger
        # (or LEDGER=0) makes every logging call a silent no-op.
        self.ledger = ledger if ledger is not None else EvidenceLedger.from_env()

    def _emit(self, event_type, payload):
        if self.bus is not None:
            self.bus.emit(event_type, payload)

    def _log_series(self, ledger_run, series, v, cause_class=None):
        """Replay a verify()'s valid trials into the ledger and finish the series'
        Braintrust experiment. Returns the permalink (or None). Never raises."""
        try:
            idx = 0
            for res in v["history"]:
                if res["error"] is not None:
                    continue
                ledger_run.trial(series, idx, res["passed"])
                idx += 1
            return ledger_run.finish_hypothesis(
                series, cause_class=cause_class, flake_rate=v["flake_rate"],
                wilson_ci=v["wilson_ci"], verdict=v["verdict"])
        except Exception:
            return None

    def _verify(self, test_code, label, isolation, hypothesis_id=None,
                emit_trials=True, conc=None):
        return verify(self.pool, test_code, self.max_trials,
                      conc if conc is not None else self.conc,
                      self.threshold, self.min_trials, self.bus, label,
                      self.timeout, isolation, hypothesis_id, emit_trials)

    def _elim_reason(self, r, orig_rate):
        """Why the evidence knocked this hypothesis out of contention."""
        if r["wilson_ci"][1] >= orig_rate:
            return "confidence interval overlaps the original flake rate"
        if r["verdict"] != "STABLE":
            return "still flaky — CI does not clear the threshold"
        return "another hypothesis reached a lower flake rate"

    def run_tournament(self, test_code, hypotheses, isolation=None, test_name=None):
        """Execute the full tournament and return the complete result dict.

        `isolation` (per-seed, defaults to the coordinator's setting) selects
        process- vs sandbox-level isolation for every trial in this run.
        `hypotheses` is caller-supplied — pass cached hypotheses to run the
        tournament without a live diagnosis (demo fallback when Fireworks is
        slow or weak).
        """
        isolation = isolation or self.isolation
        test_name = test_name or "test"
        ledger_run = self.ledger.run(test_name)

        self._emit("run_started", {
            "test_name": test_name,
            "planned_trials": self.max_trials,
        })

        # --- DETECT: is the original test actually flaky? (hypothesis_id=None) ---
        detect = self._verify(test_code, label="detect", isolation=isolation,
                              hypothesis_id=None)
        orig_rate = detect["flake_rate"]
        self._log_series(ledger_run, "detect", detect, cause_class="original")
        self._emit("detect_done", {
            "flake_rate": orig_rate,
            "wilson_ci": detect["wilson_ci"],
            "trials": detect["trials"],
            "fails": detect["fails"],
            "verdict": detect["verdict"],
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
            v = self._verify(h["patched_code"], label=h["id"], isolation=isolation,
                            hypothesis_id=h["id"], conc=self.tournament_conc)
            record = {
                "id": h["id"],
                "cause_class": h.get("cause_class"),
                "explanation": h.get("explanation"),
                "patched_code": h["patched_code"],
                "model": h.get("model"),   # generating model, for the flake genome
                **v,
            }
            with results_lock:
                results[h["id"]] = record
            # Flush this lane's Braintrust experiment (thread-safe; own series).
            self._log_series(ledger_run, h["id"], v, cause_class=h.get("cause_class"))
            self._emit("hypothesis_verified", {
                "id": h["id"],
                "flake_rate": v["flake_rate"],
                "wilson_ci": v["wilson_ci"],
                "trials": v["trials"],
                "cause_class": h.get("cause_class"),
                "verdict": v["verdict"],
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
                    "reason": self._elim_reason(r, orig_rate),
                    "cause_class": r["cause_class"],
                    "flake_rate": r["flake_rate"],
                    "wilson_ci": r["wilson_ci"],
                })

        # --- CONFIRM the winner with a fresh, independent run (trials suppressed) ---
        confirmation = None
        if winner is not None:
            confirmation = confirm(self.pool, winner["patched_code"],
                                   self.max_trials, self.conc, self.threshold,
                                   self.min_trials, self.bus,
                                   label=f"confirm:{winner['id']}",
                                   timeout=self.timeout, isolation=isolation)

        # A winner only stands if the confirmation run also reads STABLE.
        if winner is not None and confirmation["verdict"] == "STABLE":
            verdict = "FIXED"
            self._emit("winner_confirmed", {
                "id": winner["id"],
                "flake_rate": winner["flake_rate"],            # tournament round
                "confirm_flake_rate": confirmation["flake_rate"],  # confirmation round
                "cause_class": winner["cause_class"],
                "wilson_ci": confirmation["wilson_ci"],
                "orig_flake_rate": orig_rate,
                "braintrust_url": ledger_run.permalink(winner["id"]),
            })
        else:
            verdict = "QUARANTINE"
            # No fix stabilized the test — quarantine the least-bad candidate WITH evidence.
            if results:
                best = min(results.values(), key=lambda r: r["flake_rate"])
                best_id = best["id"]
                dossier = {
                    "flake_rate": best["flake_rate"],
                    "wilson_ci": best["wilson_ci"],
                    "trials": best["trials"],
                    "reason": f"no hypothesis stabilized the test below the "
                              f"{self.threshold:.0%} threshold",
                }
            else:
                best_id = ""
                dossier = {
                    "flake_rate": orig_rate,
                    "wilson_ci": detect["wilson_ci"],
                    "trials": detect["trials"],
                    "reason": "no fix hypotheses were generated",
                }
            self._emit("quarantine_confirmed", {
                "best_id": best_id,
                "dossier": dossier,
                "braintrust_url": ledger_run.permalink(best_id) if best_id else None,
            })
            winner = None

        result = {
            "verdict": verdict,
            "orig_flake_rate": orig_rate,
            "detect": detect,
            "hypotheses": list(results.values()),
            "winner": winner,
            "confirmation": confirmation if verdict == "FIXED" else None,
            "braintrust": {
                "detect": ledger_run.permalink("detect"),
                **{r["id"]: ledger_run.permalink(r["id"]) for r in results.values()},
            },
        }
        self._emit("tournament_done", {
            "verdict": verdict,
            "orig_flake_rate": orig_rate,
            "winner_id": winner["id"] if winner else None,
            "winner_flake_rate": winner["flake_rate"] if winner else None,
            "num_hypotheses": len(results),
        })
        return result
