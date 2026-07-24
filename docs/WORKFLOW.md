# Retrial — operator's workflow

How to run this project, what each piece does, and what to do when it misbehaves.
Every command here was executed against this checkout on 2026-07-25; every number
is measured, not estimated.

- **What it is** → §1
- **Get it running in 5 minutes** → §2
- **The pipeline, stage by stage** → §3
- **Command reference** → §4 · **HTTP API** → §5 · **Config** → §6
- **Demo runbook** → §7 · **Troubleshooting** → §8 · **Known gaps** → §9

---

## 1. What this project actually is

A flaky test passes and fails on the same code. That makes a green CI run
worthless as evidence: at a 50% flake rate, three greens in a row happen 12% of
the time. Retrial's claim is that **verification, not generation, is the
bottleneck** for this bug class, and it attacks that with statistics plus
disposable cloud sandboxes.

Four stages:

| Stage | What happens | Where |
|---|---|---|
| **Detect** | Rerun the suspect test across a swarm of Daytona sandboxes — fresh `python3` process per trial — and compute an empirical flake rate with a **Wilson 95% confidence interval** | `verifier.py`, `pool.py`, `trial.py` |
| **Diagnose** | 4 Fireworks models propose *competing* root-cause hypotheses (order dependency / shared state / timing / race / external dep), each with a candidate patch | `diagnosis.py` |
| **Verify** | Every patch is re-trialed across the swarm in parallel lanes. The winner is the one that survives the evidence, then a **fresh confirmation round** guards selection bias | `coordinator.py` |
| **Ship** | A human approves at a promote gate; PRSmith opens a PR carrying the rates, CIs and Braintrust permalinks | `prsmith.py` |

The thing to understand before operating it: **the tournament's objective
function is "fails least often."** That objective is maximised by a patch that
deletes the test. `guards.py` exists to stop the crude version of that. It does
not stop the subtle version — see §9.1 before you trust any verdict.

---

## 2. Five-minute start

```bash
# 0. from the repo root
cd /path/to/retrial

# 1. deps — BOTH files (requirements-dev.txt carries pytest + the test suite deps)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# 2. config
cp .env.example .env        # then fill DAYTONA_API_KEY at minimum

# 3. prove the install without spending a cent (mocked SDK, no keys needed)
.venv/bin/python -m pytest tests/ -q          # expect: 228 passed
cd ui && npm install && npm test && npm run build && cd ..   # 37 passed, build clean

# 4. prove the config end-to-end (offline, instant, no Daytona calls)
cd engine && ../.venv/bin/python -m retrial.cli doctor && cd ..

# 5. first live run — real sandboxes, a few cents
cd engine && ../.venv/bin/python -m retrial.cli check ../seeds/test_dict_order.py --max-trials 12
```

Expected output from step 5:

```
test:      test_dict_order.py
trials:    12 valid, early-stopped
flake:     5/12 fail = 42%
95% CI:    19% - 68%
verdict:   FLAKY  <- your CI is lying to you
wallclock: 3.1s
```

### Running the board

Two processes. The engine must be on **port 8000** — the UI hardcodes it in six
places (`useEventStream.ts:5`, `useDaytonaHealth.ts:4`, `TournamentBoard.tsx:28`,
`PromoteGate.tsx:8`, `SandboxObservatory.tsx:14`, `RunHistory.tsx:5`), with no env
override.

```bash
# terminal 1 — engine (MUST run from engine/, the package lives there)
cd engine && ../.venv/bin/python -m retrial.server

# terminal 2 — UI
cd ui && npm run dev          # http://localhost:5173
```

> **The board is live-only.** Replay and every `?mock=` mode were deleted in
> commit `057d107`. The only query param the UI reads today is `?tree=1`. With no
> engine on :8000 you get "Waiting for the live WebSocket on port 8000", not a
> recorded demo. `README.md`'s demo-URL block and `CLAUDE.md`'s "replay mode
> default" are both stale. See §9.3.

---

## 3. The pipeline, stage by stage

### What one trial is

The entire execution model is three lines (`trial.py:44-46`):

```python
b64 = base64.b64encode(test_code.encode("utf-8")).decode("ascii")
cmd = (f"echo '{b64}' | base64 -d > /tmp/seed.py && "
       f"python3 /tmp/seed.py; echo EXIT:$?")
```

One exec round-trip — writing and running in a single call is what took
throughput from 3.1 to 6.1 trials/s. base64 rather than a heredoc because patched
code is untrusted and could otherwise carry the heredoc sentinel.

