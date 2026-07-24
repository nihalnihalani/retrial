"""EvidenceLedger: the tournament's audit trail in Braintrust.

Braintrust Experiments ARE our public scoreboard: one project ("retrial"), one
experiment per series per run — `<test_name>/detect` for the unmodified test and
`<test_name>/<hypothesis_id>` for each candidate fix. Every trial is a row
{input: trial_index, output: passed, scores: {pass: 0|1}}; the experiment carries
{cause_class, flake_rate, wilson_ci, verdict} as metadata. The experiment
permalink is the governance receipt ("48%->0% across 50 reruns, reproducibly").

Design rules:
- NEVER let logging break a tournament. If the key is missing or any Braintrust
  call raises, the ledger degrades to a silent no-op and permalink() returns None.
- Thread-safe: hypothesis series are logged from parallel coordinator threads;
  experiments are created with set_current=False so there is no shared global
  experiment state, and our own buffers/permalinks are lock-guarded.
"""
import os
import threading

try:
    import braintrust
except Exception:  # pragma: no cover - braintrust always installed, defensive
    braintrust = None


class EvidenceLedger:
    """Factory for per-run ledgers; holds the key/project and the enabled flag."""

    def __init__(self, api_key=None, project="retrial"):
        self.api_key = api_key
        self.project = project
        self.enabled = bool(api_key) and braintrust is not None

    @classmethod
    def from_env(cls, project="retrial"):
        """Enabled when BRAINTRUST_API_KEY is set and LEDGER != '0'."""
        key = os.environ.get("BRAINTRUST_API_KEY")
        on = os.environ.get("LEDGER", "1") != "0"
        return cls(api_key=key if (key and on) else None, project=project)

    def run(self, test_name):
        """Begin a ledger for one tournament run over `test_name`."""
        return LedgerRun(self, test_name)


class LedgerRun:
    """Accumulates trial rows per series and flushes one Braintrust experiment
    per series at finish, exposing the experiment permalink."""

    def __init__(self, ledger, test_name):
        self._ledger = ledger
        self._test_name = test_name
        self._lock = threading.Lock()
        self._rows = {}         # series -> [(trial_index, passed), ...]
        self._permalinks = {}   # series -> url

    def trial(self, series, trial_index, passed):
        """Buffer one trial for `series` ("detect" or a hypothesis id)."""
        if not self._ledger.enabled:
            return
        with self._lock:
            self._rows.setdefault(series, []).append((int(trial_index), bool(passed)))

    def finish_hypothesis(self, series, cause_class=None, flake_rate=None,
                          wilson_ci=None, verdict=None):
        """Flush `series` to a Braintrust experiment and return its permalink
        (or None if disabled/failed). Name: <test_name>/<series>."""
        if not self._ledger.enabled:
            return None
        with self._lock:
            rows = list(self._rows.get(series, []))
        try:
            exp = braintrust.init(
                project=self._ledger.project,
                experiment=f"{self._test_name}/{series}",
                api_key=self._ledger.api_key,
                metadata={k: v for k, v in {
                    "series": series,
                    "cause_class": cause_class,
                    "flake_rate": flake_rate,
                    "wilson_ci": wilson_ci,
                    "verdict": verdict,
                }.items() if v is not None},
                set_current=False,   # thread-safe: no shared global experiment
                update=True,         # reuse if a same-named experiment exists
            )
            for trial_index, passed in rows:
                exp.log(input={"trial_index": trial_index},
                        output=passed,
                        scores={"pass": 1 if passed else 0})
            exp.flush()
            url = _experiment_url(exp)
            with self._lock:
                self._permalinks[series] = url
            return url
        except Exception:
            return None  # logging must never break the tournament

    def permalink(self, series):
        """The Braintrust URL for a finished series, or None."""
        with self._lock:
            return self._permalinks.get(series)


def _experiment_url(exp):
    """Best-effort extraction of the experiment permalink across SDK shapes."""
    try:
        summary = exp.summarize(summarize_scores=False)
        d = summary.as_dict() if hasattr(summary, "as_dict") else dict(summary)
        return d.get("experiment_url") or d.get("experimentUrl")
    except Exception:
        return None
