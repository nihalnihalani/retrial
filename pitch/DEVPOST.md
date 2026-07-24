# Retrial — Devpost Submission

> Paste-ready. Each `##` block maps to a Devpost field. Measured numbers only —
> every figure is sourced from this repo's calibration + engine timing runs
> (see `docs/WINNING-IDEA.md` → "MEASURED DEMO-TIMING TRUTH" and
> `calibration-results.json`). Nothing is estimated. Sponsor claims name a
> real code path or an explicitly disclosed pre-run workflow.

---

## Project name
**Retrial**

## Tagline
Your build isn't broken — it's lying. Retrial is the lie detector for flaky tests.

## Elevator summary (2–3 sentences)
Flaky tests pass and fail on the same code, so a green run proves nothing and a
red one gets ignored. Retrial reruns a suspect test across a swarm of disposable
Daytona sandboxes to measure its **empirical flake rate** with a Wilson 95%
confidence interval, then runs a **hypothesis tournament** — competing
root-cause fixes from Fireworks models, each re-verified statistically across
the swarm — and ships the survivor as an evidence-backed pull request with
Braintrust permalinks as the audit receipt. It's the machine that doesn't just
generate a fix; it *proves* the fix.

## Built with
Daytona · Fireworks · Braintrust · ElevenLabs · CodeRabbit · GitHub · Python ·
FastAPI · React · Vite

## Repo / media
- Public GitHub: https://github.com/nihalnihalani/retrial
- Demo GIF: `docs/assets/retrial-demo.gif`
- Verdict card: `docs/assets/verdict-live.jpg`
- Event: Daytona HackSprint w/ Braintrust — SF, July 2026

---

## Inspiration

Flaky tests are the number-one trust-killer in CI. A test that fails 40% of the
time will pass three times in a row often enough that a human will merge — and
will fail in production often enough that the team stops trusting the suite.
In 2026 the whole industry noticed: Bitbucket, Datadog, and Kong all shipped
flake-fixer agents. The pain is real, recurring, and budgeted.

Published research puts numbers on it (softly attributed): Google's work has
put roughly **16% of tests** somewhere on the flaky spectrum and reported about
**3.7 engineering hours** burned investigating a *single* flaky test, with
roughly **1 in 7** suite runs hitting a flaky failure. Microsoft has reported
that about **25% of CI test failures** are flaky, at ~30 minutes of
investigation each. Across the industry, teams burn an estimated **15–30% of CI
time** on reruns.

The deeper reason existing tools struggle is **verification asymmetry**. Flaky
tests are the one bug class where *verification*, not generation, is the
bottleneck. At a 40–50% flake rate, a single green run is statistically
meaningless — which is exactly how every incumbent verifies a fix. Detection
tools like Datadog and Bitbucket infer flakiness from *weeks* of accumulated CI
history. We wanted something that works on demand, on any repo, in about a
minute of sandboxes.

Everyone builds machines that generate fixes. We built the machine that proves
them.

We named it **Retrial** — literally what it does: every rerun is a re-trial; the
swarm is a court that establishes truth under evidence. (An earlier name,
Polygraph, was already taken by an Nx shipping tool.) The pitch line writes
itself: *"Every flaky test deserves a retrial. Fifty of them, actually."*

---

## What it does

Point Retrial at a flaky test. It runs a measured pipeline —

**detect → diagnose → tournament → confirm → promote → ship** —

where every stage is empirical, not asserted. A live React tournament board
subscribes to a typed event stream over WebSocket; the engine runs headless
either way.

### Act 1 — The lie detector (detect)

Retrial reruns the unmodified test across a swarm of disposable Daytona
sandboxes and measures its **empirical flake rate** with a **Wilson 95%
confidence interval**. Our locked demo seed (`seeds/test_dict_order.py`)
calibrated at **51% flake (95% CI 36–66%)** over real Daytona trials — an
order/hash-dependency flake class, not a race. A recent live detect clocked
**50% (95% CI 28–72%)**. The number *is* the lie made quantitative.

