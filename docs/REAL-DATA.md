# Seed data, and the path to real data

Answers two questions: *what data does this project have?* and *what would it take
to run on real production data?* Everything marked MEASURED was produced by this
repo's harnesses against live Daytona. Everything else is labelled.

---

## 1. Does it have seed data? Yes — and until 2026-07-25, almost all of it was synthetic

### Top-level `seeds/*.py` — hand-written fixtures

| File | Flake class | MEASURED on Daytona | Status |
|---|---|---|---|
| `test_dict_order.py` | hash-order (`next(iter(set))`) | **17/40 = 42.5%**, CI [28.5%, 57.8%] | **IDEAL** — primary demo seed |
| `test_first_key.py` | hash-order, dict-from-set variant | **18/40 = 45.0%**, CI [30.7%, 60.2%] | **IDEAL** — backup |
| `test_race_counter.py` | thread race + Barrier | **0/40 = 0%**, CI [0, 8.8%] | **REJECT** |
| `test_write_race.py` | thread race, last-write-wins | **0/40 = 0%**, CI [0, 8.8%] | **REJECT** |
| `test_always_fails.py` | deterministic `sys.exit(1)` | n/a by construction | gate fixture only |

**The two rejected seeds are the most important data in the table.** Thread and
timing races do not reproduce on this substrate — container CPU constraints
suppress them. `CLAUDE.md` reports 0/120 across three variants; only 0/80 across
two is reproducible from the tree today (the third variant survives solely as a
`.pyc`). The conclusion holds either way, and it bounds every claim this project
can make.

### `seeds/suites/order_pollution/` — the bisection fixture

Six files. `test_03_cache_writer.py` writes a truncated `/tmp/app_cache.json`;
`test_05_suspect.py` is green in a pristine environment and flips to a
`random.random() < 0.5` coin flip once that file exists. Ground truth
(`polluter_index = 3`) is stated in the docstring. Never calibrated — the
calibrator globs `seeds/test_*.py` at the top level only. This is a unit fixture
for the bisection *algorithm*, and it is the most synthetic artifact in the repo.

### `seeds/real/` — the real specimens

This is where the project stops being a demo. See §2.

---

## 2. The real specimen, now on the engine's own path

### What was already here

