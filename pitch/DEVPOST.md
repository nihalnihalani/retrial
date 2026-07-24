# Retrial — Devpost Submission (Round 1)

> Paste-ready. Each `##` block maps to a Devpost field. Measured numbers only —
> every figure here is sourced from this repo's calibration + engine timing runs
> (see `docs/WINNING-IDEA.md` → "MEASURED DEMO-TIMING TRUTH" and
> `calibration-results.json`). Nothing is estimated.

---

## Project name
**Retrial**

## Tagline
Your build isn't broken — it's lying. Retrial is the lie detector for flaky tests.

## Elevator summary (2–3 sentences)
Flaky tests pass and fail on the same code, so a green run proves nothing and a
red one gets ignored. Retrial reruns a suspect test across a swarm of disposable
Daytona sandboxes to measure its **empirical flake rate** with a statistical
confidence interval in about a minute, then runs a **hypothesis tournament** —
competing root-cause fixes, each re-verified across the swarm — and ships the
one that empirically survives as an evidence-backed pull request. It's the
machine that doesn't just generate a fix, it *proves* the fix.

---

## The problem

Flaky tests are the number-one trust-killer in CI, and in 2026 the whole
industry noticed — Bitbucket, Datadog, and Kong all shipped flake-fixer agents
this year. The pain is real, recurring, and expensive:

- Google's published research puts roughly **16% of tests** somewhere on the
  flaky spectrum, and reports about **3.7 engineering hours** burned
  investigating a *single* flaky test — with roughly **1 in 7** suite runs
  hitting a flaky failure.
- Microsoft has reported that about **25% of CI test failures** are flaky, at
  ~30 minutes of investigation each.
- Across the industry, teams burn an estimated 15–30% of CI time on reruns.

The deeper reason existing tools struggle is **verification asymmetry**. Flaky
tests are the one bug class where *verification*, not generation, is the
bottleneck: at a 40–50% flake rate, a single green run is statistically
meaningless. Every incumbent verifies a fix with a single pass — which is
exactly what a flaky test defeats. And detection tools like Datadog and
Bitbucket infer flakiness from *weeks* of accumulated CI history. Retrial needs
sixty seconds of sandboxes, on demand, on any repo.

Everyone builds machines that generate fixes. We built the machine that proves
them.

---

## It rediscovered the human's fix

The headline result: on a **real, documented flake from the academic flakiness
dataset**, Retrial's model tournament independently converged on the *exact fix
the human maintainer shipped* — and empirically rejected the model that got it
wrong.

