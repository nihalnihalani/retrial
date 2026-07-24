# MERGE-PLAN: Retrial × Rewind

Product: **Retrial — the flaky-test tournament, powered by the Rewind fork engine.**
Branch: `rewind-merge`. This plan is self-contained: implementers read this file + the code.

Paths used below:
- `RETRIAL` = `/private/tmp/claude-501/-Users-nihalnihalani-Desktop-Github-dyt-hack-sprint-/4a08c246-1ffd-4969-baa1-5f4d2e57efef/scratchpad/retrial` (the only writable tree)
- `REWIND` = `/private/tmp/claude-501/-Users-nihalnihalani-Desktop-Github-dyt-hack-sprint-/4a08c246-1ffd-4969-baa1-5f4d2e57efef/scratchpad/rewind-agent` (READ ONLY — port from it, never modify)

Global rules (apply to every seam):

- **No live SDK calls anywhere in verification.** No Daytona/Fireworks/Braintrust keys exist here. Everything is verified with `py_compile`, `pytest` with the Daytona SDK mocked, and `tsc -b && vite build`.
- **Match retrial style**: module docstring headers explaining the why; `threading.Lock`/threads (no executors, no asyncio outside FastAPI); degrade-gracefully `try/except` that returns a fallback instead of raising to the demo; env-var config read at use site with safe defaults; events emitted as `bus.emit("snake_case_type", {snake_case_payload})`.
- **EventBus discipline**: every new event type is added to `EVENT_TYPES` in `engine/retrial/events.py`, to the `RetrialEvent` union in `ui/src/types.ts`, AND to `ui/src/reducer.ts`. (Precedent to NOT repeat: `hermetic_diagnosis` is emitted by the engine but missing from the TS union — SEAM-3 fixes that too.)
- **Run acceptance happens in exactly ONE place**: a shared `_accept_run(test_name)` helper in `server.py`, called under `_run_lock` by EVERY run-starting endpoint (`/tournament`, `/bisect`, and any future seam). It performs `BUS.reset()`, clears `_pending["promotion"]` (once SEAM-3 introduces it), and sets `_running` — all inside the lock. Rationale: the prior stale-bleed bug (see `events.py::reset` comment) recurred in review as soon as a second endpoint hand-rolled its own reset block; centralizing makes a third recurrence structurally impossible. Never reset from a background thread.
- New public symbols get re-exported from `engine/retrial/__init__.py` with `__all__` updated.
- Never touch `.git`, never commit/push. Do not modify `REWIND`.
- Venv: `python3 -m venv RETRIAL/.venv && RETRIAL/.venv/bin/pip install -r RETRIAL/requirements.txt -r RETRIAL/requirements-dev.txt` (requirements-dev.txt created in SEAM-1). Python here is 3.14.

Load-bearing facts verified against the code:

- `SandboxPool` (pool.py) public surface is exactly: `warm(n)`, `ensure_warm(target)`, `resize_to(target)`, `lease()`, `release(sb, reusable=False)`, `destroy_all()`, `stats()->{"available","live"}`. Consumers (`server.py`, `cli.py`, `trial.py`, `verifier.py`) use nothing else. Any object honoring this surface is a drop-in.
- `SandboxPool.__init__(client=None, ...)` accepts an injected client — the mocked-SDK tests hook in there.
- Rewind's checkpoint primitive (`REWIND/server/engine.py`): checkpoint = `parent._experimental_fork(name=...)` then `fork.pause()` (paused fork-child captures fs+RAM; parent never stops). Re-fork = `ckpt.start()` → `ckpt._experimental_fork()` per clone → `ckpt.pause()` in a **finally** block; `_retry(op_name, fn, attempts=3, base_delay=1.0)` wraps every experimental/pause/start call; partial-fork failure deletes already-spawned clones then re-raises; parents are undeletable while children live → teardown is leaf-first.
- Trial exec pattern (trial.py): single round-trip `echo '<b64>' | base64 -d > /tmp/seed.py && python3 /tmp/seed.py; echo EXIT:$?`, parse `EXIT:(-?\d+)`, infra-errored sandboxes are never released reusable.
- CI-disjoint test (coordinator.py `_hermetic_detect`, line ~89): `overlap = not (a_ci[1] < b_ci[0] or b_ci[1] < a_ci[0])` — reuse this exact criterion for the bisection flip.
- `daytona==0.200.1` is pinned. Whether `_experimental_fork`/`pause`/`start` exist on its sandbox class **cannot be live-verified here** — there is NO dedicated `getattr`/`hasattr` probe; the fork backend relies entirely on the generic per-method `except Exception` net (which also catches the `AttributeError` a missing `_experimental_fork` raises) to degrade to snapshot, and docs claim only "mock-verified". Do not build an explicit probe.
- **Concurrency caveat (load-bearing risk)**: Rewind's only proven use of `_experimental_fork` (`REWIND/server/engine.py::fork_futures`, 168–231) forks strictly SEQUENTIALLY in a single thread. No code anywhere demonstrates concurrent forks from multiple threads on one sandbox handle, and we cannot verify that against the real SDK here. Therefore every place this plan forks from a shared handle MUST serialize the actual `_experimental_fork` call behind a lock (see `_CheckpointProbePool` in 2.1), and the mocked tests must stress that locking discipline under real threads (2.7 test 8).

---

## SEAM-1 — Fork-based provisioning (work package 1)

Goal: a `ForkSandboxPool` that warms ONE root sandbox, freezes it as a checkpoint (paused fork-child), and `_experimental_fork`s byte-identical trial clones from it — behind `RETRIAL_POOL_BACKEND=fork|snapshot` (default `snapshot`), with automatic fallback to the existing `SandboxPool` on ANY fork-path failure.

### 1.1 CREATE `engine/retrial/forkpool.py`

Module docstring: explain the statistics argument (fork clones share byte-identical initial fs+RAM state, so trial-to-trial variance is purely the flake, not provisioning noise) and the degrade contract.

Port **verbatim** from `REWIND/server/engine.py`:
- `_retry(op_name, fn, attempts=3, base_delay=1.0)` (lines 48–63) — linear backoff, raises last error.

Port **as adapted logic** (rewind semantics, retrial style):
- warm-then-freeze sequence from `create_root` + `checkpoint` (engine.py 118–166)
- wake→fork-N→refreeze with finally/partial-rollback from `fork_futures` (engine.py 168–231)
- leaf-first teardown from `destroy_all` (engine.py 322–339)

Do **not** port: `Node`/tree persistence/`STATE_FILE` (retrial has no tree state file; the EventBus is the state channel), `REWIND/server/config.py`'s hardcoded macOS `DEFAULT_CONFIG_PATH`, promote/rollback (that's SEAM-3, and it lands in server.py/prsmith flow, not the pool).

