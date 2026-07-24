# The Hardened Winning Idea — "Flaky Test Detective" (working name: Quorum)
Supersedes the "race to fix a seeded bug" framing in ADE-DESIGN.md. Reframed 2026-07-23 after a 5-agent adversarial+research swarm broke the old idea. 2-day build + Claude Code.

## The problem (real, recurring, budgeted, hot in 2026)
Flaky tests — pass sometimes, fail sometimes — are the #1 trust-killer in CI. In 2026 Bitbucket, Datadog, and Kong all shipped flake-fixer agents (validates the pain is real; judges will believe it). But none do it the way we do.

## The idea
Point it at a flaky test. It:
1. **Fireworks** models generate N *competing hypotheses* for the root cause (race condition, test-order dependency, timing, shared state…) + a candidate fix each.
2. **Daytona** spins up a swarm of 20–50 disposable parallel sandboxes and reruns each candidate K times to compute an **empirical flake rate** (e.g. 24/50 fails → 0/50 fails). VERIFIED: 16 concurrent containers create+run in 2.0s.
3. **Braintrust** scores each candidate as an eval — scorer = pass-rate over K reruns (real, reproducible eval, NOT an LLM vibe-check) — with an **agent-as-a-judge** layer inspecting the reasoning trace, not just pass/fail.
4. A **human promote gate** approves the winning diff before the PR opens (shipped as the React modal + `POST /promote` feeding PRSmith).
5. A live **tournament board** (React over WebSocket) — the grid of sandboxes flickering green/red as reruns land.

## Why this WINS where "bug race" lost (survives the devils-advocate attacks)
- **"4× cost is insane"** → flake detection *inherently needs* many reruns for statistical confidence. Parallel disposable sandboxes are the RIGHT tool. The cost IS the product.
- **"Cursor 2.2 already ships multi-agent judging"** → Cursor judges final diffs by LLM vibes; it canNOT spin 30 throwaway sandboxes to measure empirical flake rate. Genuinely different, and defensible.
- **"No flywheel / feature not company"** → flaky CI fails constantly = real recurring trigger; roadmap = repo-specific flake leaderboard that compounds ("Kimi wins 63% of race-condition fixes on your repo").
- **"Objective referee is just vibes"** → the scorer is EMPIRICAL flake rate — the most objective eval possible. This is the white space competitor research confirmed (nobody does objective-eval selection; all do human-pick or LLM-judge).
- **"Daytona is decorative"** → now LOAD-BEARING; the whole demo is Daytona's speed/parallelism/disposability.
- **Academic backing**: arXiv 2604.16529 "Recursive Tournament Voting for agentic coding" (Apr 2026) + Agent-as-a-Judge papers — cite when asked "why parallel not one good agent."

## Sponsor coverage (load-bearing only — claim nothing without a code path)
Daytona (the swarm — flagship), Fireworks (N hypothesis models; Fast tier; $1.5B raise Jul 17 to namedrop), Braintrust (empirical-flake-rate scorer — their eval-first pitch, exactly). Other sponsor tools were considered as polish but never built; only integrations with real code paths are claimed (see [SPONSORS.md](SPONSORS.md)).

## Delivery vehicle
Keep the Electron + menu-bar shell (user wants native Mac app; fits "watches your CI, tells you when flakes are fixed" like CodexBar watches usage). Build web-first (works standalone, all sponsors), wrap in Electron near the end. Menu-bar: "🟢 test suite stable · 1 flake fixed today."

## Demo (3 min)
1. Positioning: "Flaky tests are the #1 reason teams stop trusting their CI. Existing fixers guess a cause with one agent. We run a *tournament*." 
2. Point at a real flaky test (pre-seeded, genuinely nondeterministic). Show it failing ~half the time.
3. Hit go → tournament board lights up: N hypotheses × sandboxes flickering green/red live.
4. Board converges: the winning fix survives 50/50 reruns; empirical flake rate 48% → 0%. Braintrust trace = the receipt.
5. The human promote gate approves it; menu-bar flips green: "fixed and verified across fifty runs."
6. Close: roadmap = repo-specific flake leaderboard (the compounding moat). Fireworks $1.5B, Braintrust eval-first — "we're the empirical-eval layer for agentic code."

