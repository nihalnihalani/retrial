"""FastAPI server: the bridge between the engine's EventBus and the UI board.

- GET  /health      liveness + whether a tournament is currently running.
- WS   /ws          replays the EventBus ring buffer to a freshly-connected
                     board, then streams live events. Each frame is the event
                     flattened to the UI contract: {type, ...payload, seq, ts}.
- POST /tournament   {seed_path, hypotheses?, isolation?} runs a tournament in a
                     background thread against the shared pre-warmed pool,
                     emitting to the shared bus. hypotheses is optional
                     (caller/DiagnosisEngine supplies cached hypotheses; absent
                     => detect-only, quarantine path).

The pool is shared and pre-warmed at boot (env PREWARM, default 16) so a freshly
started server is demo-ready: hitting GO reuses already-warmed sandboxes and the
first trials land near-instantly. After each run the pool is resized back to
PREWARM in the background, keeping it bounded and ready for the next run.

Run: uvicorn retrial.server:app --port 8000   (the UI expects ws://localhost:8000/ws)

Binds 127.0.0.1 by default (loopback only) — CORS is wide-open (allow_origins=*)
and there is no auth, so the server must not be exposed on 0.0.0.0 without a proxy
in front. Set HOST=0.0.0.0 explicitly only behind a trusted network boundary.
"""
import asyncio
import atexit
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]  # repo root, for relative seed paths
_SEEDS_DIR = (_REPO_ROOT / "seeds").resolve()      # seed files MUST resolve inside here

import braintrust
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .bisect import FlakeBisector
from .config import DEFAULT_THRESHOLD
from .events import EventBus
from .pool import make_pool
from .coordinator import TournamentCoordinator
from .diagnosis import DiagnosisEngine
from .prsmith import PRSmith
from .genome import Genome
from .registry import REGISTRY

# Braintrust tracing: auto-instruments supported AI clients (e.g. openai).
# Degrades to a no-op when no key is configured — logging must never break the server.
if os.environ.get("BRAINTRUST_API_KEY"):
    try:
        braintrust.init_logger(project="retrial")
        braintrust.auto_instrument()
    except Exception:
        pass

# Engine tuning (env-overridable so the live demo can dial trials down/up).
MAX_TRIALS = int(os.environ.get("MAX_TRIALS", "50"))
CONC = int(os.environ.get("CONC", "16"))
# Per-lane concurrency during the parallel hypothesis phase (default 8) so peak
# sandboxes = num_hypotheses * TOURNAMENT_CONC stays bounded (~32 for 4 lanes).
TOURNAMENT_CONC = int(os.environ.get("TOURNAMENT_CONC", "8"))
THRESHOLD = float(os.environ.get("THRESHOLD", str(DEFAULT_THRESHOLD)))  # matches the UI's 10% marker; 0/40 clears it
ISOLATION = os.environ.get("ISOLATION", "process")
PREWARM = int(os.environ.get("PREWARM", "16"))  # boot pre-warm size; 0 disables
PRSMITH = os.environ.get("PRSMITH", "0") != "0"  # default OFF so runs don't spam PRs
# Human-approval promote gate (default ON): a FIXED/QUARANTINE verdict parks the
# result as a pending promotion and emits promotion_pending; a human approves via
# POST /promote before PRSmith opens the PR. PROMOTE_GATE=0 restores the old
# auto-PR behavior for headless demos.
PROMOTE_GATE = os.environ.get("PROMOTE_GATE", "1") != "0"
# Hermetic mode (default OFF): a second network-blocked detect pass to diagnose
# external-dependency flakes by infrastructure. Uses its own small sub-pool.
HERMETIC = os.environ.get("HERMETIC", "0") != "0"
HERMETIC_PREWARM = int(os.environ.get("HERMETIC_PREWARM", "8"))

