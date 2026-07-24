"""Seed C — ordering flake: consumes items from a set whose iteration order varies
with PYTHONHASHSEED (fresh interpreter = fresh seed). Asserts first-processed item.
Authentic 'works on my machine' class: hash-randomization order dependency."""
import sys

events = {f"evt-{i}" for i in range(8)}
first = next(iter(events))
sys.exit(0 if first == "evt-0" else 1)
