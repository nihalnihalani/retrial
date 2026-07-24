# Retrial 🔁⚖️

**Your build isn't broken — it's lying. Retrial is the lie detector.**

Flaky tests pass and fail on the same code. A green run proves nothing when a test only fails 40% of the time — verification, not generation, is the bottleneck. Retrial fixes that with statistics:

1. **Detect (the lie detector):** rerun a suspect test across a swarm of disposable [Daytona](https://daytona.io) sandboxes — fresh environment per trial — and measure its **empirical flake rate** with a Wilson confidence interval, in about a minute. Incumbents need weeks of CI history; Retrial needs sixty seconds.
2. **Diagnose (differential diagnosis):** [Fireworks](https://fireworks.ai) frontier models generate *competing root-cause hypotheses* (race condition, order dependency, timing, shared state) — each with a candidate fix.
3. **Verify (the tournament):** every hypothesis' fix is re-trialed across the swarm. The winner isn't the one that *looks* right — it's the one that empirically survives (48% flake → 0/50 failures, confirmed by a fresh round). Every trial is logged as a [Braintrust](https://braintrust.dev) experiment — the dashboard is the scoreboard, the permalink is the receipt.
4. **Ship:** the winning fix (or an evidence-backed quarantine) goes out as a real PR, reviewed by [CodeRabbit](https://coderabbit.ai), narrated by an [ElevenLabs](https://elevenlabs.io) flake autopsy.

> Every flaky test deserves a retrial. Fifty of them, actually.

![Retrial live demo — a fully-generated run from armed board through 4-model differential diagnosis, tournament with a broken hypothesis eliminated at 100% flake, to the proven verdict](docs/assets/retrial-demo.gif)
*A live, fully-generated run, recorded as it happened: the diagnosis window (4 real Fireworks models proposing competing theories) → the tournament (one broken fix eliminated at 100% flake while the winner holds 0% across 40 reruns, 32 sandboxes live) → **44% → 0%, proven** with a real Braintrust receipt and the flake genome incrementing. Nothing staged.*

![Retrial verdict card](docs/assets/verdict-live.jpg)

Built for **Daytona HackSprint w/ Braintrust — SF, July 2026**.

## Architecture
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Engine: Python/FastAPI. UI: React tournament board over WebSocket. Event-driven spine (every stage emits typed events consumed by UI, voice, and logs independently).

```
detect ──▶ diagnose ──▶ verify (parallel per hypothesis) ──▶ confirm ──▶ gate ──▶ ship
   │            │               │                                │
   └────────────┴───── EventBus ┴── UI / Voice / Tray / Ledger ──┘
```

## Quickstart
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in keys (see comments for hackathon coupon codes)
.venv/bin/python scripts/calibrate_seeds.py   # measure seed flake rates on Daytona
.venv/bin/python -m retrial.cli check seeds/test_race_counter.py   # run a retrial
```

The API server (`uvicorn retrial.server:app --port 8000`) binds **127.0.0.1** by
default — it has no auth and wide-open CORS, so it must stay on loopback. Set
`HOST=0.0.0.0` only behind a trusted proxy. `POST /tournament` only accepts
`seed_path`s that resolve inside `seeds/`.

## Docs
Full strategy, research, and verified Daytona findings live in [docs/](docs/) — start with [WINNING-IDEA.md](docs/WINNING-IDEA.md) and [ARCHITECTURE.md](docs/ARCHITECTURE.md).
