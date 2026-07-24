"""Elimination reasons must describe what actually happened.

The tournament board shows one sentence per eliminated lane, and that sentence is
where the product's "evidence, not opinion" claim is either true or not. A live
run on 2026-07-25 produced four hypotheses ALL at 0/40 and struck three of them
with "another hypothesis reached a lower flake rate" — which was false. They
tied; the winner was chosen by the deterministic (flake_rate, ci_upper, id) sort.
Ties are the COMMON case on a single-root-cause seed, so this is not an edge.
"""
from retrial.coordinator import TournamentCoordinator


def _reason(candidate, orig_rate=0.625, winner=None, threshold=0.10):
    c = TournamentCoordinator.__new__(TournamentCoordinator)
    c.threshold = threshold
    return c._elim_reason(candidate, orig_rate, winner)


WINNER = {"id": "h1", "flake_rate": 0.0, "wilson_ci": [0.0, 0.0876],
          "verdict": "STABLE"}


def test_exact_tie_is_named_as_a_tiebreak_not_a_ranking():
    tied = {"id": "h2", "flake_rate": 0.0, "wilson_ci": [0.0, 0.0876],
            "verdict": "STABLE"}
    reason = _reason(tied, winner=WINNER)
    assert "tied with the winner" in reason
    assert "tiebreak" in reason
    # The specific falsehood this test exists to prevent.
    assert "lower flake rate" not in reason


def test_same_rate_wider_interval_says_so():
    wider = {"id": "h3", "flake_rate": 0.0, "wilson_ci": [0.0, 0.13],
             "verdict": "STABLE"}
    reason = _reason(wider, winner=WINNER)
    assert "tied with the winner" in reason
    assert "wider" in reason
    assert "tiebreak" not in reason


def test_genuinely_higher_rate_still_reports_a_ranking():
    worse = {"id": "h4", "flake_rate": 0.05, "wilson_ci": [0.0, 0.09],
             "verdict": "STABLE"}
    assert _reason(worse, winner=WINNER) == (
        "another hypothesis reached a lower flake rate")


def test_overlapping_original_takes_precedence_over_the_tie_branch():
    overlapping = {"id": "h5", "flake_rate": 0.60, "wilson_ci": [0.4, 0.8],
                   "verdict": "FLAKY"}
    assert _reason(overlapping, winner=WINNER) == (
        "confidence interval overlaps the original flake rate")


def test_not_stable_reports_the_threshold():
    inconclusive = {"id": "h6", "flake_rate": 0.0, "wilson_ci": [0.0, 0.19],
                    "verdict": "INCONCLUSIVE"}
    assert "10%" in _reason(inconclusive, winner=WINNER)


def test_absent_winner_never_raises():
    """QUARANTINE runs eliminate every lane with winner=None."""
    lane = {"id": "h2", "flake_rate": 0.0, "wilson_ci": [0.0, 0.0876],
            "verdict": "STABLE"}
    assert _reason(lane, winner=None)
