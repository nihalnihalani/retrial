"""Shared engine defaults, centralized so a value can't drift across modules.

The flake-rate decision threshold used to live as a literal in four places
(verifier, coordinator, server env default, CLI). One constant here is the single
source of truth: 0.10 matches the UI's 10% marker, and it is loose enough that a
genuinely-fixed test (0 fails over ~40 reruns, Wilson upper ≈ 8.8%) legitimately
clears it while a 5% threshold never would.
"""
DEFAULT_THRESHOLD = 0.10
