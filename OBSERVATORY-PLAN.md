# OBSERVATORY-PLAN: the Sandbox Observatory (Phase 2)

Product goal: **visibility is the headline** — judges must be able to see inside the
Daytona swarm: every sandbox Retrial ever touches, its fork lineage, what it is
executing right now, and controls to reap it. Two sequential work packages:

- **WP-BACKEND** — `SandboxRegistry` + typed events + `/sandboxes*` endpoints +
  lifecycle/reap + CLI + tests.
- **WP-FRONTEND** — `SandboxObservatory` panel + lineage tree + detail drawer +
  destroy controls + replay-safe demo feed.

`RETRIAL` = this repo root. All conventions from `MERGE-PLAN.md` apply verbatim:
retrial style (docstring-why, `threading.Lock`, degrade-gracefully), every new
event name registered in **3 places** (`engine/retrial/events.py::EVENT_TYPES`,
`ui/src/types.ts` union, `ui/src/reducer.ts`) and enforced by the existing
ast emit-site scan in `tests/test_events.py`, run acceptance ONLY via
`server._accept_run()` under `_run_lock`, no live SDK calls anywhere in
verification, never touch `.git`, default replay stays byte-for-byte.

Two non-negotiable design rules for this phase:

1. **Observability must never break a run.** Every registry hook body is wrapped
   so failures are swallowed (a `_safe` decorator in `registry.py`); the pools,
   bisector, and trial runner behave identically with a broken/absent registry.
   This is tested by injecting a registry whose methods raise.
2. **The stale-bleed lesson, applied to the registry** (see the dedicated
   section at the bottom): the registry is NEVER reset at run acceptance —
   live sandboxes and `total_ever`/`destroyed` counters span runs because the
   pool itself spans runs. What IS per-run is the bus ring buffer; so
   `_accept_run()` (the single helper, under `_run_lock`) emits a fresh
   `registry_snapshot` immediately after `BUS.reset()`, re-seeding the new
   run's replay buffer with the current sandbox world. Never from a
   background thread.

---

## WP-BACKEND

### B1. CREATE `engine/retrial/registry.py` — the SandboxRegistry

Module docstring: the why (one thread-safe ledger of every sandbox the system
ever touches, feeding the Observatory UI), the two rules above, and the lock
discipline (below).

```python
def _safe(fn):
    """Observability must never break a run: swallow EVERYTHING."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None
    return wrapper
```

```python
class SandboxRegistry:
    def __init__(self, bus=None, exec_history=None, destroyed_retain=None):
        # exec_history default: int(os.environ.get("RETRIAL_EXEC_HISTORY", "20"))
        # destroyed_retain default: int(os.environ.get("RETRIAL_DESTROYED_RETAIN", "50"))
        self._lock = threading.RLock()
        self._records = {}    # id -> record dict (destroyed records retained only
                              #  up to destroyed_retain — see retention window below)
        self._handles = {}    # id -> live sandbox object (dropped on destroy)
        self._destroyers = {} # id -> destroy_fn(sid) supplied by the owner
        self._children = {}   # parent_id -> [child ids]  (lineage index)
        self._destroyed_order = collections.deque()  # destroyed ids, oldest first
        self._bus = bus
        self._total_ever = 0   # exact forever — NEVER derived from len(_records)
        self._destroyed = 0    # exact forever — independent of the retention window
```

