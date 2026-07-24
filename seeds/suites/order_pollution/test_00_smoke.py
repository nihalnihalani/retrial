"""Suite test 0 — smoke: benign, always green. Exists so the bisection has
pristine checkpoints on both sides of the real polluter."""
import sys

assert 1 + 1 == 2
sys.exit(0)