Incumbents need a month of CI history. We need sixty seconds.

**Detect-gate (honesty):** the tournament runs ONLY when detect verdict is
`FLAKY`. Otherwise the run terminates cleanly with no hypotheses and no PR:

| Detect verdict | Terminal | Meaning |
|----------------|----------|---------|
| ALWAYS_FAILING | REGRESSION | Fix the code, not the test |
| STABLE | ALREADY_STABLE | Nothing to fix |
| INCONCLUSIVE | INCONCLUSIVE_BASELINE | Can't prove flaky |
| ERROR | ERROR | No valid trials |

### Act 2 — Differential diagnosis (diagnose)

Fireworks frontier models generate *competing root-cause hypotheses* — order
dependency, shared state, timing, randomness — each with a concrete candidate
patch. The unit of competition is the **hypothesis**, not the model; multiple
models supply diversity of ideas. Live 4-model diagnosis takes roughly
**23–29 seconds** in parallel (bounded by the slowest model).

### Act 3 — The tournament (verify)

Every hypothesis' patched copy is re-trialed across the swarm in parallel
lanes. The board fills green/red live as trials land. Losers are eliminated as
their confidence intervals stop beating the original rate. Sustained throughput
measured at **6.1 trials/second** in process isolation — about 200 trials in
~33 seconds.

### Act 4 — Confirm (guard selection bias)

The winner isn't the one that looks right — it's the one that empirically
survives. Retrial picks the lowest flake rate whose **Wilson CI upper bound
falls below the original's rate**, then runs a **fresh confirmation round** on
that winner alone. A winning fix confirmed at **0/40** is reported honestly as
"**≤8.8% at 95% confidence**," never "0%." That recent live run went
**50% → 0/40 (FIXED)** with the full detect–diagnose–tournament–confirm arc in
**42.7 seconds** end to end — real Fireworks calls included.

### Act 5 — Ship (human in the loop)

The winning fix waits at a **promote gate** (React modal + `POST /promote`). A
human approves; then PRSmith opens a real pull request via `gh api` with the
evidence dossier in the body — flake rate before/after, Wilson CIs, Braintrust
permalinks. If nothing fully stabilizes the test, Retrial instead opens a
**quarantine PR** carrying the same dossier. The run never dead-ends.

### Extra capabilities that make it a product, not a demo script

- **Rewind engine (Daytona experimental fork):** opt-in fork-checkpoints for
  byte-identical trial starting state, plus **time-travel bisection** that
  freezes a suite at every test boundary and binary-searches to the test that
  poisons another.
- **Sandbox Observatory:** a live registry of every sandbox Retrial touches —
  role, lifecycle, fork lineage, exec history, destroy/reap controls, and an
  honest count-based spend estimate.
- **Flake genome:** every run records cause class and which model won —
  compounding into a repo-specific leaderboard (`GET /genome`).
- **ElevenLabs flake autopsy (opt-in):** a spoken verdict narrated from the
  evidence dossier after the run finishes — numbers that cannot drift from the
  board.
- **CodeRabbit (pre-run, disclosed):** reviews the evidence-backed PR Retrial
  opens; latency is 1–5 minutes, so the demo review is pre-run and we say so.

### Trap opening (how we make judges *feel* the problem)

Run the seeded test once live. Green → "Raise your hand if you'd merge." Red →
rerun until green (the anti-pattern every engineer does) → "NOW would you
merge?" Either branch reveals: it's ~51% broken. Your CI just lied to you, and
you believed it. Then Retrial is the antidote.

### Proven on a real bug: four plausible fixes, one survivor

