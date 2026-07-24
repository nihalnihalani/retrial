"""Seed D (backup) — order-dependency flake, dict-of-set variant: consumers assume
the "first" registered handler wins, but registration order comes from a set whose
iteration order varies per interpreter (PYTHONHASHSEED). Exit 0 pass, 1 fail."""
import sys

handlers = {name: f"handler_{name}" for name in {"alpha", "beta"}}
primary = next(iter(handlers))
sys.exit(0 if primary == "alpha" else 1)