# One process-wide bus: the tournament emits here, every /ws subscriber streams it.
# Buffer bumped to 2000: sandbox_exec adds ~1 event per trial (~200/run) plus
# registrations on top of the tournament's own events; 500 could evict a run's
# opening events. The reducer is upsert/out-of-order safe and registry_snapshot
# makes eviction recoverable, but 2000 keeps replay whole.
BUS = EventBus(buffer_size=2000)
# The Sandbox Observatory ledger streams its typed events onto this same bus and
# is read by the /sandboxes* endpoints. It is NEVER reset at run acceptance
# (live sandboxes + total_ever/destroyed span runs — the pool is shared); see
# _accept_run for the re-seed contract.
REGISTRY.attach_bus(BUS)

# One shared, pre-warmed pool reused across runs (see module docstring).
_POOL = None
_HPOOL = None  # hermetic (network-blocked) sub-pool, only when HERMETIC
_POOL_LOCK = threading.Lock()
_pool_status = {"prewarming": False}


def _get_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            # Backend picked by RETRIAL_POOL_BACKEND (default snapshot). The
            # fork backend degrades to snapshot on its own, emitting
            # pool_degraded on this bus — no special casing here.
            _POOL = make_pool(bus=BUS)
        return _POOL


def _get_hpool():
    global _HPOOL
    with _POOL_LOCK:
        if _HPOOL is None:
            # The fork backend propagates network_block_all to the root create;
            # if the platform rejects that combination it degrades automatically.
            _HPOOL = make_pool(bus=BUS, hermetic=True)
        return _HPOOL


def _reap_everything(bisector=None, skip_orphans=False):
    """Idempotent global teardown, leaf-first by construction (each component is
    already leaf-first internally, and probes/clones die inside their owners
    before ckpts/roots).

    Runs OUTSIDE _run_lock (to avoid starving /status), but every operation it
    performs is individually safe against a live run — SAFETY-CLASS NOTE: this
    is a DIFFERENT class of operation from a single-sandbox DELETE. DELETE of
    one leaf is self-healing (the trial layer excludes the infra error and never
    re-leases). Pool-scope reap deletes the shared checkpoint/root that in-flight
    fork batches actively use, and is safe ONLY because of three mechanisms:
    (1) ForkSandboxPool.destroy_all holds _fork_lock for its whole body, so it
    serializes against any in-flight fork batch instead of yanking the
    checkpoint mid-fork; (2) the _torn_down sentinel makes a surviving run
    thread's next lease()/warm() fail honestly (infra-excluded) rather than
    silently rebuilding a checkpoint; (3) an ACTIVE bisector is only ever
    cancelled (by the caller), never reaped from this thread — callers pass a
    bisector here ONLY when it is not actively running, and skip_orphans keeps
    reap_orphans (a safety net for DEAD owners) from racing that owner teardown.
    """
    global _POOL, _HPOOL
    total = 0
    if bisector is not None:
        try:
            total += bisector.destroy_all() or 0
        except Exception:
            pass
    # Null the module pool refs under the same guard _get_pool uses, so the NEXT
    # accepted run builds a FRESH pool — the per-instance _torn_down sentinel
    # must never leak into a new run.
    with _POOL_LOCK:
        pools = [p for p in (_POOL, _HPOOL) if p is not None]
        _POOL = None
        _HPOOL = None
    for p in pools:
        try:
            total += p.destroy_all() or 0
        except Exception:
            pass
    if not skip_orphans:
        try:
            total += REGISTRY.reap_orphans() or 0
        except Exception:
            pass
    return total


def _shutdown_reap():
    """Process-exit teardown (lifespan shutdown + atexit): cancel any active
    bisector first so its loop stops cooperating, then reap everything.
    Idempotent — destroy_all on both pools is idempotent and mark_destroyed is a
    no-op the second time, so calling this twice destroys nothing extra."""
    bis = _active["bisector"]
    if bis is not None:
        try:
            bis.cancel()
        except Exception:
            pass
    try:
        _reap_everything(bis)
    except Exception:
        pass


