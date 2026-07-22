"""Seed A — genuine data race: unsynchronized counter increments across threads.
TUNING: THREADS/ITERS control collision probability. Exit 0 = pass, 1 = fail (flake)."""
import threading, sys

THREADS, ITERS = 8, 60000
counter = 0

def work():
    global counter
    for _ in range(ITERS):
        counter += 1  # read-modify-write, not atomic across bytecode boundaries

ts = [threading.Thread(target=work) for _ in range(THREADS)]
[t.start() for t in ts]; [t.join() for t in ts]
expected = THREADS * ITERS
sys.exit(0 if counter == expected else 1)
