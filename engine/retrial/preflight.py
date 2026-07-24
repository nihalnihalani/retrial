"""Preflight: boot-time config validation + an optional live fork smoke.

SILENT DEGRADE ON STAGE IS THE DISASTER THIS PREVENTS. A misconfigured fork
backend degrades quietly to the snapshot pool (stats backend='fork-degraded');
by the time anyone notices, the demo's statistical claim is already weaker than
advertised. Preflight surfaces the misconfig up front — as a LOUD banner and a
doctor FAIL — before a single sandbox is spent.

preflight NEVER makes boot fatal: replay demos must work with zero config and no
key. `config_checks` is pure and instant (no network). `live_fork_smoke` is the
one place that touches Daytona, shared by:
  - the server's `RETRIAL_PREFLIGHT_LIVE=1` deep check (run in a daemon thread),
  - `retrial doctor --live` (wrapped in a hard outer timeout at the CLI),
  - `scripts/live_smoke.py` in PKG-C (its OWN pool-path smoke imports this too).
No caller may rely on the cooperative budget alone — see the residual-risk note
on `live_fork_smoke`.
"""
import shutil
from time import monotonic

from .settings import Settings, get_settings


def config_checks(s: Settings | None = None) -> list[dict]:
    """Pure config-level checks — no network. Returns `{name, status, detail}`
    dicts in a stable order. status is 'pass' | 'warn' | 'fail'."""
    s = s or get_settings()
    probs = {p["name"]: p for p in s.problems()}
    checks: list[dict] = []

    # 0. settings_parse — a typo'd numeric env var is a config failure like any
    # other and must reach the banner. FIRST so it is impossible to miss.
    checks.append(probs.get("settings_parse") or {
        "name": "settings_parse", "status": "pass", "detail": "all env vars parsed"})

    # 1. daytona_api_key
    if s.daytona_api_key is None:
        checks.append({"name": "daytona_api_key", "status": "fail",
                       "detail": "missing — live runs and the fork engine cannot "
                                 "start; replay demos unaffected"})
    else:
        checks.append({"name": "daytona_api_key", "status": "pass",
                       "detail": "present"})

    # 2. pool_backend
    backend = s.retrial_pool_backend.lower()
    if "pool_backend" in probs:
        checks.append(probs["pool_backend"])
    else:
        checks.append({"name": "pool_backend", "status": "pass", "detail": backend})

    # 3. fork_snapshot / fork_region (fork backend only)
    if backend == "fork":
        if "fork_snapshot" in probs:
            checks.append(probs["fork_snapshot"])
        else:
            checks.append({"name": "fork_snapshot", "status": "pass",
                           "detail": s.retrial_fork_snapshot})
        if "fork_region" in probs:
            checks.append(probs["fork_region"])
        else:
            checks.append({"name": "fork_region", "status": "pass",
                           "detail": f"{s.resolved_fork_target()} (verified)"})
        if "max_forks" in probs:
            checks.append(probs["max_forks"])
    else:
        checks.append({"name": "fork_checks", "status": "pass",
                       "detail": "snapshot backend — fork checks skipped"})

    # 4. promote_gate
    checks.append({"name": "promote_gate", "status": "pass",
                   "detail": "ON (human approves PRs)" if s.promote_gate_on
                   else "OFF (auto-PR)"})

    # 5. prsmith_gh
    if s.prsmith_on and shutil.which("gh") is None:
        checks.append({"name": "prsmith_gh", "status": "warn",
                       "detail": "PRSMITH=1 but gh CLI not on PATH — PR open will fail"})
    else:
        checks.append({"name": "prsmith_gh", "status": "pass",
                       "detail": ("gh present" if shutil.which("gh") else "gh absent")
                       + ("; PRSMITH on" if s.prsmith_on else "; PRSMITH off")})

    # 6. fireworks / braintrust — optional by design, never fail
    checks.append({"name": "fireworks", "status": "pass",
                   "detail": "key present" if s.fireworks_api_key
                   else "absent — diagnosis runs detect-only"})
    checks.append({"name": "braintrust", "status": "pass",
                   "detail": "key present" if s.braintrust_api_key
                   else "absent — evidence ledger disabled"})

    # 7. auth
    if s.retrial_auth_token:
        checks.append({"name": "auth", "status": "warn",
                       "detail": "RETRIAL_AUTH_TOKEN set — mutating endpoints require "
                                 "Bearer auth; the web UI is unauthenticated and its "
                                 "mutating buttons will 401 (API/CLI-only mode, see README)"})
    else:
        checks.append({"name": "auth", "status": "pass",
                       "detail": "unset — endpoints open (default)"})

    return checks


def _round(x):
    return round(x, 1)