# Belt-and-braces: even if the ASGI lifespan shutdown never fires (hard exit),
# atexit destroys every sandbox both pools own so a crashed process can't leave
# orphans burning credit. Idempotent with the lifespan hook.
atexit.register(_shutdown_reap)


@asynccontextmanager
async def lifespan(app):
    # Pre-warm the shared pool(s) at boot so the first run is instant.
    if PREWARM > 0:
        def prewarm():
            _pool_status["prewarming"] = True
            try:
                _get_pool().resize_to(PREWARM)
                if HERMETIC and HERMETIC_PREWARM > 0:
                    _get_hpool().resize_to(HERMETIC_PREWARM)
            finally:
                _pool_status["prewarming"] = False
        threading.Thread(target=prewarm, daemon=True).start()
    yield
    # Cancel an active bisector, then tear down every sandbox both pools own,
    # routed through the single idempotent teardown helper.
    _shutdown_reap()


app = FastAPI(title="Retrial", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serialize runs: interleaving two runs on one bus would scramble the board.
_run_lock = threading.Lock()
_running = {"active": False, "test_name": None}
# The pending human-approval promotion (guarded by _run_lock; per-run state).
# It intentionally SURVIVES _running clearing — approval happens after the run
# ends — but is wiped at the NEXT run acceptance inside _accept_run().
_pending = {"promotion": None}
# The active bisector, so a forced reap can COOPERATIVELY cancel it (never yank
# its resources — teardown belongs to the run thread's own finally). Guarded by
# _run_lock. Set in /bisect's run(), cleared in its finally.
_active = {"bisector": None}
# Accept/reap TOCTOU sentinel (guarded by _run_lock): set while destroy_all is
# tearing pools down OUTSIDE _run_lock, so a NEW run cannot be accepted in the
# gap between the 409 check and the actual teardown (which would be reaped
# without anyone consenting to force). _accept_run refuses while it is set.
_reaping = {"now": False}


def _accept_run(test_name):
    """Single point of run acceptance. MUST be called with _run_lock held.

    Everything that must be wiped between runs is wiped HERE and only here —
    the ring-buffer stale-bleed bug recurred the moment a second endpoint
    hand-rolled its own reset block. Add future per-run state to this helper.

    Resetting the bus inside the lock, before the background thread emits
    anything, matters: otherwise a WS that connects between the accepting POST
    and the run thread starting would replay the PRIOR run's tail — and
    because the buffer is capped, an early event (that run's detect_done) may
    already be evicted while its tournament_done survives, so the board shows
    a stale verdict with no matching detect. Never reset from a background
    thread.
    """
    # Close the accept/reap TOCTOU window: if a sandbox reap is in progress, a
    # run accepted now would be torn down without force ever being consented to,
    # violating the destroy_all 409 contract. The endpoints surface this as 409.
    if _reaping["now"]:
        raise RuntimeError("sandbox reap in progress — retry shortly")
    BUS.reset()
    # A promotion left unclicked by a finished run must never survive into a
    # new run (of ANY type — /tournament and /bisect both accept through here)
    # and feed /promote stale result data. Same reasoning as the bus reset.
    _pending["promotion"] = None
    _running["active"] = True
    _running["test_name"] = test_name
    # The registry is NOT reset (total_ever/live span runs — the pool is
    # shared); instead re-seed the fresh buffer so a WS that connects now sees
    # the current sandbox world instead of nothing (or a stale, half-evicted
    # tail from the prior run). See OBSERVATORY-PLAN.md, "stale-bleed lesson" —
    # and never emit this from a background thread.
    REGISTRY.emit_snapshot()


def _frame(ev):
    """Flatten a bus event {seq,type,ts,payload} into the UI's flat contract."""
    import json
    return json.dumps({**ev["payload"], "type": ev["type"], "seq": ev["seq"], "ts": ev["ts"]})


class TournamentRequest(BaseModel):
    seed_path: str
    hypotheses: list[dict] | None = None
    isolation: str | None = None  # "process" (default) | "sandbox"; falls back to env ISOLATION
    open_pr: bool = False         # open a fix/quarantine PR (also enabled by env PRSMITH=1)


@app.get("/health")
def health():
    stats = _POOL.stats() if _POOL is not None else {"available": 0, "live": 0}
    return {
        "status": "ok",
        "running": _running["active"],
        "test_name": _running["test_name"],
        "pool": {"available": stats["available"], "live": stats["live"],
                 "prewarming": _pool_status["prewarming"]},
        "config": {"max_trials": MAX_TRIALS, "conc": CONC,
                   "tournament_conc": TOURNAMENT_CONC, "threshold": THRESHOLD,
                   "isolation": ISOLATION, "prewarm": PREWARM, "prsmith": PRSMITH,
                   "promote_gate": PROMOTE_GATE, "hermetic": HERMETIC,
                   "pool_backend": os.environ.get("RETRIAL_POOL_BACKEND", "snapshot")},
    }


@app.get("/genome")
def genome():
    """Flywheel aggregates: runs, by_cause_class counts, model win-rates."""
    return Genome.from_env().aggregate()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(ev):
        # Called from bus/tournament threads — hop back onto the event loop.
        try:
            loop.call_soon_threadsafe(queue.put_nowait, ev)
        except RuntimeError:
            pass  # loop already closed

    # subscribe() replays the buffered backlog into the queue, then streams live.
    unsubscribe = BUS.subscribe(on_event)

    async def pump():
        while True:
            ev = await queue.get()
            await websocket.send_text(_frame(ev))

    pump_task = asyncio.create_task(pump())
    try:
        # We don't expect inbound frames; this await just detects disconnect.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        unsubscribe()


@app.post("/tournament")
def start_tournament(req: TournamentRequest):
    # Resolve relative seed paths (e.g. "seeds/test_dict_order.py") against the
    # repo root so the UI can send repo-relative paths regardless of server CWD.
    path = Path(req.seed_path)
    if not path.is_absolute():
        path = (_REPO_ROOT / req.seed_path)
    # Scope guard: the seed MUST live inside the repo's seeds/ directory. Without
    # this, a request could point seed_path at any file on disk (../../.env, etc.)
    # and the server would read it and ship it into a sandbox. Resolve symlinks/..
    # first, then require the result to sit under seeds/.
    resolved = path.resolve()
    if not resolved.is_relative_to(_SEEDS_DIR):
        raise HTTPException(
            status_code=400,
            detail=f"seed_path must resolve inside {_SEEDS_DIR.name}/: {req.seed_path}")
    path = resolved
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"seed not found: {req.seed_path}")
    test_code = path.read_text()
    supplied = req.hypotheses or []
    isolation = req.isolation or ISOLATION
    open_pr = req.open_pr or PRSMITH
    # Diagnose live only when no hypotheses were supplied AND Fireworks is available.
    engine = DiagnosisEngine()
    will_diagnose = (not supplied) and engine.available

    with _run_lock:
        if _running["active"]:
            raise HTTPException(status_code=409, detail="a tournament is already running")
        try:
            _accept_run(path.name)
        except RuntimeError as e:
            # Accept/reap TOCTOU sentinel tripped (a reap is tearing pools down).
            raise HTTPException(status_code=409, detail=str(e))

    def run():
        pool = _get_pool()
        try:
            hypotheses = supplied
            # Live diagnosis runs INSIDE the background thread so POST returns
            # instantly. On absent key / failure, fall through with [] (the
            # coordinator's detect-only quarantine path — never a hard error).
            if will_diagnose:
                # The real per-hypothesis model slugs (round-robin over FIREWORKS_MODELS)
                # so the UI chips show actual model names, not placeholders.
                models = [engine.models[i % len(engine.models)] for i in range(4)]
                BUS.emit("diagnosing", {"test_name": path.name, "n": 4, "models": models})
                try:
                    hypotheses = engine.diagnose(test_code, path.name, log_tail="", n=4)
                except Exception:
                    hypotheses = []
            # Top up (never trims) so the run starts demo-ready even if boot
            # pre-warm was disabled or hasn't finished yet.
            pool.ensure_warm(min(CONC, MAX_TRIALS))
            hpool = None
            if HERMETIC:
                hpool = _get_hpool()
                hpool.ensure_warm(min(CONC, MAX_TRIALS))
            coord = TournamentCoordinator(
                pool, bus=BUS, max_trials=MAX_TRIALS, conc=CONC,
                tournament_conc=TOURNAMENT_CONC,
                threshold=THRESHOLD, isolation=isolation,
                hermetic=HERMETIC, hermetic_pool=hpool)
            result = coord.run_tournament(test_code, hypotheses, test_name=path.name,
                                          isolation=isolation)
            # After the verdict, optionally ship a fix/quarantine PR. With the
            # promote gate ON (default) the result is parked as a pending
            # promotion and a human approves via POST /promote before PRSmith
            # runs; PROMOTE_GATE=0 restores the direct auto-PR path. Emitting
            # promotion_pending after tournament_done is fine — the reducer
            # treats it like pr_opened (allowed post-terminal).
            if open_pr and result.get("verdict") in ("FIXED", "QUARANTINE"):
                if PROMOTE_GATE:
                    with _run_lock:
                        _pending["promotion"] = {"result": result,
                                                 "test_name": path.name}
                    winner = result.get("winner") or {}
                    confirmation = result.get("confirmation") or {}
                    bt = result.get("braintrust") or {}
                    BUS.emit("promotion_pending", {
                        "test_name": path.name,
                        "verdict": result["verdict"],
                        "winner_id": winner.get("id"),
                        "flake_rate": result.get("orig_flake_rate"),
                        "confirm_flake_rate": confirmation.get("flake_rate"),
                        "braintrust_url": bt.get(winner.get("id")) or bt.get("detect"),
                    })
                else:
                    PRSmith(bus=BUS).open_pr(result, path.name)
        except Exception as e:
            BUS.emit("tournament_done", {"verdict": "ERROR", "error": str(e)[:200]})
        finally:
            with _run_lock:
                _running["active"] = False
                _running["test_name"] = None
            # Reset the shared pool(s) to a bounded, demo-ready size for the next
            # run (in the background — after tournament_done, invisible to the UI).
            def _reset_pools():
                _get_pool().resize_to(PREWARM)
                if HERMETIC:
                    _get_hpool().resize_to(HERMETIC_PREWARM)
            threading.Thread(target=_reset_pools, daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "test_name": path.name, "isolation": isolation,
            "diagnosing": will_diagnose,
            "num_hypotheses": None if will_diagnose else len(supplied)}