So a "test" is **one self-contained Python file that signals pass/fail by exit
code**. Not pytest — pytest is not in the container image.

| Exit code | Meaning |
|---|---|
| `0` | passed |
| `1` | failed |
| anything else | **infra error** — harness/bootstrap failure, excluded from the flake-rate denominator |
| no `EXIT:` marker | infra error |

That last rule matters more than it looks. Before it existed, a seed whose
dependency failed to install reported a confident **100% flake rate**. A
measurement tool that reports install failures as flakiness is lying in exactly
the way this product exists to detect.

### Isolation levels

| Level | Behaviour | Throughput | Use for |
|---|---|---|---|
| `process` (default) | Reuse warm pooled sandboxes; each trial is a fresh `python3` process, so fresh `PYTHONHASHSEED` and fresh scheduling | **6.1 trials/s** | order / hash-seed / scheduling flakes |
| `sandbox` | Fresh sandbox per trial, dirty ones destroyed in the background | **2.7 trials/s** | state-polluting flakes (filesystem, port, env) |

A trial that hits an infra error never returns its sandbox to the pool.

### The statistics

`verifier.py`. Wilson 95% (`z=1.96`), and every verdict comes from the interval,
never the point estimate:

| Verdict | Rule |
|---|---|
| `ERROR` | zero valid trials |
| `ALWAYS_FAILING` | all trials failed **and** n ≥ 24 |
| `STABLE` | Wilson upper bound < threshold (default 0.10) |
| `FLAKY` | passes **and** failures observed, and the whole CI sits above threshold |
| `INCONCLUSIVE` | the CI still straddles the threshold |

**Adaptive early-stop:** after each batch, stop once the CI provably excludes the
threshold. This is why a live demo fits in three minutes. Two consequences you
must know:

- A ~45%-flaky test **stops at 16 trials ~96% of the time**, not 50. `planned_trials`
  says 50 and the grid draws 50 cells; the evidence is 16. Do not say "fifty reruns"
  about a detect phase.
- The early-stop deliberately **refuses to fire on an all-failing run below 24
  trials**, because `ALWAYS_FAILING` is terminal (see the detect-gate below) and a
  high-rate flake produces an all-fail opening batch often enough to matter.

**How many clean trials a FIXED verdict needs** — measured against the 10% threshold:

| result | Wilson upper | STABLE? |
|---|---|---|
| 0/8 | 32.4% | no |
| 0/16 | 19.4% | no |
| 0/24 | 13.8% | no |
| **0/35** | **9.9%** | **yes — the exact minimum** |
| 0/40 | 8.8% | yes |
| 0/50 | 7.1% | yes |

**Never demo, record, or smoke-test at fewer than 40 trials if the run must reach
FIXED.** At 16 a perfect fix reads INCONCLUSIVE and gets quarantined.

### The detect-gate

The tournament runs **only** when the detect verdict is `FLAKY`. Otherwise the run
terminates honestly with no hypotheses and no PR:

| Detect verdict | Terminal outcome |
|---|---|
| `ALWAYS_FAILING` | `REGRESSION` — "fix the code, not the test" |
| `STABLE` | `ALREADY_STABLE` |
| `INCONCLUSIVE` | `INCONCLUSIVE_BASELINE` |
| `ERROR` | `ERROR` |

### Winner selection

1. Eligible = `wilson_ci[1] < orig_rate` **and** `verdict == "STABLE"`.
2. Sorted deterministically by `(flake_rate, ci_upper, id)` — ties never depend on
   thread arrival order.
3. Best-first, each candidate must clear `guards.neutering_check`.
4. The survivor runs a **fresh confirmation round** and must read `STABLE` again.
   If it doesn't → `QUARANTINE` with an evidence dossier. The run never dead-ends.

Two honest caveats: eligibility compares an *interval* against the original's bare
*point estimate*, so a noisy-high baseline lowers the bar; and confirmation is
powered to rule out ≥10% flakiness, not to prove 0%.

---

## 4. Command reference

Run all `retrial.cli` commands **from `engine/`**.

| Command | Purpose | Key flags (defaults) |
|---|---|---|
| `check <test>` | Detect only — the lie detector | `--max-trials` (50) · `--conc` (16) · `--threshold` (0.10) · `--isolation` (process) · `--json` |
| `diagnose <test>` | Hypotheses only, no trials. Exit 3 without `FIREWORKS_API_KEY` | `-n` (4) · `--json` |
| `bisect <suite>` | Time-travel bisection for order-dependency. **Requires the fork backend** | `--suspect` · `--max-trials` (30) · `--conc` (8) |
| `doctor` | Validate config end-to-end, non-zero exit on any failure | `--live` (real Daytona calls, ~1 sandbox-minute) · `--json` |
| `sandboxes` | Table of every tracked sandbox (HTTP client of a running server) | `--url` (localhost:8000) · `--json` |
| `reap` | Destroy all sandboxes | `--force` (cancels an active run) · `--json` |

