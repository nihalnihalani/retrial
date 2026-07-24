#!/usr/bin/env python3
"""Live fork-pool smoke: prove the PRODUCTION pool path against real Daytona.

MANUAL / CI-dispatch ONLY. This is committed, budget-capped, and NEVER imported
by the engine — it is the pool-level counterpart to the shared preflight mini
cycle (`preflight.live_fork_smoke`, exercised by `doctor --live`). Two live
paths exist by design (HARDENING-PLAN invariant #5): the preflight cycle proves
the raw SDK create/fork/exec sequence; THIS script proves the real
`ForkSandboxPool.warm/lease` + degrade detection + registry fork-lineage — the
production path the mini-cycle does not touch. Merging them would roughly double
the sandbox spend for no extra evidence (the pool flow strictly supersets the
SDK operations), so they stay separate and the orchestrator runs both.

Budget discipline (real money is at stake on venue wifi):
  * fail-fast SKIP (exit 2) if no DAYTONA_API_KEY, before any SDK construction;
  * RETRIAL_MAX_FORKS capped to <=4, auto_delete_min=10 (a crashed smoke leaks
    for minutes, not the ~10-min snapshot default);
  * a hard threading.Timer that tries a 20s-capped destroy_all then os._exit —
    the honest kill guarantee, with auto_delete_interval as the last backstop.

Exit codes: 0 = pass, 1 = a live assertion/exception failed (teardown ran via
finally), 2 = SKIP (no key), 3 = hard-timeout kill.

    DAYTONA_API_KEY=... RETRIAL_POOL_BACKEND=fork \
    RETRIAL_FORK_SNAPSHOT=daytona-vm-small RETRIAL_FORK_TARGET=us-east-1 \
    python scripts/live_smoke.py
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

# Bootstrap so `import retrial` works when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from retrial.settings import get_settings  # noqa: E402

# Module dict so the hard-kill timer can reach the pool once it exists (the
# Timer fires from another thread; a plain local would be invisible to it).
POOL = {"pool": None}


def _hard_kill():
    """Last-resort teardown then unconditional exit. os._exit skips ALL Python
    cleanup (including the `finally: destroy_all()`), so we try a best-effort,
    time-boxed destroy_all FIRST to shrink the leak window from the ~10-min
    auto-delete backstop to seconds — without ever sacrificing the hard-kill."""
    try:
        if POOL["pool"] is not None:
            t = threading.Thread(target=POOL["pool"].destroy_all, daemon=True)
            t.start()
            t.join(20)   # sub-timeout: the kill path never delays exit past 20s
    finally:
        os._exit(3)      # unconditional — the honest kill


def main():
    # Fail-fast guard (testable offline): no key => SKIP before ANY SDK import
    # or construction. os.environ is fine here — scripts/ is outside the A7 scan.
    if get_settings().daytona_api_key is None:
        print("SKIP: DAYTONA_API_KEY not set")
        return 2

    # Spend guards: cap the fork budget hard (never raise it), short auto-delete.
    try:
        cur = int(os.environ.get("RETRIAL_MAX_FORKS", "64"))
    except ValueError:
        cur = 64
    if cur > 4:
        os.environ["RETRIAL_MAX_FORKS"] = "4"

    timeout_s = int(os.environ.get("LIVE_SMOKE_TIMEOUT", "300"))
    killer = threading.Timer(timeout_s, _hard_kill)
    killer.daemon = True
    killer.start()

    # Import the heavy engine surface only after the key check passed.
    from retrial.events import EventBus
    from retrial.forkpool import ForkSandboxPool
    from retrial.registry import SandboxRegistry

    bus = EventBus()
    reg = SandboxRegistry(bus=bus)
    timings = {}
    report = {"backend": None, "counts": None, "timings": timings}

    pool = ForkSandboxPool(bus=bus, registry=reg,
                           labels={"retrial": "live-smoke"}, auto_delete_min=10)
    POOL["pool"] = pool   # arms the hard-kill teardown

    try:
        t = time.monotonic()
        made = pool.warm(2)
        timings["warm_s"] = round(time.monotonic() - t, 1)

        # assert not degraded: the whole point is to prove the FORK path served.
        stats = pool.stats()
        report["backend"] = stats.get("backend")
        if stats.get("backend") != "fork":
            reason = next((e["payload"].get("reason")
                           for e in reversed(bus.history())
                           if e["type"] == "pool_degraded"), "unknown")
            print(json.dumps({"ok": False, "reason": f"pool degraded: {reason}",
                              "backend": stats.get("backend"), "warmed": made}))
            return 1

        # Exec the trial-pattern one-liner in a real clone and assert the answer.
        t = time.monotonic()
        sb = pool.lease()
        res = sb.process.exec("python3 -c 'print(42)'", timeout=60)
        timings["lease_exec_s"] = round(time.monotonic() - t, 1)
        out = getattr(res, "result", "") or ""
        assert "42" in out, f"exec did not print 42 (got {out!r})"

        # Registry lineage: exactly one root + one checkpoint, checkpoint.parent
        # == root, >=2 trial-clones parented to the checkpoint, total_ever >= 4.
        snap = reg.snapshot()
        by_role = {}
        for rec in snap["sandboxes"]:
            by_role.setdefault(rec["role"], []).append(rec)
        roots = by_role.get("root", [])
        ckpts = by_role.get("checkpoint", [])
        clones = by_role.get("trial-clone", [])
        assert len(roots) == 1, f"expected 1 root, got {len(roots)}"
        assert len(ckpts) == 1, f"expected 1 checkpoint, got {len(ckpts)}"
        assert ckpts[0]["parent_id"] == roots[0]["id"], "checkpoint not parented to root"
        assert len(clones) >= 2, f"expected >=2 trial-clones, got {len(clones)}"
        assert all(c["parent_id"] == ckpts[0]["id"] for c in clones), \
            "a trial-clone is not parented to the checkpoint"
        counts = reg.counts()
        assert counts["total_ever"] >= 4, f"total_ever {counts['total_ever']} < 4"
        report["counts"] = counts
    except Exception as e:
        print(json.dumps({"ok": False, "reason": str(e)[:200],
                          "backend": report["backend"]}))
        return 1
    finally:
        t = time.monotonic()
        try:
            pool.destroy_all()
        except Exception:
            pass
        timings["teardown_s"] = round(time.monotonic() - t, 1)
        killer.cancel()

    live = reg.counts()["live"]
    if live != 0:
        print(json.dumps({"ok": False, "reason": f"{live} sandboxes still live "
                          "after destroy_all", "timings": timings}))
        return 1

    report["ok"] = True
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