class PromoteRequest(BaseModel):
    approve: bool = True


@app.post("/promote")
def promote(req: PromoteRequest):
    """Close the pending human-approval promotion.

    Approve: hand the parked result to PRSmith in a background thread (which
    emits pr_opened, the existing terminal event). promotion_closed is emitted
    BEFORE the PR call so the modal can dismiss immediately — but it only
    means "handed to PRSmith"; the UI must not claim shipped until pr_opened
    actually arrives (the honest-state rule). Reject: emit promotion_closed
    only.
    """
    with _run_lock:
        pending = _pending["promotion"]
        _pending["promotion"] = None
    if pending is None:
        raise HTTPException(status_code=404, detail="no promotion pending")
    if not req.approve:
        BUS.emit("promotion_closed", {"approved": False,
                                      "test_name": pending["test_name"]})
        return {"status": "rejected"}
    BUS.emit("promotion_closed", {"approved": True,
                                  "test_name": pending["test_name"]})

    def ship():
        # PRSmith degrades to None on any failure — never crashes the server.
        PRSmith(bus=BUS).open_pr(pending["result"], pending["test_name"])

    threading.Thread(target=ship, daemon=True).start()
    return {"status": "approved"}


class BisectRequest(BaseModel):
    suite_dir: str          # e.g. "seeds/suites/order_pollution"
    suspect: str | None = None   # filename within the suite; default = last test
    max_trials: int | None = None


