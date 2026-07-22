"""Seed B — timing flake: task duration vs tight timeout under scheduler noise.
TUNING: BASE_MS/TIMEOUT_MS gap and JITTER_TASKS control flake probability."""
import asyncio, sys, time

BASE_MS, TIMEOUT_MS, JITTER_TASKS = 38, 50, 25

async def noisy_neighbor():
    t = time.monotonic()
    while time.monotonic() - t < 0.08:
        await asyncio.sleep(0)  # churn the event loop

async def op():
    await asyncio.sleep(BASE_MS / 1000)
    return "ok"

async def main():
    for _ in range(JITTER_TASKS):
        asyncio.ensure_future(noisy_neighbor())
    return await asyncio.wait_for(op(), timeout=TIMEOUT_MS / 1000)

try:
    asyncio.run(main())
    sys.exit(0)
except asyncio.TimeoutError:
    sys.exit(1)
