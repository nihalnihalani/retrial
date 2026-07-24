"""FastAPI server: the bridge between the engine's EventBus and the UI board.

- GET  /health      liveness + whether a tournament is currently running.
- WS   /ws          replays the EventBus ring buffer to a freshly-connected
                     board, then streams live events. Each frame is the event
                     flattened to the UI contract: {type, ...payload, seq, ts}.
- POST /tournament   {seed_path, hypotheses?} runs a tournament in a background
                     thread against a fresh warm pool, emitting to the shared
                     bus. hypotheses is optional (caller/DiagnosisEngine supplies
                     cached hypotheses; absent => detect-only, quarantine path).

Run: uvicorn retrial.server:app --port 8000   (the UI expects ws://localhost:8000/ws)
"""
import asyncio
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .events import EventBus
from .pool import SandboxPool
from .coordinator import TournamentCoordinator

# Engine tuning (env-overridable so the live demo can dial trials down/up).
MAX_TRIALS = int(os.environ.get("MAX_TRIALS", "50"))
CONC = int(os.environ.get("CONC", "16"))
THRESHOLD = float(os.environ.get("THRESHOLD", "0.05"))
ISOLATION = os.environ.get("ISOLATION", "process")

# One process-wide bus: the tournament emits here, every /ws subscriber streams it.
BUS = EventBus()

app = FastAPI(title="Retrial")
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
    return {
        "status": "ok",
        "running": _running["active"],
        "test_name": _running["test_name"],
        "config": {"max_trials": MAX_TRIALS, "conc": CONC,
                   "threshold": THRESHOLD, "isolation": ISOLATION},
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

    with _run_lock:
        if _running["active"]:
            raise HTTPException(status_code=409, detail="a tournament is already running")
        _running["active"] = True
        _running["test_name"] = path.name

    def run():
        pool = SandboxPool()
        try:
            pool.warm(min(CONC, MAX_TRIALS))
            coord = TournamentCoordinator(
                pool, bus=BUS, max_trials=MAX_TRIALS, conc=CONC,
                threshold=THRESHOLD, isolation=isolation)
            coord.run_tournament(test_code, hypotheses, test_name=path.name,
                                 isolation=isolation)
        except Exception as e:
            BUS.emit("tournament_done", {"verdict": "ERROR", "error": str(e)[:200]})
        finally:
            pool.destroy_all()
            with _run_lock:
                _running["active"] = False
                _running["test_name"] = None

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "test_name": path.name,
            "num_hypotheses": len(hypotheses), "isolation": isolation}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