## Blocking unknowns resolved / open
- ✅ Daytona concurrency: 16 concurrent containers in 2.0s VERIFIED. 30-50 via batches fine.
- ⚠️ Confirm hackathon-tier credit burn for repeated 20-50 sandbox bursts across a full demo+rehearsals — ask Dalin (cheap: containers are ~$0.05/vCPU-hr, sub-2s lifetimes).
- ⚠️ Seed a GENUINELY flaky test that fails ~40-50% reliably (race condition on a shared resource) — must be calibrated so the demo isn't boring (all pass) or dead (all fail).
- Pending: da-originality (is flake-fixing too crowded? — my read: incumbents validate the pain, none do parallel-empirical-tournament, so it's ideal) + da-demo (2-day build order, live-demo fragility).

## WINNING-EDGE upgrades (2026-07-23 third pass — what separates top-8 from first place)
1. **THE TRAP OPENING (best presentation move available).** Run the seeded test live 3× — green, green, green. Ask the room: "Raise your hand if you'd merge this." Hands go up. Reveal: it's 42% flaky — "your CI just lied to you, and you believed it." The judges FEEL the problem instead of being told. Then the demo is the antidote.
2. **Split-screen "caught in the act":** show the SAME test passing in one sandbox and failing in another AT THE SAME INSTANT. Same code, same test, both live. The lie made visible — the screenshot/thumbnail for the Devpost video.
3. **FULLY LIVE demo — say so.** Our latency profile actually fits 3 minutes (triage ~20s, tournament ~60-90s at 16-concurrent, verified). Most finalists pre-record or stage; we can truthfully say "nothing you're watching is pre-recorded." Rare and judges notice.
4. **Competitive one-liner vs incumbents:** Datadog/Bitbucket detect flakes from WEEKS of CI history; we detect in a MINUTE of sandboxes, on demand, on any repo. "They need a month of history. We need sixty seconds."
5. **No-dead-end output — quarantine with dossier:** if no fix fully stabilizes, auto-open a PR quarantining the test WITH the evidence dossier (flake rate, CI, hypothesis autopsy). Worst case is still a valuable, real-workflow output. Completeness = Technical Execution points.
6. **Braintrust Experiments AS the tournament scoreboard:** model each hypothesis as a Braintrust Experiment so their own dashboard displays our tournament. Using the sponsor's product as the scoreboard (not just an SDK call) is the "Best Use of Braintrust" move.
7. **Impact ammo — CORRECTED 2026-07-25. Cite these, with these denominators, and nothing else.** Google (Micco, *Advances in Continuous Integration Testing @ Google*): almost **16% of 4.2M tests** have some flakiness; **1.5% of test executions** report flaky; **2-16% of compute** goes to re-running them; **84% of Pass→Fail transitions** are flaky. Microsoft (Lam et al., ISSTA 2019): **27.4% of builds** exhibit flaky tests; **4.6% of test cases** are flaky. Google TAP **4.56% of failures** flaky — note this one is reported by Luo et al. FSE 2014, NOT published by Google; attribute it to Luo.
   **RETRACTED — do not reuse:** "~3.7 engineering hours per flaky test" and "~30 min per investigation" have **no primary source** (vendor cost-calculator blogs only). "1-in-7 suite runs" is the 16%-of-*tests* figure with the denominator swapped — one fact counted twice. "~25% of CI test failures" is Microsoft's 27.4% of *builds*. "15-30% of CI time on reruns" is Google's measured "2-16% of compute resources", roughly doubled. "$400k/yr for a 50-eng team" is unsourced.
   **And do not "attribute softly."** The previous version of this line instructed exactly that while its own bracket admitted the chain was `[via StickyMinds/TestDino/Autonoma aggregations]` — i.e. a written plan to launder aggregator numbers as primary research, in a project whose CLAUDE.md forbids stating unmeasured numbers. Quote a primary or say nothing.
8. **NAME: "Polygraph" is TAKEN (Nx shipped a devtool named Polygraph in June 2026 — judges may know it). Use "Retrial"** — literally what it does: every rerun is a re-trial; statistical trials; a court that establishes truth. Pitch: "Every flaky test deserves a retrial. Fifty of them, actually." (Alts: Verdict, Alibi, Gaslight.)

## Deep-analysis refinements (2026-07-23 second pass)
1. **The thesis (say it): verification asymmetry.** Flaky tests are the one bug class where VERIFICATION, not generation, is the bottleneck — a green run proves nothing at 40% flake. That's why a disposable-sandbox swarm is the right-shaped tool here and decoration everywhere else. Pitch line: "Everyone builds machines that generate fixes. We built the machine that proves them."
2. **Race HYPOTHESES, not models** (differential diagnosis framing): the unit of competition is the root-cause hypothesis (race condition / order dependency / timing / shared state), each with a treatment; evidence eliminates them. Fireworks multi-model = hypothesis diversity (secondary). Kills the "why 4 models not 1?" attack for good.
3. **Two-act demo — triage first ("the lie detector"):** Act 1: red CI with 3 failures → swarm reports "1 real bug, 2 flakes" in seconds. Hook: "Your build isn't broken — it's lying. We built the lie detector." Act 2: fix tournament on one exposed flake. Each act stands alone if the other stumbles.
4. **Statistical rigor as a feature:** Wilson CIs (0/50 = "≤7% at 95% confidence"); CONFIRMATION ROUND on the winner (guards selection bias across 4 candidates — CTO-judge catnip); fresh-sandbox-per-run is REQUIRED for shared-state flakes (preempts "why not pytest --count=50 locally?" — the sharpest remaining attack).
5. **Cost math (narrate):** 4 hypotheses × 50 reruns = 200 sandbox-runs ≈ ~1 min wall-clock at 16-concurrent, sub-cent each. Buying statistical certainty, cheaply.
6. **#1 PRE-BUILD TASK / unsolved risk: calibrate the seed ON DAYTONA.** Race/timing bugs behave differently in virtualized envs — a 50%-local flake may be 5% or 95% in a sandbox. Build 2-3 candidate flaky tests, measure their flake rates inside Daytona sandboxes, pick the one at 40-55%. NOTHING else gets built before this is proven.

## Hardening from the 5-agent adversarial swarm (applied)
The last two attackers hit the OLD "bug race" framing, but their findings transfer:

**da-originality's kill shot — mostly NEUTRALIZED by this reframe, but respect the category.** Their attack: "every piece of the moat (cloud isolation, objective referee, review gate) already ships separately — agentbox runs fan-out on Daytona, bernstein auto-verifies with tests, and Braintrust's OWN Sandbox Evals product IS 'objective eval in a sandbox.'" Why Flaky Test Detective dodges most of it: those tools do SINGLE-PASS verification — which flaky tests specifically DEFEAT (that's what makes them flaky). Our approach (N hypotheses × empirical rerun-rate) is the specific unclaimed thing; the Bitbucket/Datadog/Kong flake-fixers are single-agent diagnostics, not hypothesis tournaments. BUT: still preempt the CATEGORY by name in the first 20s, not one competitor.
- **Opening dedupe line (say verbatim):** "Bitbucket, Datadog and Kong all shipped flaky-test fixers this year — they run ONE agent that guesses a cause. Tools like bernstein auto-verify fixes with a single test run — but a single run is exactly what a flaky test beats. We run a hypothesis tournament and PROVE the winner with fifty reruns you can audit in Braintrust."
- **Sell the AUDIT TRAIL, not just 'objective'** (da-originality's best save): the Braintrust permalink showing 48%→0% flake rate across 50 runs is a *governance receipt* — "we didn't just fix it, we proved it's fixed, reproducibly." That's the narrow, CTO-legible, unclaimed angle. Frame Braintrust as the tool we USE for the receipt (using the sponsor well), never as a "referee we invented."

**da-demo's guaranteed-failure bug — DODGED by this wedge, but noted.** Daytona preview URLs need an `x-daytona-preview-token` HEADER, which a plain `<iframe src>` CANNOT attach (only fixable in Electron via `webRequest.onBeforeSendHeaders`). The old plan's live-app-iframe centerpiece was a guaranteed on-stage blank in front of the sponsor judges. **Flaky Test Detective's UI is a status tournament BOARD (green/red rerun counts), NOT embedded live-app iframes — so this trap largely doesn't apply.** If any preview iframe IS used, build+test it inside Electron on Day 1, never defer to last. Also: verify actual package versions Day 1 rather than hunting for rumored npm tags.

**Calibrated seed (both attackers):** the flaky test must fail ~40-55% reliably (a real race condition on a shared resource/timing), pre-tested, with a scripted branch for "converges cleanly" AND a backup seed. This is now the #1 build-prep task.

## Doc hygiene (da-demo flag)
WINNING-IDEA.md is now the SINGLE SOURCE OF TRUTH. VERDICT.md (Fork Wars bug-race) and ADE-DESIGN.md (Slipstream ADE) are SUPERSEDED — do not build off them. SPONSORS.md / DAYTONA-COOKBOOK.md / PLAYBOOK.md / PRECEDENTS.md remain valid reference.

## Open question to confirm with user
Build window: the event schedule is a 1-day sprint (~5.5h), but user said "2 days + Claude Code" = likely 2 days of PRE-BUILD before presenting. Confirm. If 2 days: build the empirical-flake engine Day 1, UI+sponsors Day 2, rehearse. If 5.5h: cut to the flake-engine + board + voice, pre-seed everything.

## Name
"Flaky Test Detective" is the description. Product-name candidates: **Quorum** (many reruns must agree the test is stable — ties to objective consensus), **Heisen** (heisenbug = the dev term for flaky/nondeterministic bugs — insider cred), **Deflake**. Recommend **Quorum**.

## CALIBRATION NIGHT RESULTS (2026-07-23, 3 rounds, ~360 real Daytona trials)
- **PRIMARY SEED LOCKED: seeds/test_dict_order.py — 51% flake (95% CI 36-66%) = IDEAL.** Hash-randomization order dependency; authentic class; fix = deterministic ordering.
- Thread/timing races (3 variants incl. barrier + split read-write) NEVER flaked on this substrate (0/120) — CPU-constrained container scheduling suppresses them. Do NOT demo race-condition seeds; do NOT claim "we reproduce race conditions" — say "scheduling-dependent flakes" only where true. Backup-seed candidate if time allows: service-startup race (connect-before-ready), a common real CI flake class — calibrate before trusting.
- **TIMING TRUTH (da-build):** full trial round-trip (create+write+exec+delete) = ~1.5-2 trials/s at 16-conc, NOT the 2s-for-16 create-only number. FIX ADOPTED: per-seed ISOLATION LEVEL — `process` isolation reuses warm sandboxes (fresh python3 process = fresh PYTHONHASHSEED + scheduling; correct for order/scheduling flakes; ~10x throughput, reclaims the fully-live demo) vs `sandbox` isolation (fresh sandbox per trial, only for state-polluting flakes). This is ALSO a stronger technical story: "isolation level matched to flake class."
- **TRAP OPENING REDESIGNED (branch-proof, works at any flake rate):** run the test ONCE live. Green → "raise your hand if you'd merge." Red → "CI's red... do what we all do: hit rerun" → reruns until green (expected ≤2) → "NOW would you merge?" Either branch dramatizes the lie; the red branch is actually stronger (it acts out the rerun-until-green anti-pattern every engineer does).
- **VIDEO MUST BE RECORDED TODAY** (one-day event; no calm window tomorrow). First 15s = cold-open on the split-screen + stat ("passed 3 times today — it's 51% broken"), not scene-setting.
- **USER ACTION (only blocker no agent can clear): redeem FIREWORKS (DEVREL-WEBINAR1) and BRAINTRUST (BT-DISCOUNT-HACKATHON) keys into retrial/.env.** DiagnosisEngine = the creative core; unbuildable without the Fireworks key. Cached-hypothesis fallback will exist but live generation is the honest path.

## MEASURED DEMO-TIMING TRUTH (engine, 2026-07-23 night — cite these, not estimates)
- Process isolation (warm-pool reuse, fresh python3 per trial): **6.1 trials/s** → ~200 trials ≈ 33s. Sandbox isolation (fresh sandbox per trial): 2.7 trials/s.
- Full tournament (detect + 2 hypotheses + confirm, 80 trials): **17.3s**, verdict FIXED, all event types fired. The fully-live 3-minute demo is REAL with margin.
- Perf lever was collapsing write+run into ONE Daytona exec round-trip (~5s per 16-concurrent batch is the true unit cost, not sandbox create).
- **Pre-warm (measured): run_started → first trial = 0.60s** (was 12.5s cold; pool warm-up execs pay cold-start before GO).
- **FULLY-GENERATED RUN (2026-07-23, no cached hypotheses):** detect 44% (7/16, CI 23-67%) → 4 live Fireworks hypotheses → 3/4 correctly identified order-dependency/PYTHONHASHSEED; the 'timing' guess stayed 56% flaky and was ELIMINATED ("CI overlaps original flake rate") → winner confirmed 0/24 (CI ≤14%) → verdict FIXED with real Braintrust permalink. The differential-diagnosis story happened for real, autonomously.
- Pitch line now measured: "isolation level matched to flake class — fresh interpreter for order/scheduling flakes, full sandbox teardown only for state-polluting flakes."

## LIVE-DIAGNOSIS TIMING DECISION (2026-07-23)
- Measured: live 4-model Fireworks diagnosis = 23-29s (parallel, bounded by slowest model). POST returns instantly; `diagnosing` event fires; run_started ~29s later.
- **DEMO STRUCTURE: hit GO at second ZERO of the pitch.** The trap opening + problem statement (~45-60s of narration) covers the diagnosis window — the tournament starts live right as the story arrives at it. FULLY live, nothing cached, no dead air; the DIAGNOSING badge is a teaser during the open.
- Fallback (bad venue wifi): POST cached hypotheses (path exists), disclosed unprompted per honesty rules.
- TOURNAMENT_CONC=8 (peak ~32 concurrent at 4 lanes) verified; detect/confirm still full conc.
- Hypothesis quality across 2 live runs: 2-3 of 4 models correctly identify order_dependency w/ accurate PYTHONHASHSEED explanations; wrong guesses (shared_state, timing) get empirically eliminated with reasons — the differential-diagnosis story happens for real.
