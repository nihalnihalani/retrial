# CLAUDE.md — Retrial

Retrial detects flaky tests by measuring their **empirical flake rate** across a swarm of Daytona sandboxes, then runs a **hypothesis tournament** (competing root-cause fixes, each re-verified statistically) and ships the winner as an evidence-backed PR. Built for Daytona HackSprint SF (July 24, 2026 — one-day event, submit 3:30 PM via Devpost). The pitch: *"Your build isn't broken — it's lying. We built the lie detector."*

## Commands
```bash
.venv/bin/pip install -r requirements.txt          # Python 3.14 venv at repo root
cp .env.example .env                               # then fill keys (coupon codes in comments)

# Engine (run from engine/):
cd engine && ../.venv/bin/python -m retrial.cli check ../seeds/test_dict_order.py   # lie detector (detect-only)
../.venv/bin/python -m retrial.cli check ../seeds/test_dict_order.py --json --max-trials 16 --conc 16 --isolation process

# Calibration (measures real flake rates ON Daytona — the ground truth):
.venv/bin/python scripts/calibrate_seeds.py        # TRIALS=40 env to change count

# Server (engine/retrial/server.py): uvicorn on :8000 — GET /health, WS /ws, POST /tournament
# UI (from ui/): npm run dev  → http://localhost:5173  (replay mode default; append ?live=1 for WS)
npm run build                                       # must pass before committing UI changes
```

