"""Suite test 2 — strings: benign, always green."""
import sys

sys.exit(0 if "flaky".upper() == "FLAKY" else 1)