```python
class ForkSandboxPool:
    """Drop-in for SandboxPool: same 7-method surface, fork-clone provisioning."""

    def __init__(self, client=None, target=None, labels=None, hermetic=False,
                 auto_delete_min=None, bus=None, fallback=None):
```
- Same client construction as `SandboxPool.__init__` (`Daytona(DaytonaConfig(target=...))`, `dotenv` load). `labels` default `{"retrial": "fork-pool"}`.
- State: `_root`, `_ckpt` (sandbox handles or None), `_available: list`, `_live: dict id->sb` (trial forks only), `_lock = threading.Lock()`, `_degraded = False`, `_fallback` (a `SandboxPool` built lazily with the same ctor args on first degrade, unless injected), `_bus = bus`.
- Spend guard: `_max_forks = int(os.environ.get("RETRIAL_MAX_FORKS", "64"))` — checked before each fork batch against `len(_live)`; exceeding it raises → degrade path.

Methods (each fork-path method wraps its body in `try/except Exception: return self._degrade_and(<fallback call>, exc)`):

- `_ensure_checkpoint(self)`: under a creation guard (plain flag + lock, same shape as server `_POOL_LOCK` usage):
  1. `self._root = self._client.create(CreateSandboxFromSnapshotParams(labels=..., auto_delete_interval=..., **({"network_block_all": True} if hermetic else {})), timeout=120)` — same kwargs logic as `SandboxPool._create_one`.
  2. Pay cold start: `self._root.process.exec("echo warm")` (swallow exceptions like pool.py does).
  3. Optional bootstrap: if `RETRIAL_FORK_BOOTSTRAP_CMD` env is set, exec it in root with `timeout=180` (repo/deps/hot caches; default empty — retrial seeds only need python3, which the snapshot image has).
  4. `fork = _retry("ckpt.fork", lambda: self._root._experimental_fork(name=f"retrial-ckpt-{uuid4().hex[:6]}"))`; `_retry("ckpt.pause", fork.pause)`; `self._ckpt = fork`.
- `_fork_clones(self, n) -> list`: the `fork_futures` pattern — spend-guard check; `_retry("clones.start", self._ckpt.start)`; loop n times `_retry("clones.fork", lambda: self._ckpt._experimental_fork(name=f"retrial-trial-{uuid4().hex[:6]}"))`, registering each in `_live` under `_lock`; `except`: delete every partial clone (best-effort, swallow), re-raise; `finally`: `try: self._ckpt.pause() except Exception: pass`.
- `warm(n)`: **first line: `if self._degraded: return self._fallback.warm(n)`** — degrade is STICKY; a pool that has degraded once must never re-pay a root-create + failed-fork round-trip (seconds, live, every growth call) re-discovering the same missing primitive. Then `_ensure_checkpoint()`; clones = `_fork_clones(n)`; extend `_available`; return count. On any exception → `self._degrade(exc)` then `return self._fallback.warm(n)`.
- `ensure_warm(target)` / `resize_to(target)`: same arithmetic as pool.py (deficit → `warm`, surplus → background `_destroy` threads), delegating wholesale to `_fallback` when `_degraded` — the same short-circuit-first pattern as `warm`; all five entry-point methods check `_degraded` before touching any fork-path machinery.
- `lease()`: pop `_available` under `_lock`; else `_fork_clones(1)[0]`; degrade → `_fallback.lease()`.
- `release(sb, reusable=False)`: identical semantics to pool.py — reusable appends to `_available` (a fork clone under process isolation is reused exactly like a snapshot sandbox: each trial is a fresh `python3` process); else background-thread `_destroy(sb)` (client.delete via client.get, pop from `_live`). If `_degraded`, delegate to `_fallback` (a sandbox leased pre-degrade is still tracked in `_live`, so route by `sb.id in self._live`).
- `destroy_all()`: **leaf-first, mandatory order** — (1) all trial forks in `_live` concurrently (thread-per-sb, join), (2) `_ckpt`, (3) `_root`, then `+ self._fallback.destroy_all()` if a fallback exists. Return total.
- `stats()`: `{"available": ..., "live": ...}` (merge fallback counts when degraded) — keeps `/health` and the UI SandboxTicker working unchanged. Add `"backend": "fork-degraded" if self._degraded else "fork"` as an extra key (server ignores unknown keys today; SEAM-3 may surface it).
- `_degrade(self, exc)`: set `_degraded = True` once; build `_fallback = SandboxPool(client=?, target=..., labels=..., hermetic=..., auto_delete_min=...)` if None (fresh client is fine); emit `pool_degraded` if `_bus`:
  `{"backend": "fork", "fallback": "snapshot", "reason": str(exc)[:200]}`. Never raises.

### 1.2 MODIFY `engine/retrial/pool.py` — add factory at bottom

```python
def make_pool(bus=None, **kwargs):
    """RETRIAL_POOL_BACKEND=fork|snapshot (default snapshot, the safe choice)."""
    backend = os.environ.get("RETRIAL_POOL_BACKEND", "snapshot").lower()
    if backend == "fork":
        from .forkpool import ForkSandboxPool  # lazy: avoids import cycle
        return ForkSandboxPool(bus=bus, **kwargs)
    return SandboxPool(**kwargs)
```
(`SandboxPool` itself is untouched — it is both the default and the fallback.)

### 1.3 MODIFY `engine/retrial/server.py`

- `_get_pool()`: `_POOL = make_pool(bus=BUS)`; `_get_hpool()`: `_HPOOL = make_pool(bus=BUS, hermetic=True)` (fork backend propagates `network_block_all` to the root create; if the platform rejects that combination it degrades automatically — no special casing).
- `health()` `config` dict: add `"pool_backend": os.environ.get("RETRIAL_POOL_BACKEND", "snapshot")`.
- Import `make_pool` instead of/alongside `SandboxPool`.

### 1.4 MODIFY `engine/retrial/cli.py`

- `_cmd_check`: `pool = make_pool()` (import from `.pool`). Nothing else changes.

### 1.5 MODIFY `engine/retrial/events.py`

- Append `"pool_degraded"` to `EVENT_TYPES` (docs-not-enforcement tuple; SEAM-2/3 add more).

### 1.6 MODIFY `engine/retrial/__init__.py`

- Export `ForkSandboxPool`, `make_pool`; update `__all__`.

### 1.7 CREATE `requirements-dev.txt` (repo root)

```
pytest==8.*
```
(`httpx` is already in requirements.txt, needed by FastAPI TestClient in SEAM-3.)

### 1.8 CREATE `tests/` with the ported mocking pattern

