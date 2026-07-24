"""Genome: the flake-genome store — the compounding flywheel, made real.

Every completed tournament run appends one entry to genome.json (repo root,
gitignored). Over runs this accumulates a repo-specific taxonomy: which cause
classes show up, and which Fireworks models actually win. `aggregate()` turns it
into the numbers the UI's flywheel card shows — runs, by_cause_class counts, and
model win-rates — so "does it compound?" is answered with real data, not a claim.

No wall-clock is stored; each entry carries a monotonically increasing `seq`
(its run index) instead. Appends are lock-guarded and read-modify-write, safe
across threads within a process.
"""
import json
import os
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _REPO_ROOT / "genome.json"


class Genome:
    """Append-only JSON store of completed runs + aggregate views."""

    def __init__(self, path=None):
        self.path = Path(path) if path else _DEFAULT_PATH
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("GENOME_PATH") or _DEFAULT_PATH)

    def _load(self):
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return []

    def record(self, test_name, verdict, cause_class=None, orig_flake_rate=None,
               final_flake_rate=None, winner_model=None):
        """Append one run. Returns the stored entry (with its assigned seq)."""
        with self._lock:
            runs = self._load()
            entry = {
                "seq": len(runs) + 1,
                "test_name": test_name,
                "verdict": verdict,
                "cause_class": cause_class,
                "orig_flake_rate": orig_flake_rate,
                "final_flake_rate": final_flake_rate,
                "winner_model": winner_model,
            }
            runs.append(entry)
            try:
                self.path.write_text(json.dumps(runs, indent=2))
            except Exception:
                pass
            return entry

    def aggregate(self):
        """Flywheel view: total runs, by_cause_class counts, and model win-rates.

        A model 'win' = it generated the winning hypothesis of a FIXED run;
        win_rate = wins / (times that model produced a winner in a FIXED run).
        Here every FIXED run has exactly one winner_model, so the rate is over
        FIXED runs that have a winner_model.
        """
        with self._lock:
            runs = self._load()

        by_cause = {}
        fixed = 0
        model_wins = {}
        for r in runs:
            cc = r.get("cause_class")
            if cc:
                by_cause[cc] = by_cause.get(cc, 0) + 1
            if r.get("verdict") == "FIXED":
                fixed += 1
                m = r.get("winner_model")
                if m:
                    model_wins[m] = model_wins.get(m, 0) + 1

        model_win_rates = {
            m: {"wins": w, "win_rate": round(w / fixed, 3) if fixed else 0.0}
            for m, w in sorted(model_wins.items(), key=lambda kv: -kv[1])
        }
        return {
            "runs": len(runs),
            "fixed": fixed,
            "by_cause_class": by_cause,
            "model_win_rates": model_win_rates,
        }
