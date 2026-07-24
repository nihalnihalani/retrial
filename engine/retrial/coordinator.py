"""TournamentCoordinator: the DAG that turns hypotheses into a verified verdict.

detect (verify the unmodified test = the lie detector) -> verify every patched
hypothesis in parallel -> pick the winner (lowest flake rate whose CI excludes
the original's rate) -> confirm the winner with a fresh run -> emit typed events
at every transition. If no hypothesis statistically beats the original, the
verdict is QUARANTINE (no dead-end: the evidence dossier is still produced).
"""
import threading

from .config import DEFAULT_THRESHOLD
from .verifier import verify, confirm, verify_hermetic
from .ledger import EvidenceLedger
from .genome import Genome
from .guards import neutering_check
from .settings import get_settings


class TournamentCoordinator:
    """Runs the detect -> diagnose-verify -> confirm tournament over hypotheses."""

    def __init__(self, pool, bus=None, max_trials=50, conc=16, threshold=DEFAULT_THRESHOLD,
                 min_trials=8, timeout=60, isolation="process", ledger=None,
                 tournament_conc=None, genome=None, hermetic=None, hermetic_pool=None):
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
        # Flake-genome store (the compounding flywheel), appended each run.
        self.genome = genome if genome is not None else Genome.from_env()
        # Hermetic mode: a second, network-blocked detect pass to diagnose
        # external-dependency flakes by infrastructure. Off by default; a
        # hermetic_pool (created with network_block_all) must be supplied to run.
        self.hermetic = hermetic if hermetic is not None else get_settings().hermetic_on
        self.hermetic_pool = hermetic_pool

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

    def _hermetic_detect(self, test_code, detect, isolation):
        """Second detect pass in network-blocked sandboxes. If the hermetic flake
        rate's CI does not overlap the networked one, the flake is external-dep;
        otherwise it's env-independent (which itself eliminates the external_dep
        class). Emits hermetic_diagnosis and returns the finding dict (or None)."""
        if not (self.hermetic and self.hermetic_pool is not None):
            return None
        try:
            h = verify_hermetic(self.hermetic_pool, test_code, self.max_trials,
                                self.conc, self.threshold, self.min_trials, self.bus,
                                self.timeout, isolation)
            net_ci, herm_ci = detect["wilson_ci"], h["wilson_ci"]
            overlap = not (net_ci[1] < herm_ci[0] or herm_ci[1] < net_ci[0])
            verdict = "env_independent" if overlap else "external_dep"
            finding = {
                "verdict": verdict,
                "networked_rate": detect["flake_rate"],
                "hermetic_rate": h["flake_rate"],
                "networked_ci": net_ci,
                "hermetic_ci": herm_ci,
            }
            self._emit("hermetic_diagnosis", finding)
            return finding
        except Exception:
            return None  # hermetic diagnosis is a bonus; never break the run

    # Maps a non-FLAKY detect verdict to its honest terminal tournament verdict
    # + a plain-language message. A non-flaky baseline never enters a tournament.
    _GATE = {
        "ALWAYS_FAILING": ("REGRESSION", "test always fails — fix the code, not the test"),
        "STABLE": ("ALREADY_STABLE", "test is already stable — nothing to fix"),
        "INCONCLUSIVE": ("INCONCLUSIVE_BASELINE",
                         "baseline flake rate is inconclusive — cannot prove it's flaky"),
        "ERROR": ("ERROR", "no valid trials — could not measure the test"),
    }

    def _gate_result(self, detect_verdict, orig_rate, detect, test_name):
        """Build the terminal verdict for a non-FLAKY baseline (no tournament)."""
        verdict, message = self._GATE.get(
            detect_verdict, ("INCONCLUSIVE_BASELINE",
                             "baseline is not flaky — no tournament run"))
        done_payload = {
            "verdict": verdict,
            "orig_verdict": detect_verdict,
            "orig_flake_rate": orig_rate,
            "message": message,
            "winner_id": None,
            "winner_flake_rate": None,
            "num_hypotheses": 0,
        }
        result = {
            "verdict": verdict,
            "orig_verdict": detect_verdict,
            "orig_flake_rate": orig_rate,
            "message": message,
            "detect": detect,
            "hypotheses": [],
            "winner": None,
            "confirmation": None,
            "hermetic": None,
        }
        return {"verdict": verdict, "done_payload": done_payload, "result": result}

    def _elim_reason(self, r, orig_rate):
        """Why the evidence knocked this hypothesis out of contention."""
        if r["wilson_ci"][1] >= orig_rate:
            return "confidence interval overlaps the original flake rate"
        if r["verdict"] != "STABLE":
            return f"confidence interval does not clear the {self.threshold:.0%} threshold"
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
            "threshold": self.threshold,
        })

        # --- DETECT: is the original test actually flaky? (hypothesis_id=None) ---
        detect = self._verify(test_code, label="detect", isolation=isolation,
                              hypothesis_id=None)
        orig_rate = detect["flake_rate"]
        # verify()._verdict is the single source of truth for verdicts, including
        # ALWAYS_FAILING (100% fail) — a hard regression, not flake. Pass it through.
        detect_verdict = detect["verdict"]
        self._log_series(ledger_run, "detect", detect, cause_class="original")
        self._emit("detect_done", {
            "flake_rate": orig_rate,
            "wilson_ci": detect["wilson_ci"],
            "trials": detect["trials"],
            "fails": detect["fails"],
            "verdict": detect_verdict,
        })

        # --- STABLE-TEST GATE: only a FLAKY baseline earns a tournament. ---
        # Running the tournament on a non-flaky test is the dishonesty this project
        # exists to kill: a deterministically-failing test (ALWAYS_FAILING) would
        # otherwise be "fixed" by a model swapping the hardcoded failure for an
        # unrelated passing assertion — the neutering guard can't catch that (the
        # replacement IS a real detector, just of something else). So we stop here
        # and report the baseline honestly: no diagnosis, no hypotheses, no PR.
        if detect_verdict != "FLAKY":
            gate = self._gate_result(detect_verdict, orig_rate, detect, test_name)
            self._emit("tournament_done", gate["done_payload"])
            try:
                self.genome.record(test_name=test_name, verdict=gate["verdict"],
                                   cause_class=None, orig_flake_rate=orig_rate,
                                   final_flake_rate=orig_rate, winner_model=None,
                                   attempts=[])
            except Exception:
                pass
            return gate["result"]

        # Optional hermetic (network-blocked) detect pass — diagnose external-dep by infra.
        hermetic_finding = self._hermetic_detect(test_code, detect, isolation)

        # --- VERIFY each hypothesis in parallel (one thread per lane) ---
        results = {}
        no_patch = []          # models whose response wasn't a usable patch
        results_lock = threading.Lock()

        def race(h):
            # A model that returned no parseable patch is NOT raced against the
            # original code (that would fake a baseline race and let the UI claim
            # a theory the model never produced). It is announced and eliminated
            # honestly, and still counts as that model's attempt in the genome.
            if h.get("status") == "no_valid_patch" or not h.get("patched_code"):
                self._emit("hypothesis_created", {
                    "id": h["id"],
                    "cause_class": h.get("cause_class"),
                    "explanation": h.get("explanation"),
                    "model": h.get("model"),
                    "status": "no_valid_patch",
                })
                self._emit("hypothesis_eliminated", {
                    "id": h["id"],
                    "reason": "model did not produce a parseable patch",
                    "cause_class": h.get("cause_class"),
                    "model": h.get("model"),
                    "status": "no_valid_patch",
                })
                with results_lock:
                    no_patch.append(h)
                return
            self._emit("hypothesis_created", {
                "id": h["id"],
                "cause_class": h.get("cause_class"),
                "explanation": h.get("explanation"),
                "model": h.get("model"),   # honest attribution: which model authored this
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
                "model": h.get("model"),
            })

        threads = [threading.Thread(target=race, args=(h,)) for h in hypotheses]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # --- PICK WINNER: CI upper bound below the original rate, lowest flake ---
        # Eligibility: the patched test must read STABLE (INCONCLUSIVE / FLAKY /
        # ALWAYS_FAILING / ERROR are never winners) and its CI upper bound must
        # sit strictly below the original flake rate.
        eligible = [
            r for r in results.values()
            if r["wilson_ci"][1] < orig_rate and r["verdict"] == "STABLE"
        ]
        # Deterministic ordering so a tie never depends on thread arrival order:
        # lowest flake rate, then tightest upper bound, then id.
        eligible.sort(key=lambda r: (r["flake_rate"], r["wilson_ci"][1], r["id"]))

        # Neutering guard: a candidate may win only if its patch still genuinely
        # tests the failure condition. Walk eligible best-first; the first patch
        # that survives the guard wins, any that fail are disqualified (they
        # "won" only by deleting/neutering the assertion).
        winner = None
        neutered = {}
        for cand in eligible:
            try:
                guard = neutering_check(
                    test_code, cand["patched_code"], pool=self.pool,
                    isolation=isolation, timeout=self.timeout)
            except Exception:
                guard = None  # a guard crash must never block a legitimate fix
            if guard is None or guard.ok:
                winner = cand
                break
            neutered[cand["id"]] = guard.reason
            self._emit("hypothesis_eliminated", {
                "id": cand["id"],
                "reason": guard.reason,
                "cause_class": cand["cause_class"],
                "flake_rate": cand["flake_rate"],
                "wilson_ci": cand["wilson_ci"],
                "model": cand.get("model"),
                "neutered": True,
            })

        for r in results.values():
            if r["id"] in neutered:
                continue  # already eliminated by the neutering guard above
            if winner is None or r["id"] != winner["id"]:
                self._emit("hypothesis_eliminated", {
                    "id": r["id"],
                    "reason": self._elim_reason(r, orig_rate),
                    "cause_class": r["cause_class"],
                    "flake_rate": r["flake_rate"],
                    "wilson_ci": r["wilson_ci"],
                    "model": r.get("model"),
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
            genome_cause = winner["cause_class"]
            genome_final = confirmation["flake_rate"]
            genome_model = winner.get("model")
            self._emit("winner_confirmed", {
                "id": winner["id"],
                "flake_rate": winner["flake_rate"],            # tournament round
                "confirm_flake_rate": confirmation["flake_rate"],  # confirmation round
                "confirm_trials": confirmation["trials"],
                "cause_class": winner["cause_class"],
                "model": winner.get("model"),                  # winning model slug (same key as diagnosing)
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
                genome_cause = best["cause_class"]
                genome_final = best["flake_rate"]
                dossier = {
                    "flake_rate": best["flake_rate"],
                    "wilson_ci": best["wilson_ci"],
                    "trials": best["trials"],
                    "reason": f"no hypothesis stabilized the test below the "
                              f"{self.threshold:.0%} threshold",
                }
            else:
                best_id = ""
                genome_cause = None
                genome_final = orig_rate
                dossier = {
                    "flake_rate": orig_rate,
                    "wilson_ci": detect["wilson_ci"],
                    "trials": detect["trials"],
                    "reason": "no fix hypotheses were generated",
                }
            genome_model = None  # no winning model on a quarantine
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
            "hermetic": hermetic_finding,
            "braintrust": {
                "detect": ledger_run.permalink("detect"),
                **{r["id"]: ledger_run.permalink(r["id"]) for r in results.values()},
            },
        }
        result["orig_verdict"] = detect_verdict  # ALWAYS_FAILING passes through as a regression
        self._emit("tournament_done", {
            "verdict": verdict,
            "orig_verdict": detect_verdict,   # a 100%-failing original is a regression, not flake
            "orig_flake_rate": orig_rate,
            "winner_id": winner["id"] if winner else None,
            "winner_flake_rate": winner["flake_rate"] if winner else None,
            "num_hypotheses": len(results) + len(no_patch),
        })

        # --- GENOME: append this run to the flywheel, then publish the aggregate ---
        # Record EVERY hypothesis attempt (model + won) so /genome can report a
        # true per-model win_rate = wins / attempts, not wins / FIXED-runs. A model
        # that produced no parseable patch still made an attempt (and lost).
        attempts = [{
            "model": r.get("model"),
            "won": bool(winner is not None and r["id"] == winner["id"]),
        } for r in results.values()]
        attempts += [{"model": h.get("model"), "won": False} for h in no_patch]
        try:
            self.genome.record(
                test_name=test_name, verdict=verdict, cause_class=genome_cause,
                orig_flake_rate=orig_rate, final_flake_rate=genome_final,
                winner_model=genome_model, attempts=attempts)
            agg = self.genome.aggregate()
            self._emit("genome_updated", {
                "runs": agg["runs"],
                "by_cause_class": agg["by_cause_class"],
            })
            result["genome"] = agg
        except Exception:
            pass  # the genome must never break a tournament

        return result