A healthy `doctor` looks like:

```
PASS  settings_parse      all env vars parsed
PASS  daytona_api_key     present
PASS  pool_backend        snapshot
PASS  fork_checks         snapshot backend — fork checks skipped
PASS  promote_gate        ON (human approves PRs)
PASS  prsmith_gh          gh present; PRSMITH off
PASS  fireworks           key present
PASS  braintrust          key present
PASS  auth                unset — endpoints open (default)
doctor: OK
```

Other entry points:

```bash
.venv/bin/python scripts/calibrate_seeds.py       # measure seed flake rates on Daytona (TRIALS=40)
.venv/bin/python scripts/certify_fallback.py      # prove the bad-wifi payload still yields FIXED
.venv/bin/python scripts/live_smoke.py            # prove the fork pool against real Daytona
```

---

## 5. HTTP API

Server binds **127.0.0.1** by default. It has no auth and wide-open CORS — keep it
on loopback unless it's behind a trusted proxy.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | open | pool stats + full config echo |
| GET | `/preflight` | open | boot check results |
| GET | `/genome` | open | aggregate flake genome |
| GET | `/runs?limit=N` | open | run history, newest first, limit clamped 1–100 |
| GET | `/sandboxes` | open | full registry snapshot + spend estimate |
| GET | `/sandboxes/{id}` | open | detail incl. exec history + preview link |
| WS | `/ws` | open | replays the ring buffer, then streams |
| POST | `/tournament` | gated | `{seed_path, hypotheses?, isolation?, open_pr?}` |
| POST | `/promote` | gated | `{approve: bool}` — 404 if nothing pending |
| POST | `/bisect` | gated | **400 unless `RETRIAL_POOL_BACKEND=fork`** |
| DELETE | `/sandboxes/{id}` | gated | 409 if it has live fork-children |
| POST | `/sandboxes/destroy_all` | gated | 409 while a run is active unless `?force=1` |

"gated" = requires `Authorization: Bearer …` **only when `RETRIAL_AUTH_TOKEN` is
set**. The web UI never sends a token, so setting that var makes every mutating
button in the UI return 401. Use it for headless/API demos, not UI demos.

`POST /tournament` and `POST /bisect` reject any path that doesn't resolve inside
`seeds/` (400), symlinks and `..` included.

```bash
curl -X POST http://127.0.0.1:8000/tournament \
  -H 'Content-Type: application/json' \
  -d '{"seed_path":"seeds/test_dict_order.py"}'
# {"status":"started","test_name":"test_dict_order.py","isolation":"process",
#  "diagnosing":true,"num_hypotheses":null}
```

**Pydantic v2 silently drops unknown body keys.** Never assume a field is wired
because the POST returned 200 — confirm it in the response echo or in behaviour.

### Event contract

27 event types, and the engine's `EVENT_TYPES` matches `ui/src/types.ts` exactly —
enforced by an AST scan in `tests/test_events.py` that walks every `.emit(...)`
call site. **That scan checks event *names* only, never payload *fields*.**
`TournamentDone` is declared in TypeScript as `{ type }` while the engine emits six
fields; harmless today because the reducer ignores them, but nothing would catch a
real payload regression. Change the contract in both places in the same commit.

---

## 6. Configuration

33 env vars, all read through one typed `pydantic-settings` surface
(`settings.py`). `get_settings()` constructs fresh on every call and **can never
raise** — a malformed value falls back to the default and surfaces as a loud
`settings_parse` failure rather than crashing boot. Full table: `README.md`.

The ones that actually decide how a run behaves:

| Env | Default | Why you'd touch it |
|---|---|---|
| `DAYTONA_API_KEY` | — | required for any live run |
| `DAYTONA_TARGET` | `us` | **containers only exist in `us`.** See §8.1 |
| `RETRIAL_FORK_TARGET` | → `us-east-1` | fork/VM path only |
| `MAX_TRIALS` | 50 | **≥40 for any run that must reach FIXED** |
| `CONC` | 16 | trials in flight |
| `TOURNAMENT_CONC` | 8 | per-lane concurrency; peak sandboxes ≈ lanes × this |
| `PREWARM` | 16 | pool warm at boot; `0` = no spend until a run starts |
| `THRESHOLD` | 0.10 | decision threshold. **Server-only — the CLI ignores it** |
| `ISOLATION` | `process` | server-only |
| `HERMETIC` | 0 | second network-blocked detect pass |
| `PROMOTE_GATE` | 1 | human approval before any PR |
| `PRSMITH` | 0 | enable PR opening at all |
| `FIREWORKS_MODELS` | — | comma-separated slugs; **"p" not "."** (`glm-5p2`) |