## Repo map
- `engine/retrial/` — Python core: `pool.py` (SandboxPool), `trial.py` (TrialRunner), `verifier.py` (Wilson CI + adaptive early-stop), `coordinator.py` (TournamentCoordinator), `events.py` (EventBus, ring buffer 500), `cli.py`, `server.py` (FastAPI/WS)
- `ui/` — React+Vite TournamentBoard; `src/types.ts` **is the authoritative event contract**; `src/mockRun.ts` scripted replay (demo fallback)
- `seeds/` — calibrated flaky tests (plain python scripts, exit 0=pass 1=fail; NOT pytest)
- `scripts/calibrate_seeds.py` — the calibration harness (the verified Daytona usage pattern lives here — copy it, don't reinvent)
- `docs/` — strategy + verified research. `WINNING-IDEA.md` = product/demo source of truth. `ARCHITECTURE.md` = design. `DAYTONA-COOKBOOK.md` = verified SDK patterns. `EVENT-RULES.md` = official hackathon rules.
- `calibration-results.json` — measured data (gitignored), regenerate rather than edit

## Daytona — verified facts (do NOT rediscover these)
- **Containers: `target="us"`**, default snapshot, create ≈0.7–2s, python3 preinstalled, `/tmp` writable. **Linux VMs: only `target="us-east-1"`** (special grant), need a NEW snapshot (`CreateSnapshotParams(..., sandbox_class=SandboxClass.LINUX_VM, image="ubuntu:22.04")`), bare image (no python3/curl), home is `/root`, declarative builder disabled there. This project's swarm uses **containers in `us`** — VMs are not on the critical path.
- **One exec round-trip per trial** (write seed + run in a single `process.exec`) — this doubled throughput (3.1→6.1 trials/s). True unit cost ≈5s per 16-concurrent batch of execs, NOT sandbox create time.
- Auth: `.env` `DAYTONA_API_KEY` (already filled). SDK: `pip install daytona` (plain). `sandbox.copy()` is Pydantic model-copy, NOT a fork — fork is `SandboxApi.fork_sandbox` (low-level client, VM-only, sequential, source must be STARTED).
- Never call `network_block_all=True` on a running sandbox mid-demo — it can kill the SDK control channel.

## Isolation levels (core design decision — respect it)
- `isolation="process"` (DEFAULT): reuse warm pooled sandboxes; each trial = fresh `python3` process = fresh PYTHONHASHSEED + scheduling. Correct for order/scheduling flakes. **6.1 trials/s measured.**
- `isolation="sandbox"`: fresh sandbox per trial (destroy dirty in background, lazy refill). ONLY for state-polluting flakes (filesystem/port/env). 2.7 trials/s.
- A trial with an INFRA error never returns its sandbox to the pool. Infra errors are excluded from flake-rate math (`errors` key), never counted as failures.

## Seeds — calibration law
- A seed is only usable if **calibrated on Daytona** (not locally!): 40+ trials, target 40–55% fail = IDEAL. Current primary: `seeds/test_dict_order.py` — **51% (CI 36–66%), locked**.
- **Thread/timing races DO NOT flake on this substrate** (0/120 measured across 3 variants incl. Barrier + split read-modify-write). Do not write race/timing seeds; do not claim "we reproduce race conditions" in any copy.
- Local flake rates are meaningless — CPython version and CPU constraints differ. Always calibrate via `scripts/calibrate_seeds.py`.
- Seeds are dependency-free single-file python scripts using `sys.exit(0|1)`. Keep them that way (they must run on the bare container python3).

## Event contract (engine ⇄ UI — `ui/src/types.ts` is authoritative)
Types: `run_started {test_name, planned_trials}` · `trial_done {hypothesis_id|null, trial_index, passed, duration_s}` · `detect_done {flake_rate, wilson_ci, trials, fails}` · `hypothesis_created {id, cause_class, explanation}` · `hypothesis_verified {id, flake_rate, wilson_ci, trials}` · `hypothesis_eliminated {id, reason?}` · `winner_confirmed {id, flake_rate, confirm_flake_rate}` · `quarantine_confirmed {best_id, dossier}` · `tournament_done`.
Rules: `trial_index` is per-context 0-based (detect series = `hypothesis_id: null`; each lane its own series). `wilson_ci` = `[low, high]` fractions 0..1. Change the contract in BOTH types.ts and the engine emitter in the same commit, or don't change it.

## Statistics — non-negotiable
- Wilson 95% CI everywhere a rate is shown ("0/50" is reported as "≤7% at 95% confidence", never "0%").
- Winner = lowest flake rate whose CI upper bound < original's rate, then a **fresh confirmation round** (guards selection bias). No winner → QUARANTINE verdict with evidence dossier — the run never dead-ends.
- Adaptive early-stop: stop when CI fully excludes the decision threshold. Don't remove it; it's why the live demo fits 3 minutes.

## Sponsor integrations (depth over breadth — sponsor usage is a BONUS criterion, not a pillar)
Load-bearing: **Daytona** (the swarm), **Braintrust** (each hypothesis = an Experiment; each batch = eval run; permalink = the audit receipt), **Fireworks** (DiagnosisEngine: OpenAI-compat, base_url `https://api.fireworks.ai/inference/v1`, models `accounts/fireworks/models/{glm-5.2,kimi-k2.7,...}` — verify slugs on dashboard day-of). Bonus: **CodeRabbit** (reviews the real output PR; latency 1–5 min — always pre-run, never claim live turnaround), **ElevenLabs** (v3 emotional-tag narration, OUTPUT only — never live voice input). Cut: CopilotKit, WorkOS. Do not add sponsor calls that aren't load-bearing.

## Honesty rules (judges include the engineers who built these tools)
- Never state a latency/throughput/flake number that wasn't measured in THIS repo. Measured numbers live in `docs/WINNING-IDEA.md` ("MEASURED DEMO-TIMING TRUTH") and `calibration-results.json`.
- Anything pre-computed for the demo (cached hypotheses, pre-run CodeRabbit review) is disclosed unprompted in the pitch, never passed off as live.
- The trap opening is branch-proof by design (green → "would you merge?"; red → rerun until green → "would you merge NOW?"). Don't script a version that needs 3 consecutive greens (P≈12% at 51% flake).

## Workflow
1. Make changes → 2. `python3 -m compileall -q engine/retrial` (fast) and/or `npm run build` in ui/ → 3. If engine behavior changed, run a real smoke: `retrial.cli check` with `--max-trials 8` against live Daytona (keys in .env; cheap) → 4. Commit.
- Commit style: imperative summary + `Co-Authored-By: Claude <model> <noreply@anthropic.com>`. Push to `origin main` (private repo `nihalnihalani/retrial`).
- Multi-agent work: file boundaries are law — engine/ vs ui/ vs seeds/+scripts/ vs docs/. Never edit outside your lane; coordinate contract changes through the lead.
- Sandbox hygiene: every code path that creates sandboxes MUST delete them (try/finally). Check for leaks with `daytona sandbox list` if a run crashes.

## Don'ts
- DON'T run `pytest` inside sandboxes — seeds are plain scripts; pytest isn't installed in the container image.
- DON'T "fix" the 51% flake rate of `test_dict_order.py` or make it deterministic — the flakiness IS the fixture. (The tournament's winning hypothesis patches a COPY, never the seed file.)
- DON'T write to `/` or `/work` in sandboxes (permission denied as non-root) — use `/tmp` (containers) or `/root` (VMs).
- DON'T create sandboxes in a loop without concurrency (use threads, 16 at a time) or without cleanup.
- DON'T add retry-forever loops around Daytona calls; fail the trial as an infra error and move on.
- DON'T import heavyweight frameworks into the engine (no celery/redis/langchain) — threads + stdlib + the 4 SDK deps only.
- DON'T trust a request field is wired just because the POST succeeded — Pydantic v2 silently DROPS unknown body keys. When adding an API field, verify it in the response echo or behavior, not absence of errors.
- DON'T commit `.env`, `calibration-results.json`, or `ui/node_modules` (gitignored — keep it that way).
- DON'T touch `docs/VERDICT.md` / `docs/ADE-DESIGN.md` framing — superseded history; `docs/WINNING-IDEA.md` is the only product source of truth.

## Demo controls (UI)
Default URL = winning-path REPLAY, spotless console. `?mock=quarantine` = no-winner rehearsal. `?live=1` = attempt engine WS (falls back to replay). ↻ Replay button restarts cleanly. Mock test name must always be an ORDER-DEPENDENCY-class name (never race/timing — we measured those don't flake here and must not imply otherwise).

## Env keys (.env at repo root; coupon codes in .env.example comments)
`DAYTONA_API_KEY` ✅ filled · `FIREWORKS_API_KEY` ⏳ (blocks DiagnosisEngine) · `BRAINTRUST_API_KEY` ⏳ · `ELEVENLABS_API_KEY` ⏳ · `GITHUB_TOKEN` (or gh CLI) for PRSmith.

## When you make a mistake this file didn't prevent
Add the rule here in the same commit as the fix. Keep this file under 150 lines — delete rules the code now makes obvious.