@app.post("/bisect")
def start_bisect(req: BisectRequest):
    # Honest gate: bisection's capability IS the fork — there is no snapshot
    # fallback to fake it with, so refuse up front rather than fail mid-run.
    if os.environ.get("RETRIAL_POOL_BACKEND", "snapshot") != "fork":
        raise HTTPException(
            status_code=400,
            detail="bisection requires the fork backend (set RETRIAL_POOL_BACKEND=fork)")
    # Resolve relative suite paths against the repo root, same as /tournament.
    path = Path(req.suite_dir)
    if not path.is_absolute():
        path = _REPO_ROOT / req.suite_dir
    # Scope guard: the suite MUST live inside the repo's seeds/ directory.
    # Without this, a request could point suite_dir at any directory on disk
    # and the server would read every test_*.py in it and ship them into a
    # sandbox. Resolve symlinks/.. first, then require the result under seeds/.
    resolved = path.resolve()
    if not resolved.is_relative_to(_SEEDS_DIR):
        raise HTTPException(
            status_code=400,
            detail=f"suite_dir must resolve inside {_SEEDS_DIR.name}/: {req.suite_dir}")
    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail=f"suite not found: {req.suite_dir}")
    files = sorted(resolved.glob("test_*.py"))
    if not files:
        raise HTTPException(status_code=404,
                            detail=f"no test_*.py files in {req.suite_dir}")
    suite = [(f.name, f.read_text()) for f in files]
    names = [n for n, _ in suite]
    suspect_index = None
    if req.suspect is not None:
        if req.suspect not in names:
            raise HTTPException(status_code=404,
                                detail=f"suspect not in suite: {req.suspect}")
        suspect_index = names.index(req.suspect)
    resolved_suspect = suspect_index if suspect_index is not None else len(suite) - 1

    with _run_lock:
        if _running["active"]:
            raise HTTPException(status_code=409, detail="a run is already active")
        try:
            _accept_run(resolved.name)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))

    def run():
        try:
            bisector = FlakeBisector(bus=BUS,
                                     max_trials=req.max_trials or MAX_TRIALS,
                                     conc=CONC, threshold=THRESHOLD)
            # Track the active bisector so a forced reap can cooperatively
            # cancel it (never destroy_all it from another thread).
            with _run_lock:
                _active["bisector"] = bisector
            # run() is degrade-gracefully: errors come back as a terminal
            # bisect_done {"error": ...}, never a traceback to the client.
            bisector.run(suite, suspect_index=suspect_index,
                         suite_name=resolved.name)
        except Exception as e:
            BUS.emit("bisect_done", {"error": str(e)[:200]})
        finally:
            with _run_lock:
                _running["active"] = False
                _running["test_name"] = None
                _active["bisector"] = None

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "suite": resolved.name,
            "n_tests": resolved_suspect,  # prefix length = checkpointed tests
            "suspect": names[resolved_suspect]}