**Retention window (bounded snapshot growth):** destroyed records are kept only
for the `destroyed_retain` most recently destroyed sandboxes (default 50, env
`RETRIAL_DESTROYED_RETAIN`). `mark_destroyed` appends the id to
`_destroyed_order` and then prunes: while `len(_destroyed_order) >
destroyed_retain`, pop the oldest destroyed id and delete it from `_records`,
`_children` (both its own entry and its slot in its parent's child list), and
`_handles`/`_destroyers` (already dropped). The aggregate counters
`_total_ever`/`_destroyed` are plain ints bumped at register/destroy time and
must NEVER be recomputed from retained records — counts stay exact for the
whole process lifetime while the record map stays bounded. A pruned id returns
`None` from `record()` (server → 404) — acceptable for long-dead sandboxes.
Without this, `registry_snapshot` (re-broadcast at EVERY run acceptance) and
the judge-facing Grid grow without bound over a multi-hour demo day.

Record shape (plain JSON-serializable dict, snake_case — it goes over the wire
verbatim):

```python
{"id", "role",          # "root"|"checkpoint"|"trial-clone"|"snapshot-pool"|"bisect-probe"
 "backend",             # "fork"|"snapshot"
 "state",               # "creating"|"warm"|"paused"|"running-cmd"|"destroyed"|"degraded"
 "parent_id",           # fork lineage; None for roots / snapshot-pool sandboxes
 "created_ts", "updated_ts",   # time.monotonic() based, rounded like EventBus.ts
 "labels",              # the pool's labels dict (the flake-class isolation tag lives
                        #  here + "isolation" below)
 "isolation",           # "process"|"sandbox"|None — set by the trial exec hook
 "current_cmd",         # cmd string (truncated 160) while running-cmd, else None
 "exec_count",          # total execs ever on this sandbox
 "recent_execs",        # list(deque maxlen=exec_history) of
                        #  {"cmd","exit_code","output_tail","duration_s","ts"}
 "preview_url"}         # None until lazily resolved (see preview())
```

Methods — **every public method below is decorated `@_safe`** except the pure
readers (`snapshot`, `record`, `counts`, `lineage`), which still hold the lock
but may not raise by construction:

- `attach_bus(bus)` — server wires the process BUS once at import.
- `register(sb, role, backend, parent_id=None, labels=None, isolation=None, destroy_fn=None, state="creating")`
  → creates the record, bumps `_total_ever`, indexes lineage, stores handle +
  destroyer, emits `sandbox_registered` (payload = the record minus
  `recent_execs`).
- `set_state(sid, state, current_cmd=None)` → updates state + `updated_ts`,
  emits `sandbox_state {"id", "state", "current_cmd"}`. No-op on unknown id.
- `exec_started(sid, cmd, isolation=None)` → state `running-cmd`,
  `current_cmd=cmd[:160]`, sets `isolation` if given. Emits `sandbox_state`.
- `exec_finished(sid, cmd, exit_code, output_tail, duration_s)` → appends to
  the ring buffer, `exec_count += 1`, state back to `warm`, `current_cmd=None`,
  emits **one** `sandbox_exec {"id","cmd"(160),"exit_code","duration_s",
  "output_tail"(200),"exec_count"}` (the exec event IS the running-cmd→warm
  transition; no second `sandbox_state` per exec — event-volume discipline,
  see B6).
- `mark_destroyed(sid)` → state `destroyed`, `_destroyed += 1`, drops handle +
  destroyer, appends to `_destroyed_order` and prunes past the retention
  window (see above), emits `sandbox_destroyed {"id","role"}`. Idempotent
  (second call is a no-op — no double count, no double retention entry).
- `mark_degraded(sid)` → state `degraded` (applied to a fork root/ckpt when the
  pool degrades), emits `sandbox_state`.
- `preview(sid, port=None)` → lazy: try
  `handle.get_preview_link(port or int(os.environ.get("RETRIAL_PREVIEW_PORT","8080")))`,
  store `.url` (or the str) into `preview_url`. **Caching rule — positive
  results cache forever; negative results NEVER cache in a non-terminal
  state.** Roots and checkpoints spend almost their whole life `paused` (the
  wake→fork→re-pause cycle), and a paused sandbox will typically fail to
  produce a preview link — so a failure while state is
  `creating|warm|paused|running-cmd` returns `None` WITHOUT caching (the next
  detail-drawer open retries, and may catch the sandbox awake). Only once
  state is `destroyed` is `None` cached permanently. Otherwise the first
  drawer-open on a paused checkpoint would poison the cache and the preview
  button — the headline demo feature — would never appear for the fork-tree
  spine. Never called on hot paths — only from `GET /sandboxes/{id}`.
- `snapshot()` → `{"sandboxes": [records minus recent_execs], "counts": {"live", "total_ever", "destroyed"}, "lineage": {parent_id: [child_ids]}}`
  (`live` = records whose state not in ("destroyed",)). Thanks to the
  retention window, `sandboxes` is bounded at live + ≤`destroyed_retain`
  destroyed records; `counts` come from the exact int counters, never from
  `len(sandboxes)`.
- `record(sid)` → the FULL record incl. `recent_execs` (list-ified), or None.
- `counts()` → the counts dict above.
- `emit_snapshot()` → `bus.emit("registry_snapshot", self.snapshot())`. `@_safe`.
- `destroy(sid)` → route to the owner's `destroy_fn(sid)` if present, else
  best-effort delete via the stored handle's client-less `handle.delete()` if it
  exists; returns True if a destruction was initiated. **Must NOT hold
  `self._lock` while calling `destroy_fn`** (lock discipline below).
- `reap_orphans()` → leaf-first pass over all still-live records that have a
  handle: sort by lineage depth descending (children before parents), destroy
  each. Returns count. Used by the global teardown as the safety net for
  records whose owning pool is already gone.

Module tail:

```python
REGISTRY = SandboxRegistry()   # the process-wide default; tests build their own
```

**Lock discipline (write it as a comment on `_lock`):** registry methods never
call pool/bisector code while holding `_lock` (only `destroy()` calls out, and
it snapshots the destroyer then releases the lock first). Pool code calls
registry hooks only OUTSIDE its own `_lock` scopes. Lock order is therefore
strictly one-directional per call (pool-lock → release → registry-lock), so no
deadlock is possible. `RLock` because `exec_finished` may be invoked from a
`@_safe`-wrapped path that already holds it in a future refactor — cheap
insurance, zero cost.

### B2. MODIFY `engine/retrial/events.py`

Append to `EVENT_TYPES` (one new group comment `# sandbox observatory`):
`"sandbox_registered"`, `"sandbox_state"`, `"sandbox_exec"`,
`"sandbox_destroyed"`, `"registry_snapshot"`.
(The ast emit-site scan in `tests/test_events.py` is the binding enforcement —
`registry.py` lives in `engine/retrial/`, so its emit sites are scanned
automatically. Grep of the tuple stays smoke-only.)

### B3. Registry hook points — exact locations

All pools/bisector gain a ctor kwarg `registry=None` → `self._registry =
registry if registry is not None else REGISTRY` (tests inject a fresh one; the
server uses the default). Every hook call sits OUTSIDE the component's own lock
and is already `@_safe` on the registry side — no extra try/except at call
sites (one mechanism, one place).

**`pool.py` (SandboxPool):**
- `_create_one` — after `self._client.create(...)` and after registering in
  `_live` (outside `_lock`):
  `self._registry.register(sb, role="snapshot-pool", backend="snapshot", labels=self._labels, destroy_fn=self._evict, state="creating")`.
- `warm.mk` — after the `echo warm` cold-start exec:
  `self._registry.set_state(sb.id, "warm")`. (A leased-on-demand sandbox from
  `lease()→_create_one` goes warm on its first trial exec via the trial hook.)
- `_destroy` — in the `finally`, after popping `_live`:
  `self._registry.mark_destroyed(sb.id)`.
- NEW method `_evict(self, sid)` (the per-sandbox destroyer the registry
  routes `DELETE /sandboxes/{id}` through): under `_lock` remove the sandbox
  from `_available` (match by `.id`) and read the handle from `_live`; outside
  the lock call `self._destroy(handle_or_stub)`. Idempotent, returns None.
  Rationale comment: destruction must go through the owner so pool
  bookkeeping can never lease a destroyed sandbox.
- `make_pool(bus=None, registry=None, **kwargs)` — pass `registry` through to
  both backends.

**`forkpool.py` (ForkSandboxPool):**
- `_ensure_checkpoint` — after root create:
  `register(root, role="root", backend="fork", labels=self._labels, destroy_fn=None, state="creating")`;
  after the `echo warm`: `set_state(root.id, "warm")`; after
  `fork.pause()` succeeds:
  `register(fork, role="checkpoint", backend="fork", parent_id=root.id, labels=self._labels, state="paused")`.
  (Root/checkpoint have no `destroy_fn` — they die only via `destroy_all`,
  leaf-first; `DELETE` on them is leaf-guarded by the server, B4.)
- `_fork_clones` — after `_retry("clones.start", ...)`:
  `set_state(self._ckpt.id, "warm")`; per successful fork (after `_live`
  insert, outside `_lock`):
  `register(fork, role="trial-clone", backend="fork", parent_id=self._ckpt.id, labels=self._labels, destroy_fn=self._evict, state="warm")`;
  in the `finally` after the re-pause: `set_state(self._ckpt.id, "paused")`.
- `_destroy` — `mark_destroyed(sb.id)` in its `finally`, after the `_live` pop.
- `_degrade` — when `first` is True: `mark_degraded` on `self._ckpt.id` and
  `self._root.id` (when non-None) so the tree shows the dead fork spine
  honestly.
- `destroy_all` — **behavior change (fatal-race fix), not just a hook add**:
  the current implementation deletes `self._ckpt`/`self._root` holding only
  `self._lock`, while `_fork_clones` holds `_fork_lock` for the whole
  start→fork×N→pause batch — a concurrent forced reap can delete the
  checkpoint mid-fork of another thread's in-flight batch, and afterwards the
  still-running tournament thread's next `lease()`/`warm()` silently rebuilds
  a brand-new root+checkpoint, breaking the byte-identical-clone statistical
  invariant with no warning. Fix, in one place:
  1. `destroy_all` wraps its ENTIRE body in `with self._fork_lock:` — it
     therefore blocks until any in-flight fork batch finishes and no batch can
     start mid-teardown. (No deadlock: `_destroy` worker threads take only
     `self._lock`; `_fork_lock` is never taken inside `self._lock` anywhere.)
  2. Under that same `_fork_lock`, FIRST set `self._torn_down = True` (new
     sticky bool, same lock-free-read style as `_degraded`), then tear down
     clones → ckpt → root as today, then `mark_destroyed` each.
  3. `warm()`, `lease()` and `_ensure_checkpoint()` gain a leading
     `if self._torn_down: raise RuntimeError("fork pool torn down by "
     "destroy_all — refusing to rebuild a checkpoint mid-process")`. A
     tournament thread that survives a forced reap now fails HONESTLY: its
     remaining trials surface as infra errors (already excluded by the trial
     layer) instead of silently continuing against a different checkpoint.
     Docstring must say exactly why (statistical invariant).
- NEW `_evict(self, sid)` — same shape as SandboxPool's (pop `_available` by
  id + `_live`, then `_destroy`).
- **`pool.py` symmetry**: `SandboxPool.destroy_all` sets the same
  `_torn_down` sentinel (under `_lock`), and `warm`/`lease`/`_create_one`
  check it — a torn-down snapshot pool must not silently re-create sandboxes
  for a still-running run either.

**`bisect.py` (FlakeBisector + _CheckpointProbePool):**
- `FlakeBisector.__init__` gains `registry=None` → default `REGISTRY`; passes
  it into every `_CheckpointProbePool(ckpt, self._client, registry=self._registry)`.
- `_create_root` — after create: `register(root, role="root", backend="fork",
  labels=self._labels, state="creating")`; after `echo warm`:
  `set_state(root.id, "warm")`.
