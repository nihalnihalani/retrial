"""Suite test 3 — THE POLLUTER: writes a stale, truncated cache file and exits
green. The classic silent polluter — itself always passes, but every later test
that trusts /tmp/app_cache.json inherits poisoned state. Filesystem pollution
survives across processes and is captured byte-for-byte by fs+RAM checkpoints,
which is exactly what time-travel bisection exploits. Ground truth:
polluter_index = 3."""
import sys
from pathlib import Path

# Looks like a cache refresh; actually truncates the payload mid-write.
Path("/tmp/app_cache.json").write_text('{"version": 1, "entries": [')
sys.exit(0)
