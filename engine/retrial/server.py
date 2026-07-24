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
"""
import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import braintrust
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .events import EventBus
from .pool import SandboxPool
from .coordinator import TournamentCoordinator
from .diagnosis import DiagnosisEngine

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
THRESHOLD = float(os.environ.get("THRESHOLD", "0.05"))
ISOLATION = os.environ.get("ISOLATION", "process")
PREWARM = int(os.environ.get("PREWARM", "16"))  # boot pre-warm size; 0 disables

# One process-wide bus: the tournament emits here, every /ws subscriber streams it.
BUS = EventBus()

# One shared, pre-warmed pool reused across runs (see module docstring).
_POOL = None
_POOL_LOCK = threading.Lock()
_pool_status = {"prewarming": False}


def _get_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = SandboxPool()
        return _POOL


@asynccontextmanager
async def lifespan(app):
    # Pre-warm the shared pool at boot so the first run is instant.
    if PREWARM > 0:
        def prewarm():
            _pool_status["prewarming"] = True
            try:
                _get_pool().resize_to(PREWARM)
            finally:
                _pool_status["prewarming"] = False
        threading.Thread(target=prewarm, daemon=True).start()
    yield
    # Tear down every sandbox the shared pool owns on shutdown.
    if _POOL is not None:
        _POOL.destroy_all()


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


@app.get("/health")
def health():
    stats = _POOL.stats() if _POOL is not None else {"available": 0, "live": 0}
    return {
        "status": "ok",
        "running": _running["active"],
        "test_name": _running["test_name"],
        "pool": {"available": stats["available"], "live": stats["live"],
                 "prewarming": _pool_status["prewarming"]},
        "config": {"max_trials": MAX_TRIALS, "conc": CONC, "threshold": THRESHOLD,
                   "isolation": ISOLATION, "prewarm": PREWARM},
    }


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
    path = Path(req.seed_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"seed not found: {req.seed_path}")
    test_code = path.read_text()
    hypotheses = req.hypotheses or []
    isolation = req.isolation or ISOLATION

    # No hypotheses supplied and Fireworks is available -> diagnose live.
    # If the key is absent or diagnosis fails, fall through with [] (the
    # coordinator's detect-only quarantine path — never a hard error).
    if not hypotheses:
        engine = DiagnosisEngine()
        if engine.available:
            try:
                hypotheses = engine.diagnose(test_code, path.name, log_tail="", n=4)
            except Exception:
                hypotheses = []

    with _run_lock:
        if _running["active"]:
            raise HTTPException(status_code=409, detail="a tournament is already running")
        _running["active"] = True
        _running["test_name"] = path.name

    def run():
        pool = _get_pool()
        try:
            # Top up (never trims) so the run starts demo-ready even if boot
            # pre-warm was disabled or hasn't finished yet.
            pool.ensure_warm(min(CONC, MAX_TRIALS))
            coord = TournamentCoordinator(
                pool, bus=BUS, max_trials=MAX_TRIALS, conc=CONC,
                threshold=THRESHOLD, isolation=isolation)
            coord.run_tournament(test_code, hypotheses, test_name=path.name,
                                 isolation=isolation)
        except Exception as e:
            BUS.emit("tournament_done", {"verdict": "ERROR", "error": str(e)[:200]})
        finally:
            with _run_lock:
                _running["active"] = False
                _running["test_name"] = None
            # Reset the shared pool to a bounded, demo-ready size for the next run
            # (in the background — this is after tournament_done, invisible to the UI).
            threading.Thread(target=lambda: _get_pool().resize_to(PREWARM),
                             daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "test_name": path.name,
            "num_hypotheses": len(hypotheses), "isolation": isolation}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