---

## 7. Demo runbook

**T-24h**

1. `doctor` clean, `pytest tests/ -q` green, `npm run build` green.
2. `scripts/calibrate_seeds.py` — confirm the primary seed still reads 40–55%.
3. `scripts/certify_fallback.py` — confirm the bad-wifi payload still yields FIXED.
4. Decide the trial budget. **`MAX_TRIALS=50`.**

**T-10m**

5. Start the engine on **:8000** with `PREWARM=16`. Pre-warm is what makes
   `run_started → first trial` 0.60s instead of 12.5s.
6. `curl localhost:8000/health` → `preflight_ok: true`, `pool.available: 16`.
7. Open the board. Confirm no red degrade banner.

**During**

8. Hit GO at second zero — live 4-model diagnosis measures **23–29s**, and the
   trap opening covers that window.
9. Watch for the degrade banner. It never auto-dismisses; if it appears, say so
   rather than letting a judge spot it.

**If the wifi dies**

```bash
curl -X POST http://127.0.0.1:8000/tournament -H 'Content-Type: application/json' \
  -d @scripts/fallback_hypotheses.json
```

Disclose that the hypotheses are cached, unprompted. That is a house rule, and the
honesty is worth more than the illusion.

**Cleanup**

```bash
cd engine && ../.venv/bin/python -m retrial.cli reap --force
# or: daytona sandbox list   # to check for leaks after a crash
```

---

## 8. Troubleshooting

### 8.1 Every trial is an infra error / verdict is ERROR with 0 valid trials

**Cause:** `DAYTONA_TARGET=us-east-1`. Container snapshots do not exist in that
region — it is the **VM/fork** region. Every `create` fails, so every trial is an
infra error, so `n == 0` and the verdict is `ERROR`.

**Fix:**
```bash
DAYTONA_TARGET=us
RETRIAL_FORK_TARGET=us-east-1     # the fork path keeps its own region
```

This is not hypothetical: it produced **10 consecutive `ERROR` runs** in
`genome.json` (seq 12–19, all `trials: 0`).

### 8.2 `ModuleNotFoundError: No module named 'retrial'`

You ran the CLI from the repo root. The package lives in `engine/`. `cd engine`
first, or set `PYTHONPATH=engine`.

### 8.3 `No module named pytest` / `pydantic_settings`

`CLAUDE.md`'s quickstart installs only `requirements.txt`. Install both:
`.venv/bin/pip install -r requirements.txt -r requirements-dev.txt`.

### 8.4 Board says "Waiting for the live WebSocket on port 8000"

The engine isn't on :8000. There is **no replay fallback** any more (§9.3). If
another checkout already holds :8000, that's the one your UI is talking to —
check with `lsof -nP -iTCP:8000 -sTCP:LISTEN`.

### 8.5 A perfect fix got QUARANTINEd

`MAX_TRIALS` was below 35. 0/16 gives a Wilson upper of 19.4%, which is not
`STABLE` at a 10% threshold. Re-run at 50.

### 8.6 Zero hypotheses / instant QUARANTINE on a fresh clone

`FIREWORKS_MODELS` unset. The code default is a single slug and `.env.example`
doesn't set the var; a wrong slug 404s on every call, the exception is swallowed,
and you get `hypotheses = []`. Set the four verified slugs in `.env`.

### 8.7 Vite serves on 5174, or Chrome can't reach it

5173 was taken. Also: vite may bind IPv6-only (`[::1]`), which leaves
`127.0.0.1:5174` refusing connections. Force it:
`npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`.

---

## 9. Known gaps — read before trusting a verdict

### 9.1 The neutering guard does not catch semantic deletion

It verifies that a comparison still **governs the exit code**. It does not verify
that the comparison still **means what it meant**. Those are different properties
and only the second is safety.

Measured on this checkout, hint-free, 2026-07-25: a live 4-model diagnosis of the
real penman specimen returned two valid patches, and **both** replaced
`rearrange(t, model.random_order)` with `model.canonical_order` — deleting the code
path under test — then rewrote the expected string to match. **The guard passed
both.** The same shape won the earlier archived penman experiment.