- `tests/conftest.py`: `sys.path.insert(0, str(ROOT / "engine"))` so `import retrial` works from repo root (mirror `REWIND/tests/test_engine_unit.py` lines 18–19). Shared fakes, hand-rolled in the rewind style (`FakeCheckpointSandbox` with a scripted `_fork_results` list of `("child", id) | ("raise",)`, recording `started/pause_calls/children`; `FakeChild` recording `delete/pause`; a `FakeClient` with `create/get/delete` returning fakes).
- `tests/test_forkpool.py` — port the hardening assertions from `REWIND/tests/test_engine_unit.py` (fixture stubs SDK symbols: `monkeypatch.setattr(forkpool_mod, "Daytona", lambda *a, **k: object())`, same for `DaytonaConfig`, `CreateSandboxFromSnapshotParams`; inject `client=FakeClient(...)`):
  1. `warm(2)` → root created once, checkpoint = fork-of-root then paused, 2 clones live, checkpoint `pause_calls` incremented after the clone batch (woken then re-frozen).
  2. fork raises mid-batch → partial clones deleted (no orphans in `_live`), checkpoint still re-paused exactly once, pool degraded, `warm` returned via fallback (`SandboxPool` with a fake client), and `pool_degraded` was emitted on a real `EventBus` (assert via `bus.history()`).
  3. `make_pool()` with env unset → `isinstance(SandboxPool)`; with `RETRIAL_POOL_BACKEND=fork` (monkeypatch.setenv) → `isinstance(ForkSandboxPool)`. Default is snapshot.
  4. `destroy_all()` order: trial forks deleted before checkpoint before root (record deletion order in fakes).
  5. `release(sb, reusable=True)` returns clone to `_available`; `stats()` keys `available`/`live` present.
  6. Spend guard: `RETRIAL_MAX_FORKS=1` → `warm(2)` degrades (no raise to caller).
  7. **Degrade stickiness**: after one induced degrade (fork raises), a second `warm()` call goes straight to `_fallback.warm` and does NOT re-invoke `_ensure_checkpoint`/`_fork_clones` — assert via call counters on the fakes (e.g. `FakeClient.create_calls` unchanged, checkpoint fork count unchanged between call 1 and call 2).

### 1.9 CREATE `tests/test_pool_contract.py` — backend contract parity

Parametrized over BOTH `SandboxPool(client=FakeClient(...))` and `ForkSandboxPool(client=FakeClient(...))` (and a third param: `ForkSandboxPool` pre-forced into `_degraded`), asserting the external contract `server.py`/`/health`/SandboxTicker actually rely on is identical across backends — this is what stops a stub `ForkSandboxPool` passing its own tests while drifting from the drop-in surface:
- `stats()` returns a dict containing exactly the keys `{"available", "live"}` with `int` values (extra keys like `"backend"` allowed, but those two always present and int-typed, including mid-degrade).
- `lease()` never returns `None` when the fake can provision.
- `release(None)` / releasing an unknown sandbox is a no-op (no raise).
- `warm(0)` returns an int; `ensure_warm`/`resize_to` return without raising; `destroy_all()` returns an int and is idempotent.

### SEAM-1 acceptance (no keys)

