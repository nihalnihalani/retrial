"""Seed A2 — split read-modify-write race: a thread switch between the read
and the write loses updates. Exit 0 = pass, 1 = fail (flake)."""
import threading, sys
sys.setswitchinterval(1e-6)

THREADS, ITERS = 8, 2000
counter = 0
barrier = threading.Barrier(THREADS)

def work():
    global counter
    barrier.wait()
    for _ in range(ITERS):
        tmp = counter      # read
        counter = tmp + 1  # write — switch between these loses an update

ts = [threading.Thread(target=work) for _ in range(THREADS)]
[t.start() for t in ts]; [t.join() for t in ts]
sys.exit(0 if counter == THREADS * ITERS else 1)
