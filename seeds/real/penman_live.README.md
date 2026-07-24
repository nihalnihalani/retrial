# `penman_live.py` — the real specimen, on the engine's own path

The first REAL, third-party, catalogued flaky test that `POST /tournament` can
run unmodified. Everything else in `seeds/` is either a hand-written fixture or a
repro that only a bespoke script could execute.

## Why this file exists separately from the docstring

**The engine sends the seed file verbatim to the diagnosis models.** Anything
written in `penman_live.py` — docstring, comment, variable name — is part of the
prompt. So provenance, the measured rate, and the root cause live *here*, in a
file the engine never reads.

This is not a hypothetical. It has now happened twice:

1. The original `penman_test_rearrange_repro.py` carried an 8-line header naming
   the root cause. The resulting "3 of 4 models independently rediscovered the
   maintainer's fix" claim was invalid and was retracted — see the
   `⚠️ Correction (2026-07-23)` block in `penman_test_rearrange.md`.
2. **2026-07-25:** the first draft of `penman_live.py` cited `penman#102` and the
   literal `random.seed(1)` fix in its docstring "for provenance." A live
   4-model diagnosis then returned 3/4 patches that kept `random_order` and
   seeded the RNG — i.e. the maintainer's fix, read off the page. Measured, not
   suspected: the run is in the scratchpad diagnosis dump for that date.

**Rule: if a fact would help a model diagnose it, it does not belong in the seed.**

## Provenance

| Field | Value |
|---|---|
| Project | [goodmami/penman](https://github.com/goodmami/penman) 1.2.1 — MIT, pure Python, pip-installable |
| Test | `tests/test_layout.py::test_rearrange` |
| Dataset | [IDoFT](https://github.com/TestingResearchIllinois/idoft) `py-data.csv` — category **NOD**, status **Accepted** |
| SHA detected | `7770dfe14b3d0d197cedc6640f3ff7e3bd695726` |
| Human fix | [goodmami/penman#102](https://github.com/goodmami/penman/pull/102), merged 2021-11-15 — a one-line diff |

Root cause and the maintainer's reasoning: `penman_test_rearrange.md`.

## Measured on Daytona (this repo, 2026-07-25)

| Run | Result |
|---|---|
| `cli check --max-trials 40 --conc 16` | **13/16 = 81%**, Wilson 95% **[57%, 93%]**, early-stopped, 0 infra errors, **4.6s** |
| Earlier bespoke-script calibration | 35/40 = 87.5%, CI [73.9%, 94.5%] (`penman_daytona_result.json`) |
| Full tournament via `POST /tournament` | detect **93.75%** → winner `glm-5p2` → confirmed → **FIXED**, Braintrust receipt written |

The three rates are mutually consistent — 81%, 87.5% and 93.75% all sit inside
each other's intervals. Quote the interval, not the point.

## Two engine changes this specimen forced

Both were found by running it, not by reasoning about it.

1. **Non-verdict exit codes are infra errors** (`trial.py`). A seed with a real
   dependency can fail to *install*. Under the old rule (`passed = code == 0`)
   every bootstrap failure counted as a test failure, so a broken install read as
   a confident **100% flake rate** — the exact class of lie this product exists to
   detect. Exit codes outside `{0, 1}` are now excluded from the denominator.
   First run of this seed produced 40/40 infra errors and correctly reported
   `ERROR`, not `100% flaky`.
2. **`ALWAYS_FAILING` needs 24 valid trials, not 8** (`verifier.py`). At a true
   rate of ~88%, an all-fail opening batch of 16 happens ~13% of the time. It
   happened on the *first* live tournament against this seed: 16/16 → the
   detect-gate terminated the run as `REGRESSION` and never diagnosed a
   genuinely flaky test. The early-stop now refuses to fire on an all-failing run
   below the floor.

## Operational notes

- **Needs network in the sandbox.** Do **not** run under `HERMETIC=1`.
- Under `isolation=process` the ~3–5s install is paid once per warm sandbox and
  reused by every later trial in it.
- It deliberately breaks CLAUDE.md's "seeds are dependency-free" rule. That is
  the point of the file: it proves the swarm can measure a test with a real
  third-party dependency. Keep the other seeds dependency-free.
- The same-process re-import after `pip install` needs
  `importlib.invalidate_caches()` plus an explicit user-site `sys.path` entry —
  without them the install succeeds and the import still fails, because the
  interpreter's path finders predate the newly created user-site directory.