We pointed Retrial at `test_rearrange` from
[penman](https://github.com/goodmami/penman) `v1.2.1` — a real MIT-licensed
Python OSS project, catalogued in **IDoFT** (Illinois Dataset of Flaky Tests,
category NOD) and already fixed by the maintainer in
[penman#102](https://github.com/goodmami/penman/pull/102). Root cause:
`model.random_order` orders roles with Python's `random` module. We fed the
models a **sanitized reproduction with every root-cause hint stripped**, then
ran the full four-model tournament:

- Detect measured **100% flake (16/16, 95% CI 81–100%)** on the sanitized repro.
- **All four models correctly identified the randomness root cause** from the
  failing test alone.
- **Only one produced a fix that actually worked.** `glm-5p2`'s patch — a
  *valid alternative*, not the maintainer's seed approach — drove the flake to
  **0/16**, then survived a **fresh confirmation round of 25 trials across 5
  sandboxes (0 failures, 95% CI 0–13%)**.
- The other three looked just as plausible and were rejected on measured
  evidence: `glm-5p1` **69% (11/16)**, `kimi-k2p6` **88% (14/16)**,
  `deepseek-v4-pro` **94% (15/16)**.
- Whole tournament: **85.5 seconds, 0 infra errors.**

That's the thesis in one run: four fixes that all sounded right, one that held
up under reruns. Naming the cause is cheap; proving the cure is the hard part.

> *Transparency:* an earlier run of this experiment let the reproduction's
> comments name the root cause, and we initially over-claimed that models
> "rediscovered the maintainer's exact fix." A sanitized rerun did **not**
> support that. The numbers above are the corrected, hint-free run. Source:
> `seeds/real/penman_test_rearrange.md`.

We reproduce and repair the honest class the substrate reaches —
**randomness and hash-ordering flakes**. Thread/timing races do not flake on
this Daytona container substrate (measured **0/120** across three variants) —
we do not claim otherwise.

---

## How we built it

Python engine (FastAPI + WebSocket) and a React/Vite tournament board, connected
by a typed event spine. The whole pipeline runs end-to-end **headless** before
any UI exists — the board is a subscriber, not the system. `ui/src/types.ts` is
the authoritative engine⇄UI contract; emitters live in `engine/retrial/`.

| Component | Role |
|-----------|------|
| `SandboxPool` / `ForkSandboxPool` | Warm, lease, recycle, destroy Daytona sandboxes; fork degrades stickily to snapshot |
| `TrialRunner` | One test execution in one leased sandbox; write+run collapsed into a single `process.exec` |
| `Verifier` | Wilson 95% CIs, adaptive early-stop, confirmation round, hermetic diagnosis |
| `DiagnosisEngine` | Fireworks multi-model structured `{cause_class, explanation, patch}` |
| `TournamentCoordinator` | DAG: detect → diagnose → verify-per-hypothesis → confirm → gate → ship |
| `EventBus` | Typed JSON events, ring buffer, fan-out to UI / logs |
| `EvidenceLedger` | Braintrust Experiments as the public scoreboard + permalink receipts |
| `PRSmith` | Winner ⇒ fix PR; no winner ⇒ quarantine PR — via `gh api`, behind promote gate |
| `SandboxRegistry` | Observatory feed: every sandbox, fork lineage, exec history, destroy controls |
| `Narrator` | ElevenLabs `eleven_v3` dossier-templated autopsy (opt-in) |
| Flake genome | SQLite/JSON cause-class taxonomy + model win-rates per repo |

### Daytona — the swarm (load-bearing)

This is the substrate. Retrial's entire premise — measure empirical flake rate —
requires many isolated reruns, and disposable parallel sandboxes are the
exactly-right tool. We use Daytona in two complementary modes.

#### 1. Snapshot sandbox pool (default demo path)

`engine/retrial/pool.py` warms a pool of **container** sandboxes from a
pre-baked snapshot in region **`target="us"`**. Verified on this account:

- Create → started ≈ **0.7s**; **16 concurrent creates ≈ 2.0s**
- Pre-warmed pool: `run_started` → first trial ≈ **0.60s** (was 12.5s cold)
- Every sandbox gets `auto_delete_interval` (`AUTO_DELETE_MIN`, default **60**)
  so a crashed run cannot leak credit forever
- Infra errors never return a sandbox to the pool and are excluded from flake
  math (`errors` key) — they never inflate the fail rate

**Isolation level matched to flake class** (the core design decision):

| Mode | Behavior | Measured throughput |
|------|----------|---------------------|
| `process` (default) | Reuse warm sandbox; fresh `python3` per trial = fresh `PYTHONHASHSEED` + scheduling | **6.1 trials/s** |
| `sandbox` | Fresh sandbox per trial; destroy dirty in background | **2.7 trials/s** |

Process isolation is correct for order/scheduling flakes and is why a fully
live 3-minute demo fits with margin. Sandbox isolation is reserved for
state-polluting flakes (filesystem / port / env). Fresh-env-per-trial is a
scientific requirement, not an implementation detail.

**Throughput lever:** we collapsed write+run into a **single Daytona exec
round-trip** (base64 seed → `python3 /tmp/seed.py`). That doubled throughput
(**3.1 → 6.1 trials/s**). The true unit cost is ~**5s per 16-concurrent batch
of execs**, not sandbox create time.

**Hermetic mode (optional, default OFF):** a second detect pass creates
sandboxes with `network_block_all=True` **at create only** (never mid-run —
that can kill the SDK control channel). CI overlap with the open-network
detect → `env_independent` (eliminates external_dep by infrastructure);
non-overlap → `external_dep`. Validated live: 56% vs 50%, env_independent.

#### 2. Rewind engine — Daytona `_experimental_fork` (opt-in)

Ported from our Rewind execution-search project and selected with
`RETRIAL_POOL_BACKEND=fork` (default remains the safe `snapshot` pool).

Instead of creating N independent sandboxes (each with its own cold-start
history), `ForkSandboxPool` (`engine/retrial/forkpool.py`):

1. Warms **ONE root** sandbox (repo + deps + hot caches) on the fork-capable
   region (**`us-east-1`**, Linux VM snapshot — containers in `us` are the
   critical-path demo substrate; VMs/fork are the scientific upgrade).
2. Freezes a **checkpoint** — a paused fork-child capturing full filesystem +
   RAM while the root keeps running.
3. Calls Daytona's **`_experimental_fork`** to spawn N **byte-identical trial
   clones** from that checkpoint.

Why this matters statistically: every trial clone starts from the **same**
initial fs+RAM state, so trial-to-trial variance is purely the flake under
test — not provisioning noise (different cache states, different first-exec
timings). Tighter trial distribution → tighter Wilson CI for the same trial
budget.

**Honesty about the experimental surface:**

- The fork/pause/start APIs are experimental. Forks are **serialized** (parallel
  fork returns HTTP 409). Spend is guarded (`RETRIAL_MAX_FORKS`, default 64).
- The fork path is exercised against a **mocked SDK** in CI. Fork-primitive
  timings we cite come from the Rewind spike (`docs/SPIKE-RESULTS.md`) and are
  attributed as such — not re-claimed as re-measured in this repo.
- **Sticky degrade contract:** on ANY fork-path failure (including a missing
  `_experimental_fork` AttributeError), the pool permanently falls back to the
  proven snapshot `SandboxPool` for its lifetime and emits `pool_degraded`. The
  UI shows an honest "snapshot fallback" banner. We never silently pretend fork
  worked.

**Time-travel flake bisection** (`engine/retrial/bisect.py`, `retrial bisect` /
`POST /bisect`): for order-dependency flakes across a suite, run the suite
prefix in a live root while freezing a checkpoint at every test boundary, then
rerun ONLY the suspect from each checkpoint with the same Wilson-CI oracle,
binary-searching to the exact test that poisons it. Requires the fork backend
(the capability *is* the fork — no snapshot fallback). Assumes the flake rate
is a monotonic step function across checkpoints; noisy probes get a full-budget
confirmation pass; a contradicted confirmation reports inconclusive rather than
guessing.

#### 3. Sandbox Observatory

A thread-safe `SandboxRegistry` tracks **every** sandbox Retrial ever touches —
pool sandboxes, fork roots/checkpoints/trial-clones, and bisect probes — with
role, lifecycle state, fork-lineage parent, the command running now, a bounded
ring of recent execs, and a Daytona preview link when one is exposed. Typed
events stream over `/ws`; `GET /sandboxes` returns the live grid + lineage tree
+ exact live/total-ever/destroyed counts; `DELETE /sandboxes/{id}` and
`POST /sandboxes/destroy_all` are real resilience controls (mid-run single
delete is safe — infra errors are excluded from flake math). The Observatory
panel in the UI is the backstage view of the swarm.

Daytona is not decorative. The product *is* Daytona's speed, parallelism,
disposability — and, when opted in, its experimental fork checkpoints.

### Fireworks — the differential-diagnosis engine

`DiagnosisEngine` (`engine/retrial/diagnosis.py`) calls Fireworks'
OpenAI-compatible API (`https://api.fireworks.ai/inference/v1`) across a panel
of models — `glm-5p2`, `glm-5p1`, `kimi-k2p6`, `deepseek-v4-pro` (read from
`FIREWORKS_MODELS`, never hardcoded; slugs use `"p"` not `"."`, verified live).
Each model produces structured `{cause_class, explanation, patched_code}`. Model
diversity is hypothesis diversity. Without a key the engine honestly degrades to
cached hypotheses or detect-only.

### Braintrust — experiments as the scoreboard

`EvidenceLedger` (`engine/retrial/ledger.py`) models each hypothesis as a
Braintrust **Experiment**; each batch of reruns is an eval run whose scorer is
the empirical pass rate — a real, reproducible eval, not an LLM vibe-check. The
Braintrust dashboard literally *is* our tournament scoreboard, and the
permalinks are the **governance receipt**. A shipped PR
([retrial#1](https://github.com/nihalnihalani/retrial/pull/1)) carries multiple
Braintrust experiment permalinks documenting a measured fix across the swarm.
We didn't just fix it — we proved it's fixed, reproducibly, at a link you can
audit. Without a key, ledger calls are silent no-ops.

### ElevenLabs — the flake autopsy (opt-in, OUTPUT only)

`engine/retrial/narrator.py` turns the final evidence dossier into a short
spoken autopsy after `tournament_done` — hesitant over eliminated hypotheses,
confident over the confirmed fix. Served at `GET /narration/<run_id>` and
offered as a play button under the verdict card (`NarrationPlayer` in the UI).

Three rules we built around:

1. **The script is derived, not generated.** Narration text is templated
   deterministically from the result dict — **no LLM** sits between the dossier
   and the words. An LLM narrator could hallucinate a number that contradicts
   the board mid-pitch; a template cannot.
2. **The Wilson law applies to speech too.** "0 of 50" is spoken as "at most
   8.8 percent, at 95 percent confidence," never "zero percent."
3. **It can never cost a verdict.** Synthesis runs off the run thread; every
   failure degrades to "no audio." `NARRATE` defaults to **0** so the demo path
   is byte-identical unless opted in (~20s synth, ~50s of audio per verdict on
   this account). Model: **`eleven_v3`** (audio-tag support). Voice: Matilda
   (verified live). Retrial never takes voice **input**.

### CodeRabbit — the PR gate (pre-run, disclosed)

CodeRabbit is not an in-engine API call. After PRSmith opens the fix (or
quarantine) PR, CodeRabbit's GitHub App reviews that PR for quality and safety
before merge — the natural last mile on an evidence-backed patch. Its review
latency is **1–5 minutes**, which does not fit inside a live 3-minute stage
demo, so for the pitch we **pre-run the review and disclose that unprompted**.
We never claim live CodeRabbit turnaround on stage. Depth over theatre.

### GitHub / PRSmith — shipping

`prsmith.py` creates fix/quarantine PRs server-side via `gh api` (ref → blob →
PR), never touching the local working tree. Default path is behind the human
promote gate. Real output example:
[retrial#1](https://github.com/nihalnihalani/retrial/pull/1) — statistical
receipts + Braintrust permalinks in the body.

### Statistics as a product feature

- Wilson 95% CI everywhere a rate is shown (UI, PR, spoken autopsy).
- Adaptive early-stop when a CI fully excludes the decision threshold — why the
  live demo fits 3 minutes.
- Confirmation round on the winner guards selection bias across candidates.
- Neutering guard disqualifies trivial-pass patches (`sys.exit(0)`, `assert True`,
  deleted assertions) so a stub in a bad-wifi fallback cannot ship as FIXED.
- **Never `rate or <default>`** — `0.0` is falsy in Python; a measured zero
  flake rate must use explicit `is None` checks or the narrator invents
  nonsense. Fabricating a number is worse than showing none.

---

## Challenges we ran into

**1. Seeds must be calibrated on Daytona, not locally.** Local flake rates are
meaningless — CPython version and CPU constraints differ. We built
`scripts/calibrate_seeds.py` (40+ trials, target 40–55% fail = IDEAL) and ran
~360 real Daytona trials across three rounds before locking
`seeds/test_dict_order.py` at 51%.

**2. The flake class the substrate can actually reproduce.** We assumed
thread/timing races would be the natural demo. They measured **0/120** across
three variants (including Barrier + split read-modify-write) — CPU-constrained
container scheduling suppresses them. Claiming "we reproduce race conditions"
would have been a lie. We pivoted to order/hash and randomness flakes, and we
say so in the pitch.

**3. Throughput vs scientific isolation.** Full create+write+exec+delete was
~1.5–2 trials/s — too slow for a live tournament. Process-isolation reuse plus
collapsing write+run into one exec round-trip got us to **6.1 trials/s** without
abandoning fresh-interpreter-per-trial for the flake classes we target.

**4. Daytona's experimental fork is powerful and fragile.** Parallel fork → 409;
`sandbox.copy()` is a Pydantic false-friend (not a fork); `_experimental_fork`
may be missing depending on target/snapshot. We built sticky degrade to the
snapshot pool with a visible `pool_degraded` banner rather than failing the demo
or lying about which backend served the trials.

**5. Selection bias and dishonest "fixes."** Racing four candidates and picking
the luckiest green streak is bad science — hence the confirmation round.
Trivial-pass patches look perfect under any trial budget — hence the neutering
guard. Always-failing tests are not flakes — hence the detect-gate that refuses
to tournament a REGRESSION.

**6. Demo-config law (learned the hard way).** Strict CI-upper rule: **0/40 →
upper 8.76% < 10% ✓ FIXED**; **0/16 → upper ~19% ✗ INCONCLUSIVE → QUARANTINE**
even for a perfect fix. Short trial budgets silently quarantine genuine fixes on
stage. `MAX_TRIALS >= 40` is non-negotiable for any run that must reach FIXED.

**7. Live diagnosis latency vs a 3-minute pitch.** Four Fireworks models take
23–29s. Solution: hit GO at second zero of the pitch; the trap opening covers
the diagnosis window; the tournament starts live as the story arrives. Bad venue
wifi → certified cached hypotheses (`scripts/fallback_hypotheses.json`),
disclosed unprompted — never passed off as live generation.

**8. Statistical honesty binds spoken output too.** An early narrator used
`rate or default`; a measured `0.0` flake rate is falsy, so the autopsy announced
"flaked 100 percent" for a candidate the board showed at 0%. We fixed it with
explicit `is None` checks and made Wilson phrasing mandatory in speech.

---

## Accomplishments that we're proud of

- **Calibrated an IDEAL demo seed on the real substrate:** `test_dict_order.py`
  at **51% flake (95% CI 36–66%)** after ~360 Daytona trials — not a local guess.
- **Process isolation at 6.1 trials/s** with a warm pool first-trial latency of
  **0.60s**, making a fully live 3-minute demo real with margin.
- **A fully-generated live run with no cached hypotheses:** detect **44%**
  (7/16) → 4 live Fireworks hypotheses → a wrong 'timing' guess stayed **56%
  flaky and was eliminated** ("CI overlaps original flake rate") → winner
  confirmed **0/24 (CI ≤14%)** → FIXED with a real Braintrust permalink. The
  differential-diagnosis story happened for real, autonomously.
- **End-to-end arcs that fit the pitch:** full tournament (detect + 2
  hypotheses + confirm, 80 trials) in **17.3s**; a recent full arc including
  live Fireworks in **42.7s** (50% → 0/40 FIXED).
- **Real OSS evidence:** the penman IDoFT specimen — four models named
  randomness; only one fix survived confirmation; the other three (69/88/94%)
  were eliminated on evidence. We published our own overclaim correction when
  the sanitized rerun changed the story.
- **A real evidence-backed PR** with statistical receipts and Braintrust
  permalinks in the body — not a mocked ship step.
- **The Rewind merge:** `_experimental_fork` pool + time-travel bisect + Sandbox
  Observatory, with sticky degrade honesty when the experimental path fails.
- **ElevenLabs autopsy that cannot lie to the room:** dossier-templated script,
  Wilson spoken aloud, opt-in so it never burns demo time unless asked.
- **A compounding flake genome** already accumulating — live `GET /genome`
  returns real cause-class and model win-rate data. No single model dominates;
  the tournament beats picking a favorite.

---

## What we learned

**Verification asymmetry is the product.** Flaky tests defeat single-pass
verification by definition. Parallel disposable sandboxes aren't a sponsor
checkbox here — they are the shape of the solution.

**The substrate chooses which bugs you get to fix.** Thread/timing races that
flake on a laptop may be silent inside a CPU-constrained container. Calibrate on
the swarm you ship on, or your demo is fiction.

**Depth beats breadth on sponsor tools.** Daytona + Fireworks + Braintrust are
load-bearing. ElevenLabs and CodeRabbit are natural polish with honest limits
(opt-in narration; pre-run review). Kitchen-sink integrations score worse than
one substrate used flagship-deep — including Daytona's experimental fork, used
for a real statistical reason (identical initial state), with a real degrade
path when the experiment fails.

**Statistical pedantry is a feature judges (and CTOs) feel.** Wilson CIs,
confirmation rounds, detect-gates, and neutering guards are not academic
decoration — they are why a 0/16 "fix" doesn't ship and why a spoken autopsy
cannot contradict the board.

**Plan B is often the winning path.** Concurrent snapshot container fan-out in
`us` is the measured, demo-safe critical path. Experimental VM fork in
`us-east-1` is the upgrade for identical-state trials and time-travel bisect —
not a requirement to claim Daytona was used well.

**Honesty compounds.** Disclosing pre-computed pieces (cached hypotheses on bad
wifi, pre-run CodeRabbit) unprompted builds more trust than claiming everything
was live. Correcting our own penman overclaim in writing is part of the product
story, not a footnote to hide.

---

## What's next for Retrial

The compounding moat is the **flake genome**, and it's already accumulating —
not a roadmap slide. Every run classifies the flake and records which model won
on which cause class. Live data already shows no single model dominates, which
is exactly why a tournament beats picking one favorite. That's the seed of a
repo-specific **flake leaderboard**: a statistically-weighted model routing
table that sharpens the more you run it.

From there:

- **Detection → prediction → prevention.** A CI gate that knows your suite's
  failure taxonomy before the flake ever reaches a human.
- **Hermetic external-dep path in production workflows** — network-blocked
  second passes that distinguish env-independent flakes from third-party
  dependency flakes by infrastructure, not vibes.
- **Productionize fork/bisect time-travel** for order-pollution suites: the
  Rewind engine already checkpoints at every test boundary; the next step is
  making that a default CI affordance wherever fork-capable VMs are available,
  with snapshot fan-out remaining the universal fallback.
- **Richer Observatory governance** — spend meters, lineage-aware reaping, and
  audit exports that sit next to Braintrust receipts in the PR body.

Detection today. Prediction and prevention next. Every flaky test deserves a
retrial. Fifty of them, actually.