# --------------------------- Sandbox Observatory ---------------------------
@app.get("/sandboxes")
def sandboxes():
    """Full registry snapshot: every live sandbox plus the most-recently
    destroyed (bounded by RETRIAL_DESTROYED_RETAIN), the fork-lineage tree, and
    exact live/total-ever/destroyed counts. `est_resources` is a COUNT-BASED
    estimate — Daytona does not expose per-sandbox RAM here, so we do not claim
    it."""
    snap = REGISTRY.snapshot()
    snap["est_resources"] = {
        "live_sandboxes": snap["counts"]["live"],
        "note": "count-based estimate; Daytona does not expose per-sandbox RAM here",
    }
    return snap


@app.get("/sandboxes/{sid}")
def sandbox_detail(sid: str):
    """Full detail for one sandbox incl. its recent-exec history and a lazily
    resolved Daytona preview URL (None when Daytona does not expose one — paused
    sandboxes are retried on the next open, not cached as failed)."""
    rec = REGISTRY.record(sid)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"unknown sandbox: {sid}")
    if rec["state"] != "destroyed":
        rec["preview_url"] = REGISTRY.preview(sid)  # lazy, cached, None on failure
    return rec


@app.delete("/sandboxes/{sid}")
def destroy_sandbox(sid: str):
    """Destroy ONE sandbox (allowed even mid-run). Killing a trial sandbox
    surfaces as an infra error, which the trial layer already excludes and never
    re-leases — kill-a-sandbox-live is a legitimate resilience demo.

    SAFETY-CLASS BOUNDARY: this covers a single LEAF sandbox only. It does NOT
    extend to pool-scope reap. destroy_all deletes the shared provisioning
    source (checkpoint/root) that in-flight fork batches actively use, which is a
    different safety class entirely (see POST /sandboxes/destroy_all and
    _reap_everything). Future maintainers must NOT reuse this single-sandbox
    justification for pool-scope operations."""
    rec = REGISTRY.record(sid)
    if rec is None or rec["state"] == "destroyed":
        raise HTTPException(status_code=404,
                            detail=f"unknown or already-destroyed sandbox: {sid}")
    snap = REGISTRY.snapshot()
    kids = [c for c in snap["lineage"].get(sid, [])
            if (REGISTRY.record(c) or {}).get("state") != "destroyed"]
    if kids:
        raise HTTPException(
            status_code=409,
            detail="sandbox has live fork-children — destroy leaves first or "
                   "use /sandboxes/destroy_all")
    if not REGISTRY.destroy(sid):
        raise HTTPException(status_code=502, detail="destroy could not be initiated")
    return {"status": "destroying", "id": sid}


