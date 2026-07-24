# Retrial — Architecture
Patterns stolen from orca + CodexBar (ideas, zero code). Verified Daytona primitives from DAYTONA-COOKBOOK.md. Single source of truth for the build; pairs with WINNING-IDEA.md.

## Stack decision
- **Engine + server: Python** (FastAPI) — all Daytona code is already verified in Python; Fireworks is OpenAI-compat; Braintrust has a first-class Python SDK.
- **UI: React + Vite** (tournament board) talking to the server over WebSocket/SSE.
- **Native feel: OPTIONAL Electron wrap + Tray at the end** — rubric gives no points for packaging; the Tray is a 30-min flourish only if everything else is done.

## Component map (with provenance)

```
                                ┌────────────────────────────────────────────┐
                                │  SURFACES                                  │
                                │  • Web UI: TournamentBoard (React)         │
                                │  • CLI: `retrial check <test>` (orca-CLI   │
                                │    pattern) + CI workflow (`retrial.yml`)  │
                                │  • serve daemon: FastAPI + WS  (CodexBar   │
                                │    `codexbar serve` pattern)               │
                                │  • Tray/menu-bar (CodexBar shell pattern,  │
                                │    STRETCH)                                │
                                └───────────────▲────────────────────────────┘
                                                │ typed events (WS/SSE)
┌──────────────┐   events   ┌───────────────────┴──────────────┐
│ HOOKS/EVENT  │◄───────────│  TOURNAMENT COORDINATOR          │  ← orca orchestration-coordinator
│ BUS          │            │  DAG: detect → diagnose →        │    pattern (dispatch → worker_done
│ (CodexBar    │            │  verify-per-hypothesis (parallel)│    → decision gates)
│ hooks-engine │            │  → confirm winner → gate → ship  │
│ pattern:     │            └──┬──────────┬──────────┬─────────┘
│ event→JSON→  │               │          │          │
│ subscriber,  │        ┌──────▼───┐ ┌────▼─────┐ ┌──▼────────────┐
│ rate-limited)│        │DIAGNOSIS │ │VERIFIER  │ │ PR SMITH      │
│ Subscribers: │        │ENGINE    │ │(stats)   │ │ fix PR or     │
│ • UI stream  │        │Fireworks:│ │Wilson CI,│ │ quarantine PR │
│ • log        │        │N models →│ │confirm   │ │ + evidence    │
│              │        │competing │ │round,    │ │ dossier;      │
│              │        │hypotheses│ │adaptive  │ │ human promote │
│              │        │+ patches │ │early-stop│ │ gate approves │
└──────────────┘        └──────────┘ └────┬─────┘ └───────────────┘
                                          │ leases fresh env per trial
                        ┌─────────────────▼──────────────────┐
                        │  SANDBOX POOL                      │ ← orca OrcaVmRecipe pattern
                        │  recipe: warm/lease/run/recycle/   │   (create/suspend/resume/destroy)
                        │  destroy; pre-baked Daytona        │
                        │  snapshot; 16-concurrent verified  │
                        │  (2.0s for 16); fresh-per-trial    │
                        └─────────────────┬──────────────────┘
                                          │
                        ┌─────────────────▼──────────────────┐
                        │  EVIDENCE LEDGER                   │
                        │  • Braintrust: 1 Experiment per    │
                        │    hypothesis, 1 span per trial —  │
                        │    their dashboard IS the          │
                        │    scoreboard; permalink = receipt │
                        │  • SQLite: flake genome (repo      │
                        │    taxonomy, model win-rates) —    │
                        │    the compounding flywheel        │
                        └────────────────────────────────────┘
```

## Components in detail