```bash
cd RETRIAL
.venv/bin/python -m py_compile engine/retrial/*.py
.venv/bin/python -m pytest tests/test_forkpool.py tests/test_pool_contract.py -q   # all pass
RETRIAL_POOL_BACKEND=snapshot .venv/bin/python -c "from retrial import make_pool, ForkSandboxPool"  # with PYTHONPATH=engine
```
Also: `server.py`/`cli.py` no longer construct `SandboxPool()` directly (grep `SandboxPool(` in those two files → only via factory/hermetic fallback). Note: grep-for-a-string in `events.py` is NOT acceptance evidence for event registration — `EVENT_TYPES` is non-enforcing documentation. The authoritative check is the emit-site scan test (`tests/test_events.py`, specified in 3.5, which SEAM-1's `pool_degraded` emit must also pass once it lands; until then, the `bus.history()` assertion in test 2 above is the binding check).

---

## SEAM-2 — Time-travel flake bisection (work package 2)

Goal: for `order_dependency`/`shared_state` flakes, checkpoint a multi-test suite at every test boundary (run test i in a root sandbox, fork+pause = checkpoint i+1), probe checkpoints by forking N clones and rerunning the suspect with the Wilson-CI oracle, and binary-search to the exact polluting test. CLI subcommand + server endpoint + typed events.

### 2.1 CREATE `engine/retrial/bisect.py`

Imports: `_retry` from `.forkpool`; `verify` (the oracle) and `wilson` from `.verifier`; base64/re exec pattern constants mirroring `trial.py`.

**Checkpoint indexing convention (write it in the docstring):** checkpoint `k` = suite state after the first `k` tests have run (`k=0` is pristine). If the flip lies between checkpoints `k` and `k+1`, the polluter is suite test index `k` (0-based).

```python
class FlakeBisector:
    def __init__(self, client=None, target=None, bus=None, labels=None,
                 max_trials=30, conc=8, threshold=DEFAULT_THRESHOLD,
                 min_trials=8, timeout=60, auto_delete_min=None):
```
Own root sandbox lifecycle (NOT via the pool — bisection needs direct fork control): same client construction and `CreateSandboxFromSnapshotParams` kwargs as `SandboxPool._create_one`, labels `{"retrial": "bisect"}`.

Internal pieces:

- `_exec_test(self, sandbox, code) -> dict`: exactly the `trial.py` single-round-trip pattern (b64 → `/tmp/seed.py` → `EXIT:$?` → parse `_EXIT_RE`), returning the same result dict shape. Null-safe on `r.result` (rewind lesson: `.result` may be None); every exec gets `timeout=`.
- `class _CheckpointProbePool` (private adapter): exposes `lease()`/`release(sb, reusable=False)` over ONE started checkpoint so `verifier.verify()` is reused **verbatim** as the per-checkpoint oracle (adaptive early-stop and all). **Concurrency rule (this is load-bearing, see the caveat in the facts section):** `verify()` calls `lease()` from up to `conc` worker threads simultaneously, but the ONLY proven usage of `_experimental_fork` is strictly sequential from a single thread on one handle — so `lease()` MUST hold a dedicated `self._fork_lock: threading.Lock` around the entire `_retry(ckpt._experimental_fork(name=f"retrial-probe-{...}"))` call. Worker threads queue on the lock and forks are issued one at a time; the trial EXECUTION afterwards still runs fully parallel, so the throughput cost is only the (small) serial fork latency, not trial serialization. Write this rationale as a comment on the lock — do not "optimize" it away. Each fork is tracked in a local `_live` dict under a separate state lock; `release()` = background-thread delete (probe forks are single-use: `verify` is called with `isolation="sandbox"` so it never marks them reusable). `destroy_all()` deletes leftovers. Duck-typing is sufficient — `verify` only calls `lease`/`release`. Residual honest risk to note in the module docstring: even serialized, "fork while started, from a checkpoint being probed" is mock-verified only; if the live SDK rejects it, `run()`'s error path reports honestly (bisection has no snapshot fallback — the capability IS the fork; state that limitation in the docstring and CLI help, don't hide it).
- `_probe(self, k) -> dict`: `ckpt = self._ckpts[k]`; `_retry("probe.start", ckpt.start)`; `try:` build `_CheckpointProbePool(ckpt)`; `res = verify(probe_pool, self._suspect_code, max_trials=self.max_trials, conc=self.conc, threshold=self.threshold, min_trials=self.min_trials, bus=None, isolation="sandbox", emit_trials=False)`; `finally:` `probe_pool.destroy_all()`; `try: ckpt.pause() except Exception: pass` (checkpoint ALWAYS re-frozen — the fork_futures invariant). Cache result in `self._probes[k]`; emit `checkpoint_probed`.
- `_disjoint(a_ci, b_ci)`: the coordinator criterion inverted — `a_ci[1] < b_ci[0] or b_ci[1] < a_ci[0]`.
- `run(self, suite, suspect_index=None, suite_name="") -> dict` where `suite = [(name, code), ...]` and the suspect defaults to the last entry (prefix = everything before it):
  1. Emit `bisect_started {"suite": suite_name, "n_tests": len(prefix), "suspect": suspect_name, "max_trials": ...}`.
  2. Create root, warm exec, checkpoint 0 (`fork+pause`), emit `checkpoint_created {"k": 0, "label": "pristine"}`.
  3. Forward pass: for each prefix test i — `_exec_test(root, code_i)` (its pass/fail is informational, log into the event), then checkpoint i+1 = `_retry(root._experimental_fork(...))` + `_retry(fork.pause())`, emit `checkpoint_created {"k": i+1, "label": name_i}`. (Root keeps running throughout — the rewind "parent never stops" property.)
  4. Probe endpoints: `base = _probe(0)`, `full = _probe(K)`. Sanity gate: if NOT `_disjoint(base["wilson_ci"], full["wilson_ci"])` → no order dependency detectable; emit `bisect_done {"polluter_test": None, "reason": "flake rate does not flip across the suite", "base": {...}, "full": {...}}`, teardown, return.
  5. Binary search: `lo, hi = 0, K` (invariant: lo behaves like base, hi like full). While `hi - lo > 1`: `mid = (lo+hi)//2`; `r = _probe(mid)`; flipped = `_disjoint(base_ci, r_ci)`; `hi = mid` if flipped else `lo = mid`; emit `bisect_narrowed {"lo": lo, "hi": hi, "k": mid, "flipped": flipped}`.
  6. **Confirmation pass (noise armor)**: the search assumes the true flake rate is a monotonic step function across checkpoints; a single noisy probe (min_trials is only 8 and `verify` early-stops) could flip the wrong way. So after convergence, re-probe BOTH `lo` and `hi` with the full budget — `verify(..., min_trials=self.max_trials, max_trials=self.max_trials)` (early-stop defeated), bypassing the `self._probes` cache. If the confirmed pair still satisfies `lo`≈base / `hi` flipped, proceed; if not, emit `bisect_done {"polluter_test": None, "reason": "confirmation pass contradicts search (noisy probe); rerun with higher --max-trials", "probes": [...]}` — an honest inconclusive beats a confidently wrong culprit. Document the monotonicity assumption + this mitigation in the module docstring AND the CLI `--help` epilog (2.4).
  7. Polluter = `prefix[lo]` (test between checkpoints lo and lo+1=hi). Emit `bisect_done {"polluter_test": name, "polluter_index": lo, "suspect": suspect_name, "checkpoints": K+1, "probes": [{k, flake_rate, wilson_ci, trials, verdict} ...], "base_flake_rate", "full_flake_rate", "confirmed": true}`.
  8. `finally:` `destroy_all()` — leaf-first: any probe leftovers, then every checkpoint, then root.
  Errors: wrap the whole body; on exception emit `bisect_done {"error": str(e)[:200]}` and return `{"error": ...}` (degrade-gracefully — CLI/server map it to a nonzero exit / terminal event, never a traceback to the user).
- `destroy_all(self)`: leaf-first teardown; idempotent.

Event payload shape for `checkpoint_probed`: `{"k": int, "flake_rate": float, "wilson_ci": [lo, hi], "trials": int, "verdict": str}` — mirrors `detect_done` naming so `FlakeMeter` can render it directly.

### 2.2 MODIFY `engine/retrial/events.py`

Append to `EVENT_TYPES`: `"bisect_started"`, `"checkpoint_created"`, `"checkpoint_probed"`, `"bisect_narrowed"`, `"bisect_done"`.

### 2.3 CREATE `seeds/suites/order_pollution/` — the demo suite

Six self-contained `sys.exit(0|1)` scripts (retrial seed style, NOT pytest), run in filename order; pollution is filesystem-based so it survives across processes and is captured by fs+RAM forks:

- `test_00_smoke.py`, `test_01_math.py`, `test_02_strings.py`, `test_04_parse.py` — benign, always `sys.exit(0)`.
- `test_03_cache_writer.py` — **the polluter**: writes a stale/truncated `/tmp/app_cache.json` and exits 0 (itself green — the classic silent polluter).
- `test_05_suspect.py` — **the suspect**: if `/tmp/app_cache.json` exists, fail with probability ~0.5 (`random.random() < 0.5 → sys.exit(1)`); otherwise always `sys.exit(0)`. Ground truth: polluter_index = 3.

### 2.4 MODIFY `engine/retrial/cli.py`

New subparser following the `_cmd_check`/`set_defaults(func=)` pattern:

```
bis = sub.add_parser("bisect", help="time-travel bisection: find the test that pollutes a flaky suite")
bis.add_argument("suite", help="directory of seed tests, run in filename order (last = suspect unless --suspect)")
bis.add_argument("--suspect", help="filename of the suspect test within the suite")
bis.add_argument("--json", action="store_true"); --max-trials; --conc; --threshold  # same shapes as check
bis.set_defaults(func=_cmd_bisect)
```
`_cmd_bisect(args)`: read `sorted(Path(args.suite).glob("test_*.py"))` into `[(name, code)]`; resolve suspect (default last, error 2 if `--suspect` not found); `b = FlakeBisector(max_trials=..., conc=..., threshold=...)`; `result = b.run(...)`; `--json` → dump; human output prints the probe table (k, flake%, CI, verdict) and `polluter: <name>  <- run before the suspect, this test poisons it`; return 1 on `result.get("error")`, 0 otherwise. Subparser `epilog` (shown by `--help`) states the two limitations honestly: (a) requires fork-capable Daytona — no snapshot fallback, the capability IS the fork, so without it the run reports an honest error; (b) assumes the flake rate is a monotonic step function across checkpoints — noisy probes are mitigated by a full-budget confirmation pass on the converged pair, and a contradicted confirmation reports inconclusive rather than guessing.

