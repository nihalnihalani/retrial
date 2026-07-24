"""Suite test 4 — parse: benign, always green (does not read the cache)."""
import sys

sys.exit(0 if int("42") == 42 else 1)