1. **SandboxPool** *(orca's OrcaVmRecipe lifecycle, reimplemented)* — `warm(n)` pre-creates from a pre-baked snapshot (deps installed once — verified pattern); `lease()` hands a virgin sandbox to a trial; `recycle()`/`destroy()` after. Fresh-env-per-trial is the scientific requirement (shared-state flakes), not an implementation detail — say so in the pitch.
2. **TrialRunner** — one test execution in one leased sandbox → `{pass|fail, duration, log_tail}`. Dumb on purpose; all intelligence lives above.
3. **Verifier** — batches trials; computes flake rate + **Wilson 95% CI**; **adaptive early-stop** (stop when the CI excludes the decision threshold — saves time/cost, and is a genuine technical-depth flex); **confirmation round** on the winner (guards selection bias across hypotheses).
4. **DiagnosisEngine** *(Fireworks)* — differential diagnosis: prompts N models (glm-5p2, glm-5p1, kimi-k2p6, deepseek-v4-pro (verified)) for structured `{cause_class, explanation, patch}`. Hypotheses are the racers; models supply diversity.
5. **TournamentCoordinator** *(orca's orchestration pattern)* — the DAG: **detect** (rerun unmodified test → flake rate = the lie detector) → **diagnose** → **verify each patched hypothesis in parallel** → **confirm** winner → **gate** → **ship**. Emits typed events at every transition; decision gates handle all-pass (efficiency tiebreak), all-fail (quarantine path), and mid-race progress.
6. **EventBus / Hooks** *(CodexBar's hooks-engine)* — every event (`trial_done`, `detect_done`, `hypothesis_eliminated`, `winner_confirmed`, `promotion_pending`, `bisect_narrowed`, …) is a typed JSON payload fanned to subscribers: the UI stream and logs. Every emitted type is registered in `events.EVENT_TYPES` and enforced by an ast-based emit-site scan in `tests/test_events.py`.
7. **EvidenceLedger** — Braintrust Experiments as the public scoreboard + permalink receipts; SQLite **flake genome** (cause-class taxonomy per repo, model win-rates per cause) — the flywheel card in the UI and the "does it compound?" answer.
8. **PRSmith + promote gate** — winner ⇒ fix PR with the dossier in the body (flake 48%→0%, CI, Braintrust link); no winner ⇒ **quarantine PR** with dossier. Either waits at the human promote gate (a React modal + FastAPI `/promote` endpoint — a human approves before PRSmith opens the PR via `gh api`; `PROMOTE_GATE=0` restores auto-PR). The demo cannot dead-end.
9. **Surfaces** — TournamentBoard web UI (trap view → split-screen caught-in-the-act → lanes with live trial counters → genome card); `retrial` CLI (`check` / `diagnose` / `bisect`) + GitHub Actions workflow (`.github/workflows/retrial.yml`, which runs `python -m retrial.cli check` on failing CI — the real-world trigger); FastAPI serve daemon *(CodexBar serve)*; Tray *(stretch)*.

## Fork engine & time travel (the Rewind merge)

Two components ported from the Rewind execution-search engine (see `MERGE-PLAN.md`):

- **ForkSandboxPool** (`engine/retrial/forkpool.py`, `RETRIAL_POOL_BACKEND=fork`) — warms ONE root sandbox, freezes it as a checkpoint (`_experimental_fork` + `pause`: the paused fork-child captures fs+RAM while the root keeps running), then forks byte-identical trial clones from the checkpoint. The statistics argument: identical initial state means trial-to-trial variance is purely the flake, not provisioning noise. Same 7-method surface as `SandboxPool` (drop-in via the `make_pool` factory); on ANY fork-path failure it degrades — stickily — to a `SandboxPool` fallback and emits `pool_degraded`. Spend guard: `RETRIAL_MAX_FORKS`. Default backend stays `snapshot`.
- **FlakeBisector** (`engine/retrial/bisect.py`, `retrial bisect` / `POST /bisect`) — time-travel bisection for order-dependency flakes: run the suite prefix in a live root while freezing a checkpoint at every test boundary (checkpoint *k* = state after the first *k* tests), probe checkpoints by forking single-use clones and rerunning ONLY the suspect with the verifier's Wilson-CI oracle, and binary-search to the flip. Emits `bisect_started` / `checkpoint_created` / `checkpoint_probed` / `bisect_narrowed` / `bisect_done`; the UI renders the checkpoint rail. Honest limitations (also in `--help`): requires the fork backend (no snapshot fallback — the capability IS the fork), assumes a monotonic step function across checkpoints (mitigated by a full-budget confirmation pass; a contradiction reports inconclusive). Fork issuance from a shared checkpoint handle is serialized behind a lock — the only proven `_experimental_fork` usage is single-threaded, and the mocked-SDK stress test pins that discipline.

Verification honesty: no live keys exist in CI, so the whole fork path is exercised against a mocked Daytona SDK (`tests/`); live fork-primitive timings cite Rewind's spike results, clearly attributed.

## 2-day build order (risk-ordered)
**Day 1 (today):** ① Seed calibration ON DAYTONA (2-3 candidate flaky tests, measure rates, pick 40-55%) — GO/NO-GO. ② SandboxPool + TrialRunner + Verifier → the lie detector works headless. ③ DiagnosisEngine + Coordinator → full tournament headless. ④ Braintrust wiring.
**Day 2 (pre-event):** ⑤ TournamentBoard UI + EventBus stream. ⑥ PRSmith. ⑦ Record the 2-min Devpost video off a clean run. ⑧ Tray/Electron only if everything above is done. On-site 5.5h: polish, live-CI trigger, rehearse trap opening, re-record video if better.

## Why this architecture wins (rubric mapping)
- **Impact 25%:** real recurring pain (Google: ~16% of tests flaky, 3.7 eng-hrs each; Microsoft: ~25% of CI failures) + real workflow outputs (PRs, CI trigger) — not a demo toy.
- **Technical 25%:** verified sandbox swarm, real statistics (Wilson, confirmation, adaptive stop), event-driven DAG, works end-to-end headless before any UI exists.
- **Creativity 25%:** verification-asymmetry thesis + differential-diagnosis tournament + empirical selection — the unclaimed niche 5 adversarial agents couldn't break.
- **Presentation 25%:** trap opening, split-screen lie, fully-live demo, genome close — beats designed in, not bolted on. (An earlier draft listed a "voice autopsy" beat; there is no voice/audio code path in this repo — see [SPONSORS.md](SPONSORS.md) for the retraction.)
- **Bonus:** Daytona + Braintrust integrated at "their-product-is-our-substrate" depth (two Best Use shots), Fireworks load-bearing. Only integrations with real code paths are claimed — see [SPONSORS.md](SPONSORS.md).
