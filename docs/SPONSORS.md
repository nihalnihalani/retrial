# Sponsor integrations — what Retrial actually uses

Event: Daytona HackSprint #5 — SF, July 2026 (https://luma.com/hacksprint-sf).

This file used to be a pre-event sponsor playbook full of *plans*. Plans are
not integrations, and earlier drafts of this repo were (rightly) dinged for
claiming sponsor tools that had no code path. This is the honest list: every
entry below names the module that uses it, and how it is verified.

## Integrated (code paths exist)

### Daytona — the substrate
- **Snapshot sandbox pool** (`engine/retrial/pool.py`): warm/lease/release/destroy
  of disposable container sandboxes; every trial runs in one. Live timings
  measured in [DAYTONA-COOKBOOK.md](DAYTONA-COOKBOOK.md) (create ~0.7s, 16
  concurrent ~2.0s).
- **Fork engine** (`engine/retrial/forkpool.py`, `engine/retrial/bisect.py`,
  ported from the Rewind project): `_experimental_fork` + `pause`/`start`
  checkpoints — one warm root frozen, byte-identical trial clones forked from
  it; time-travel bisection checkpoints a suite at every test boundary.
  **Verification honesty:** the fork path is exercised against a *mocked* SDK
  in `tests/` (no live keys in CI); fork-primitive timings cite Rewind's spike
  measurements (see [SPIKE-RESULTS.md](SPIKE-RESULTS.md)), clearly attributed,
  not re-verified here. On any live fork failure the pool degrades to the
  snapshot backend and says so (`pool_degraded`).

### Fireworks — the diagnosis brains
- `engine/retrial/diagnosis.py`: N competing root-cause hypotheses from
  round-robined Fireworks models (OpenAI-compatible API,
  `base_url=https://api.fireworks.ai/inference/v1`). Real code path; requires
  `FIREWORKS_API_KEY`. The JSON parsing is pure and unit-tested
  (`tests/test_diagnosis_parse.py`); without a key the engine falls back to
  cached hypotheses / detect-only, honestly.

### Braintrust — the evidence ledger
- `engine/retrial/ledger.py`: one experiment per hypothesis, one log per
  trial; the permalink in the verdict card and PR body is the receipt.
  Optional: with no `BRAINTRUST_API_KEY` every ledger call is a silent no-op.
- `braintrust.init_logger` + `auto_instrument` tracing in `server.py`/`cli.py`,
  same conditional pattern.

### GitHub (`gh` CLI) — shipping
- `engine/retrial/prsmith.py`: fix/quarantine PRs created server-side via
  `gh api` (ref/blob/PR), never touching the local working tree — behind the
  human promote gate (`POST /promote`, default on).

## Not integrated (and not claimed)

No other sponsor tool has a code path in this repo. Earlier pitch material
mentioned a code-review gate and a voice narration layer that were never
built; those claims have been removed rather than reworded. If a future
integration lands, it gets added here with its module path — that is the bar.
