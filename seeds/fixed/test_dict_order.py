"""Seed C — ordering flake fixed: use a deterministically ordered sequence so the first-processed item is stable regardless of PYTHONHASHSEED."""
import sys

events = [f"evt-{i}" for i in range(2)]
first = next(iter(events))
sys.exit(0 if first == "evt-0" else 1)