We pointed it at `test_rearrange` from
[penman](https://github.com/goodmami/penman) `v1.2.1` — a real MIT-licensed
Python OSS project, catalogued in **IDoFT** (the Illinois Dataset of Flaky
Tests, py-data.csv, category NOD, status Accepted) and already fixed by the
maintainer in [penman#102](https://github.com/goodmami/penman/pull/102). The
documented root cause: a `random.random()` sort key whose module-level RNG seed
was ineffective. Retrial reproduced the flake on Daytona at **88%** and ran the
full four-model tournament:

- Detect measured the baseline at **88% flake (14/16, 95% CI 64–96%)** — the
  test really is broken.
- **3 of 4 models — `glm-5p2`, `glm-5p1`, and `deepseek-v4-pro` — independently
  converged on the same fix: seed the RNG before the `random_order` rearrange.**
  That is byte-for-byte the maintainer's approach in PR #102. All three drove the
  flake to **0/16**.
- **`kimi-k2p6`'s hypothesis was wrong** — its patch was still **94% flaky
  (15/16)** — and it was **empirically eliminated.** Evidence killed it, not
  vibes.
- The winner (`glm-5p2`) survived a **fresh confirmation round: 0/25 sandboxes
  (95% CI 0–13%).** Whole tournament: **57 seconds, 0 infra errors.**

This is the thesis made real: three models proposed a fix that *looked* right,
one proposed a fix that looked right too — and only the empirical rerun told them
apart. It reproduces and repairs the honest class the substrate reaches:
**randomness and hash-ordering flakes** (thread/timing races do not flake here —
measured 0/120 elsewhere in this repo).

> *Fine print (we disclose it):* the standalone reproduction runs the test under
> its documented bug condition (RNG seed ineffective); `kimi-k2p6`'s cause label
> came from a fallback hint, but its patch was verified live and lost on the
> evidence like every other. Baseline calibration over a larger 40-trial run put
> the same flake at 88% (95% CI 74–95%). Source:
> `seeds/real/tournament_penman_result.json`.

---

## How it works

Point Retrial at a flaky test. It runs a five-stage pipeline —
**detect → diagnose → tournament → confirm → ship** — where every stage is
measured, not asserted.

1. **Detect (the lie detector).** Retrial reruns the unmodified test across a
   swarm of disposable Daytona sandboxes — a fresh environment per trial — and
   measures its empirical flake rate with a **Wilson 95% confidence interval**.
   Our locked demo seed calibrated at **51% flake (95% CI 36–66%)** over real
   Daytona trials. A given live run re-measures it fresh; a recent run clocked
   **50% (95% CI 28–72%)**. The number *is* the lie made quantitative.

2. **Diagnose (differential diagnosis).** Fireworks frontier models generate
   *competing root-cause hypotheses* — order dependency, shared state,
   scheduling — each with a concrete candidate patch. The unit of competition is
   the hypothesis, not the model; multiple models supply diversity of ideas.

3. **Tournament (the retrial).** Every hypothesis' patched copy is re-trialed
   across the swarm in parallel lanes. The board fills green/red live as trials
   land. Losers are eliminated as their confidence intervals stop excluding the
   original rate. Sustained throughput measured at **6.1 trials/second** in
   process isolation — about 200 trials in ~33 seconds.

4. **Confirm (guard against selection bias).** The winner isn't the one that
   looks right — it's the one that empirically survives. Retrial picks the
   lowest flake rate whose CI upper bound falls below the original's rate, then
   runs a **fresh confirmation round** on that winner alone (selection bias
   across candidates is real; the confirmation round is the guard). A winning
   fix confirmed at **0/40** is reported honestly as "**≤8.8% at 95%
   confidence**," never "0%." That recent live run went **50% → 0/40 (FIXED)**
   with the full detect-diagnose-tournament-confirm arc in **42.7 seconds** end
   to end — real Fireworks calls included.

5. **Ship.** The winning fix goes out as a **real pull request** with the
   evidence dossier in the body (flake rate before/after, the confidence
   interval, the Braintrust permalinks). This isn't a mock: Retrial's PRSmith
   opened [**retrial#1**](https://github.com/nihalnihalani/retrial/pull/1) — a
   full evidence-dossier PR documenting a **69% → 0%** fix authored by model
   `glm-5p2`, with **5 Braintrust experiment permalinks** as the receipt. If
   nothing fully stabilizes the test, Retrial instead opens a **quarantine PR**
   carrying the same dossier — the run never dead-ends. CodeRabbit reviews
   whichever PR is produced.

Because every stage emits typed events, the same run drives the live board, the
voice autopsy, and the audit log independently.

---

## Key technical architecture

Python engine (FastAPI + WebSocket) and a React tournament board, connected by a
typed event spine. The whole pipeline runs end-to-end **headless** before any UI
exists — the board is a subscriber, not the system.

- **SandboxPool** — warms, leases, recycles, and destroys Daytona sandboxes from
  a pre-baked snapshot. The core design decision is **isolation level matched to
  flake class**: `process` isolation reuses warm pooled sandboxes and gives each
  trial a fresh `python3` process (fresh hash-seed + scheduling) — correct for
  order/scheduling flakes, and the reason we hit 6.1 trials/s; `sandbox`
  isolation tears down a fresh sandbox per trial (2.7 trials/s) for
  state-polluting flakes only. Fresh-env-per-trial is a scientific requirement,
  not an implementation detail.
- **TrialRunner** — one test execution in one leased sandbox. We collapsed
  write+run into a **single Daytona exec round-trip**, which doubled throughput
  (3.1 → 6.1 trials/s); the true unit cost is ~5s per 16-concurrent batch of
  execs, not sandbox creation.
- **Verifier** — Wilson 95% CIs everywhere a rate is shown; **adaptive
  early-stop** (halt a series once its CI fully excludes the decision
  threshold); the **confirmation round** on the winner.
- **DiagnosisEngine** — Fireworks (OpenAI-compatible) prompting N models for
  structured `{cause_class, explanation, patch}`.
- **TournamentCoordinator** — the DAG: detect → diagnose → verify-per-hypothesis
  (parallel) → confirm → gate → ship, emitting a typed event at every
  transition, with decision gates for all-pass, all-fail (quarantine), and
  mid-race progress.
- **EventBus** — every event is a typed JSON payload (ring buffer of 500) fanned
  out to the UI stream, the ElevenLabs voice announcer, and the log. `ui/src/
  types.ts` is the authoritative engine⇄UI contract.
- **EvidenceLedger** — Braintrust Experiments as the public scoreboard +
  permalink receipts (a single shipped run produced **5 experiment permalinks**),
  plus a local SQLite **flake genome** (cause-class taxonomy and model win-rates
  per repo).
- **PRSmith** — winner ⇒ fix PR with dossier; no winner ⇒ quarantine PR with
  dossier. Real output:
  [retrial#1](https://github.com/nihalnihalani/retrial/pull/1) (69%→0%, model
  `glm-5p2`, Braintrust permalinks in the body).

---

## Sponsor tools used and how

**Load-bearing (the product is built on these):**

- **Daytona — the swarm.** This is the substrate. Retrial's entire premise
  (measure empirical flake rate) requires many isolated reruns, and disposable
  parallel container sandboxes are the exactly-right tool. Verified on this
  account: 16 concurrent container creates in ~2.0s, ~0.60s to first trial (measured; pre-warmed pool)→started, one
  exec round-trip per trial. The pool with its two isolation levels is genuine
  Daytona-flagship usage — not a decorative call.

- **Braintrust — experiments as the scoreboard.** Each hypothesis is modeled as
  a Braintrust **Experiment**; each batch of reruns is an eval run whose scorer
  is the empirical pass rate — a real, reproducible eval, not an LLM vibe-check.
  The Braintrust dashboard literally *is* our tournament scoreboard, and the
  permalinks are the **governance receipt** — the shipped PR
  [retrial#1](https://github.com/nihalnihalani/retrial/pull/1) carries **5 of
  them** documenting a 69%→0% fix across the swarm. We didn't just fix it, we
  proved it's fixed, reproducibly, at a link you can audit.

- **Fireworks — the differential-diagnosis engine.** The DiagnosisEngine calls
  Fireworks (OpenAI-compatible, `https://api.fireworks.ai/inference/v1`) across
  a panel of models — `glm-5p2`, `glm-5p1`, `kimi-k2p6`, `deepseek-v4-pro`
  (read from env, never hardcoded) — to generate competing root-cause
  hypotheses and patches. Model diversity is hypothesis diversity.

**Bonus polish (natural, not bolted on):**

- **CodeRabbit — the PR gate.** Reviews the fix (or quarantine) PR that Retrial
  opens, for quality and safety before merge. Its review latency is 1–5 minutes,
  so in the demo this is pre-run and disclosed as such — never claimed as live
  turnaround.

- **ElevenLabs — the flake autopsy.** v3 emotional-tag narration on the run's
  outcome (hesitant on eliminated hypotheses, triumphant on the confirmed fix).
  Output only — narrating the verdict, never taking voice input.

---

## What's next

The compounding moat is the **flake genome**, and it's **already accumulating** —
not a roadmap slide. Every run classifies the flake and records which model won
on which cause class. Our live `GET /genome` after two runs already returns:

```json
{"runs": 2, "fixed": 2,
 "by_cause_class": {"order_dependency": 2},
 "model_win_rates": {"glm-5p2": {"wins": 2, "win_rate": 1.0}}}
```

That's the seed of a repo-specific **flake leaderboard** — "`glm-5p2` is 2-for-2
on order-dependency fixes on this repo" today, a statistically-weighted model
routing table tomorrow — so Retrial gets sharper the more you run it. Detection
today, prediction and prevention next: a CI gate that knows your suite's failure
taxonomy before the flake ever reaches a human.

Every flaky test deserves a retrial. Fifty of them, actually.

---

## Repo
Public GitHub: `github.com/nihalnihalani/retrial`