- `_checkpoint` — after `fork.pause()`:
  `register(fork, role="checkpoint", backend="fork", parent_id=self._root.id, labels=self._labels, state="paused")`.
- `_exec_test` — bracket the root exec:
  `exec_started(sandbox.id, cmd)` before `sandbox.process.exec`, and
  `exec_finished(sandbox.id, cmd, exit_code, log_tail, duration)` in both the
  parsed and the exception return paths (call just before each `return`).
- `_probe` — after `_retry("probe.start", ...)`: `set_state(ckpt.id, "warm")`;
  in the `finally` after `ckpt.pause()`: `set_state(ckpt.id, "paused")`.
- `_CheckpointProbePool.lease` — after the fork (outside `_fork_lock`):
  `register(fork, role="bisect-probe", backend="fork", parent_id=self._ckpt.id, state="warm", destroy_fn=None)`.
- `_CheckpointProbePool._destroy` — after the owned pop wins:
  `mark_destroyed(sb.id)`.
- `FlakeBisector.destroy_all` — `mark_destroyed` for each ckpt + root deleted.
- **`FlakeBisector` synchronization + cooperative cancel (fatal-race fix)**:
  `self._lock` is currently declared in `__init__` (bisect.py:157) and never
  acquired anywhere — `destroy_all()` reassigns `self._ckpts` while `_probe()`
  concurrently indexes `self._ckpts[k]` with zero synchronization. Two-part
  fix:
  1. **Make `_lock` real**: acquire it around every mutation/swap of
     `_ckpts`/`_root`/`_probe_pool` (`_create_root`, `_checkpoint`'s append,
     `destroy_all`'s swap-outs) and around the `self._ckpts[k]` read in
     `_probe`. Cheap, and removes the lying declared-but-unused lock.
  2. **Force never yanks an active bisector's resources** — add
     `self._cancelled = threading.Event()` and `def cancel(self):
     self._cancelled.set()`. `_run()` checks `self._cancelled.is_set()` at
     each loop boundary (before building each checkpoint, before each probe)
     and raises `RuntimeError("bisect cancelled by destroy_all(force)")`;
     `run()`'s existing `except` turns that into the `bisect_done` error
     payload and its existing `finally: self.destroy_all()` reaps leaf-first
     from the run thread itself — the ONLY thread that was using those
     resources. The server never calls `destroy_all()` on an active bisector
     (see B4): force = cooperative cancel, teardown by the owner.

**`trial.py` (`run_trial`) — the exec hook for pooled sandboxes** (module-level
`from .registry import REGISTRY`; `run_trial` has no object state, so the
process default is the right channel; the registry’s `@_safe` guarantees a
test-constructed pool with a private registry merely double-books into an
inert default — harmless, and the mocked suites assert via injected
registries on the pool paths):
- before `sb.process.exec`: `REGISTRY.exec_started(sb.id, cmd, isolation=isolation)`.
- before each `return` (success, no-marker, exception):
  `REGISTRY.exec_finished(sb.id, cmd, exit_code_or_None, log_tail, duration)`.

### B4. MODIFY `engine/retrial/server.py` — endpoints, lifecycle, snapshot-at-accept

- Imports: `from .registry import REGISTRY`; right after `BUS = EventBus()`:
  `REGISTRY.attach_bus(BUS)`.
  **Bump the bus buffer**: `BUS = EventBus(buffer_size=2000)` — sandbox_exec
  adds up to ~1 event per trial (~200/run) plus registrations; 500 could evict
  the run's own opening events. The reducer is upsert/out-of-order safe and
  `registry_snapshot` makes eviction recoverable, but 2000 keeps replay whole.
- `_accept_run(test_name)` — append AFTER the existing `BUS.reset()` +
  `_pending` wipe + `_running` set (still inside `_run_lock`, still the only
  place):
  ```python
  # Registry is NOT reset (total_ever/live span runs — the pool is shared);
  # instead re-seed the fresh buffer so a WS that connects now sees the
  # current sandbox world instead of nothing. See OBSERVATORY-PLAN.md,
  # "stale-bleed lesson" — and never emit this from a background thread.
  REGISTRY.emit_snapshot()
  ```
- Track the active bisector so reap can reach it: module state
  `_active = {"bisector": None}` (guarded by `_run_lock`). In `/bisect`'s
  `run()`: construct the `FlakeBisector`, then `with _run_lock: _active["bisector"] = bisector`;
  in its `finally`, clear it back to None under the lock.

- `GET /sandboxes`:
  ```python
  @app.get("/sandboxes")
  def sandboxes():
      snap = REGISTRY.snapshot()
      snap["est_resources"] = {
          "live_sandboxes": snap["counts"]["live"],
          "note": "count-based estimate; Daytona does not expose per-sandbox RAM here",
      }
      return snap
  ```
- `GET /sandboxes/{sid}`:
  ```python
  @app.get("/sandboxes/{sid}")
  def sandbox_detail(sid: str):
      rec = REGISTRY.record(sid)
      if rec is None: raise HTTPException(404, f"unknown sandbox: {sid}")
      if rec["state"] != "destroyed":
          rec["preview_url"] = REGISTRY.preview(sid)   # lazy, cached, None on failure
      return rec
  ```
- `DELETE /sandboxes/{sid}` — destroy ONE sandbox (allowed even mid-run: a
  killed trial sandbox surfaces as an infra error, which the trial layer
  already excludes and never re-leases — kill-a-sandbox-live is a legitimate
  resilience demo; document that in the handler docstring). **The docstring
  MUST also state the boundary of that argument**: it covers a single LEAF
  sandbox only. It does NOT extend to pool-scope reap — `destroy_all` deletes
  the shared provisioning source (checkpoint/root) that in-flight fork batches
  actively use, which is a different safety class entirely (see the
  force-reap contract below). Future maintainers must not reuse the
  single-sandbox justification for pool-scope operations.
  ```python
  @app.delete("/sandboxes/{sid}")
  def destroy_sandbox(sid: str):
      rec = REGISTRY.record(sid)
      if rec is None or rec["state"] == "destroyed":
          raise HTTPException(404, ...)
      snap = REGISTRY.snapshot()
      kids = [c for c in snap["lineage"].get(sid, [])
              if REGISTRY.record(c)["state"] != "destroyed"]
      if kids:
          raise HTTPException(409, "sandbox has live fork-children — destroy leaves first or use /sandboxes/destroy_all")
      if not REGISTRY.destroy(sid):
          raise HTTPException(502, "destroy could not be initiated")
      return {"status": "destroying", "id": sid}
  ```
- `POST /sandboxes/destroy_all` (`force: bool = False` query param) — **the
  force-reap contract** (this is the fix for the reachable
  destroy-mid-fork race; `_reap_everything` still runs OUTSIDE `_run_lock` to
  avoid starving `/status`, but every operation it performs is now
  individually safe against a live run — see the three mechanisms below):
  ```python
  with _run_lock:
      active = _running["active"]
      bisector = _active["bisector"]
      if active and not force:
          raise HTTPException(409, "a run is active — pass ?force=1 to cancel it and reap")
      _reaping["now"] = True   # closes the accept/reap TOCTOU window (below)
  try:
      cancelled = False
      if active and bisector is not None:
          bisector.cancel()  # cooperative: the run thread's own finally reaps
          cancelled = True   # leaf-first; we NEVER destroy_all an active bisector
      n = _reap_everything(bisector=None if cancelled else bisector,
                           skip_orphans=cancelled)
  finally:
      with _run_lock:
          _reaping["now"] = False
  return {"status": "destroyed", "count": n, "forced": bool(force),
          "bisector_cancelled": cancelled, **REGISTRY.counts()}
  ```
  **The accept/reap TOCTOU sentinel** (`_reaping = {"now": False}`, module
  state guarded by `_run_lock`): without it, a NEW run could be accepted in
  the gap between releasing `_run_lock` (after the 409 check) and
  `_reap_everything()` actually tearing pools down — that run would then be
  torn down without anyone having passed `force`, violating the 409 contract
  even though `_torn_down` makes the failure honest. Fix: `_accept_run()`
  gains a leading `if _reaping["now"]: raise RuntimeError("sandbox reap in "
  "progress — retry shortly")` (it already runs under `_run_lock`), which the
  endpoints surface as 409. The window is tiny but the check is one line —
  the 409 contract should hold by construction, not by timing luck.
  Why this is now safe with a tournament run in flight: (1)
  `ForkSandboxPool.destroy_all` holds `_fork_lock` for its whole body, so it
  serializes against any in-flight fork batch instead of yanking the
  checkpoint mid-fork; (2) the `_torn_down` sentinel makes the surviving run
  thread's next `lease()`/`warm()` fail honestly (infra-excluded trials)
  instead of silently rebuilding a new checkpoint and corrupting the
  byte-identical-clone invariant; (3) an active bisector is only ever
  cancelled, never reaped from this thread — its own `finally` tears down,
  and `skip_orphans=True` keeps `reap_orphans` from racing that in-progress
  owner teardown (`reap_orphans` is a safety net for DEAD owners only).
- NEW module function `_reap_everything(bisector=None, skip_orphans=False)` —
  idempotent global teardown, leaf-first by construction (each component is
  already leaf-first internally, and probes/clones die inside their owners
  before ckpts/roots):
  1. `bisector.destroy_all()` if given (probes → ckpts → root) — callers pass
     a bisector ONLY when it is not actively running,
  2. `_POOL.destroy_all()` / `_HPOOL.destroy_all()` when non-None
     (fork pool: clones → ckpt → root, under `_fork_lock` per B3), then set
     the module pool refs to `None` (under the same guard `_get_pool` uses)
     so the NEXT accepted run builds a fresh pool — the torn-down sentinel is
     per-instance and must never leak into a new run,
  3. `REGISTRY.reap_orphans()` unless `skip_orphans` (safety net,
     deepest-first, for records whose owning pool object is already gone).
  Returns the summed count. Every step wrapped best-effort. Docstring repeats
  the safety-class distinction: single-sandbox DELETE is self-healing by the
  trial layer; pool-scope reap is safe ONLY because of `_fork_lock` +
  `_torn_down` + cancel-not-destroy for active bisectors.
- Lifespan + atexit: replace the current shutdown loop body with
  `bis = _active["bisector"]; bis and bis.cancel(); _reap_everything(bis)`
  (process is exiting — cancel first so the loop stops cooperating, then reap;
  `destroy_all` is now internally locked so even this overlap is serialized),
  and the same lambda at module scope via `atexit.register`.
  Idempotency: `destroy_all` on both pools is already idempotent;
  `mark_destroyed` is a no-op the second time — calling `_reap_everything`
  twice destroys nothing extra (test B8-5).
- `health()` — no change needed (SandboxTicker keeps using `/health`); the
  Observatory reads `/sandboxes`.

### B5. MODIFY `engine/retrial/cli.py` — `sandboxes` and `reap`

The registry lives in the SERVER process; a CLI in another process cannot see
it, so both subcommands are thin HTTP clients of the running server (stdlib
`urllib.request` — no new deps), with an injectable fetcher for tests:

- `_http_json(method, url, timeout=10)` — returns parsed JSON; raises
  `URLError`-family which callers map to a friendly exit-2 message
  ("engine not reachable at {url} — is `uvicorn retrial.server:app` running?").
- `_cmd_sandboxes(args, fetch=_http_json)`: `GET {args.url}/sandboxes`;
  `--json` dumps raw; human output = a fixed-width table (stdlib formatting,
  retrial style — no `rich` dependency):
  `ID(12)  ROLE(13)  BACKEND(8)  STATE(11)  PARENT(12)  EXECS(5)  AGE(7)  CURRENT_CMD`
  followed by a totals line `live N · total-ever M · destroyed D`.
- `_cmd_reap(args, fetch=_http_json)`: `POST {args.url}/sandboxes/destroy_all`
  (+`?force=1` when `--force`); on 409 print the server's detail and exit 1;
  on success print `destroyed N sandboxes`.
- Subparsers follow the `set_defaults(func=)` pattern:
  `sb = sub.add_parser("sandboxes", help="observatory: list every sandbox the engine tracks")`
  with `--url` (default `http://localhost:8000`) and `--json`;
  `rp = sub.add_parser("reap", help="destroy every live sandbox (409-guarded while a run is active; --force cancels the run and reaps)")`
  with `--url`, `--force`, `--json`.

### B6. Event payloads — reference (mirrored EXACTLY by `ui/src/types.ts` in WP-FRONTEND)

| type | payload |
|---|---|
| `sandbox_registered` | `{id, role, backend, state, parent_id, created_ts, labels, isolation, exec_count, preview_url}` |
| `sandbox_state` | `{id, state, current_cmd}` (`current_cmd` null unless running-cmd) |
| `sandbox_exec` | `{id, cmd (≤160), exit_code (int\|null), duration_s, output_tail (≤200), exec_count}` |
| `sandbox_destroyed` | `{id, role}` |
| `registry_snapshot` | `{sandboxes: [record-minus-recent_execs], counts: {live, total_ever, destroyed}, lineage: {parent: [children]}}` |

Volume discipline: exactly ONE `sandbox_exec` per trial (no paired
state events), lifecycle `sandbox_state` only on real transitions
(creating→warm, paused↔warm, degraded), snapshot only at `_accept_run` and on
demand — worst case a 200-trial run adds ~250 events; buffer 2000 holds it.

### B7. MODIFY `engine/retrial/__init__.py`

Export `SandboxRegistry`, `REGISTRY`; update `__all__` and the docstring line
(`SandboxRegistry  thread-safe ledger of every sandbox — the Observatory feed`).

### B8. Tests (extend the existing suite/fakes — do NOT fork a parallel fake hierarchy)

`tests/conftest.py` additions: give `FakeChild`/`FakeRootSandbox`/
`FakeCheckpointSandbox` a `get_preview_link(port)` returning an object with a
`.url` (`f"https://preview.fake/{self.id}:{port}"`), and a
`RaisingRegistry` fake whose every public method raises (for the
never-break-a-run tests).

- CREATE `tests/test_registry.py`:
  1. register→exec_started→exec_finished→mark_destroyed walk: record fields,
     ring buffer bounded at `exec_history` (push history+5, assert len),
     `exec_count` keeps counting past the ring, counts
     `{live, total_ever, destroyed}` correct; `total_ever` NEVER decremented.
  2. `mark_destroyed` idempotent (second call: `destroyed` count unchanged).
  3. lineage: root→ckpt→2 clones gives `lineage[root]==[ckpt]`,
     `lineage[ckpt]` has both clones; `snapshot()` is JSON-serializable
     (`json.dumps` round-trip).
  4. events on a REAL `EventBus`: history shows `sandbox_registered`,
     `sandbox_state`, `sandbox_exec`, `sandbox_destroyed` in order, snake_case
     payloads matching B6.
  5. `@_safe` proof: `attach_bus` a bus whose `emit` raises → every hook still
     returns None without raising; `exec_finished` on unknown id is a no-op.
  6. `preview()` — happy path caches the fake URL (handle called once across
     two `preview()` calls); a handle whose `get_preview_link` raises while
     the record is `paused` → returns None WITHOUT caching (a second call
     hits the handle again — this is the paused-checkpoint retry path); after
     `mark_destroyed`, None is returned without touching the handle.
  7. **Thread-safety stress**: 16 real threads (barrier-start), each
     registering 20 sandboxes, exec-ing each, destroying half — join, then
     assert `total_ever == 320`, `destroyed == 160`, live == 160, no exception
     escaped (collect via thread wrapper), and `snapshot()` during the churn
     (a 17th reader thread in a loop) never raises.
  8. `reap_orphans` leaf-first: registry with root→ckpt→clone (fakes recording
     deletion order via the shared `FakeClient.deleted`-style list) → clone
     deleted before ckpt before root.
  9. **Retention window**: registry with `destroyed_retain=50`; register and
     destroy 60 sandboxes → `snapshot()["sandboxes"]` holds exactly the 50
     most-recently-destroyed (oldest 10 pruned, `record()` → None for them),
     while `counts` report `total_ever == 60`, `destroyed == 60` — counters
     exact despite pruning; lineage lists contain no pruned ids.
- MODIFY `tests/test_pool.py` + `tests/test_forkpool.py` (new tests, existing
  ones untouched):
  - pool with injected fresh `SandboxRegistry`: `warm(2)` → 2 records, role
    `snapshot-pool`, state `warm`; `release(reusable=False)` → destroyed
    record (poll like the existing destroy tests); `_evict(sid)` removes from
    `_available` and destroys.
  - forkpool: `warm(2)` → registry holds root(state warm) + checkpoint(paused,
    parent=root) + 2 trial-clones(parent=ckpt); after `_fork_clones` the ckpt
    record is back to `paused`; induced degrade → root/ckpt records
    `degraded`; `destroy_all` → all records destroyed.
  - **never-break-a-run**: both pools constructed with `RaisingRegistry` →
    `warm/lease/release/destroy_all` all succeed exactly as without one.
  - **destroy-mid-fork race (the fatal fix, forkpool)**: fake client whose
    fork blocks on a `threading.Event`; thread A enters `warm(2)` (holds
    `_fork_lock` mid-batch), thread B calls `destroy_all()` → assert via the
    fake's call log that B's ckpt/root deletion happens strictly AFTER the
    batch's re-pause (destroy_all blocked on `_fork_lock`); afterwards
    `lease()` and `warm()` raise the torn-down RuntimeError and the fake's
    `create` was never called again (no silent rebuild). Same sentinel
    assertion for `SandboxPool` (`destroy_all` → `lease()` raises).
- MODIFY `tests/test_bisect.py`: probe run over fakes → probe forks appear as
  `bisect-probe` with `parent_id == ckpt.id` and end destroyed; `_exec_test`
  produces one `sandbox_exec` on the bus with `exit_code` parsed from the
  fake's `EXIT:` output (extend `FakeProcess` to script a result string).
  Plus **cooperative cancel**: call `cancel()` after the first checkpoint is
  built (hook the fake) → `run()` returns a `bisect_done` error payload
  mentioning cancellation, no probe starts after the cancel point, and the
  fake deletion log shows `destroy_all` ran exactly once, from the run
  thread's own `finally` (leaf-first order preserved).
- MODIFY `tests/test_server_endpoints.py` (reuse the `server` fixture; add
  `monkeypatch.setattr(server_mod, "REGISTRY", fresh_registry)` and
  re-`attach_bus` to the test bus):
  1. `GET /sandboxes` shape: `sandboxes`/`counts`/`lineage`/`est_resources`
     keys; counts ints.
  2. `GET /sandboxes/{id}`: 404 unknown; known id returns `recent_execs` and
     a `preview_url` from the fake handle.
  3. `DELETE /sandboxes/{id}`: 404 unknown/destroyed; 409 when the record has
     live children; 200 routes through the owner's `_evict` (assert the fake
     pool recorded it / registry marks destroyed).
  4. **`destroy_all`-during-run 409** (spec-mandated): seed
     `_running["active"]=True` under `_run_lock` → `POST /sandboxes/destroy_all`
     is 409; with `?force=1` → 200 and the stubbed pools' `destroy_all` called;
     with `_running` inactive → 200 without force.
  4b. **force = cancel, not yank, for an active bisector**: seed
     `_running["active"]=True` AND `_active["bisector"]` = a stub with
     `cancel()`/`destroy_all()` spies → `?force=1` returns 200 with
     `bisector_cancelled: true`, the stub's `cancel()` was called and its
     `destroy_all()` was NOT called by the server (teardown belongs to the
     run thread's finally); `reap_orphans` was skipped. Also assert that
     after a forced reap the module pool refs are reset to None (next run
     builds fresh — the torn-down sentinel never leaks into a new run).
  4c. **accept/reap TOCTOU sentinel**: seed `_reaping["now"] = True` under
     `_run_lock` → a `/tournament` POST is rejected (409, "reap in
     progress"); after the destroy_all handler's `finally` clears it, the
     same POST is accepted. Also assert the handler clears the sentinel even
     when `_reap_everything` raises (patch it to raise, then check
     `_reaping["now"]` is False).
  5. `_reap_everything` idempotent: call twice, second returns 0 extra
     destructions.
  6. **snapshot-at-accept (the stale-bleed regression)**: register a live
     sandbox in the test registry, drive a stubbed `/tournament` accept, then
     assert the bus history AFTER the reset contains a `registry_snapshot`
     whose `counts.total_ever` still includes pre-run sandboxes — proving the
     registry survived acceptance while the buffer was re-seeded, inside the
     lock (assert it precedes the run thread's first event by seq).
- CREATE `tests/test_observatory_e2e.py` — **the binding proof that the real
  wiring exists** (closes the "facade registry nothing actually calls" hole:
  every other suite deliberately injects a private registry or monkeypatches
  `server_mod.REGISTRY`, so none of them would catch a regression where the
  pool the server runs and the registry the server reads diverge, e.g. an
  accidental `registry=SandboxRegistry()` at a construction site):
  - **NO registry monkeypatching anywhere in this file.** Boot the TestClient
    against the server module's own process-default `REGISTRY`. Fakes are
    injected ONLY at the Daytona-client boundary (the same conftest
    mechanism the existing server tests use to stub `make_pool`'s client) —
    the entire coordinator → `_get_pool()` → pool → `trial.py` →
    registry-hook chain runs real production code.
  - Capture `REGISTRY.counts()` before, drive a stubbed-SDK `/tournament` run
    to completion (poll `/status` like the existing run tests), then
    `GET /sandboxes` on the same client and assert: `counts.total_ever`
    increased, and at least one returned record has role
    `snapshot-pool`/`trial-clone` with `exec_count > 0` — records the test
    itself never inserted, so they can only have arrived via the real
    pool/trial hooks into the same singleton the endpoint reads.
  - Assert deltas, not absolutes (the process REGISTRY is shared across the
    test session; do not reset it — that would defeat the point).
- MODIFY `tests/test_events.py`: nothing — the ast emit-site scan
  automatically covers `registry.py`; add only a literal assertion that the 5
  new names are in `EVENT_TYPES` (cheap, documents intent).
- CREATE `tests/test_cli_sandboxes.py`: `_cmd_sandboxes`/`_cmd_reap` with an
  injected `fetch` returning canned JSON → table lines contain id/role/state,
  totals line correct, `--json` round-trips; fetch raising `URLError` → exit 2
  with the friendly message; reap 409 dict → exit 1.

### B9. Docs (honesty is a product requirement)

MODIFY `README.md` (+ a short paragraph in `docs/ARCHITECTURE.md`): new
"Sandbox Observatory" section — `GET /sandboxes`, `GET /sandboxes/{id}`,
`DELETE /sandboxes/{id}`, `POST /sandboxes/destroy_all` (409 while a run is
active unless `?force=1`), `retrial sandboxes` / `retrial reap`, env knobs
`RETRIAL_EXEC_HISTORY`, `RETRIAL_PREVIEW_PORT`, `RETRIAL_DESTROYED_RETAIN`.
Claim discipline: say "**preview links when Daytona exposes them**
(`get_preview_link`; None otherwise — paused sandboxes are retried on next
open, not cached as failed)" and "the resource meter is a **count-based
estimate** — Daytona does not provide per-sandbox RAM metrics here". Do NOT
claim RAM/CPU metrics. Document the retention window: "`/sandboxes` returns
all live sandboxes plus the 50 most-recently-destroyed; the live/total-ever/
destroyed counters are exact regardless." Document the force semantics
honestly and separately from single-sandbox DELETE: "deleting ONE sandbox
mid-run is a safe resilience demo (the trial layer excludes the infra error
and never re-leases); `destroy_all?force=1` is a different class of
operation — it CANCELS an active bisect run cooperatively and tears down the
tournament pools, after which remaining trials fail as infra-excluded; the
run does not silently continue on rebuilt sandboxes." Keep the existing
verbatim claim-discipline sentence intact.

### WP-BACKEND acceptance (no keys)

```bash
cd RETRIAL
.venv/bin/python -m py_compile engine/retrial/*.py
.venv/bin/python -m pytest tests/ -q                          # whole suite green, incl. new files
PYTHONPATH=engine .venv/bin/python -c "from retrial import SandboxRegistry, REGISTRY"
PYTHONPATH=engine .venv/bin/python -m retrial.cli sandboxes --help && \
PYTHONPATH=engine .venv/bin/python -m retrial.cli reap --help  # both exit 0
# smoke greps (binding check = the emit-site scan test):
grep -n "sandbox_registered\|sandbox_state\|sandbox_exec\|sandbox_destroyed\|registry_snapshot" engine/retrial/events.py
grep -n "emit_snapshot" engine/retrial/server.py   # exactly one hit, inside _accept_run
grep -n "_safe" engine/retrial/registry.py         # decorator present on hooks
grep -cn "registry" engine/retrial/pool.py engine/retrial/forkpool.py engine/retrial/bisect.py engine/retrial/trial.py
# ^ SMOKE ONLY. Grep is not evidence of wiring (the same rule this plan
#   applies to EVENT_TYPES). The BINDING proof that the hooks feed the
#   server's real REGISTRY is tests/test_observatory_e2e.py, which runs in
#   the pytest line above with zero registry monkeypatching.
```
Diff-review checks: no registry call site sits inside a pool `_lock` block; no
endpoint hand-rolls `BUS.reset()`; `atexit.register` present; the lifespan
teardown routes through `_reap_everything`.

---

## WP-FRONTEND

Depends on WP-BACKEND only for the FINAL event names/payloads (B6 — mirror
exactly, fields required where the engine always sends them). No new RUNTIME
npm deps (plain React + `styles.css` grammar; no framer-motion/zustand); one
dev-only exception: `vitest`, added solely to automate the sacred
default-replay regression guard (F7b).

### F1. MODIFY `ui/src/types.ts`

- Wire types: `SandboxRole`, `SandboxLifeState`
  (`'creating'|'warm'|'paused'|'running-cmd'|'destroyed'|'degraded'`),
  `SandboxExecEntry {cmd; exit_code: number | null; output_tail; duration_s; ts}`,
  `SandboxRecordWire {id; role: SandboxRole; backend: 'fork'|'snapshot'; state: SandboxLifeState; parent_id: string | null; created_ts: number; labels?: Record<string,string>; isolation?: string | null; exec_count: number; preview_url?: string | null}`.
- Event interfaces mirroring B6 REQUIRED-where-always-sent (the tsc-drift
  rule from `hermetic_diagnosis`): `SandboxRegistered`, `SandboxStateEvent`
  (`type: 'sandbox_state'` — named to avoid colliding with the state-string
  type), `SandboxExec`, `SandboxDestroyed`, `RegistrySnapshot
  {type:'registry_snapshot'; sandboxes: SandboxRecordWire[]; counts:{live:number; total_ever:number; destroyed:number}; lineage: Record<string,string[]>}`.
- Extend the `RetrialEvent` union with all 5.
- Derived state:
  ```ts
  export interface ObservatorySandbox extends SandboxRecordWire {
    currentCmd: string | null;
    recentExecs: SandboxExecEntry[];   // grown client-side from sandbox_exec
    lastExecSeq: number;               // bumps per exec — drives the card pulse
  }
  export interface ObservatoryState {
    sandboxes: Record<string, ObservatorySandbox>;
    counts: { live: number; totalEver: number; destroyed: number };
    seen: boolean;   // any real registry event arrived (live or ?mock=observatory)
  }
  ```
- `BoardState` gains `observatory: ObservatoryState` (initialised empty,
  `seen: false`).

### F2. MODIFY `ui/src/reducer.ts`

- `initialState.observatory = { sandboxes: {}, counts: {live:0,totalEver:0,destroyed:0}, seen: false }`.
- **`resetPerRun` must NOT clear `observatory`** — sandboxes outlive runs
  exactly like `genome`/`poolDegraded` (add that to its comment).
- New cases (all pure upserts, out-of-order safe):
  - `sandbox_registered` → upsert record (`seen: true`), recompute counts
    client-side (live+1, totalEver+1 only if id unseen).
  - `sandbox_state` → patch `state`/`currentCmd`; unknown id ⇒ create a stub
    record (lossy-replay rule, same spirit as `ensureBisect`).
  - `sandbox_exec` → append to `recentExecs` (cap 20 client-side), bump
    `exec_count`, `lastExecSeq++`, state back to `'warm'`.
  - `sandbox_destroyed` → state `'destroyed'`, counts live−1/destroyed+1
    (guard double-count by prior state).
  - `registry_snapshot` → REPLACE the map wholesale from
    `event.sandboxes` + `event.counts` (idempotent, the authoritative
    re-seed; preserve each existing record's `recentExecs`/`lastExecSeq` by id
    so the drawer doesn't blank on re-seed). The wholesale replace is safe
    for client memory/render because the server bounds `sandboxes` to live +
    the last-50 destroyed (B1 retention window) — long-dead cards age out of
    the Grid by design while the header counters stay exact from
    `event.counts`.
- Add all 5 types to the `baseline_verdict` passthrough allowlist (registry
  traffic is pool-level, honest post-terminal — same rationale as
  `pool_degraded`).
- The default replay emits NONE of these types ⇒ every existing state
  transition is untouched; byte-for-byte default behavior preserved by
  construction (new `case` arms only).

### F3. CREATE `ui/src/observatoryReconstruct.ts` — the replay-reconstruction derivation

Pure function, called from the panel (NOT the reducer — zero contact with the
sacred replay flow):

```ts
export function reconstructObservatory(state: BoardState): ObservatoryState
```
- Tournament replay: synthesize 16 `snapshot-pool` cards (`sb-replay-01…16`,
  matching the recorded SandboxTicker's "16 sandboxes"), distribute
  `exec_count` round-robin from `detect.trials.length + Σ hypothesis trials`,
  mark cards `running-cmd` while the phase is detect/tournament and the last
  distributed trial index is recent, else `warm`. `preview_url` null.
- Bisect replay (`state.bisect` set): synthesize `root` + one `checkpoint` per
  rail row (parent root) + `bisect-probe` cards for probed checkpoints
  (exec_count = probe trials).
- Deterministic (pure of BoardState) so replays render identically each pass.
- The panel labels this source **"replay reconstruction"** (F4) — honesty rule:
  it is a derived visualization of recorded trial events, not recorded
  registry data.

### F4. CREATE `ui/src/components/SandboxObservatory.tsx` (+ card/tree/drawer in-file or sibling files)

`<SandboxObservatory state={state} mode={mode} runActive={runActive} />`,
mounted from `TournamentBoard` (F6). Data source rule:
`const obs = state.observatory.seen ? state.observatory : reconstructObservatory(state);`
`const source = state.observatory.seen ? (mode === 'live' ? 'live' : 'scripted feed') : 'replay reconstruction';`

- **Header strip** (`.obs-header`): `⬢ Sandbox Observatory` + source tag
  (`.obs-source`, amber for reconstruction) + counters
  `live N · total-ever M · destroyed D` (tabular-nums) + view toggle
  Grid|Tree (reuse `.view-toggle` classes) + **Destroy all** button:
  opens a confirm modal (full-screen overlay, PromoteGate's CSS-transition
  grammar) with the live/destroyed counts, a force checkbox shown only when
  `runActive` whose label tells the truth about consequences (mirrors the
  backend force-reap contract — no fine print, judges will click this):
  "Force: CANCELS the active run — a bisect stops at its next probe; a
  tournament's remaining trials fail as infra-excluded. Sandboxes are not
  rebuilt mid-run." CONFIRM →
  `fetch('http://localhost:8000/sandboxes/destroy_all' + (force ? '?force=1' : ''), {method:'POST'})`;
  409 → inline toast "a run is active — check force to cancel it and reap".
  Button disabled unless `mode === 'live'` (`title="live only"`), and while
  `runActive` without the force checkbox.
- **Grid view**: cards grouped by role in fixed order
  root → checkpoint → trial-clone → snapshot-pool → bisect-probe, each group
  with a `.obs-group-label` header + count. Card (`.obs-card`): short id
  (mono), role chip, backend chip, state badge with color + a `.obs-pulse`
  animation keyed on `lastExecSeq` (retrigger via `key={id + lastExecSeq}` —
  same fresh-cell trick as `.cell.fresh`), `current_cmd` one-liner (mono,
  ellipsis) while running-cmd, `exec_count` badge, per-card ✕ destroy button
  (live only; `DELETE /sandboxes/{id}`; 409 leaf-guard → toast "has live
  fork-children"). Destroyed cards render dimmed + strikethrough id and sink
  to the group tail.
- **Tree view** (`.obs-tree`): fork lineage — roots at depth 0, children
  indented under a 1px spine (the TreeTimeline rail grammar: absolute
  gradient spine + status dots), derived from `parent_id` client-side.
  Sandboxes with no parent and role `snapshot-pool` group under a synthetic
  "snapshot pool" header row rather than fake lineage.
- **Detail drawer** (`.obs-drawer`, right-side slide-in, CSS transition):
  opens on card click. Full record: every field, created/updated age,
  labels, isolation. **Exec feed**: scrolling (`overflow-y:auto`) mono list of
  `recentExecs` — cmd, exit-code badge (0 green / non-0 red / null grey
  "infra"), duration, output tail in a `<pre>`. In LIVE mode the drawer
  fetches `GET /sandboxes/{id}` on open for the full server-side history +
  `preview_url`; replay/reconstruction uses in-state data only. **Preview
  button**: rendered only when `preview_url` is non-null →
  `window.open(preview_url, '_blank', 'noopener')`; otherwise a dim
  "no preview link (Daytona did not expose one)" note — honesty rule.
  Per-drawer destroy button mirrors the card's.

### F5. MODIFY `ui/src/styles.css`

New classes in the existing grammar (CSS vars from `:root`, mono/tabular-nums
conventions): `.obs-panel`, `.obs-header`, `.obs-source`, `.obs-counters`,
`.obs-group-label`, `.obs-card` (+ `.destroyed`, `.degraded`), `.obs-state`
(state→color map: creating amber, warm green, paused blue, running-cmd cyan +
`.obs-pulse` keyframes, destroyed grey, degraded red), `.obs-chip`,
`.obs-tree`, `.obs-tree-spine`, `.obs-drawer` (+ `.open`), `.obs-exec-feed`,
`.obs-exec-row`, `.exit-badge` (`.ok/.fail/.infra`), `.obs-destroy-btn`,
`.obs-modal` (reuse the PromoteGate overlay classes where they already fit —
prefer reuse over duplication).

### F6. MODIFY `ui/src/components/TournamentBoard.tsx`

- Topbar-right gains an `⬢ Observatory` toggle button (`.obs-toggle`, count
  badge = live count when `observatory.seen`); local
  `const [obsOpen, setObsOpen] = useState(false)`.
- Render `<SandboxObservatory …/>` as a collapsible panel between
  `<main>` and the footer when `obsOpen` (never replaces the phase router —
  the tournament stays the star; the Observatory is the backstage pass).
- Pass `runActive` (already computed) down for the destroy-all guard.

### F7. MODIFY `ui/src/useEventStream.ts` + `ui/src/mockRun.ts` — replay-safe demo feed

- `MockOutcome` union += `'observatory'`; `MOCK_OUTCOMES` += it. `readParams`
  is already a lookup over `MOCK_OUTCOMES` — no other change; `?mock` unset
  stays byte-identical (`buildMockScript` default branch untouched).
- `mockRun.ts`:
  - NEW pure helper `mergeSchedules(a, b): ScriptedEvent[]` — convert each
    delta list to absolute times, merge-sort stably, convert back to deltas.
    Never touches `buildSchedule`'s math; unit-testable by hand in review.
  - NEW `observatoryTrack(): ScriptedEvent[]` — a scripted registry feed
    telling the fork story alongside the recorded run: opening
    `registry_snapshot` (16 snapshot-pool sandboxes, matching the recorded
    ticker), then `sandbox_registered` root → checkpoint → 8 trial-clones,
    interleaved `sandbox_exec` pulses (exit_code 0/1 mix mirroring the
    recorded pass/fail rhythm), a mid-run `DELETE`-style `sandbox_destroyed`
    wave on non-reusable clones, closing counts consistent
    (totalEver = 25, destroyed = 8 — keep the arithmetic honest, the reducer
    recomputes).
  - `buildMockScript`: `if (outcome === 'observatory') return mergeSchedules(buildSchedule(realRun as …), observatoryTrack());`
    — the recorded frames keep their exact relative timing; only the parallel
    track is interleaved. All other branches untouched.
- Default replay never emits registry events, so the panel shows the F3
  reconstruction, clearly labeled — "never empty in default replay" satisfied
  without touching `realRun.json` or its schedule path.

### F7b. CREATE `ui/src/mockRun.test.ts` — AUTOMATED guard on the sacred default replay

The default-replay invariant is the single most sacred guarantee in the
codebase, and this exact team already shipped a regression against it once
(the stale-bleed bug) on the strength of manual smoke checks. Structural
protection (pure `reconstructObservatory`, new reducer arms only) is
necessary but not sufficient — pin it in CI:

- **Dev-dep exception**: add `vitest` to `ui/package.json` devDependencies +
  `"test": "vitest run"` script. This deliberately relaxes this plan's
  "no new npm deps" rule — that rule protects the runtime bundle; a
  dev-only test runner guarding the sacred invariant is exactly what the
  exception exists for. No runtime dependency changes.
- Tests (import `buildMockScript`/`buildSchedule`/`realRun`/`reduce`
  directly):
  1. `buildMockScript()` with no outcome param **deep-equals**
     `buildSchedule(realRun)` (`toEqual`) — proves the default branch is a
     pure pass-through of the pre-Observatory baseline, never merged with any
     new track.
  2. No event in that default schedule has a type in
     `['sandbox_registered','sandbox_state','sandbox_exec','sandbox_destroyed','registry_snapshot']`
     — also guards `realRun.json` itself against accidental edits.
  3. Feed the full default schedule through the reducer from `initialState`:
     final `observatory.seen === false` and `observatory.sandboxes` empty —
     the default replay cannot light up the Observatory even by accident.
  4. `mergeSchedules(a, b)` unit checks (absolute-time stability, delta
     round-trip) — the one new pure helper in the mock path.
- CI: the ui job gains one line — `npm test` after `npm run build`.

### F8. Docs

README UI section: `?mock=observatory` added to the demo-URL list; one
paragraph on the Observatory panel + the "replay reconstruction" label meaning
(derived from recorded trial events, not recorded registry data).

### WP-FRONTEND acceptance (no keys)

```bash
cd RETRIAL/ui && npm install --no-audit --no-fund && npm run build   # tsc -b && vite build, zero errors
npm test    # F7b vitest suite — the AUTOMATED sacred-default-replay guard
cd ..
# 3-place registration smoke (binding check = tests/test_events.py emit-site scan):
for e in sandbox_registered sandbox_state sandbox_exec sandbox_destroyed registry_snapshot; do
  grep -l "$e" engine/retrial/events.py ui/src/types.ts ui/src/reducer.ts | wc -l   # 3 each
done
grep -n "observatory" ui/src/reducer.ts | grep -n "resetPerRun" ; true  # observatory NOT in resetPerRun's wipe list (manual diff check)
grep -n "'observatory'" ui/src/mockRun.ts ui/src/useEventStream.ts       # opt-in outcome wired
grep -n "buildSchedule(realRun" ui/src/mockRun.ts                        # default branch still the untouched realRun path
grep -n "replay reconstruction" ui/src/components/SandboxObservatory.tsx # honesty label present
.venv/bin/python -m pytest tests/ -q                                     # backend suite still green
```
Manual smoke (`npm run dev`) — now a SECONDARY check; the binding guard for
the default-replay invariant is the automated F7b suite above (given the
prior stale-bleed regression, manual eyeballing alone is not acceptance):
default URL → board byte-identical, Observatory
toggle shows the labeled reconstruction; `?mock=observatory` → cards churn
with the run, tree shows root→ckpt→clones; `?mock=bisect`/`?mock=promote`/
`?mock=quarantine` unchanged; destroy buttons disabled with "live only" hint
in every replay mode.

---

## Where the retrial-eventbus-stale-bleed lesson applies (read before coding)

The prior bug class: shared long-lived state (the bus ring buffer, then
`_pending["promotion"]`) bleeding a previous run into the next because reset
was hand-rolled per-endpoint or done from a background thread. The registry is
the third instance of shared long-lived state — but with a twist: **it must
NOT be wiped at acceptance.** Live sandboxes and `total_ever`/`destroyed`
genuinely span runs (the pool is shared and pre-warmed across runs); wiping
would orphan real sandboxes from the UI and lie about totals.

So the acceptance contract is:
- `_accept_run()` stays the ONLY acceptance point, under `_run_lock`,
  and gains exactly one line: `REGISTRY.emit_snapshot()` AFTER `BUS.reset()`.
  The fresh buffer therefore begins with an authoritative
  `registry_snapshot`, so a WS that connects mid-run reconstructs the sandbox
  world instead of seeing either (a) nothing or (b) a stale tail of
  registry events from the prior run whose earlier half was evicted — the
  exact detect_done/tournament_done asymmetry that caused the original bug.
- Never emit the snapshot from the run's background thread (same rule as the
  reset itself); never add a second snapshot call in any endpoint.
- The reducer mirrors this: `registry_snapshot` REPLACES the map (idempotent
  re-seed), `resetPerRun` leaves `observatory` alone, and cumulative counts
  come from the snapshot when present.
- Regression tests pin all of it: B8 server test 6 (snapshot survives
  acceptance with `total_ever` intact, inside the lock), test_registry.py
  test 1 (`total_ever` never decremented), and the destroy_all-during-run
  409/force tests (lifecycle can't yank sandboxes out from under an accepted
  run without `force`, and even `force` cancels-then-reaps rather than
  deleting resources an unsynchronized live loop is using — see the
  force-reap contract in B4).

## Critique-revision ledger (what changed in this revision, and why)

- **Fatal — forced destroy_all vs in-flight fork batch**: CONFIRMED against
  source (`forkpool.py` `destroy_all` at :327 takes only `self._lock` at
  :332, never `_fork_lock`; `_fork_clones` at :142-193 holds `_fork_lock`
  then `self._lock` in that order, so wrapping `destroy_all` in `_fork_lock`
  keeps a single one-directional lock order — no deadlock;
  `FlakeBisector._lock` is assigned at bisect.py:157 and never acquired;
  `run()`'s existing `finally: self.destroy_all()` at bisect.py:291-292 is
  what makes cancel-not-destroy sound — teardown already lives in the owner
  thread; `_reap_everything` was specified to run after `_run_lock` release).
  Fixed via four mechanisms: `_fork_lock` around the whole forkpool
  `destroy_all` body, a sticky `_torn_down` sentinel checked in
  `warm`/`lease`/`_ensure_checkpoint` (both pools) so a surviving run fails
  honestly instead of silently rebuilding the checkpoint,
  cancel-not-destroy for active bisectors (real `_lock` usage + cooperative
  `cancel()`; teardown only ever runs in the owner thread's `finally`), and
  — found while verifying the fix, beyond the critique as filed — a
  `_reaping` sentinel closing the accept/reap TOCTOU (a run accepted between
  the 409 check and the actual teardown would otherwise be reaped without
  force ever being consented to). B3, B4, B8 race/cancel/TOCTOU tests, and
  the F4 modal copy all updated.
- **Major — unbounded registry_snapshot growth**: fixed with the
  `RETRIAL_DESTROYED_RETAIN` window (default 50) pruning destroyed records
  while keeping `total_ever`/`destroyed` as exact independent counters; B1,
  F2, README, and registry test 9 updated.
- **Major — no proof the real REGISTRY singleton is wired**: fixed with
  `tests/test_observatory_e2e.py` (zero registry monkeypatching, real
  coordinator→pool→trial path, delta assertions on `GET /sandboxes`); the
  acceptance grep is explicitly demoted to smoke-only, matching this plan's
  own EVENT_TYPES rule.
- **Minor — preview() negative-cache poisoning on paused checkpoints**:
  fixed — negative results are never cached in non-terminal states; None is
  cached only once destroyed. Registry test 6 updated to pin the retry.
- **Minor — sacred default replay guarded only by manual smoke**: fixed with
  F7b (`vitest` dev-dep exception, deep-equal baseline + zero-new-event-types
  + reducer-inertness tests, one-line CI addition).
- **Minor — single-sandbox DELETE justification leaking to pool-scope reap**:
  fixed — the safety-class boundary is now stated in the DELETE handler
  docstring, `_reap_everything`'s docstring, and the README force-semantics
  paragraph.
- **Rejected critiques: none.** All six items were verified against the
  actual source before revising; every one identified a real defect in the
  previous draft of this plan.

## Sequencing & handoff

1. WP-BACKEND lands first and freezes the 5 event names + payload shapes (B6)
   — WP-FRONTEND mirrors them verbatim; do not rename casually.
2. WP-FRONTEND touches no engine file; WP-BACKEND touches no `ui/` file — the
   packages are independently revertible.
3. Both packages extend the existing test fakes in `tests/conftest.py`; do not
   fork parallel fake hierarchies.
4. CI gains exactly one line: `npm test` in the ui job after the build step
   (runs the F7b vitest guard). The pytest job already covers every new
   backend test file, including `test_observatory_e2e.py`.
5. The orchestrator commits; implementers only edit files under `RETRIAL`.
