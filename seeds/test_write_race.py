"""Seed B2 — last-write-wins race: two unsynchronized writers, final value
depends on scheduling. Exit 0 = pass, 1 = fail (flake)."""
import threading, sys

result = None
barrier = threading.Barrier(2)

def writer(value, delay_us):
    global result
    barrier.wait()
    for _ in range(delay_us):
        pass  # tiny compute jitter
    result = value

a = threading.Thread(target=writer, args=("primary", 50))
b = threading.Thread(target=writer, args=("stale", 60))
a.start(); b.start(); a.join(); b.join()
sys.exit(0 if result == "primary" else 1)