def live_fork_smoke(client=None, budget_s=180) -> dict:
    """The shared live deep check: ONE budget-capped fork cycle mirroring the
    proven forkpool sequence (create root -> exec -> fork+pause a checkpoint ->
    start -> fork one clone -> exec 42 -> teardown leaf-first).

    Budget guard, two honest layers:
    (a) per-call timeouts on every SDK call that accepts one (create, exec) —
        a hung create/exec on venue wifi times out INSIDE the call.
    (b) a between-step monotonic check: if monotonic()-t0 > budget_s at any step
        boundary, abort with reason and run teardown. No signal/alarm (must be
        thread-safe under the server).
    RESIDUAL RISK, stated plainly: _experimental_fork/pause/start are not
    documented to take a per-call timeout; if one wedges, layer (b) cannot
    preempt it. That is why the server runs this in a daemon thread (boot is
    never blocked) and `doctor --live` adds a hard outer timeout at the CLI —
    no caller may rely on the budget alone.
    """
    from daytona import (Daytona, DaytonaConfig,
                         CreateSandboxFromSnapshotParams)
    from .forkpool import _retry

    s = get_settings()
    t0 = monotonic()
    timings: dict = {}
    root = ckpt = clone = None

    def over_budget():
        return (monotonic() - t0) > budget_s

    def result(ok, reason=None):
        timings["total_s"] = _round(monotonic() - t0)
        out = {"ok": ok, "timings": timings}
        if reason is not None:
            out["reason"] = str(reason)[:200]
        return out

    if client is None:
        client = Daytona(DaytonaConfig(target=s.resolved_fork_target()))

    try:
        # create root (10-min auto-delete: even a crashed smoke can't leak long)
        c0 = monotonic()
        root = client.create(
            CreateSandboxFromSnapshotParams(
                snapshot=s.retrial_fork_snapshot,
                labels={"retrial": "preflight"},
                auto_delete_interval=10),
            timeout=120)
        timings["create_s"] = _round(monotonic() - c0)
        if over_budget():
            return result(False, "budget exceeded at create")

        root.process.exec("echo warm", timeout=60)
        if over_budget():
            return result(False, "budget exceeded at warm")

        # fork + pause a checkpoint
        k0 = monotonic()
        ckpt = _retry("preflight.ckpt.fork",
                      lambda: root._experimental_fork(name="retrial-preflight-ckpt"))
        _retry("preflight.ckpt.pause", ckpt.pause)
        timings["checkpoint_s"] = _round(monotonic() - k0)
        if over_budget():
            return result(False, "budget exceeded at checkpoint")

        # start it, fork ONE clone
        f0 = monotonic()
        _retry("preflight.start", ckpt.start)
        clone = _retry("preflight.clone.fork",
                       lambda: ckpt._experimental_fork(name="retrial-preflight-clone"))
        timings["fork_s"] = _round(monotonic() - f0)
        try:
            ckpt.pause()
        except Exception:
            pass
        if over_budget():
            return result(False, "budget exceeded at fork")

        # exec 42 in the clone and assert it
        e0 = monotonic()
        res = clone.process.exec("python3 -c 'print(42)'", timeout=60)
        timings["exec_s"] = _round(monotonic() - e0)
        out = getattr(res, "result", None)
        if out is None:
            out = getattr(res, "output", "")
        if "42" not in str(out):
            return result(False, f"clone exec did not return 42: {str(out)[:80]!r}")

        return result(True)
    except Exception as exc:
        return result(False, exc)
    finally:
        # teardown leaf-first: clone -> checkpoint -> root (best-effort, swallow)
        d0 = monotonic()
        for sb in (clone, ckpt, root):
            if sb is None:
                continue
            try:
                client.delete(client.get(sb.id))
            except Exception:
                pass
        timings["teardown_s"] = _round(monotonic() - d0)


def run_preflight(live: bool = False, client=None) -> dict:
    """Run config checks (+ optional live deep check). Returns EXACTLY the
    `preflight_done` event payload:
    `{ok, live_checked, checks, timings|None}`.

    `ok` = no check has status "fail" (warns never fail). When `live` and the
    config passed, run `live_fork_smoke` and fold its result in as a `live_smoke`
    check. NEVER raises — an internal error becomes a single fail check."""
    try:
        checks = config_checks()
        ok = not any(c["status"] == "fail" for c in checks)
        timings = None
        if live and ok:
            smoke = live_fork_smoke(client=client)
            timings = smoke.get("timings")
            detail = "live fork cycle passed" if smoke["ok"] else smoke.get(
                "reason", "live fork cycle failed")
            checks = checks + [{"name": "live_smoke",
                                "status": "pass" if smoke["ok"] else "fail",
                                "detail": detail}]
            ok = ok and smoke["ok"]
        return {"ok": ok, "live_checked": bool(live), "checks": checks,
                "timings": timings}
    except Exception as e:  # degrade-gracefully: preflight can never crash boot
        return {"ok": False, "live_checked": bool(live),
                "checks": [{"name": "preflight", "status": "fail",
                            "detail": str(e)[:200]}],
                "timings": None}