### 2.5 MODIFY `engine/retrial/server.py` — `POST /bisect`

Mirror `/tournament` exactly:

```python
class BisectRequest(BaseModel):
    suite_dir: str          # e.g. "seeds/suites/order_pollution"
    suspect: str | None = None
    max_trials: int | None = None
```
Handler `start_bisect(req)` (sync `def`, FastAPI threadpool):
1. Gate: `if os.environ.get("RETRIAL_POOL_BACKEND", "snapshot") != "fork": raise HTTPException(400, "bisection requires the fork backend (set RETRIAL_POOL_BACKEND=fork)")` — honest, no fake fallback.
2. Scope guard: resolve `suite_dir` against `_REPO_ROOT`, `.resolve()`, require `is_relative_to(_SEEDS_DIR)` (same block + rationale comment as `/tournament`); 404 if missing/empty of `test_*.py`.
3. **Create the shared acceptance helper** (this seam refactors, SEAM-3 extends): add to `server.py`
   ```python
   def _accept_run(test_name):
       """Single point of run acceptance. MUST be called with _run_lock held.
       Everything that must be wiped between runs is wiped HERE and only here —
       the ring-buffer stale-bleed bug recurred the moment a second endpoint
       hand-rolled its own reset block. Add future per-run state to this helper."""
       BUS.reset()
       _running.update(active=True, test_name=test_name, ...)
   ```
   and refactor `start_tournament`'s existing accept block to call it (behavior identical). `/bisect` then does: `with _run_lock:` reject 409 if `_running["active"]`; else `_accept_run(suite_dir.name)`. (SEAM-3 adds `_pending["promotion"] = None` inside `_accept_run` — so a promotion left pending by a finished tournament cannot bleed into a bisect run or vice versa.)
4. Background daemon thread: build `FlakeBisector(bus=BUS, max_trials=req.max_trials or MAX_TRIALS, conc=CONC, threshold=THRESHOLD)`, load suite files, `run(...)`; `except Exception as e: BUS.emit("bisect_done", {"error": str(e)[:200]})`; `finally:` clear `_running` under `_run_lock`.
5. Return `{"status": "started", "suite": ..., "n_tests": ..., "suspect": ...}`.

### 2.6 MODIFY `engine/retrial/__init__.py`

Export `FlakeBisector`.

### 2.7 CREATE `tests/test_bisect.py` (mocked, same fake pattern as SEAM-1)

Script the probes instead of the SDK where possible; where sandboxes are needed, reuse `FakeCheckpointSandbox`/`FakeChild`:
1. **Search correctness (pure)**: monkeypatch `FlakeBisector._probe` to return canned results — flake_rate 0.0 CI [0,0.05] for k ≤ 3, 0.5 CI [0.3,0.7] for k ≥ 4 (6-test suite, suspect last) → `run` reports `polluter_index == 3`, `polluter_test == "test_03_cache_writer.py"`; assert `bisect_started`/`bisect_narrowed`/`bisect_done` appear in `bus.history()` in order, payloads snake_case.
2. **No-flip sanity gate**: identical CIs at k=0 and k=K → `polluter_test is None`, honest `reason`, no `bisect_narrowed` emitted.
3. **Probe invariants**: with fakes — after `_probe(k)`, checkpoint `pause_calls` incremented (re-frozen even when a probe fork raises mid-verify), probe forks all deleted (no `_live` leftovers).
4. **Teardown order**: probes → checkpoints → root.
5. **Events registered**: every new type is in `events.EVENT_TYPES`.
6. **Server gate** (FastAPI `TestClient` from httpx/starlette): `POST /bisect` with backend unset → 400; `suite_dir="../.."` → 400 scope guard; monkeypatch backend env + `FlakeBisector.run` to a stub → 200 `{"status":"started"}` and 409 on immediate second POST. Assert both `/tournament` and `/bisect` route through `_accept_run` (e.g. monkeypatch-count it).
7. **Suite seeds compile**: `py_compile` every file in `seeds/suites/order_pollution/`.
8. **Probe-pool concurrency stress (the fatal-risk regression test)**: build `_CheckpointProbePool` over a `FakeCheckpointSandbox` whose `_experimental_fork` (i) increments an `in_flight` counter on entry, sleeps ~1ms, decrements on exit, and records `max_in_flight`; (ii) returns children with unique ids. Fire N=16 REAL `threading.Thread`s calling `lease()` simultaneously (barrier-start), join, then assert: `max_in_flight == 1` (the `_fork_lock` discipline actually serializes), 16 distinct sandbox ids, `_live` has exactly the 16 entries (no corruption/duplicates), and after `release()` on all + `destroy_all()`, `_live` is empty and every child was deleted exactly once. This proves the locking at the mocked layer since the real SDK cannot be exercised here.
9. **Confirmation pass**: canned probes where the search converges but the full-budget re-probe of `lo` contradicts base → `bisect_done` has `polluter_test is None` and the "confirmation pass" reason; and the happy path re-probes with `min_trials == max_trials` (assert via the stubbed `verify` call kwargs).

### SEAM-2 acceptance (no keys)

```bash
cd RETRIAL
.venv/bin/python -m py_compile engine/retrial/*.py seeds/suites/order_pollution/*.py
.venv/bin/python -m pytest tests/test_bisect.py tests/test_forkpool.py -q
PYTHONPATH=engine .venv/bin/python -m retrial.cli bisect --help   # exits 0, shows subcommand
```
Diff-review checks (grep alone is not evidence — `EVENT_TYPES` is non-enforcing; the emit-site scan test in 3.5 is the binding check): `/bisect` handler holds `_run_lock` and accepts via `_accept_run` (never a hand-rolled `BUS.reset()` block); `_CheckpointProbePool.lease` holds `_fork_lock` around `_experimental_fork`.

---

## SEAM-3 — UI merge, promote gate, tests/CI port, honest docs (work package 3)

### 3.1 Server: human-approval promote gate feeding PRSmith

MODIFY `engine/retrial/server.py`:

