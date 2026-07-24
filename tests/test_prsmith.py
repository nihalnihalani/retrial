"""PRSmith statistical-receipts tests (pure — no `gh`, no subprocess).

The '## Statistical receipts' section is the governance table: every number the
verdict rests on, and NOTHING when a datum is absent (no 'n/a'-as-evidence, no
invented figures). Constructing PRSmith with an explicit repo skips the `gh
repo view` autodetect, so these never shell out.
"""
from retrial.prsmith import PRSmith, _receipts


def _smith():
    return PRSmith(repo="acme/widgets")


FIXED = {
    "verdict": "FIXED",
    "orig_flake_rate": 0.44,
    "detect": {"trials": 40, "fails": 18, "wilson_ci": [0.30, 0.59],
               "flake_rate": 0.44},
    "winner": {"id": "h2", "cause_class": "race_condition",
               "explanation": "shared dict mutated across threads",
               "patched_code": "import sys\nsys.exit(0)\n", "model": "qwen",
               "flake_rate": 0.02, "wilson_ci": [0.0, 0.10], "trials": 50},
    "confirmation": {"flake_rate": 0.0, "wilson_ci": [0.0, 0.08], "trials": 40},
    "hypotheses": [{"id": "h2", "cause_class": "race_condition",
                    "flake_rate": 0.02, "wilson_ci": [0.0, 0.10], "trials": 50}],
    "braintrust": {"detect": "https://bt.test/detect",
                   "h2": "https://bt.test/h2"},
}

QUARANTINE = {
    "verdict": "QUARANTINE",
    "orig_flake_rate": 0.5,
    "detect": {"trials": 40, "fails": 20, "wilson_ci": [0.35, 0.65],
               "flake_rate": 0.5},
    "hypotheses": [{"id": "h1", "cause_class": "timing", "flake_rate": 0.3,
                    "wilson_ci": [0.2, 0.45], "trials": 50}],
    "braintrust": {"detect": "https://bt.test/detect"},
}


def test_fixed_receipts_full():
    body = _smith()._dossier(FIXED, "test_dict_order.py")
    assert "## Statistical receipts" in body
    # Before + after + confirmation rates all present.
    assert "Before (detect)" in body and "44%" in body
    assert "After (winner)" in body and "2%" in body
    assert "Confirmation:" in body and "40 reruns" in body
    # Both CIs rendered.
    assert "95% CI 30%-59%" in body
    assert "95% CI 0%-8%" in body
    # Braintrust permalinks as markdown links.
    assert "[detect](https://bt.test/detect)" in body
    assert "[h2](https://bt.test/h2)" in body
    # The honest confirmation-independence sentence and the method footer.
    assert "independent re-verify" in body
    assert "Wilson 95% score intervals" in body


def test_quarantine_receipts_no_winner_claim():
    body = _smith()._dossier(QUARANTINE, "test_flaky.py")
    assert "## Statistical receipts" in body
    assert "Best candidate" in body and "30%" in body
    assert "No candidate's CI cleared the threshold" in body
    # No winner / confirmation claims on a quarantine.
    assert "After (winner)" not in body
    assert "Confirmation:" not in body


def test_sparse_receipts_absent_data_says_nothing():
    sparse = {
        "verdict": "QUARANTINE",
        "orig_flake_rate": 0.5,
        "detect": {"trials": 30, "fails": 15, "wilson_ci": [0.33, 0.67],
                   "flake_rate": 0.5},
        # no braintrust, no hypotheses, no confirmation
    }
    lines = _receipts(sparse)
    body = "\n".join(lines)
    # The detect line renders (it has data)...
    assert "Before (detect)" in body and "30 reruns" in body
    # ...but braintrust honestly declares its own absence rather than inventing.
    assert "Braintrust ledger: not recorded for this run" in body
    # No winner/confirmation lines fabricated from missing data.
    assert "After (winner)" not in body
    assert "Confirmation:" not in body


def test_dossier_never_raises_on_minimal_dict():
    # Degrade-gracefully: a bare verdict dict must still produce a string body.
    body = _smith()._dossier({"verdict": "QUARANTINE"}, "test_min.py")
    assert isinstance(body, str)
    assert "## Statistical receipts" in body
    assert "Braintrust ledger: not recorded" in body
