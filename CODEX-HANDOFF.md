# CODEX-HANDOFF — Where this branch stands and how to finish it

**Branch:** `rewind-merge` · **Repo:** `nihalnihalani/retrial` · **Handoff written:** 2026-07-24, after commit `f8fa5ed`

This file is the continuation brief for any agent (Codex or otherwise) picking up the work.
Read it top to bottom before writing code.

---

## 1. What is DONE, verified, and pushed (do not redo)

Commit `f8fa5ed` — "Retrial, powered by the Rewind engine" — a full merge of the
`rewind-agent` fork engine into Retrial. All of it passed a 4-verifier panel
(backend pytest with mocked Daytona SDK, `tsc -b && vite build`, adversarial code
audit, spec/honesty audit):

- **SEAM-1 fork provisioning** — `engine/retrial/forkpool.py`: `ForkSandboxPool`
  (warm one root sandbox → `_experimental_fork` + `pause()` checkpoint → wake →
  fork-N byte-identical clones → re-pause in `finally`, partial-clone rollback,
  leaf-first teardown, `_fork_lock` serializing fork batches). Selected by
  `RETRIAL_POOL_BACKEND=fork|snapshot` (default `snapshot`) via `make_pool()` in
  `pool.py`; **sticky degrade** to the snapshot pool on any fork failure, emitting
  a `pool_degraded` event.
- **SEAM-2 time-travel bisection** — `engine/retrial/bisect.py`: `FlakeBisector`
  checkpoints the suite at every test boundary (fork+pause), probes checkpoints
  through the existing Wilson-CI `verifier.verify()` oracle, binary-searches to the
  polluting test. Surfaced as CLI subcommand `bisect` and `POST /bisect`
  (400 unless fork backend). Seed suite: `seeds/suites/order_pollution/`.
- **SEAM-3 UI + shipping + tests/CI** — `ui/src/components/TreeTimeline.tsx`,
  `PromoteGate.tsx`; human-approval gate in front of PRSmith (`POST /promote`,
  events `promotion_pending`/`promotion_closed`, `PROMOTE_GATE=0` restores auto);
  mocked-Daytona pytest suite in `tests/` with `conftest.py` fakes; two-job CI in
  `.github/workflows/ci.yml`; honest docs (CodeRabbit/ElevenLabs vaporware claims
  removed; historical pitch decks kept only under ARCHIVED disclaimers).

Everything above is committed and pushed. `main` is untouched.

## 2. What is IN PLAN but NOT implemented (your job)

**Phase 2: Sandbox Observatory + lifecycle controls.** The architect's file-level
plan is in **`OBSERVATORY-PLAN.md`** (committed alongside this file). Execution
stopped after the plan + critique stages (agent session limit), so:

- `OBSERVATORY-PLAN.md` exists but **has NOT been revised** to address the
  devil's-advocate critique in §3 below. Revise it first (or just honor the fixes
  during implementation).
- **Zero phase-2 code has been written.** Work packages, in order:
  - **WP-BACKEND** — thread-safe `SandboxRegistry` tracking every sandbox
    (id, role root|checkpoint|trial-clone|snapshot-pool|bisect-probe, backend,
    state, parent-id fork lineage, timestamps, flake-class, current command,
    bounded ring buffer of recent execs with exit codes/output tails, exec counts,
    Daytona preview URL via `sandbox.get_preview_link(port)` when obtainable else
    None). Hook it into `SandboxPool`, `ForkSandboxPool`, and `FlakeBisector` at
    create/fork/pause/start/exec/destroy points — every hook wrapped so a registry
    failure can NEVER break a run. New typed events (`sandbox_registered`,
    `sandbox_state`, `sandbox_exec`, `sandbox_destroyed`, `registry_snapshot`)
    registered in **all 3 places** (see MERGE-PLAN.md conventions). Endpoints:
    `GET /sandboxes` (snapshot + lineage + live/total/destroyed counts),
    `GET /sandboxes/{id}` (full detail incl. exec history),
    `DELETE /sandboxes/{id}`, `POST /sandboxes/destroy_all` (409 during an active
    run unless `?force=1` — but see FATAL flaw below), atexit + FastAPI shutdown
    reaping. CLI: `retrial sandboxes` (table) and `retrial reap`.
  - **WP-FRONTEND** — `SandboxObservatory` panel: role-grouped live grid with
    state colors, fork-lineage tree (root → checkpoint → clones/probes), detail
    drawer (full record, scrolling exec feed, preview-link button opening the
    Daytona preview URL), header strip live/total/destroyed + Destroy-all with
    confirm modal, per-card destroy. Consumes the new WS events via `reducer.ts`.
    Demo without keys: opt-in `?mock=observatory` scripted feed AND a clearly
    labeled "replay reconstruction" derived from existing replay trial events so
    the panel is never empty in default replay.