This is the tournament's objective function working as designed: "fails least
often" is maximised by a test that no longer tests anything. Until a semantic
preservation check exists, **the human promote gate is the only real defence** —
read the diff, do not merge on the strength of the flake rate.

What the guard *does* reliably catch: bare `sys.exit(0)`, assertion-free files,
assertion-count reductions, tautologies (`expected = first; first == expected`),
and patches whose failure branch is unreachable.

### 9.2 The substrate only reproduces one flake class

Thread and timing races **do not flake here** — 0/80 measured across two variants
(`calibration-results.json`), reported as 0/120 across three in `CLAUDE.md`
(the third variant's `.py` is no longer in the tree). Container CPU constraints
suppress them. Every working seed is hash-order / unordered-collection / randomness
class. Say "scheduling-dependent and order-dependent flakes"; never claim race
reproduction.

For scale, use the Python numbers — Gruber et al., ICST 2021, n=7,571 flaky tests:

| Category | Share | Retrial's actual reach |
|---|---|---|
| Order-dependent (**inter-test** pollution across a suite) | **59%** | Only via `bisect.py` — fork-backend-only, mocked-SDK-only, never live-verified |
| Test-infrastructure / environmental | **28%** | **The best fit, and it is unpitched** — `isolation="sandbox"` and hermetic mode are exactly the right instrument, and no incumbent does this well |
| Randomness | **~4.8%** | What the engine demonstrably does today |
| Network | ~5.5% | Hermetic mode, partially |

Do **not** claim the 59% by pointing at the word "order dependency". Gruber's
category means *test A pollutes test B*; this repo's working seeds are single-file
hash-iteration-order scripts with no inter-test dependency at all. The honest
demonstrated scope is the ~5% randomness bucket.

The strategically interesting number is the **28%**. Fresh-sandbox-per-trial is
the correct tool for environmental flakiness, incumbents mining CI history are
structurally bad at it, and it appears nowhere in the pitch.

(The older Java taxonomy — Luo et al., FSE 2014, n=161: async-wait 45%,
concurrency 20%, randomness + unordered collections ~3% — is the wrong
denominator for a Python-only tool. Cite Gruber.)

### 9.3 Replay mode is gone

`mockRun.ts` (359 lines), `realRun.json` (193 recorded events) and
`realRunQuarantine.json` still exist but are imported only by tests. Three
documents still promise demo URLs that don't resolve. Either re-wire a `?mock=`
branch or delete the assets and fix the docs — the current state is the worst of
both.

### 9.4 The fork engine is mocked-only

`forkpool.py` (461 LOC) and `bisect.py` (482 LOC) are exercised against a mocked
SDK. `scripts/live_smoke.py` exists to prove them against real Daytona and is
`workflow_dispatch`-only. Until it has been run, "fork-checkpoint byte-identical
trials" is a design, not a measurement.

### 9.5 PRSmith writes a new file, it does not fix the test

`prsmith.py:218` hardcodes `seeds/fixed/{test_name}`. The PR **adds a file**; it
never edits the flaky test. On any repo other than this one that path doesn't
exist. Describe it as "opens a PR proposing the patch."

### 9.6 Diagnosis runs before detect

`server.py` diagnoses before the coordinator detects, so a non-flaky seed briefly
shows the diagnosing state and burns one Fireworks call the gate then discards.
Outcome is still honest; deferred by decision.

---

## 10. Where things live

```
engine/retrial/
  cli.py server.py            entry points
  coordinator.py              the run: detect → gate → tournament → confirm
  verifier.py                 Wilson CI, early-stop, verdict table
  trial.py                    one trial in one sandbox
  pool.py forkpool.py         snapshot pool (default) / fork pool
  diagnosis.py                Fireworks differential diagnosis
  guards.py                   neutering guard — read §9.1
  registry.py                 Sandbox Observatory
  prsmith.py ledger.py        PRs / Braintrust receipts
  genome.py history.py        flake genome / run history
  settings.py preflight.py    typed config / boot validation
ui/src/
  types.ts                    THE event contract — authoritative
  reducer.ts                  board state machine
  useEventStream.ts           WebSocket transport (live-only)
seeds/                        fixtures; seeds/real/ = real OSS specimens
scripts/                      calibration, fallback certification, live smoke
tests/                        228 tests, mocked SDK, no credentials
```

**Rule that keeps this repo honest:** never state a latency, throughput, or flake
number that wasn't measured in this repo. Measured numbers live in
`calibration-results.json`, `docs/WINNING-IDEA.md`, and
`seeds/real/penman_live.README.md`.
