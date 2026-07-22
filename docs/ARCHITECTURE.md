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
                                │    pattern) + `retrial ci` (GitHub Action) │
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
│ • Voice      │        │N models →│ │confirm   │ │ + evidence    │
│   (11Labs v3)│        │competing │ │round,    │ │ dossier;      │
│ • Tray       │        │hypotheses│ │adaptive  │ │ CodeRabbit    │
│ • log        │        │+ patches │ │early-stop│ │ reviews it    │
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
4. **DiagnosisEngine** *(Fireworks)* — differential diagnosis: prompts N models (GLM-5.2, Kimi K2.7, MiniMax M3, DeepSeek) for structured `{cause_class, explanation, patch}`. Hypotheses are the racers; models supply diversity.
5. **TournamentCoordinator** *(orca's orchestration pattern)* — the DAG: **detect** (rerun unmodified test → flake rate = the lie detector) → **diagnose** → **verify each patched hypothesis in parallel** → **confirm** winner → **gate** → **ship**. Emits typed events at every transition; decision gates handle all-pass (efficiency tiebreak), all-fail (quarantine path), and mid-race progress.
6. **EventBus / Hooks** *(CodexBar's hooks-engine)* — every event (`trial_done`, `flake_confirmed`, `hypothesis_eliminated`, `winner_confirmed`, `gate_cleared`) is a typed JSON payload fanned to subscribers: UI stream, **VoiceAnnouncer** (ElevenLabs v3 with emotional tags — "hesitant" on eliminations, "triumphant" on confirmation), Tray, logs. Rate-limited like CodexBar's.
7. **EvidenceLedger** — Braintrust Experiments as the public scoreboard + permalink receipts; SQLite **flake genome** (cause-class taxonomy per repo, model win-rates per cause) — the flywheel card in the UI and the "does it compound?" answer.
8. **PRSmith** — winner ⇒ fix PR with the dossier in the body (flake 48%→0%, CI, Braintrust link); no winner ⇒ **quarantine PR** with dossier. CodeRabbit reviews either. The demo cannot dead-end.
9. **Surfaces** — TournamentBoard web UI (trap view → split-screen caught-in-the-act → lanes with live trial counters → genome card); `retrial` CLI + `retrial ci` GitHub Action (the real-world trigger); FastAPI serve daemon *(CodexBar serve)*; Tray *(stretch)*.

## 2-day build order (risk-ordered)
**Day 1 (today):** ① Seed calibration ON DAYTONA (2-3 candidate flaky tests, measure rates, pick 40-55%) — GO/NO-GO. ② SandboxPool + TrialRunner + Verifier → the lie detector works headless. ③ DiagnosisEngine + Coordinator → full tournament headless. ④ Braintrust wiring.
**Day 2 (pre-event):** ⑤ TournamentBoard UI + EventBus stream. ⑥ PRSmith + CodeRabbit. ⑦ Voice. ⑧ Record the 2-min Devpost video off a clean run. ⑨ Tray/Electron only if everything above is done. On-site 5.5h: polish, live-CI trigger, rehearse trap opening, re-record video if better.

## Why this architecture wins (rubric mapping)
- **Impact 25%:** real recurring pain (Google: ~16% of tests flaky, 3.7 eng-hrs each; Microsoft: ~25% of CI failures) + real workflow outputs (PRs, CI trigger) — not a demo toy.
- **Technical 25%:** verified sandbox swarm, real statistics (Wilson, confirmation, adaptive stop), event-driven DAG, works end-to-end headless before any UI exists.
- **Creativity 25%:** verification-asymmetry thesis + differential-diagnosis tournament + empirical selection — the unclaimed niche 5 adversarial agents couldn't break.
- **Presentation 25%:** trap opening, split-screen lie, fully-live demo, voice autopsy, genome close — beats designed in, not bolted on.
- **Bonus:** Daytona + Braintrust integrated at "their-product-is-our-substrate" depth (two Best Use shots), Fireworks load-bearing, CodeRabbit/ElevenLabs natural.