@app.post("/sandboxes/destroy_all")
def destroy_all_sandboxes(force: bool = False):
    """Leaf-first teardown of every live sandbox across all pools/bisectors.

    409 while a run is active unless ?force=1. FORCE-REAP CONTRACT (this is the
    fix for the reachable destroy-mid-fork race): an active bisector is
    cooperatively CANCELLED — never destroy_all'd from this thread — so its own
    finally reaps leaf-first; a live tournament's pools are torn down under
    _fork_lock with the _torn_down sentinel, after which the run's remaining
    trials fail as infra-excluded rather than continuing on rebuilt sandboxes.
    See _reap_everything for the full safety-class rationale."""
    with _run_lock:
        active = _running["active"]
        bisector = _active["bisector"]
        if active and not force:
            raise HTTPException(
                status_code=409,
                detail="a run is active — pass ?force=1 to cancel it and reap")
        _reaping["now"] = True   # closes the accept/reap TOCTOU window
    try:
        cancelled = False
        if active and bisector is not None:
            bisector.cancel()   # cooperative: the run thread's own finally reaps
            cancelled = True    # leaf-first; we NEVER destroy_all an active bisector
        n = _reap_everything(bisector=None if cancelled else bisector,
                             skip_orphans=cancelled)
    finally:
        with _run_lock:
            _reaping["now"] = False
    return {"status": "destroyed", "count": n, "forced": bool(force),
            "bisector_cancelled": cancelled, **REGISTRY.counts()}


if __name__ == "__main__":
    import uvicorn
    # Loopback by default (see module docstring): no auth + open CORS means this
    # must not listen on all interfaces unless deliberately placed behind a proxy.
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
