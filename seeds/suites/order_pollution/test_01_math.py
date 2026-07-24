"""Suite test 1 — math: benign, always green."""
import sys

total = sum(range(10))
sys.exit(0 if total == 45 else 1)
