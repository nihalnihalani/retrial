"""Suite test 5 — THE SUSPECT: order-dependency flake. Pristine environment
(no /tmp/app_cache.json): always green. After the polluter has run: fails
~50% of the time — the flake rate the checkpoint probes measure flipping."""
import random
import sys
from pathlib import Path

if Path("/tmp/app_cache.json").exists():
    # Poisoned state reaches this test only via suite order — a retry-in-place
    # "fixes" it, which is why it looks flaky instead of broken.
    sys.exit(1 if random.random() < 0.5 else 0)
sys.exit(0)