- Module state: `_pending = {"promotion": None}` guarded by `_run_lock` (reuse it; promotion is per-run). Env knob: `PROMOTE_GATE = os.environ.get("PROMOTE_GATE", "1") != "0"` (default ON; `0` restores today's auto-PR behavior for headless demos).
- In `run()` replace the auto-PR block:
  ```python
  if open_pr and result.get("verdict") in ("FIXED", "QUARANTINE"):
      if PROMOTE_GATE:
          with _run_lock:
              _pending["promotion"] = {"result": result, "test_name": path.name}
          BUS.emit("promotion_pending", {
              "test_name": path.name, "verdict": result["verdict"],
              "winner_id": (result.get("winner") or {}).get("id"),
              "flake_rate": ..., "confirm_flake_rate": ..., "braintrust_url": ...})  # from result, best-effort .get chains
      else:
          PRSmith(bus=BUS).open_pr(result, path.name)
  ```
  Note: `promotion_pending` is emitted after `tournament_done` — that ordering is fine because the reducer treats it like `pr_opened` (allowed post-terminal, see 3.3). The pending promotion intentionally SURVIVES `_running` clearing (approval happens after the run ends) but is cleared at the next run acceptance by adding `_pending["promotion"] = None` **inside `_accept_run()`** (the single shared helper created in 2.5) — NOT in any endpoint-local reset block. Because `/tournament` AND `/bisect` both accept via `_accept_run`, a promotion left unclicked from a finished tournament can never survive into a subsequent bisect run (or any future run type) and feed `/promote` stale result data. Same reasoning as the ring-buffer reset; regression-tested in 3.5.
- New endpoint:
  ```python
  class PromoteRequest(BaseModel):
      approve: bool = True
  @app.post("/promote")
  def promote(req: PromoteRequest):
  ```
  Under `_run_lock` pop `_pending["promotion"]`; 404 if none. If `req.approve`: in a background daemon thread call `PRSmith(bus=BUS).open_pr(result, test_name)` (emits `pr_opened` — the existing terminal event; WinnerCard/QuarantineCard `prUrl` wiring untouched) and emit `promotion_closed {"approved": true, "test_name": ...}` before the PR call so the modal can dismiss immediately. If reject: emit `promotion_closed {"approved": false, "test_name": ...}` only. Return `{"status": "approved"|"rejected"}`.
  Honest-state rule (port of rewind `store.ts` approvePromote): the UI must not claim "shipped" until `pr_opened` actually arrives; `promotion_closed(approved)` only means "handed to PRSmith".
- `EVENT_TYPES` (events.py): append `"promotion_pending"`, `"promotion_closed"`, AND the missing `"hermetic_diagnosis"` — the tuple is stale today (coordinator.py emits it but the tuple never listed it, not just the TS union). The emit-site scan test in 3.5 then keeps the tuple honest forever.

What we deliberately do NOT port from rewind's promote: `engine.promote`'s git-diff/apply-in-root-sandbox — retrial's winner is a patched seed file, not a git workspace; PRSmith already writes `seeds/fixed/<name>` with `patched_code` via `gh api`. The gate interposes a human; the shipping mechanism stays PRSmith. Docs must describe it exactly that way.

### 3.2 UI types — `ui/src/types.ts`

Add event interfaces (snake_case fields, mirror engine payloads exactly) and extend the union:
- `HermeticDiagnosis { type: 'hermetic_diagnosis'; verdict: string; networked_rate: number; hermetic_rate: number; networked_ci: WilsonCI; hermetic_ci: WilsonCI }` — **fixes the existing drift**. These field names are read directly from what `coordinator.py::_hermetic_detect` actually emits (`verdict/networked_rate/hermetic_rate/networked_ci/hermetic_ci`); mirror the emit call exactly and keep the fields REQUIRED (not optional) so `tsc` catches any future payload drift instead of silently accepting it.
- `PoolDegraded { type: 'pool_degraded'; backend: string; fallback: string; reason: string }`
- `BisectStarted { type: 'bisect_started'; suite: string; n_tests: number; suspect: string }`
- `CheckpointCreated { type: 'checkpoint_created'; k: number; label: string }`
- `CheckpointProbed { type: 'checkpoint_probed'; k: number; flake_rate: number; wilson_ci: WilsonCI; trials: number; verdict?: string }`
- `BisectNarrowed { type: 'bisect_narrowed'; lo: number; hi: number; k: number; flipped: boolean }`
- `BisectDone { type: 'bisect_done'; polluter_test?: string | null; polluter_index?: number; reason?: string; error?: string; probes?: ... }`
- `PromotionPending { type: 'promotion_pending'; test_name: string; verdict: string; winner_id?: string; flake_rate?: number; confirm_flake_rate?: number; braintrust_url?: string }`
- `PromotionClosed { type: 'promotion_closed'; approved: boolean; test_name?: string }`

Derived state additions to `BoardState`:
- `bisect: BisectState | null` where `BisectState = { suite: string; suspect: string; nTests: number; checkpoints: { k: number; label: string; probe: { flakeRate: number; wilsonCi: WilsonCI; trials: number } | null; inWindow: boolean }[]; window: [number, number] | null; polluter: string | null; done: boolean; error: string | null }`
- `promotion: { testName: string; verdict: string; winnerId: string | null; braintrustUrl: string | null; open: boolean } | null`
- `poolDegraded: { reason: string } | null`
- New `Phase` member `'bisect'`.

### 3.3 Reducer — `ui/src/reducer.ts`

- `bisect_started` → `resetPerRun` (preserves genome, like `diagnosing`/`run_started`) + phase `'bisect'`, init `bisect` state.
- `checkpoint_created` / `checkpoint_probed` / `bisect_narrowed` (updates `window`, marks `inWindow`) / `bisect_done` (sets `polluter`/`error`, `done: true`) — all upsert-by-`k`, out-of-order safe like `upsertCell`.
- `promotion_pending` → set `promotion` (must be allowed in ALL terminal phases: add to the `baseline_verdict` late-event allowlist alongside `pr_opened`; likewise `promotion_closed`, `pool_degraded`, `hermetic_diagnosis`, and the bisect types where sensible — audit that allowlist while in there).
- `promotion_closed` → `promotion.open = false` (keep record for the card's "awaiting PR…" state until `pr_opened` sets `prUrl`).
- `pool_degraded` → set `poolDegraded` (rendered as an honest badge, not an error).
- `hermetic_diagnosis` → store on a new `hermetic` field or fold into detect state (small, but no longer silently dropped).

### 3.4 New components (adapt rewind's visual language — read `REWIND/web/components/TimelineRail.tsx` and `PromoteCard.tsx` for the grammar; re-implement in retrial's plain-React + styles.css idiom, NO framer-motion/zustand/new deps)

- CREATE `ui/src/components/TreeTimeline.tsx`: the tournament rendered as a tree on a vertical timeline rail (absolute 1px gradient spine + status dots with pulse, per TimelineRail). Root card = flaky test + detect `FlakeMeter` + verdict pulse (TESTS RED/GREEN). Branch rows = one per `Hypothesis` (cause_class chip, model chip via `models.ts` helpers, status badge RACING/KILLED/WINNER — strikethrough terminated, rewind's `BranchRow` treatment), each embedding a small `TrialGrid size="sm"` as its leaves. When `state.bisect` is set, the same rail renders checkpoint rows instead: label, probe `FlakeMeter`, `inWindow` highlight, polluter row flagged `POLLUTER`. Pure function of `BoardState` — no fetching.
- CREATE `ui/src/components/PromoteGate.tsx`: full-screen overlay modal shown when `state.promotion?.open` (CSS transition, not framer-motion). Content per PromoteCard in spirit: verdict, winner id/model, evidence bars (orig vs confirm flake rate via `FlakeMeter`), Braintrust receipt link when present, copy "the AI recommends — you decide", buttons REJECT and APPROVE → `fetch(POST http://localhost:8000/promote, {approve})` (same URL pattern/fallback as `TournamentBoard.startRun`). After approve, show "opening PR…" until `pr_opened` fills `prUrl` (honest-state rule). In replay mode, buttons disabled with a "live only" hint.
- MODIFY `ui/src/components/TournamentBoard.tsx`: add a Grid | Tree view toggle in TopBar (default Grid — the existing star stays default); mount `TreeTimeline` as the alternate main view and whenever phase === 'bisect'; render `<PromoteGate/>` at root level; SandboxTicker: if a `pool_degraded` was seen, show a small "snapshot fallback" tag.
- MODIFY `ui/src/styles.css`: rail/spine/dot/badge/modal classes (port the visual vocabulary: spine gradient, tabular-nums scores, KILL badge, winner glow).
- MODIFY `ui/src/mockRun.ts` AND `ui/src/useEventStream.ts` — **the default replay is sacred**: with no query params, judges see the `realRun.json`-driven replay, a real captured recording and the safest demo asset. Do NOT splice synthetic frames into it (hand-authoring `_t` values into `buildSchedule`'s delta math risks breaking the primary judge path). Instead, demo the new features ONLY behind explicit opt-in params, the exact pattern `?mock=quarantine` already proves safe:
  - `useEventStream.ts`: extend `MockOutcome` from the binary `'winner' | 'quarantine'` to the union `'winner' | 'quarantine' | 'promote' | 'bisect'`, and replace `readParams()`'s binary ternary with a lookup against that array (unknown values fall back to `'winner'`).
  - `mockRun.ts`: add a `promote` script = the existing winner script + scripted `promotion_pending` after `tournament_done` (and, on scripted approval unavailability in replay, the gate renders with buttons disabled per PromoteGate above); add a `bisect` script exercising `bisect_started → checkpoint_created×K → checkpoint_probed → bisect_narrowed×m → bisect_done`.
  - `?mock` unset ⇒ byte-identical behavior to today (assert by not touching the default branch at all).

### 3.5 Tests port (complete the suite started in SEAMs 1–2)

CREATE at `RETRIAL/tests/` (pattern source: `REWIND/tests/test_engine_unit.py` — module-symbol monkeypatching, hand-rolled fakes, zero network):
- `tests/test_events.py`: EventBus — subscribe replays backlog; `reset()` clears buffer but seq stays monotonic; emit shape `{seq,type,ts,payload}`; subscriber exceptions swallowed. PLUS the **emit-site registry scan** (this is the enforcement `EVENT_TYPES` lacks): walk every `engine/retrial/*.py` with `ast`, collect the first-arg string literal of every `*.emit("...")`/`bus.emit("...")` call, and assert each collected name is a member of `events.EVENT_TYPES` — a typo'd event type at any emit site (which grep-the-tuple would never catch, and which today's already-stale tuple proves happens: add the missing `hermetic_diagnosis` to `EVENT_TYPES` while here) fails the suite instead of silently breaking the UI.
- `tests/test_verifier.py`: `wilson()` known values ((0,0)→(0,0,1); 5/10 CI brackets 0.5); `_verdict` table (ERROR/ALWAYS_FAILING/STABLE/FLAKY/INCONCLUSIVE); `verify()` with a `FakePool` whose scripted trials early-stop below/above threshold; infra errors excluded from `trials` but counted in `errors`.
- `tests/test_pool.py`: `SandboxPool` with injected `FakeClient` — lease pops warm else creates; `release(reusable=False)` destroys (join the daemon thread or poll `_live`); `resize_to` trims/grows; `stats()` shape.
- `tests/test_guards.py`: pure — `neutering_check` accepts a legit fix, rejects `sys.exit(0)` neutering and assert-deletion, `_mutate_for_canary` flips the final compare; guard crash (malformed AST input) returns ok=True-shaped non-blocking result per its contract.
- `tests/test_diagnosis_parse.py`: `_parse_hypothesis` — valid JSON/patch → ok; garbage → `no_valid_patch`; never substitutes original code.
- `tests/test_server_endpoints.py` (TestClient, monkeypatch `_get_pool`→FakePool factory and `PRSmith.open_pr`→stub): `/tournament` seed scope guard (400 on `../../.env`), 409 double-run, `/promote` 404-when-nothing-pending → pending→approve emits `promotion_closed` then stubbed `pr_opened`; reject leaves no PR call. `/health` shape includes `pool_backend`. PLUS the **stale-promotion regression test** (guards the memory-file bug class): drive a tournament to a pending-promotion state (seed `_pending["promotion"]` via the stubbed run path), then `POST /bisect` (backend env monkeypatched to fork, `FlakeBisector.run` stubbed), then assert `_pending["promotion"] is None` and a subsequent `POST /promote` returns 404 — proving `_accept_run` wipes it for every run type, not just `/tournament`.
- Keep `tests/test_forkpool.py` + `tests/test_pool_contract.py` (SEAM-1) and `tests/test_bisect.py` (SEAM-2).

### 3.6 CI — CREATE `.github/workflows/ci.yml`

Adapt `REWIND/.github/workflows/ci.yml` (two jobs, on push/PR). Changes from the rewind original: python-version "3.12" kept (matches pinned deps; 3.14 is only the local env), install adds `-r requirements-dev.txt`, web job → `ui/` with `npm install --no-audit --no-fund` (keep the npm/cli#4828 comment — retrial's lockfile is also macOS-generated) then `npm run build` (which is `tsc -b && vite build`), node 22, `cache-dependency-path: ui/package-lock.json`. **Keep `.github/workflows/retrial.yml` untouched** (manual live-Daytona dispatch job — it is not CI).

### 3.7 Product naming & README (honesty is a product requirement)

MODIFY `README.md`, `docs/SPONSORS.md`, `docs/ARCHITECTURE.md` (add a short "Fork engine & time travel" section), `.env.example` (if present at repo root or engine/):

- **Name/tagline**: "**Retrial** — a flaky-test tournament, **powered by the Rewind engine**: Daytona fork-checkpoints give every trial a byte-identical starting universe, and time-travel bisection replays the suite from any checkpoint to pinpoint the test that poisons another." Rewind is the engine inside Retrial, not a separate product; say exactly that once.
- **Remove vaporware**: delete the README Step-4 claim "reviewed by CodeRabbit, narrated by an ElevenLabs flake autopsy" and every CodeRabbit/ElevenLabs mention in docs/pitch that implies a code path (none exists — verified). Remove `ELEVENLABS_API_KEY` from `.env.example`. Do NOT import rewind's fabricated CopilotKit or roadmap-only WorkOS claims; the promote gate is described as "a React modal + FastAPI `/promote` endpoint — a human approves before PRSmith opens the PR via `gh api`".
- **Truthful integration list**: Daytona (sandbox pool + experimental fork/pause checkpoints — fork path exercised against a mocked SDK in CI; live timings for the snapshot pool are the ones already measured in `docs/DAYTONA-COOKBOOK.md`; fork-primitive timings cite `REWIND` spike numbers as *Rewind's measured results*, clearly attributed, not re-verified here), Fireworks (4-model diagnosis, real code path, requires `FIREWORKS_API_KEY`), Braintrust (evidence ledger + tracing, optional, silently degrades), GitHub `gh` (PRSmith). Nothing else.
- **New config documented**: `RETRIAL_POOL_BACKEND` (snapshot default; fork = Rewind engine, auto-falls back to snapshot and emits `pool_degraded`), `RETRIAL_MAX_FORKS`, `RETRIAL_FORK_BOOTSTRAP_CMD`, `PROMOTE_GATE`. New commands: `python -m retrial.cli bisect seeds/suites/order_pollution`, `POST /bisect`, `POST /promote`.
- **Claim discipline sentence** (include verbatim in README): "Everything above is exercised by the mocked-SDK test suite in `tests/` (`pytest`, no credentials); anything requiring live Daytona/Fireworks/Braintrust keys is labeled as such."

### SEAM-3 acceptance (no keys)

```bash
cd RETRIAL
.venv/bin/python -m pytest tests/ -q                      # whole suite green
cd ui && npm install --no-audit --no-fund && npm run build # tsc -b && vite build, zero errors
cd ..
grep -rin "coderabbit\|elevenlabs\|copilotkit" README.md docs/ .env.example 2>/dev/null   # ZERO hits (pitch/ may keep historical decks only if clearly archived; prefer cleaning)
grep -n "promotion_pending\|promotion_closed" engine/retrial/events.py ui/src/types.ts ui/src/reducer.ts  # all three hit (smoke only — the binding check is the emit-site scan in tests/test_events.py)
grep -n "hermetic_diagnosis" ui/src/types.ts engine/retrial/events.py   # drift fixed in BOTH places
```
Manual smoke (still no keys): `PYTHONPATH=engine .venv/bin/python -c "import retrial.server"` imports clean; `ui` dev replay (`npm run dev`): default URL (no params) is byte-identical to today's `realRun.json` replay; `?mock=promote` shows the promote gate after the winner beat; `?mock=bisect` shows the checkpoint rail.

---

## Sequencing & handoff notes

1. SEAM-1 lands `tests/` + `requirements-dev.txt` + fakes in `conftest.py`; SEAM-2 and SEAM-3 extend them — do not fork parallel fake hierarchies.
2. SEAM-2 depends on SEAM-1's `_retry` and fake vocabulary. SEAM-3 depends on both (event names must already be final — they are specified verbatim above; do not rename casually, the TS union mirrors them).
3. Every new engine event name appears in exactly three places when done: `events.EVENT_TYPES`, `ui/src/types.ts`, `ui/src/reducer.ts`. Grep before declaring a seam finished.
4. The orchestrator commits; implementers only edit files under `RETRIAL`.

---

## Devil's-advocate review — resolution log (rev 2)

All nine flagged flaws were verified against the plan/code and accepted; none were rejected as wrong. Where each fix landed:

1. (fatal) Concurrent `_experimental_fork` from `verify()` worker threads on one checkpoint handle was an unproven pattern with no degrade path → `_CheckpointProbePool.lease()` now serializes the fork call behind a dedicated `_fork_lock` (trial execution stays parallel; only fork issuance is serial), the risk is called out in the load-bearing facts + module docstring + CLI help, and 2.7 test 8 stress-tests the locking with 16 real threads against the fake.
2. (major) `_pending["promotion"]` stale bleed across `/bisect`'s independent reset block (the memory-file bug class, third occurrence waiting to happen) → run acceptance centralized in one `_accept_run()` helper (created 2.5, extended 3.1) used by ALL run-starting endpoints; regression test in `test_server_endpoints.py`.
3. (major) Splicing synthetic frames into the default `realRun.json` replay endangered the primary judge demo, and `useEventStream.ts` was missing from the edit list → default replay declared untouchable; promote/bisect demos live only behind `?mock=promote` / `?mock=bisect`; `useEventStream.ts` added to 3.4 with `MockOutcome` widened from the binary ternary to a proper union.
4. (major) `warm()` lacked the degraded short-circuit and would re-pay root-create + failed-fork on every growth call after degrade → `warm()` now checks `_degraded` first like the other methods; stickiness test 1.8-7 asserts no re-invocation via call counters.
5. (minor) `HermeticDiagnosis` placeholder didn't match the real payload → replaced with the actual shape (`verdict/networked_rate/hermetic_rate/networked_ci/hermetic_ci`), fields required so tsc catches drift.
6. (minor) No cross-backend contract test → new `tests/test_pool_contract.py` (1.9) parametrized over both pools plus a degraded fork pool.
7. (minor) Bisection assumed noiseless monotonicity → full-budget confirmation pass on the converged `lo`/`hi` pair (run step 6) with honest-inconclusive on contradiction; limitation documented in docstring + `--help`; tested (2.7 test 9).
8. (minor) `EVENT_TYPES` is non-enforcing and already stale (`hermetic_diagnosis` missing) → ast-based emit-site scan test added to `tests/test_events.py`; `hermetic_diagnosis` added to the tuple (3.1); grep-based acceptance lines demoted to smoke checks throughout.
9. (minor) Facts section implied a `getattr` probe that no method spec builds → reworded: the generic per-method exception net (which catches `AttributeError`) is the mechanism; no dedicated probe exists or should be built.
