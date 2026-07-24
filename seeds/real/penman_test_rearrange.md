# Real-world flake specimen — `penman::test_rearrange`

The **real patient**: a documented flaky test from a real OSS Python project that
reproducibly flakes inside Daytona container sandboxes. This is *evidence* for Q&A
("we found a real flake in the wild and reproduced it on the same substrate"),
NOT the timed-demo specimen. The calibrated demo seed remains
`seeds/test_dict_order.py` (51%, locked).

## Provenance

| Field | Value |
|---|---|
| Project | [goodmami/penman](https://github.com/goodmami/penman) — PENMAN notation for graphs (AMR), pure-Python, MIT |
| Version | `penman==1.2.1` (pip-installable, no compiled deps) |
| Test | `tests/test_layout.py::test_rearrange` |
| IDoFT row | [`py-data.csv`](https://github.com/TestingResearchIllinois/idoft/blob/main/py-data.csv), Category **NOD** (non-order-dependent), Status **Accepted** |
| SHA (detected) | `7770dfe14b3d0d197cedc6640f3ff7e3bd695726` |
| Fix PR | [goodmami/penman#102](https://github.com/goodmami/penman/pull/102) |

## Documented root cause (randomness class — NOD)

`Model.random_order` is a role sort-key that returns `random.random()`
(`penman/model.py:300`):

```python
def random_order(self, role: Role):
    """Role sorting key that randomizes the order."""
    return random.random()
```

`test_rearrange` calls `rearrange(t, model.random_order)` and then asserts the
serialized tree equals **one specific permutation**. That assertion is only stable
if the `random` stream is seeded to the exact sequence that produces that
arrangement. The maintainer's fix (PR #102) moved `random.seed(1)` from module
level into the test body, because — in their words — *"the globally defined
`random.seed(1)` seems not working well here … setting the random seed within the
test itself seems safer than doing it within the module."* When the module-level
seed was ineffective (consumed/reset before the test ran), the sort produced a
random permutation and the assertion failed intermittently.

This is the **"NOD with randomness"** class the Retrial substrate reproduces:
a fresh `python3` process = fresh `random` entropy = a different permutation each run.
(Contrast with thread/timing races, which we measured do **not** flake on this substrate.)

## Minimal standalone reproduction

Faithful to the real test's `random_order` branch, run **unseeded** — i.e. under the
exact condition the bug created (seed ineffective). Requires `pip install penman==1.2.1`.
Exit 0 = pass, 1 = fail.

```python
# penman test_layout.py::test_rearrange -- random_order branch, unseeded (the bug).
import sys
from penman.model import Model
from penman.codec import PENMANCodec
from penman.layout import rearrange

codec = PENMANCodec()
model = Model()

t = codec.parse('''
    (a / alpha
       :ARG0 (b / beta
                :ARG0 (g / gamma)
                :ARG1 (d / delta))
       :ARG0-of d
       :ARG1 (e / epsilon))''')

rearrange(t, model.random_order)
expected = (
    '(a / alpha\n'
    '   :ARG0-of d\n'
    '   :ARG1 (e / epsilon)\n'
    '   :ARG0 (b / beta\n'
    '            :ARG0 (g / gamma)\n'
    '            :ARG1 (d / delta)))')
sys.exit(0 if codec.format(t) == expected else 1)
```

## Measured Daytona flake rate

Calibrated on Daytona containers (`target="us"`), warm-pool with **process isolation**
(install `penman==1.2.1` once per sandbox, then run the repro as N fresh `python3`
processes — each process = fresh random entropy). Sound here because the flake is
per-process random state, not filesystem/port pollution.

| Metric | Value |
|---|---|
| Trials | 40 (8 sandboxes × 5) |
| Fails | 35 |
| **Flake rate** | **88%** |
| Wilson 95% CI | **[74%, 95%]** |
| Infra errors | 0 |
| Wallclock | 10.9 s (incl. 8× pip install) |
| Local cross-check | 37/40 = 92% fail (fresh-process, macOS Python 3.14) |

Both passes and failures occur (5/40 passed on Daytona) — genuinely
non-deterministic, not merely broken. The rate is **skewed high** because the test
asserts one exact permutation out of many; when the seed is ineffective the random
sort rarely matches. This is the authentic behavior of the real test's random branch
— the rate is measured, not tuned. (It is not in the 40–55% "ideal demo" band, which
is why the demo keeps the calibrated `test_dict_order.py`.)

## Takeaway for the pitch / Q&A

- **Yes, this reproduces on the real substrate.** A real, IDoFT-catalogued, maintainer-fixed
  Python flake (randomness class) flakes on Daytona containers at 88% (CI 74–95%), 0 infra errors.
- Confirms the substrate's reach: **randomness/hash-ordering flakes reproduce; thread/timing
  races do not** (measured 0/120 elsewhere in this repo). Retrial's detector targets exactly the
  class that reproduces.
- Reproduced via the same process-isolation model the engine uses (`isolation="process"`),
  with a one-time `pip install` per warm sandbox.

## ULTIMATE EXPERIMENT — full Retrial tournament (ORIGINAL run — see correction below)

> **⚠️ Correction (2026-07-23).** An audit found this original run **invalid for its
> headline "independently rediscovered" claim**: the repro file fed to the models
> (`penman_test_rearrange_repro.py`) had header comments that *literally named the
> root cause* — "module-level `random.seed(1)` was ineffective … run this exact branch
> UNSEEDED". The models were told the answer, so "independently rediscovered the
> maintainer's fix" was overclaimed. This section is **kept verbatim for transparency**.
> The corrected experiment — a **sanitized rerun with no cause hints** — is in
> [Sanitized rerun](#sanitized-rerun--no-cause-hints-corrects-the-above) below, and its
> result is different. Trust the sanitized section for any claim.

We ran a **complete Retrial tournament** on this real flake: the engine's
Fireworks `diagnose()` (imported as-is from `retrial.diagnosis`, 4 models
round-robin) generated 4 competing hypotheses; each was re-verified by rerunning
its `patched_code` on Daytona (pip-install penman once per warm sandbox, fresh-process
trials, Wilson 95% CI). Script: `seeds/real/tournament_penman.py`, raw data:
`seeds/real/tournament_penman_result.json`.

**Result: 3 of 4 models independently rediscovered the maintainer's fix (PR #102)
— add an effective `random.seed(1)` before `rearrange(t, model.random_order)` —
and evidence eliminated the 4th.**

| Hypothesis | Model | Stated cause | Re-measured flake rate | Verdict |
|---|---|---|---|---|
| baseline (unpatched) | — | — | 14/16 = **88%** (CI 64–96%) | flaky |
| **h1 (WINNER)** | glm-5p2 | order_dependency | **0/16 = 0%** (CI 0–19%) | fixes it |
| h2 | glm-5p1 | shared_state | 0/16 = 0% (CI 0–19%) | fixes it |
| h3 | kimi-k2p6 | timing | 15/16 = **94%** (CI 72–99%) | **eliminated** |
| h4 | deepseek-v4-pro | order_dependency | 0/16 = 0% (CI 0–19%) | fixes it |

- **Winner h1 (glm-5p2)** — explanation: *"…sorts roles using `random.random()`, but the
  RNG is never seeded deterministically…"* Its patch adds `random.seed(1)` immediately
  before the `rearrange` call — **the same fix goodmami/penman shipped in PR #102.**
  Confirmation round: **25 fresh-process trials across 5 sandboxes, 0 fails = 0%** (CI 0–13%).
- **h3 (kimi-k2p6)** is the instructive counter-example: it returned an empty explanation
  and a patch that still flaked at **94%** — so the empirical reruns **eliminated it on
  evidence**, exactly the mechanism Retrial exists for (objective flake-rate, not LLM vibes).
- Winner selection rule (engine's): lowest flake rate whose Wilson CI upper bound < the
  original rate. h1/h2/h4 all qualify (CI upper 19% < 88%); h1 chosen (ties broken by rate,
  then order). Total tournament wallclock: **57s** including diagnosis + 89 Daytona trials.

**The claim this unlocks:** *Retrial took a real, IDoFT-catalogued OSS flake, and its
model tournament independently converged on the exact fix the human maintainer shipped —
verified with 0% flake across a fresh confirmation round.* **← SUPERSEDED. This claim
depended on the repro's cause-naming comments. It does NOT survive the sanitized rerun
below; do not use it.**

## Sanitized rerun — no cause hints (corrects the above)

To test the honest question — *what do the models propose from the failing test **alone**?* —
we re-ran the identical tournament against a **sanitized repro**
(`seeds/real/penman_repro_sanitized.py`): the same failing test and its code, but with
**every root-cause comment stripped** and a neutral docstring ("reproduction of a flaky
penman test, extracted at v1.2.1"). The only inputs the models see are the failing test
source (which genuinely calls `rearrange(t, model.random_order)` — that method name is the
real library API, not an injected hint) and a real failure log showing GOT vs EXPECTED
serialization (which names no cause). Script: `seeds/real/tournament_penman_sanitized.py`;
full artifacts (prompt sent + raw model response + extracted patch + per-trial results per
hypothesis, plus run metadata and UTC timestamps): `seeds/real/tournament_penman_sanitized_result.json`.
Run: 2026-07-23T05:51:58Z, 85.5 s, 105 fresh-process Daytona trials, 0 infra errors.

**Result: convergence on the maintainer's specific fix did NOT hold. 0 of 4 models seeded
the RNG.** The one model that returned a verifiable patch fixed the flake a *different*
valid way; the other three did not return a parseable patch (harness fell back to the
unmodified repro, so they measured at baseline).

| Hypothesis | Model | Cause hint given | Stated cause | What it actually did | Re-measured flake | Verdict |
|---|---|---|---|---|---|---|
| baseline (unpatched) | — | — | — | sanitized repro, unchanged | 16/16 = **100%** (CI 81–100%) | flaky |
| **h1 (WINNER)** | glm-5p2 | order_dependency | randomness in `random_order` | swapped `random_order`→`original_order` **and** rewrote `expected` to the deterministic serialization (does **not** seed the RNG) | **0/16 = 0%** (CI 0–19%) | fixes it (verifiable) |
| h2 | glm-5p1 | shared_state | randomness in `random_order` (in prose) | **no JSON returned** — 8.5 KB of prose reasoning; parser fell back to the original repro | 11/16 = **69%** (CI 44–86%) | parse-failure → baseline |
| h3 | kimi-k2p6 | timing | randomness / PYTHONHASHSEED (in prose) | **no JSON returned** — 9.1 KB of prose; fallback to original repro | 14/16 = **88%** (CI 64–97%) | parse-failure → baseline |
| h4 | deepseek-v4-pro | race_condition | randomness in `random_order` | **truncated JSON** (130 chars, cut off before `patched_code`); fallback to original repro | 15/16 = **94%** (CI 72–99%) | parse-failure → baseline |

- **Winner h1 (glm-5p2)** — the only model to emit a well-formed, parseable patch. It
  correctly diagnosed the randomness root cause (*"`model.random_order` … uses Python's
  `random` module to produce a non-deterministic ordering … the expected string captures
  only one possible permutation"*) and de-flaked the test by **removing the random path**
  (call `model.original_order`, update `expected` to match) rather than seeding it.
  Confirmation round: **25 fresh-process trials across 5 sandboxes, 0 fails = 0%** (CI 0–13%).
  This is a *legitimate deterministic fix* — but it is **not** the maintainer's PR #102 fix
  (which kept `random_order` and made `random.seed(1)` effective), and it changes what the
  test exercises (it no longer tests the `random_order` branch).
- **h2/h3/h4** are not "wrong hypotheses" so much as **format failures**: their raw responses
  (saved in the artifact JSON) show they *also* correctly identified randomness/`random_order`
  as the cause in prose, but they did not return the strict JSON object with a `patched_code`
  field the engine requires, so `_parse_hypothesis` fell back to the unmodified input. Two
  returned free-form prose; one returned truncated JSON. They therefore measured at ~baseline.
- **Diagnosis vs fix.** At the *diagnosis* level all four models pointed at `random_order`/
  randomness even without hints — so the models *can* infer the cause from the failing test
  alone. At the *specific-fix* level, convergence on the maintainer's seed fix did **not**
  reproduce: 0/4 seeded the RNG, and only 1/4 produced any verifiable patch at all.

**The honest claim this actually supports:** *Given a real IDoFT-catalogued OSS flake with
no cause hints, Retrial's model tournament independently identified the randomness root
cause, and the one model that produced a verifiable patch shipped a valid deterministic fix
that Retrial confirmed at 0% flake (0/25 fresh trials) — while empirical reruns correctly
declined to crown the three non-fixes.* Retrial's value proposition — **evidence, not LLM
vibes, decides the winner** — is if anything *strengthened* here: three plausible-looking
model outputs were rejected on measured flake rate, and the winner was chosen and
independently re-confirmed by rerun statistics, not by trusting the model.

> **Note for the engine team (not fixed here — out of the seeds/ lane):** 3 of 4 models
> failed to emit parseable JSON under `response_format=json_object` at temperature 0.7
> (two returned prose, one truncated at `max_tokens=2048`). The engine currently falls back
> to the *original* code on a parse failure, which silently turns a non-answer into a
> baseline-flake "hypothesis." Consider surfacing parse failures as an explicit hypothesis
> status rather than a silent fallback.

## Reproduce it yourself

```bash
# from retrial/ with .env populated:
SCR=$(mktemp -d)
cp seeds/real/... # (repro embedded above -> $SCR/repro.py)
# 8 sandboxes x 5 trials:
POOL=8 PER=5 SCR=$SCR .venv/bin/python <calibrate_penman.py>
```
Calibration harness used: `scratchpad/calibrate_penman.py` (adapted from
`scripts/calibrate_seeds.py` — adds one-time `pip install penman==1.2.1` per warm
sandbox and reuses each sandbox for `PER` fresh-process trials).