## 3. Devil's-advocate critique of OBSERVATORY-PLAN.md — MUST be honored

1. **[FATAL] `destroy_all?force=1` race:** `ForkSandboxPool.destroy_all()` as
   planned deletes `self._ckpt`/`self._root` without acquiring `_fork_lock`, while
   `_fork_clones()` holds `_fork_lock` for the whole start→fork×N→pause sequence —
   a forced reap can delete the checkpoint mid-fork. **Fix:** `destroy_all()` must
   acquire `_fork_lock` and set a torn-down sentinel under that lock so later
   `lease()`/`warm()` fail or degrade honestly. Same discipline for
   `FlakeBisector` (`self._lock` must guard all `_root`/`_ckpts`/probe-pool
   mutations in both `_run()` and `destroy_all()`).
2. **[MAJOR] Unbounded `registry_snapshot`:** records survive destruction and the
   snapshot broadcast grows all day. **Fix:** cap snapshots to live sandboxes +
   most-recently-destroyed ~50, prune older destroyed records, keep
   `total_ever`/`destroyed` counters exact independent of retention. Document the
   retention window honestly in README.
3. **[MAJOR] Facade-registry risk:** planned tests all inject/monkeypatch a fresh
   registry. **Fix:** add ONE true end-to-end test with NO registry
   monkeypatching — boot TestClient with the module-default `REGISTRY`, drive a
   mocked-SDK `/tournament` to completion through the real
   `_get_pool()`/coordinator/trial path, assert `GET /sandboxes` shows records
   with nonzero `exec_count` the test never inserted.
4. **[minor] Preview caching:** never permanently cache a `None` preview while a
   sandbox is `paused`/`creating` (roots/checkpoints live paused — the button
   would never appear). Cache `None` permanently only once `destroyed`; otherwise
   retry or short TTL.
5. **[minor] Sacred-replay regression test:** add an automated test asserting the
   default (no query params) mock script emits **zero** of the 5 new sandbox/registry
   event types and its schedule is deep-equal to the pre-Observatory baseline.
   (This team already shipped a stale-bleed regression once — do not rely on a
   manual smoke check.)
6. **[minor] Docstrings must not justify pool-scope force-reap by analogy to
   single-sandbox DELETE** — they are different safety classes; document separately.

## 4. Hard rules (same ones the merge was built and verified under)

- **The default replay demo is sacred.** `ui/src/useEventStream.ts` with no query
  params replays `realRun.json` — its behavior must remain byte-identical. New
  demos go behind opt-in `?mock=` params only.
- **Honesty is a product requirement.** Docs may claim only what code does. Say
  "preview links when Daytona exposes them"; do not claim RAM metrics Daytona
  doesn't provide. (History: both parent repos were dinged for vaporware sponsor
  claims; don't reintroduce any.)
- **Degrade gracefully everywhere** — observability/logging must never break a
  run; sponsor integrations no-op on failure. Match existing style: EventBus event
  pattern (registration in 3 places — see MERGE-PLAN.md), locks for thread safety,
  `_run_lock` + `BUS.reset()` acceptance block in `server.py` (mind the
  stale-bleed lesson: reset shared state inside the run lock at acceptance).
- **No live API calls in tests.** No keys are required: the pytest suite mocks the
  Daytona SDK via `tests/conftest.py` fakes; follow that pattern.

## 5. How to verify (all runnable without any API keys)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
find engine scripts -name '*.py' -print0 | xargs -0 .venv/bin/python -m py_compile
.venv/bin/python -m pytest tests/ -q          # full suite must pass
cd ui && npm install && npm run build          # tsc -b && vite build must pass
```

Acceptance for phase 2 = full pytest green (including the new registry/lifecycle/
409/end-to-end tests and the sacred-replay regression test), UI build green, and a
self-audit that every README claim maps to code.

## 6. Finish line

1. Revise/implement per §2 + §3, verify per §5.
2. Update README's feature list (honestly) with the Observatory + lifecycle controls.
3. Commit on `rewind-merge` and push to `origin` (`nihalnihalani/retrial`).
4. Optional: open a PR `rewind-merge` → `main`.

Key files map: `engine/retrial/{pool,forkpool,bisect,events,server,cli,verifier,
coordinator,trial,guards,prsmith,ledger,genome}.py` · `ui/src/{types,reducer,
useEventStream}.ts` + `ui/src/components/` · `tests/` + `tests/conftest.py` ·
plans: `MERGE-PLAN.md` (phase 1, done) and `OBSERVATORY-PLAN.md` (phase 2, to do).
