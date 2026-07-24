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

from .config import DEFAULT_THRESHOLD
from .events import EventBus
from .pool import SandboxPool
from .coordinator import TournamentCoordinator
from .diagnosis import DiagnosisEngine
from .prsmith import PRSmith
from .genome import Genome

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
# Hermetic mode (default OFF): a second network-blocked detect pass to diagnose
# external-dependency flakes by infrastructure. Uses its own small sub-pool.
HERMETIC = os.environ.get("HERMETIC", "0") != "0"
HERMETIC_PREWARM = int(os.environ.get("HERMETIC_PREWARM", "8"))

# One process-wide bus: the tournament emits here, every /ws subscriber streams it.
BUS = EventBus()

# One shared, pre-warmed pool reused across runs (see module docstring).
_POOL = None
_HPOOL = None  # hermetic (network-blocked) sub-pool, only when HERMETIC
_POOL_LOCK = threading.Lock()
_pool_status = {"prewarming": False}


def _get_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = SandboxPool()
        return _POOL


def _get_hpool():
    global _HPOOL
    with _POOL_LOCK:
        if _HPOOL is None:
            _HPOOL = SandboxPool(hermetic=True)
        return _HPOOL


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
    # Tear down every sandbox both pools own on shutdown.
    for p in (_POOL, _HPOOL):
        if p is not None:
            p.destroy_all()


app = FastAPI(title="Retrial", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serialize tournaments: interleaving two runs on one bus would scramble the board.
_run_lock = threading.Lock()
_running = {"active": False, "test_name": None}


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
                   "hermetic": HERMETIC},
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
        _running["active"] = True
        _running["test_name"] = path.name

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
            # After the verdict, optionally open a fix/quarantine PR (emits pr_opened).
            if open_pr and result.get("verdict") in ("FIXED", "QUARANTINE"):
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


if __name__ == "__main__":
    import uvicorn
    # Loopback by default (see module docstring): no auth + open CORS means this
    # must not listen on all interfaces unless deliberately placed behind a proxy.
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