`seeds/real/` documented a genuine IDoFT-catalogued flake from
[goodmami/penman](https://github.com/goodmami/penman) — `tests/test_layout.py::test_rearrange`,
category **NOD**, status **Accepted**, fixed upstream by a one-line diff in
[penman#102](https://github.com/goodmami/penman/pull/102). MEASURED at **87.5%**
(CI 73.9–94.5%) over 40 Daytona trials.

But it could only be run by **bespoke standalone scripts** (`calibrate_penman.py`,
`tournament_penman_sanitized.py`) that reimplemented Wilson, the pool, and winner
selection, and never called the neutering guard. It was evidence *about* the
approach, not a run *of* the product. Worse, one of those scripts now raises
`TypeError` against the current engine signature — the flagship third-party
result could not be reproduced.

### What now exists: `seeds/real/penman_live.py`

The same real test, runnable through `POST /tournament` and `cli check` with no
special-casing. It self-bootstraps its dependency into the sandbox on first use.

MEASURED 2026-07-25, this checkout:

| Run | Result |
|---|---|
| `cli check --max-trials 40 --conc 16` | **13/16 = 81%**, Wilson 95% **[57%, 93%]**, early-stopped, 0 infra errors, **4.6s** |
| Full tournament via `POST /tournament` | detect **93.75%** → winner `glm-5p2` → confirmation → **FIXED**, real Braintrust permalink |

81%, 87.5% and 93.75% all sit inside each other's intervals — consistent
measurements of one test, and a good reminder to quote the interval, not the point.

Operational notes and the leak-prevention rule live in
`seeds/real/penman_live.README.md`. The short version: **the engine sends the seed
file verbatim to the diagnosis models, so anything you write in it is part of the
prompt.**

### Three defects this specimen exposed

None of these were found by reasoning. All three were found by running it.

**1. Dependency failures were being reported as flakiness.**
`trial.py` mapped every non-zero exit to "test failed". The first run of
`penman_live.py` failed to install on all 40 trials — under the old rule that is a
confident **100% flake rate** on a test that never executed. Exit codes outside
`{0, 1}` are now infra errors, excluded from the denominator. The corrected run
reported `ERROR` with 0 valid trials, which is the truth.

**2. `ALWAYS_FAILING` was reachable at 8 trials, and it is terminal.**
At a true rate near 88%, an all-fail opening batch of 16 happens ~13% of the time.
It happened on the **first** live tournament against this seed: 16/16 → the
detect-gate ended the run as `REGRESSION` → no diagnosis, no tournament, on a test
that is genuinely flaky. `ALWAYS_FAILING` now requires 24 valid trials and the
early-stop refuses to fire on an all-failing run below that floor. A true
regression still terminates correctly — verified: `test_always_fails.py` reads
32/32 → `ALWAYS_FAILING` in 4.2s.

**3. A leaked answer in a docstring, caught in the act.**
The first draft of `penman_live.py` cited penman#102 and the literal fix in its
docstring "for provenance." A hint-free rerun after removing it changed the result
completely:

| | with the fix named in the docstring | hint-free |
|---|---|---|
| models naming the right cause | 3/4 | 2/4 |
| models reproducing the maintainer's fix | **3/4** | **0/4** |
| valid patches that delete the code path under test | 0/4 | **2/2** |

This repo had already made — and publicly retracted — this exact mistake once
(`penman_test_rearrange.md`, correction dated 2026-07-23). It is easy to make and
invisible unless you A/B it. **Any claim about model diagnosis quality is void
unless the seed is hint-free.**

### The finding that matters most

Hint-free, both valid patches replaced `rearrange(t, model.random_order)` with
`model.canonical_order` — **removing the feature under test** — and rewrote the
expected string to match. `guards.neutering_check` passed both.

That is the tournament's objective function working exactly as specified: "fails
least often" is maximised by a test that no longer tests anything. It is not a
one-off; it reproduced across different models and a different substituted
attribute than the archived experiment. Read `docs/WORKFLOW.md` §9.1 before
trusting any `FIXED` verdict.

---

## 3. Going further: four paths to production data

Effort/payoff assessed against this codebase.

### (a) More sanitized single-file specimens — LOW effort, HIGH payoff-per-hour
No engine changes; `penman_live.py` is now the template. Best next candidate from
IDoFT's `py-data.csv`:

**`pythological/kanren::tests/test_assoccomm.py::test_eq_assoccomm`** — category
NOD. Builds a `set` of expected tuples and compares against unification results, so
the ordering dependency is set-iteration order: the same PYTHONHASHSEED class this
substrate provably reproduces. Pure-Python deps (`pip install miniKanren`).

Others worth calibrating: `Samreay/ChainConsumer::test_summary_power` (NOD,
**Accepted** — the only other Accepted NOD row in the file, so same evidentiary
weight as penman, but heavy numpy/scipy deps); `ericmjl/nxviz::test_correct_negative_angle`
(Hypothesis draws fresh inputs per process). Avoid `lithoxyl` and `RandomFileTree`
— both seed the RNG at module import, so a fresh process is deterministic and they
will read 0%.

*Risk:* every extraction is a judgment call, and each one can leak its answer. A/B
each specimen hint-free before quoting any diagnosis result.

### (b) Real bootstrap + real pytest in the sandbox — MEDIUM effort, HIGH payoff
The credibility unlock: run `tests/test_layout.py::test_rearrange` *as pytest, in
the real repo*, rather than an extract. Needs:

1. A bootstrap hook on `SandboxPool` (only `ForkSandboxPool` has one today, via
   `RETRIAL_FORK_BOOTSTRAP_CMD` — and the fork backend is VM-only, `us-east-1`-only,
   off by default, and mocked).
2. `trial.py`'s exec line to become `cd <repo> && python -m pytest '<nodeid>' -q`,
   changed in `bisect.py` in the same commit.
3. **pytest's exit codes mapped** — 0=pass, 1=failed, 2–5 = interrupted / internal
   / usage / no-tests-collected. The `{0,1}`-only rule added for `penman_live.py`
   already covers this correctly; without it a typo'd node id reads as a 100%
   flaky test.
4. Whole-file `patched_code` to become a targeted edit.

*What breaks:* the silent-degrade trap — a fork degrade drops you onto a pool with
no bootstrap, and per (3) that would read as pure flakiness. Bootstrap must be a
hard, per-sandbox verified precondition, never best-effort. **Cheapest version: a
prebuilt per-project Daytona snapshot keyed on the lockfile hash**, which sidesteps
fork-VM availability entirely and keeps the 60-second claim true.

### (c) Arbitrary GitHub repo + test node id — HIGH effort, this is the product
Everything in (b), plus dependency resolution across
requirements/pyproject/poetry/conda, Python version matrices, service fixtures,
private repos and credentials, and replacing the `seeds/`-only guard with an auth
model for executing arbitrary third-party code. Weeks, not days. Roadmap slide.

### (d) IDoFT corpus batch benchmark — MEDIUM-HIGH effort, MEDIUM payoff
Fully blocked behind (b), and the headline number will be modest. `py-data.csv`
holds **1,618 rows across 343 projects**, but:

- **1,180 (73%) are order-dependent** (OD-Vic 804, OD-Brit 322, OD 54) — they need
  a suite run in a specific order, which the single-file model cannot express.
  **235 are NOD**, the class that reproduces here.
- **849 (52%) sit in Unmaintained / RepoArchived / RepoDeleted projects** at old
  SHAs. Only **69 rows are status `Accepted`**.

Realistic yield after install failures and substrate mismatch: **tens of rows, not
hundreds.** Framed as *"we reproduced N of M, and here is exactly why the rest
didn't"*, that is a strong, publishable artifact. Framed as coverage, it is a
liability. Note IDoFT ships **no LICENSE file** — cite the ICST 2019 iDFlakies
paper and the dataset entry; do not redistribute the CSV as your own.

### Recommended order

1. **(a) now** — one more hint-free specimen (kanren), calibrated. Retires "your
   demo seed is a toy" for a day's work and zero engine risk.
2. **(b) narrowly** — a prebuilt snapshot for one real project plus the pytest exec
   change, proven against penman's *actual* pytest test. That single demo —
   "here is the real upstream test, unmodified, under real pytest, at 88%" —
   retires "it only runs toys."
3. (c) and (d) are roadmap.

**Before any of it: fix the semantic-deletion hole (`WORKFLOW.md` §9.1).** Running
on more real repos without it just produces more confidently-attested deleted
tests.
