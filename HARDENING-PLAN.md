# HARDENING-PLAN: demo-day hardening (T1-1..T1-5, T2-1..T2-4)

Retrial — powered by the Rewind engine. Branch `rewind-merge`. This plan is
self-contained: implementers read this file + the code. All conventions from
`MERGE-PLAN.md` and `OBSERVATORY-PLAN.md` apply verbatim:

- Retrial style: module-docstring-why, `threading.Lock`/threads, degrade-
  gracefully try/except, snake_case event payloads.
- **Every new event type is registered in 3 places** — `engine/retrial/events.py::EVENT_TYPES`,
  the `RetrialEvent` union in `ui/src/types.ts`, and a `case` arm in
  `ui/src/reducer.ts`. Enforcement is split honestly: the ast emit-site scan
  in `tests/test_events.py` binds the **Python side only** (it walks
  `engine/retrial/*.py` string literals and never sees TypeScript). The TS
  side has NO automatic exhaustiveness — `reducer.ts`'s switch has a
  catch-all `default: return state`, so a missing case arm compiles clean
  and silently drops the event. Therefore every new event type ships **in
  the same package** with a reducer test asserting its case arm mutates
  state (see A7 for `preflight_done`); grep is smoke only.
- Run acceptance ONLY via `server._accept_run()` under `_run_lock`. Never
  reset/emit re-seeds from a background thread.
- **No live SDK calls and no real keys in verification.** Everything below is
  acceptance-checkable with the mocked conftest fakes + `tsc`/vitest. The
  live paths (preflight `--live`, doctor `--live`, `scripts/live_smoke.py`)
  are code we ship but never execute here.
- **The sacred default replay** (no query params) stays byte-identical; the
  vitest deep-equal guard in `ui/src/mockRun.test.ts` must keep passing and
  is NOT edited by any package below (nothing in this plan touches
  `buildMockScript`'s default branch or `realRun.json`).
- Work only in this repo; never touch `.git`; new Python deps go into `.venv`
  AND `requirements.txt`; `cd ui && npm run build && npm test` and the full
  `pytest tests/ -q` stay green after every package.

Three sequential work packages. Each lands whole, with its own acceptance.

- **PKG-A** — T1-3 typed Settings (first — everything else reads it), T1-1
  preflight + loud-degrade backend, T1-2 doctor CLI, T1-5 auth gate.
- **PKG-B** — T1-4 spend meter (backend + Observatory chip), the persistent
  degrade banner UI, T2-3 UI tests.
- **PKG-C** — T2-1 live-smoke CI, T2-2 PR statistical receipts, T2-4 run
  history (SQLite + `/runs` + UI panel).

New event types introduced by this plan (final names — do not rename):

| event | package | emitted by | payload |
|---|---|---|---|
| `preflight_done` | A | server lifespan + re-seeded by `_accept_run` | `{ok: bool, live_checked: bool, checks: [{name, status: "pass"\|"warn"\|"fail", detail}], timings: {...}\|null}` |

That is the ONLY new event type. T1-4 extends the existing
`registry_snapshot` payload with a `spend` object (payload change, mirrored
in `types.ts`, not a new type). T2-4 deliberately adds NO events (the run
history panel is fetch-on-open, read-only).

---

## PKG-A — Settings, preflight, doctor, auth gate

### A1. CREATE `engine/retrial/settings.py` (T1-3) — the single env surface

Add `pydantic-settings==2.*` to `requirements.txt` (installs `pydantic` as a
dependency; FastAPI already pins a compatible pydantic v2).

Module docstring: why (env reads were scattered across 14 modules; one typed
surface, validated once, documented once in README), and the three
behavior-preservation rules below — write them in the docstring, they are
load-bearing:

1. **Env var NAMES are frozen.** Field names are the lowercase of the env
   var, no prefix, `case_sensitive=False` (pydantic-settings default) —
   `retrial_pool_backend` ⇔ `RETRIAL_POOL_BACKEND`, `max_trials` ⇔
   `MAX_TRIALS`. No renames, no new names for existing knobs.
2. **`get_settings()` constructs a FRESH `Settings()` on every call** (cheap;
   none of these sites are hot paths). This preserves today's read-at-use-site
   semantics and keeps every existing `monkeypatch.setenv` test working. Do
   NOT cache/singleton it.
3. **Read-site timing is preserved exactly**: values read at ctor time today
   stay ctor-time (`RETRIAL_MAX_FORKS`, registry rings), values read at call
   time stay call-time (`RETRIAL_FORK_SNAPSHOT`, `RETRIAL_POOL_BACKEND` in
   `make_pool`, the `/bisect` gate). The refactor changes WHERE the string is
   parsed, never WHEN the env is consulted.

```python
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")   # at import: os.environ must stay populated
                                   # for the Daytona SDK (which reads
                                   # DAYTONA_API_KEY itself) — Settings'
                                   # env_file does NOT export to os.environ,
                                   # so this one load_dotenv is kept, HERE,
                                   # and dropped from pool.py/forkpool.py.

class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    # --- Daytona / provisioning ---
    daytona_api_key: str | None = None        # presence-checked only; SDK reads its own env
    daytona_target: str | None = None         # pool/bisect default "us" applied at use site
    retrial_fork_target: str | None = None    # forkpool chain: fork_target or daytona_target or "us-east-1"
    retrial_fork_snapshot: str = "daytona-vm-small"
    retrial_fork_bootstrap_cmd: str = ""
    retrial_pool_backend: str = "snapshot"    # "fork" | "snapshot"
    retrial_max_forks: int = 64
    auto_delete_min: int = 60
    # --- engine tuning (None = "apply the call site's own default") ---
    max_trials: int | None = None             # server/check default 50; cli bisect default 30
    conc: int | None = None                   # server/check 16; cli bisect 8
    tournament_conc: int = 8
    threshold: float | None = None            # None -> DEFAULT_THRESHOLD
    isolation: str = "process"
    prewarm: int = 16
    hermetic_prewarm: int = 8
    # --- flag-style vars: RAW strings + `!= "0"` properties (see below) ---
    prsmith: str = "0"
    promote_gate: str = "1"
    hermetic: str = "0"
    ledger: str = "1"
    retrial_preflight_live: str = "0"
    # --- integrations ---
    fireworks_api_key: str | None = None
    fireworks_models: str = ""
    braintrust_api_key: str | None = None
    retrial_repo: str | None = None
    genome_path: str | None = None
    # --- observatory ---
    retrial_exec_history: int = 20
    retrial_destroyed_retain: int = 50
    retrial_preview_port: int = 8080
    # --- new in this plan ---
    retrial_auth_token: str | None = None                     # T1-5
    retrial_est_rate_per_sandbox_hour: float | None = None    # T1-4 (None = no $ estimate)
    retrial_db: str = str(_REPO_ROOT / ".retrial" / "history.db")  # T2-4
    # --- server bind ---
    host: str = "127.0.0.1"
    port: int = 8000
```

- **Flag semantics preserved byte-for-byte**: today's flags are
  `os.environ.get(X, default) != "0"` — note `PRSMITH=""` is truthy under
  that rule, and pydantic bool parsing would silently change it. So flags are
  stored as raw `str` fields and exposed as properties:
  `@property def prsmith_on(self): return self.prsmith != "0"` (same for
  `promote_gate_on`, `hermetic_on`, `ledger_on`, `preflight_live_on`).
  Pin `PRSMITH="" -> True` in tests so this can never drift.
- **Empty-string normalization**: `daytona_target`/`retrial_fork_target`/
  `retrial_repo`/`fireworks_api_key` etc. use a shared `field_validator` that
  maps `""` to `None` (documented, deliberate: `FOO=` in a .env means unset).
- **Validation must NEVER be able to crash the server.** pydantic-core
  raises `ValidationError` at CONSTRUCTION time on a malformed value
  (`MAX_TRIALS=abc`), and `server.py` calls `get_settings()` at import time —
  a naive `Settings()` there would kill boot before lifespan/preflight ever
  run, violating the charter ("server must still boot for replay demos").
  So `get_settings()` is the ONLY constructor anyone calls, and it can't
  raise:
  ```python
  class Settings(BaseSettings):
      _parse_error: str | None = PrivateAttr(default=None)  # set by get_settings on fallback
      ...

  def get_settings() -> Settings:
      try:
          return Settings()
      except ValidationError as e:
          # Substitute the DEFAULT only for the offending fields; every
          # valid env var is preserved (explicit kwargs override the env
          # source in pydantic-settings). A typo'd MAX_TRIALS must not
          # silently discard a correct RETRIAL_POOL_BACKEND.
          bad = {err["loc"][0] for err in e.errors() if err["loc"]}
          overrides = {f: Settings.model_fields[f].default
                       for f in bad if f in Settings.model_fields}
          try:
              s = Settings(**overrides)
          except ValidationError:              # pathological: fall to pure defaults
              s = Settings.model_construct()
          s._parse_error = "; ".join(
              f"{err['loc'][0]}={err.get('input')!r}: {err['msg']}"
              for err in e.errors())[:200]
          return s
  ```
  Every field in the model has a default, so both fallback paths always
  succeed. `_parse_error` feeds `problems()` (below) so the typo becomes a
  RED `settings_parse` check → loud banner + doctor FAIL — never a boot
  crash, never silent.
- `def problems(self) -> list[dict]` returning
  `{"name", "status": "warn"|"fail", "detail"}` entries used by preflight and
  doctor (NOT raised — the server must boot regardless):
  - `fail  settings_parse`    FIRST, when `_parse_error` is set ("malformed
    env var(s) — defaults substituted for: <detail>"). This is the loud-
    degrade surface for type-coercion failures, which `problems()`'
    coherence checks below can never see.
  - `fail  daytona_api_key`   when None ("no DAYTONA_API_KEY in env or .env").
  - `fail  pool_backend`      when not in ("fork", "snapshot").
  - `fail  fork_region`       when backend fork and the resolved fork target
    (fork_target or daytona_target or "us-east-1") == "us" — the known-bad
    container-region default; fork VMs verified only in us-east-1
    (forkpool.py:66-72, commit 156f98a).
  - `warn  fork_region`       when backend fork and resolved target is
    anything else ≠ "us-east-1" ("fork snapshots verified only in us-east-1;
    misconfig degrades silently to the snapshot pool").
  - `fail  fork_snapshot`     when backend fork and retrial_fork_snapshot is
    empty ("container default snapshot rejects _experimental_fork").
  - `warn  max_forks` when retrial_max_forks <= 0.
- `def resolved_fork_target(self) -> str` and
  `def resolved_pool_target(self) -> str` helpers so the chain logic lives in
  ONE place (forkpool default "us-east-1", pool/bisect default "us").
- `get_settings()` as specified above (fallback-on-ValidationError) at
  module tail — plain `Settings()` is never called outside `settings.py`
  and its tests (the A7 enforcement scan gains a second assertion for this).
- Export `Settings`, `get_settings` from `engine/retrial/__init__.py`;
  update `__all__`.

### A2. The refactor touch list — every `os.environ` read that moves

After PKG-A, `grep -rn "os.environ" engine/retrial/*.py` must hit ONLY
`settings.py`. This is enforced by test (A7), not by grep. Exact sites
(verified against source 2026-07-25):

| file:line (today) | env var | becomes | timing preserved |
|---|---|---|---|
| pool.py:37 | DAYTONA_TARGET | `target or get_settings().resolved_pool_target()` | ctor |
| pool.py:53 | AUTO_DELETE_MIN | `get_settings().auto_delete_min` | ctor |
| pool.py:227 | RETRIAL_POOL_BACKEND | `get_settings().retrial_pool_backend.lower()` in `make_pool` | per call |
| forkpool.py:70-72 | RETRIAL_FORK_TARGET/DAYTONA_TARGET | `target or get_settings().resolved_fork_target()` | ctor |
| forkpool.py:119 | RETRIAL_MAX_FORKS | `get_settings().retrial_max_forks` | ctor |
| forkpool.py:139 | RETRIAL_FORK_SNAPSHOT | `get_settings().retrial_fork_snapshot` | call (`_ensure_checkpoint`) |
| forkpool.py:144 | AUTO_DELETE_MIN | `get_settings().auto_delete_min` | call |
| forkpool.py:168 | RETRIAL_FORK_BOOTSTRAP_CMD | `get_settings().retrial_fork_bootstrap_cmd.strip()` | call |
| bisect.py:151 | DAYTONA_TARGET | `target or get_settings().resolved_pool_target()` | ctor |
| bisect.py:155 | AUTO_DELETE_MIN | `get_settings().auto_delete_min` | ctor |
| cli.py:40-41 | MAX_TRIALS/CONC | `args.max_trials or get_settings().max_trials or 50` (conc 16) | per cmd |
| cli.py:79 | FIREWORKS_API_KEY | `get_settings().fireworks_api_key` | per cmd |
| cli.py:117-118 | MAX_TRIALS/CONC | `... or 30` / `... or 8` (bisect keeps its OWN defaults) | per cmd |
| cli.py:296 | BRAINTRUST_API_KEY | `get_settings().braintrust_api_key` | main() |
| diagnosis.py:35 | FIREWORKS_MODELS | `get_settings().fireworks_models.strip()` | per call |
| diagnosis.py:126,184 | FIREWORKS_API_KEY | `api_key or get_settings().fireworks_api_key` | per call/ctor |
| coordinator.py:47 | HERMETIC | `get_settings().hermetic_on` | ctor |
| genome.py:31 | GENOME_PATH | `get_settings().genome_path or _DEFAULT_PATH` | per call |
| ledger.py:45-46 | BRAINTRUST_API_KEY/LEDGER | `get_settings()` in `from_env` | per call |
| registry.py:113,115 | RETRIAL_EXEC_HISTORY/RETRIAL_DESTROYED_RETAIN | `get_settings()` | ctor |
| registry.py:385 | RETRIAL_PREVIEW_PORT | `get_settings().retrial_preview_port` | per call |
| prsmith.py:30 | RETRIAL_REPO | `get_settings().retrial_repo` | per call |
| server.py:51 | BRAINTRUST_API_KEY | `get_settings().braintrust_api_key` | import |
| server.py:59-76 | MAX_TRIALS..HERMETIC_PREWARM | one `_S = get_settings()` at import; constants keep their NAMES (`MAX_TRIALS = _S.max_trials or 50`, etc.) so every existing test/monkeypatch of `server_mod.MAX_TRIALS` still works | import |
| server.py:294 | RETRIAL_POOL_BACKEND (/health) | `get_settings().retrial_pool_backend` | per request |
| server.py:492 | RETRIAL_POOL_BACKEND (/bisect gate) | `get_settings().retrial_pool_backend` | per request — tests monkeypatch.setenv then POST; fresh-construction rule (A1.2) keeps them green |
| server.py:655 | HOST/PORT | `get_settings().host/.port` | `__main__` |
| pool.py:28 / forkpool.py:40 | `load_dotenv(...)` | DELETED — settings.py's import-time load_dotenv is the one place | import |

NOT migrated (out of scope, documented as such in the settings.py docstring):
`scripts/calibrate_seeds.py` and `scripts/certify_fallback.py` (standalone
live-side scripts, not `engine/retrial/`); the enforcement scan covers
`engine/retrial/*.py` only.

### A3. CREATE `engine/retrial/preflight.py` (T1-1 backend + shared live smoke)

Module docstring: silent degrade on stage is the disaster this prevents; the
preflight NEVER makes boot fatal (replay demos must work with zero config).

- `def config_checks(s: Settings | None = None) -> list[dict]` — pure, no
  network. Returns check dicts `{name, status, detail}` in a stable order:
  0. `settings_parse` — from `Settings.problems()`: fail when `get_settings()`
     fell back on a malformed env var (detail names the var(s)); pass
     ("all env vars parsed") otherwise. First in the list: a typo'd numeric
     env var is a config failure like any other and must reach the banner.
  1. `daytona_api_key` — pass ("present") / fail ("missing — live runs and
     the fork engine cannot start; replay demos unaffected").
  2. `pool_backend` — pass with the value; fail if invalid.
  3. `fork_snapshot` / `fork_region` — from `Settings.problems()` when
     backend is fork; when backend is snapshot emit a single pass line
     ("snapshot backend — fork checks skipped").
  4. `promote_gate` — pass, detail "ON (human approves PRs)" / "OFF (auto-PR)".
  5. `prsmith_gh` — when `prsmith_on` and `shutil.which("gh") is None` →
     warn ("PRSMITH=1 but gh CLI not on PATH"); else pass, honestly stating
     whether gh is present.
  6. `fireworks` / `braintrust` — pass either way, detail states
     present-or-absent honestly ("key present" / "absent — diagnosis runs
     detect-only" / "absent — evidence ledger disabled"). Never fail: both
     are optional by design.
  7. `auth` — unset → pass, "unset — endpoints open (default)"; set →
     **warn**, "RETRIAL_AUTH_TOKEN set — mutating endpoints require Bearer
     auth; the web UI is unauthenticated and its mutating buttons will 401
     (API/CLI-only mode, see README)". Warn, not pass: the operator chose
     it, but it changes UI behavior and doctor must say so.
- `def live_fork_smoke(client=None, budget_s=180) -> dict` — **the shared
  deep check** (used by preflight when `RETRIAL_PREFLIGHT_LIVE=1`, by
  `doctor --live`, and imported by `scripts/live_smoke.py` in PKG-C). One
  budget-capped mini fork cycle, mirroring the proven forkpool sequence and
  reusing `_retry` from `.forkpool`:
  1. `t0` marks; client = injected or `Daytona(DaytonaConfig(target=resolved_fork_target()))`.
  2. create root (`client.create(CreateSandboxFromSnapshotParams(snapshot=..., labels={"retrial": "preflight"}, auto_delete_interval=10), timeout=120)` — 10-minute auto-delete: even a crashed smoke can't leak long; explicit create timeout mirrors forkpool.py:150).
  3. `root.process.exec("echo warm", timeout=60)`; fork+pause a checkpoint;
     start it; fork ONE clone;
     `clone.process.exec("python3 -c 'print(42)'", timeout=60)` and
     assert "42" in the result.
  4. teardown leaf-first in a `finally`: clone → checkpoint → root
     (best-effort, swallow).
  5. Budget guard, two layers — honest about what each can and cannot do:
     a. **Per-call timeouts on every SDK call that accepts one** — `create`
        and `process.exec` demonstrably do (forkpool.py:150,170; the
        conftest fakes model the kwarg). A hung create/exec on conference
        wifi times out inside the call instead of wedging the thread.
     b. **Between-step monotonic check**: if `monotonic()-t0 > budget_s` at
        any step boundary, abort with `{"ok": False, "reason": "budget
        exceeded at <step>"}` and run teardown. (No signal/alarm — must be
        thread-safe under the server.)
     Residual risk, stated plainly: `_experimental_fork`/pause/start are not
     documented to take a per-call timeout; if one wedges, layer (b) cannot
     preempt it. That is why the server runs the deep check in a daemon
     thread (boot is never blocked) and why `doctor --live` gets its own
     hard outer timeout at the CLI layer (A5) — no caller of
     `live_fork_smoke` may rely on the budget alone.
  Returns `{"ok", "reason" (on failure, str ≤200), "timings": {"create_s",
  "checkpoint_s", "fork_s", "exec_s", "teardown_s", "total_s"}}` — timings
  rounded to 0.1s, only the steps actually reached.
- `def run_preflight(live=False, client=None) -> dict`:
  `checks = config_checks()`; `ok = no check has status "fail"`; if `live`
  and ok → `timings = live_fork_smoke(client)`, append a `live_smoke` check
  (pass/fail from its `ok`), fold `not ok` in. Returns
  `{"ok", "live_checked": bool, "checks", "timings": timings or None}` —
  exactly the `preflight_done` payload.

### A4. MODIFY `engine/retrial/server.py` — preflight wiring + loud degrade

- Module state: `_preflight = {"last": None}`; `_STICKY = {"pool_degraded": None}`.
- At import, right after `REGISTRY.attach_bus(BUS)`:
  ```python
  def _track_sticky(ev):
      # The degrade banner must survive BUS.reset(): remember the last
      # pool_degraded payload so _accept_run can re-seed it into every fresh
      # buffer (same stale-bleed cure as registry_snapshot — a WS connecting
      # during run 2 must still learn that the pool degraded during run 1).
      if ev["type"] == "pool_degraded":
          _STICKY["pool_degraded"] = ev["payload"]
  BUS.subscribe(_track_sticky)
  ```
- `lifespan`: BEFORE starting the prewarm thread, run the config-level
  preflight synchronously (pure + fast, no network):
  `res = run_preflight(live=False)`; `_preflight["last"] = res`;
  `BUS.emit("preflight_done", res)`. Then, if
  `get_settings().preflight_live_on` AND `res["ok"]`, spawn a daemon thread
  that runs `run_preflight(live=True)`, overwrites `_preflight["last"]`, and
  emits a second `preflight_done` (the reducer upserts — last write wins).
  NEVER raise from either path (wrap in try/except; on internal error store
  `{"ok": False, "checks": [{"name": "preflight", "status": "fail",
  "detail": str(e)[:200]}], ...}`). Boot proceeds regardless.
- `_accept_run(test_name)` — append AFTER `REGISTRY.emit_snapshot()` (still
  inside `_run_lock`, still the only place):
  ```python
  # Re-seed sticky pool-level facts into the fresh buffer (stale-bleed rule):
  if _preflight["last"] is not None:
      BUS.emit("preflight_done", _preflight["last"])
  if _STICKY["pool_degraded"] is not None:
      BUS.emit("pool_degraded", _STICKY["pool_degraded"])
  ```
- New endpoint:
  ```python
  @app.get("/preflight")
  def preflight():
      """Boot-time config preflight (+ optional live deep check). Read-only,
      never fatal — a failed preflight is a LOUD banner, not a dead server."""
      last = _preflight["last"]
      if last is None:
          return {"status": "pending"}
      return {**last, "pool_degraded_seen": _STICKY["pool_degraded"]}
  ```
- `health()` config dict gains `"preflight_ok": (_preflight["last"] or {}).get("ok")`
  (one honest boolean for the live poller; the banner itself is event-driven).

### A5. MODIFY `engine/retrial/cli.py` — `retrial doctor` (T1-2)

New subcommand in the `set_defaults(func=)` pattern:

```
doc = sub.add_parser("doctor", help="validate config end-to-end: PASS/FAIL per check; --live runs a real budget-capped fork smoke")
doc.add_argument("--live", action="store_true", help="create+fork+exec+destroy ONE real sandbox and report timings (requires DAYTONA_API_KEY; costs ~1 sandbox-minute)")
doc.add_argument("--json", action="store_true")
doc.set_defaults(func=_cmd_doctor)
```

`_cmd_doctor(args, preflight_fn=run_preflight)` (injectable for tests):
- Offline (`not args.live`): `res = preflight_fn(live=False)` directly —
  pure and instant, no wrapper.
- `--live`: **hard external timeout so a wedged SDK call can never hang the
  operator's terminal** (the smoke's internal budget is cooperative — see
  A3.5 — and a pre-demo `doctor --live` on venue wifi is exactly when a
  hang costs the most):
  ```python
  box = {}
  t = threading.Thread(target=lambda: box.update(res=preflight_fn(live=True)),
                       daemon=True)
  t.start(); t.join(budget_s + 30)          # smoke budget + grace
  if "res" not in box:
      print("FAIL  live_smoke        timed out after %ss — wedged SDK call; "
            "sandbox auto-deletes in <=10 min (auto_delete_interval)" % ...)
      return 1                               # daemon thread: process exits clean
  res = box["res"]
  ```
  Tested with an injected `preflight_fn` that sleeps past a shrunken budget.
- `--json` → `print(json.dumps(res))`.
- Human output: one line per check —
  `PASS  daytona_api_key   present` / `WARN  fork_region ...` /
  `FAIL  daytona_api_key   missing — ...` (status column width 4, name 18);
  when live: a `timings:` block (`create 4.2s · checkpoint 1.1s · fork 0.7s
  · exec 0.3s · teardown 2.0s · total 8.3s`); final line
  `doctor: OK` / `doctor: FAILED (n failing checks)`.
- Exit code: 0 iff `res["ok"]` (warns don't fail); 1 otherwise. Errors from
  the preflight function itself → printed, exit 1 (degrade-gracefully).
- The `--help` epilog states honestly: config checks are offline; `--live`
  performs real Daytona API calls and is the same code path as the server's
  `RETRIAL_PREFLIGHT_LIVE=1` deep check and `scripts/live_smoke.py`.

### A6. MODIFY `engine/retrial/server.py` — auth gate (T1-5)

```python
from fastapi import Depends, Header

def _auth_guard(authorization: str | None = Header(default=None)):
    """Optional bearer gate on destructive/mutating endpoints. When
    RETRIAL_AUTH_TOKEN is unset (default) this is a no-op — behavior
    identical to today. Read-only endpoints and /ws are NEVER gated.
    Settings are read PER REQUEST (fresh get_settings) so the gate can be
    toggled without a restart and tests can monkeypatch the env."""
    token = get_settings().retrial_auth_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401,
                            detail="missing or invalid bearer token")
```

Attach `dependencies=[Depends(_auth_guard)]` to EXACTLY these five routes:
`POST /tournament`, `POST /bisect`, `POST /promote`,
`DELETE /sandboxes/{sid}`, `POST /sandboxes/destroy_all`.
`GET /health`, `/genome`, `/preflight`, `/sandboxes`, `/sandboxes/{sid}`,
(PKG-C's `/runs`), and `WS /ws` stay open. Comparison is exact-string
(`Bearer <token>`); no constant-time ambition claimed (loopback demo tool —
say so in the docstring, honestly).

**Scope, stated loudly (UI compatibility):** the UI ships UNAUTHENTICATED —
it has no token field and attaches no `Authorization` header. Enabling
`RETRIAL_AUTH_TOKEN` therefore 401s every UI-initiated mutating action
(Start Tournament, Promote, sandbox Destroy/Destroy-all; bisect has no UI
trigger — it is CLI/API-only today, verified). This plan does NOT build
token management into the UI; instead:

- README's "Auth (optional)" paragraph says verbatim: *"API/CLI-only. The
  web UI does not send tokens — with `RETRIAL_AUTH_TOKEN` set, mutating
  buttons in the UI will be rejected with 401 (each shows an explicit
  auth-gate message). Enable it for headless/API demos, not UI demos."*
- The `auth` line in `config_checks` (A3.7) carries the same warning in its
  detail when the token is set.
- PKG-B (B4b) makes the 401 unmistakable at all four UI mutating fetch
  sites, so an operator who enables the gate anyway gets a diagnostic, not
  a mystery.

*Rejected-critique note (partial):* review claimed UI actions would 401
"silently with no visible error". Not literally true today —
`PromoteGate.tsx:37` renders `engine returned <status>` and
`TournamentBoard.tsx:141-153` toasts `Couldn't start run` with the last
HTTP status; the Observatory destroy paths also toast on non-2xx. The
substance stands (nothing names auth as the cause, no token affordance
exists), hence the scoping + B4b above rather than a token UI.

Docs: README gains an "Auth (optional)" paragraph (wording above) + the
config table row.

### A7. Events + tests (PKG-A)

- `events.py::EVENT_TYPES` — append `"preflight_done"` under a
  `# preflight / doctor` comment. The ast emit-site scan auto-covers the new
  server emit sites.
- `ui/src/types.ts` — add
  `PreflightCheck { name: string; status: 'pass' | 'warn' | 'fail'; detail: string }`,
  `PreflightDone { type: 'preflight_done'; ok: boolean; live_checked: boolean; checks: PreflightCheck[]; timings: Record<string, number> | null }`;
  extend the `RetrialEvent` union. `BoardState` gains
  `preflight: { ok: boolean; liveChecked: boolean; checks: PreflightCheck[] } | null`.
- `ui/src/reducer.ts` — `case 'preflight_done'` → store on `state.preflight`
  (upsert, last write wins). Sticky like `poolDegraded`: NOT cleared in
  `resetPerRun` (pool-level fact — extend the resetPerRun comment). Add
  `'preflight_done'` to the `baseline_verdict` passthrough allowlist.
  (Banner RENDERING lands in PKG-B; the state plumbing lands here so the
  event is never silently dropped — the hermetic_diagnosis lesson.)
- CREATE `ui/src/reducer.test.ts` **in PKG-A, not deferred** (PKG-B extends
  this same file — B5). Minimal but binding for the TS half the ast scan
  cannot see (`default: return state` swallows a missing case arm and tsc
  will not object):
  1. `preflight_done` upserts `state.preflight` (ok/liveChecked/checks land).
  2. A second `preflight_done` overwrites the first (live deep-check update).
  3. It survives the per-run reset (sticky) and the `baseline_verdict` phase.
  4. Default-replay inertness: fold `buildMockScript()` through the reducer
     → `preflight === null` (sacred path unaffected).
  Without this, PKG-A could ship the event correctly emitted and registered
  in `EVENT_TYPES` yet silently dropped by the UI, and every other PKG-A
  acceptance command would still pass.
- CREATE `tests/test_settings.py`:
  1. Defaults: fresh `Settings()` with monkeypatch-cleared env → every
     default above (spot-check the load-bearing ones: backend snapshot,
     fork snapshot `daytona-vm-small`, max_forks 64, retain 50).
  2. Env override roundtrip via `monkeypatch.setenv` for one field of each
     type (str/int/float/flag/optional).
  3. Flag semantics pinned: `PRSMITH=""` → `prsmith_on is True`;
     `PROMOTE_GATE="0"` → False; unset → documented defaults.
  4. Empty-string normalization: `DAYTONA_TARGET=""` → `daytona_target is None`
     → `resolved_pool_target() == "us"`.
  5. `problems()` matrix: fork+`DAYTONA_TARGET=us` → fail `fork_region`;
     fork+us-east-1+key → no fork problems; snapshot backend → no fork checks.
  6. `resolved_fork_target()` chain: RETRIAL_FORK_TARGET wins over
     DAYTONA_TARGET wins over "us-east-1".
  7. Parse-failure fallback (the boot-crash guard):
     `monkeypatch.setenv("MAX_TRIALS", "abc")` +
     `setenv("RETRIAL_POOL_BACKEND", "fork")` → `get_settings()` does NOT
     raise; `max_trials` is the default; `retrial_pool_backend == "fork"`
     (valid env preserved through the fallback); `problems()[0]` is the
     `settings_parse` fail naming `max_trials`; and `run_preflight()["ok"]`
     is False with a `settings_parse` check present. Also the pathological
     branch: monkeypatch `Settings.__init__`-level double failure →
     `model_construct` defaults returned, still no raise.
  8. **THE ENFORCEMENT SCAN** (`test_no_direct_environ_reads_outside_settings`):
     ast-walk every `engine/retrial/*.py` except `settings.py`; fail on any
     `Attribute` node `os.environ` (covers `.get`, `[]`, `in`). Second
     assertion in the same scan: no `Settings(` call outside `settings.py`
     — everyone goes through the crash-proof `get_settings()`. Same spirit
     as the emit-site scan — grep is not evidence; this test is.
- CREATE `tests/test_preflight.py`:
  1. `config_checks` with no env → `daytona_api_key` fail present, `ok`
     False through `run_preflight`.
  2. Key set + snapshot backend → ok True; key set + fork + `DAYTONA_TARGET=us`
     → `fork_region` fail.
  3. `live_fork_smoke(client=FakeClient())` (conftest fakes) → ok True,
     timings has all six keys, and the fake's call log shows create → root
     fork → ckpt pause → ckpt start → clone fork → clone exec → deletions
     leaf-first (clone before ckpt before root, via `FakeClient.deleted`).
  4. Budget abort: monkeypatch `time.monotonic` (module-level, mirroring
     conftest `_fast_retry` style) to jump past budget after create →
     `ok False`, reason mentions budget, teardown still ran.
  5. Fork raising (client with `root_fork_fails=True`) → ok False, honest
     reason, root deleted anyway.
- CREATE `tests/test_doctor.py`: `_cmd_doctor` with injected `preflight_fn`
  returning canned pass/warn/fail results → exit codes 0/0/1, PASS/WARN/FAIL
  lines present, `--json` round-trips, `--live` flag forwarded
  (`preflight_fn` called with `live=True`).
- CREATE `tests/test_auth.py` (TestClient, reuse the server fixture pattern
  from `test_server_endpoints.py` with `_get_pool` stubbed):
  1. Token UNSET: all five mutating endpoints behave exactly as today
     (assert one 200-shaped and one existing-4xx-shaped case each — no 401s
     anywhere).
  2. Token SET (`monkeypatch.setenv("RETRIAL_AUTH_TOKEN", "s3cret")`):
     each of the five → 401 with no/wrong header; with
     `Authorization: Bearer s3cret` → the endpoint's normal status (200/409/
     404 as per its own logic — auth must not mask the real answer).
  3. Read-only endpoints (`/health`, `/sandboxes`, `/preflight`, `/genome`)
     and a `/ws` connect stay open with token set.
- MODIFY `tests/conftest.py`: shared `lifespan_client` fixture —
  ```python
  @pytest.fixture
  def lifespan_client(server_app):          # or the per-file server fixture
      with TestClient(server_app) as c:     # context manager => lifespan RUNS
          yield c
  ```
  **Project-wide gotcha, promoted to a rule:** every existing server test
  uses bare `TestClient(app)` (test_server_endpoints.py:69,
  test_bisect.py:233, test_observatory_e2e.py:57), which does not reliably
  run the ASGI lifespan across starlette/httpx versions — so
  `_preflight["last"]` stays `None` and preflight/health assertions
  false-pass or confusingly fail. Every NEW test in any package that
  touches `/preflight`, `health()["preflight_ok"]`, or the sticky re-seed
  MUST use `lifespan_client` (or an explicit `with TestClient(...)`); the
  fixture exists so nobody re-derives this. Existing bare-client tests are
  untouched (they don't read preflight state).
- MODIFY `tests/test_server_endpoints.py`:
  1. `GET /preflight` → pending with a bare (no-lifespan) client / full
     shape via `lifespan_client`; `checks` list non-empty (first check name
     `settings_parse`); `pool_degraded_seen` key present.
  2. **Sticky degrade re-seed regression** (the loud-degrade contract):
     emit a `pool_degraded` on the test bus (simulating a mid-run-1
     degrade), drive a stubbed `/tournament` accept, then assert the
     post-reset `BUS.history()` contains BOTH a `preflight_done` and a
     `pool_degraded` with the original reason — the banner can never be
     lost to `BUS.reset()`.
- MODIFY existing tests only where the refactor requires: none should — the
  fresh-construction rule keeps every `monkeypatch.setenv` site working, and
  server import-time constant NAMES are unchanged. Fix forward if a stray
  assumption surfaces; never weaken an assertion.

### A8. Docs (PKG-A)

README: new "Configuration reference" section — ONE table listing every env
var above (name, default, consumer, description), including the new
`RETRIAL_AUTH_TOKEN`, `RETRIAL_PREFLIGHT_LIVE`, `RETRIAL_EST_RATE_PER_SANDBOX_HOUR`
(marked "PKG-B"), `RETRIAL_DB` (marked "PKG-C"). New "Doctor & preflight"
section: `python -m retrial.cli doctor [--live]`, `GET /preflight`, the
honest sentence "config checks are offline; `--live` and
`RETRIAL_PREFLIGHT_LIVE=1` perform real Daytona calls (~1 sandbox-minute,
budget-capped)". Keep the existing claim-discipline sentence verbatim.

### PKG-A acceptance (no keys)

```bash
cd <repo>
.venv/bin/pip install -r requirements.txt          # pydantic-settings lands
.venv/bin/python -m py_compile engine/retrial/*.py
.venv/bin/python -m pytest tests/ -q               # whole suite green incl. new files
PYTHONPATH=engine .venv/bin/python -m retrial.cli doctor; echo "exit=$?"   # runs, exit=1 (no key), FAIL line for daytona_api_key — honest
PYTHONPATH=engine .venv/bin/python -m retrial.cli doctor --json | .venv/bin/python -m json.tool >/dev/null
PYTHONPATH=engine .venv/bin/python -c "from retrial import Settings, get_settings"
MAX_TRIALS=abc PYTHONPATH=engine .venv/bin/python -c "import retrial.server" # boot-crash guard: malformed env must NOT kill import
cd ui && npm run build && npm test && cd ..        # green incl. NEW reducer.test.ts (preflight_done state plumbing verified here, not deferred)
grep -rn "os.environ" engine/retrial/*.py | grep -v "^engine/retrial/settings.py"   # SMOKE only — zero hits; binding check = test_settings.py scan
grep -n "preflight_done" engine/retrial/events.py ui/src/types.ts ui/src/reducer.ts # 3-place smoke; binding = emit-site scan (Python side) + reducer.test.ts (TS side)
```

---

## PKG-B — spend meter, degrade banner UI, UI tests

### B1. MODIFY `engine/retrial/registry.py` — lifetime seconds + spend (T1-4)

- Record gains `"destroyed_ts": None` (monotonic-based like `created_ts`).
- New exact-forever aggregate: `self._destroyed_seconds = 0.0` — accumulated
  in `mark_destroyed` (`self._destroyed_seconds += now - rec["created_ts"]`,
  inside the existing idempotence guard so a double-destroy can't
  double-bill) BEFORE pruning. Like `_total_ever`/`_destroyed`, it is NEVER
  recomputed from the pruned record map — pruning cannot lose seconds.
- New pure reader (lock-held, never raises):
  ```python
  def spend(self, rate_per_hour=None):
      """Sandbox-time meter. HONESTY CONTRACT: these are wall-clock
      sandbox-lifetime seconds measured on OUR monotonic clock
      (created->destroyed), not Daytona billing data; est_cost_usd is a
      clearly-labeled estimate computed from an env-supplied rate and is
      None when no rate is configured — we never invent a price."""
      with self._lock:
          now = self._now()
          live_s = sum(now - r["created_ts"] for r in self._records.values()
                       if r["state"] != "destroyed")
          total_s = self._destroyed_seconds + live_s
      rate = rate_per_hour  # caller passes get_settings().retrial_est_rate_per_sandbox_hour
      est = round(total_s / 3600.0 * rate, 2) if rate else None
      return {"live_sandbox_seconds": int(live_s),
              "total_sandbox_seconds": int(total_s),
              "est_cost_usd": est,
              "rate_per_sandbox_hour": rate,
              "note": "estimate — measured sandbox lifetime x env-configured rate; not Daytona billing data"}
  ```
  (Seconds as ints, cost to cents — no fake precision.)
- `snapshot()` gains `"spend": self.spend(rate)` where rate comes from
  `get_settings()` (call-time read — the one settings touch in this file
  beyond A2). `emit_snapshot()` therefore carries it automatically, and
  `GET /sandboxes` (which returns `snapshot()`) exposes it with zero server
  change. `counts()` unchanged.

### B2. MODIFY `ui/src/types.ts` + `ui/src/reducer.ts` — spend on the wire

- `types.ts`: `SpendWire { live_sandbox_seconds: number; total_sandbox_seconds: number; est_cost_usd: number | null; rate_per_sandbox_hour: number | null; note: string }`;
  `RegistrySnapshot` gains REQUIRED `spend: SpendWire` (the engine always
  sends it after B1 — required fields catch drift, the hermetic_diagnosis
  rule). `ObservatoryState` gains `spend: SpendWire | null`.
- `reducer.ts`: `initialState.observatory.spend = null`;
  `case 'registry_snapshot'` stores `event.spend`. No other arm touches it
  (per-event spend updates would be fake precision; the snapshot + live poll
  are the honest sources).
- `ui/src/mockRun.ts`: the `observatoryTrack()` scripted
  `registry_snapshot` gains an honest fake `spend` (small consistent
  numbers, `est_cost_usd: null` — the mock configures no rate). **Only the
  `?mock=observatory` branch — the default branch is untouched**, so the
  sacred deep-equal guard still passes without edits.
- `ui/src/observatoryReconstruct.ts`: reconstruction sets `spend: null`
  (we have no recorded lifetimes — the reconstruction never invents money).

### B3. MODIFY `ui/src/components/SandboxObservatory.tsx` — the spend chip

- Header strip gains a chip after the counters:
  - hidden when `obs.spend === null` (default replay / reconstruction — no
    invented numbers);
  - else `⏱ {fmtHours(total_sandbox_seconds)} sandbox-h` plus, when
    `est_cost_usd !== null`, `· est. ${est_cost_usd}`; when rate is null:
    `· set RETRIAL_EST_RATE_PER_SANDBOX_HOUR for $ est.` (dim). `title` =
    the wire `note` string. Class `.obs-spend` (mono, tabular-nums), always
    prefixed "est." when showing dollars — never a bare price.
- Live freshness: when `mode === 'live'` and the panel is open, poll
  `GET /sandboxes` every 10s (useEffect + setInterval, cleared on
  close/unmount) and use the fetched `spend` (and `counts`) for the header;
  event-driven state remains the source for cards. Replay modes never fetch.

### B4. CREATE `ui/src/components/DegradeBanner.tsx` + mount (T1-1 UI)

- Pure component over `{ poolDegraded, preflight }`:
  - `poolDegraded !== null` → the RED persistent banner (highest priority):
    `⚠ FORK POOL DEGRADED — running on the snapshot fallback: <reason>`
    (`role="alert"`, class `.degrade-banner`, full-width, never
    auto-dismisses, no close button — loud is the point).
  - else `preflight && !preflight.ok` → red banner
    `⚠ PREFLIGHT FAILED — <first failing check name>: <detail>` (+ "run
    `retrial doctor` for the full report").
  - else `preflight` has warn checks → slim amber variant
    (`.degrade-banner.warn`) listing warn names only.
  - else null.
- MODIFY `ui/src/components/TournamentBoard.tsx`: render
  `<DegradeBanner poolDegraded={poolDegraded} preflight={state.preflight} />`
  as the FIRST child of `.board`, above `<TopBar>` — TournamentBoard is the
  root of every view (diagnosing / grid / tree / bisect rail / observatory
  panel / footer), so one mount point covers "all views, not just
  Observatory" by construction. The existing small `SandboxTicker`
  "snapshot fallback" tag stays (belt and braces).
- MODIFY `ui/src/styles.css`: `.degrade-banner` (red bg/border, bold mono
  reason, subtle pulse on the ⚠), `.degrade-banner.warn` (amber),
  `.obs-spend`. Existing grammar/vars only.
- Replay-safety: the default replay emits neither `pool_degraded` nor
  `preflight_done`, so the banner cannot appear on the sacred path (pinned
  by B5 reducer tests, and the existing mockRun deep-equal guard is
  untouched).

### B4b. MODIFY the four UI mutating fetch sites — explicit 401 surfacing (T1-5 companion)

The auth gate is API/CLI-only (A6); this makes the UI fail LOUDLY and
diagnostically if someone enables it anyway. One shared helper
`ui/src/authError.ts`:

```ts
export const AUTH_401_MSG =
  'engine auth is on (RETRIAL_AUTH_TOKEN) — the UI is unauthenticated; ' +
  'use the CLI/API with a Bearer token or unset the env var';
export const authAware = (res: Response, fallback: string) =>
  res.status === 401 ? AUTH_401_MSG : fallback;
```

Wired into the exact four mutating fetch sites (verified — bisect has no UI
trigger): `TournamentBoard.tsx:136` (POST /tournament → toast),
`PromoteGate.tsx:32` (POST /promote → error line),
`SandboxObservatory.tsx:76` (DELETE sandbox → toast),
`SandboxObservatory.tsx:613` (destroy_all → toast). Each existing
non-2xx branch swaps its generic message for `authAware(res, <existing>)` —
no behavior change for any other status, no new fetch paths, replay modes
never fetch so the sacred path is untouched.

### B5. UI tests (T2-3) — EXTEND `ui/src/reducer.test.ts` (created in PKG-A), CREATE `ui/src/components/SandboxObservatory.test.tsx`, `ui/src/components/DegradeBanner.test.tsx`

Dev-deps: add `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`
to `ui/package.json` devDependencies (test-only; runtime bundle unchanged —
same exception rule as vitest itself, F7b precedent). Component test files
start with `// @vitest-environment jsdom` so `mockRun.test.ts` keeps running
in the default node environment — **the sacred-replay guard file is not
edited**.

- `reducer.test.ts` (pure, node env; the PKG-A `preflight_done` cases stay):
  1. The 5 sandbox events: registered→exec→destroyed walk updates counts
     exactly once; `sandbox_state`/`sandbox_exec`/`sandbox_destroyed` on an
     unknown id synthesize a stub (out-of-order/lossy-replay rule);
     double `sandbox_destroyed` doesn't double-count; `sandbox_exec` on a
     destroyed record leaves state `destroyed`; `registry_snapshot`
     replaces the map wholesale while preserving per-id
     `recentExecs`/`lastExecSeq` and stores `spend`.
  2. Degrade banner state: `pool_degraded` sets `poolDegraded`; it SURVIVES
     `bisect_started`/`run_started`/`diagnosing` resets (sticky), and is
     passed through in `baseline_verdict` phase.
  3. `preflight_done` upserts `state.preflight`; survives resetPerRun; a
     second event overwrites the first (live deep-check update).
  4. Default-replay inertness for the NEW state: fold `buildMockScript()`
     through the reducer → `preflight === null`, `poolDegraded === null`,
     `observatory.spend === null` (complements, never edits, the F7b guard).
- `SandboxObservatory.test.tsx` (jsdom):
  1. Replay reconstruction: BoardState with detect trials and
     `observatory.seen=false` → renders cards + the exact source label
     "replay reconstruction"; no spend chip.
  2. Empty registry, live mode: `seen=true`, zero sandboxes → honest empty
     state (assert the panel renders counters `live 0` and no cards, no
     crash).
  3. Missing preview link: drawer for a record with `preview_url: null`
     shows the "no preview link" note and NO preview button; with a url →
     button present.
  4. Destroy confirm modal gating: destroy-all button disabled unless
     `mode === 'live'`; in live mode click opens the confirm modal; force
     checkbox rendered only when `runActive`; CONFIRM calls `fetch` with
     `?force=1` iff checked (mock `global.fetch`, assert URL); 409 response
     → toast text rendered.
  5. Spend chip: snapshot-fed state with `est_cost_usd: 0.12` → chip text
     contains "est. $0.12"; with rate null → contains "set
     RETRIAL_EST_RATE_PER_SANDBOX_HOUR"; `spend: null` → no chip.
  6. Auth-401 surfacing (B4b): mock `global.fetch` returning status 401 on
     the destroy path → toast text contains "RETRIAL_AUTH_TOKEN" and
     "unauthenticated"; a 409 still shows the existing conflict message
     (authAware falls through).
- `DegradeBanner.test.tsx` (jsdom): the four render branches (degraded /
  preflight-fail / warn-only / null), `role="alert"` present, degraded wins
  over preflight-fail.

### B6. Backend tests (PKG-B)

- MODIFY `tests/test_registry.py`:
  1. Spend determinism: monkeypatch the instance's `_now` (bind a counter)
     → register at t=0, destroy at t=10 → `spend()` total 10s, live 0;
     a second live sandbox at t=15 → live 5s, total 15s; double
     `mark_destroyed` adds nothing.
  2. Pruning keeps seconds: retain=1, destroy 3 sandboxes with known
     lifetimes → `_destroyed_seconds` equals the exact sum although records
     were pruned.
  3. `spend(rate_per_hour=None)` → `est_cost_usd is None`; with rate →
     rounded cents; snapshot() contains the `spend` key and is still
     `json.dumps`-able.
- MODIFY `tests/test_server_endpoints.py`: `GET /sandboxes` payload has
  `spend` with the five keys; with `monkeypatch.setenv("RETRIAL_EST_RATE_PER_SANDBOX_HOUR", "0.10")`
  → `est_cost_usd` is a float (fresh get_settings at call time proves the
  env is honored without restart).

### B7. Docs (PKG-B)

README: "Spend meter" paragraph — what is measured (our monotonic
sandbox-lifetime seconds), what is NOT (Daytona billing), the env rate, the
"est." labeling rule, `spend` in `GET /sandboxes`. "Loud degrade" paragraph:
the banner, its event sources, and the sticky re-seed contract. ARCHITECTURE
one-liner each.

### PKG-B acceptance (no keys)

```bash
cd <repo>
.venv/bin/python -m pytest tests/ -q                    # green incl. new spend tests
cd ui && npm install --no-audit --no-fund && npm run build
npm test                                                # F7b sacred guard UNCHANGED and passing + all new suites
cd ..
grep -n "spend" ui/src/types.ts ui/src/reducer.ts engine/retrial/registry.py   # wired (smoke)
grep -n "DegradeBanner" ui/src/components/TournamentBoard.tsx                  # mounted at board root (smoke)
# Manual smoke: npm run dev — default URL byte-identical (no banner, no chip);
# ?mock=observatory shows the chip (hours, no $); banner appears only if a
# pool_degraded / failed preflight_done event is injected.
```

---

## PKG-C — live-smoke CI, PR receipts, run history

### C1. CREATE `scripts/live_smoke.py` + `.github/workflows/live-smoke.yml` (T2-1)

`scripts/live_smoke.py` — the proven flow as a committed, budget-capped
script. Never imported by the engine. **Deliberately its OWN path, not a
wrapper around `preflight.live_fork_smoke`**: the smoke's job is to prove
the PRODUCTION pool path (`ForkSandboxPool.warm/lease`, degrade detection,
registry lineage) — the preflight mini-cycle proves only the raw SDK
create/fork/exec sequence. Calling `live_fork_smoke()` first and then the
pool flow would roughly double the sandbox spend for no extra evidence
(the pool flow strictly supersets the SDK operations). So: two live code
paths exist by design — see the reconciled invariant #5 — and the
orchestrator's live verification must exercise BOTH (doctor `--live` for
the shared preflight path, this script for the pool path):

1. Bootstrap: `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))`.
2. Fail-fast guard (testable offline): if `get_settings().daytona_api_key`
   is None → print `SKIP: DAYTONA_API_KEY not set` and `sys.exit(2)` before
   ANY SDK construction.
3. Hard timeout with best-effort teardown first — `os._exit` skips ALL
   Python cleanup including the `finally: pool.destroy_all()`, so the kill
   path tries to shrink the leak window from the ~10-min auto-delete
   backstop to seconds without sacrificing the hard-kill guarantee:
   ```python
   def _hard_kill():
       try:
           if POOL["pool"] is not None:      # module dict set once pool exists
               t = threading.Thread(target=POOL["pool"].destroy_all, daemon=True)
               t.start(); t.join(20)         # sub-timeout: never delays exit >20s
       finally:
           os._exit(3)                       # unconditional — the honest kill
   threading.Timer(int(os.environ.get("LIVE_SMOKE_TIMEOUT", "300")), _hard_kill)
   ```
   started as a daemon safety net; `auto_delete_interval` remains the
   credit backstop if even the 20s teardown wedges. (`os.environ` is fine
   here: scripts/ is outside the A7 scan, by design.)
4. Spend guards: force `RETRIAL_MAX_FORKS=4` into the env unless already
   lower; `auto_delete_min=10` passed to the pool ctor.
5. Own bus + registry: `bus = EventBus(); reg = SandboxRegistry(bus=bus)`.
6. `pool = ForkSandboxPool(bus=bus, registry=reg, labels={"retrial": "live-smoke"}, auto_delete_min=10)`;
   `POOL["pool"] = pool` (arms the kill-path teardown in step 3);
   `warm(2)`; **assert not degraded**: `pool.stats()["backend"] == "fork"`
   else print the `pool_degraded` reason from `bus.history()` and exit 1.
7. `sb = pool.lease()`; exec the trial-pattern one-liner
   (`python3 -c 'print(42)'` via `sb.process.exec`) and assert "42".
8. Registry lineage assertions: exactly one `root` and one `checkpoint`
   record, checkpoint.parent_id == root.id, ≥2 `trial-clone` records with
   parent_id == checkpoint.id; `counts["total_ever"] >= 4`.
9. `finally: pool.destroy_all()`; then assert `reg.counts()["live"] == 0`.
10. Print a JSON report (timings from a `time.monotonic` bracket per phase,
    counts, backend) and exit 0. Any assertion/exception → print honestly,
    exit 1 (teardown already ran via finally).

`.github/workflows/live-smoke.yml`:

```yaml
name: live-smoke
# MANUAL ONLY. Real Daytona spend (~2-4 sandbox-minutes, budget-capped).
# Never wired to push/PR — CI stays credential-free (see ci.yml).
on:
  workflow_dispatch:
    inputs:
      fork_snapshot: { description: "VM snapshot", default: "daytona-vm-small" }
      fork_target:   { description: "Region",      default: "us-east-1" }
jobs:
  live-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - name: Live fork smoke (budget-capped)
        env:
          DAYTONA_API_KEY: ${{ secrets.DAYTONA_API_KEY }}
          RETRIAL_POOL_BACKEND: fork
          RETRIAL_FORK_SNAPSHOT: ${{ github.event.inputs.fork_snapshot }}
          RETRIAL_FORK_TARGET: ${{ github.event.inputs.fork_target }}
        run: python scripts/live_smoke.py | tee live-smoke-report.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: live-smoke-report, path: live-smoke-report.json }
```

`.github/workflows/ci.yml` and `retrial.yml` untouched.

### C2. MODIFY `engine/retrial/prsmith.py` — statistical receipts (T2-2)

New pure helper + one insertion point in `_dossier` (before the Braintrust
section):

```python
def _receipts(result):
    """'## Statistical receipts' — every number the verdict rests on, with
    honest omissions: a line renders ONLY when its datum exists; absent data
    says nothing (never 'n/a' dressed up as evidence, never a claim without
    a measurement)."""
```

Content (all via defensive `.get` chains, matching the real result shape
from `coordinator.py`):
- Detect (before): `detect.trials` valid trials, `detect.fails` failures,
  empirical flake rate `_pct(orig_flake_rate)`, Wilson 95% CI
  `_ci(detect.wilson_ci)` — one bullet.
- After (FIXED only): winner tournament rate + CI + trials; confirmation
  round rate + CI + trials + the explicit sentence "confirmation was an
  independent re-verify; the winner stands only because it read STABLE".
- QUARANTINE: best candidate's rate + CI + trials (already computed for the
  body) restated as the receipt, plus "no candidate's CI cleared the
  threshold".
- Braintrust: `braintrust.detect` / winner-series permalinks as markdown
  links WHEN non-None; when absent, a single honest line
  "Braintrust ledger: not recorded for this run (no BRAINTRUST_API_KEY)".
- Footer line: "Method: Wilson 95% score intervals over independent sandbox
  reruns; verdicts require the whole CI to clear the threshold, not the
  point estimate."

Wire into `_dossier` for BOTH verdict branches. Existing body lines stay
(they are the narrative; receipts are the table — slight duplication is
fine and honest).

- CREATE `tests/test_prsmith.py` (pure — no `gh`, no subprocess):
  1. Full FIXED result (build the dict per coordinator keys) → dossier
     contains "## Statistical receipts", before/after rates, both CIs,
     confirmation trials, Braintrust links.
  2. QUARANTINE result → receipts present, no winner/confirmation claims.
  3. Sparse result (no braintrust, no confirmation, detect only) → the
     absent-data lines appear, and NO invented numbers ("est", "n/a"-as-data
     absent; assert the braintrust honest-absence line).
  4. `_dossier` never raises on a minimal `{"verdict": "QUARANTINE"}`-shaped
     dict (degrade-gracefully).

### C3. Run history (T2-4)

CREATE `engine/retrial/history.py`:

- Module docstring: persistence must NEVER break a run (registry rule); no
  ORM, stdlib `sqlite3`; one connection per operation (open→write→close)
  under a module lock — runs finish seconds apart, contention is nil.
- Every connect (read AND write) runs `PRAGMA journal_mode=WAL` and
  `PRAGMA busy_timeout=250` first: a `GET /runs` that lands exactly as a
  run's INSERT commits reads the last-committed state instead of queueing
  behind the writer, and worst-case reader latency is bounded well under
  the module lock's hold time (single-row INSERT on WAL — sub-10ms; this
  bound goes in the README so a demo-time pause is never misread as a
  hang). WAL's `-wal`/`-shm` sidecar files live inside `.retrial/`, which
  the gitignore line below already covers. The PRAGMAs themselves are
  inside the `_safe`/returns-`[]` envelopes like everything else.
- ```python
  _SCHEMA = """CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL,            -- 'tournament'|'bisect'
      test_name TEXT, verdict TEXT,
      orig_flake_rate REAL, final_flake_rate REAL,
      winner_model TEXT, braintrust_url TEXT,
      started_at REAL, finished_at REAL)"""               -- wall-clock time.time()

  class RunHistory:
      def __init__(self, db_path=None):   # None -> get_settings().retrial_db
      @_safe  # reuse registry._safe (import it) — one mechanism, one place
      def record(self, kind, test_name, verdict, orig_flake_rate=None,
                 final_flake_rate=None, winner_model=None,
                 braintrust_url=None, started_at=None, finished_at=None): ...
          # mkdir parents, connect, executescript(_SCHEMA), INSERT, commit, close
      def recent(self, limit=20):   # pure reader: list[dict] newest-first;
          # returns [] on ANY failure (missing/corrupt db) — never raises
  HISTORY = RunHistory()
  ```
- `.gitignore`: append `.retrial/`.

MODIFY `engine/retrial/server.py`:

- **Placement rule — a history write may NEVER sit where its failure could
  be conflated with, or overwrite, the run verdict.** The naive placement
  (record inside the same `try` whose `except` re-emits a terminal event
  with `verdict="ERROR"`) means any surprise raise from the record path —
  a history.py bug, a monkeypatched class attribute that bypasses the
  `_safe` decorator, an unexpected result shape — would mask an
  already-correct FIXED/POLLUTER verdict with ERROR: exactly the
  dishonest-verdict class the registry `_safe` rule exists to prevent. So
  record from a `finally`, after all verdict/terminal-event/promote-gate/PR
  logic, with a call-site local guard as defense-in-depth on top of `_safe`
  (two independent layers; the server-level test below proves the layer,
  not the decorator):
  ```python
  # /tournament's run(): started_at = time.time() at thread start;
  # row = None in the preamble.
  try:
      ... existing verdict / terminal-event / promote-gate / PR logic ...
      w = result.get("winner") or {}
      row = dict(kind="tournament", test_name=path.name,
                 verdict=result.get("verdict"),
                 orig_flake_rate=result.get("orig_flake_rate"),
                 final_flake_rate=(result.get("confirmation") or {}).get("flake_rate"),
                 winner_model=w.get("model"),
                 braintrust_url=(result.get("braintrust") or {}).get("detect"))
  except Exception:
      ... existing terminal-event handling unchanged ...
      row = dict(kind="tournament", test_name=path.name, verdict="ERROR")
  finally:
      try:                      # local guard: history can never touch the verdict
          if row is not None:
              HISTORY.record(**row, started_at=started_at, finished_at=time.time())
      except Exception:
          pass
  ```
- In `/bisect`'s `run()`: same shape — build `row` after `bisector.run(...)`
  returns `res` (`kind="bisect"`, `test_name=<suite name>`,
  `verdict=("POLLUTER:" + res["polluter_test"]) if res.get("polluter_test") else ("ERROR" if res.get("error") else "INCONCLUSIVE")`,
  rates from `res.get("base_flake_rate")/res.get("full_flake_rate")` when
  present; `verdict="ERROR"` row in the except arm), record in the
  `finally` behind the same local guard, after the existing terminal-event
  handling.
- New endpoint (read-only → NOT auth-gated):
  ```python
  @app.get("/runs")
  def runs(limit: int = 20):
      """Recent completed runs from the SQLite history (RETRIAL_DB).
      Read-only; empty list when no history exists — never an error."""
      return {"runs": HISTORY.recent(min(max(limit, 1), 100))}
  ```

UI — CREATE `ui/src/components/RunHistory.tsx` + wiring:

- Collapsible read-only panel, same grammar as the Observatory: TopBar gains
  a `☰ Runs` toggle (state in TournamentBoard, rendered next to the
  Observatory panel slot). On open AND `mode === 'live'`: fetch
  `GET /runs` once (+ manual ↻ refresh button; no polling — it's history).
- Row: kind chip (tournament/bisect), test name (mono), verdict badge
  (FIXED green / QUARANTINE amber / POLLUTER:* purple / ERROR red /
  INCONCLUSIVE grey), `orig% → final%` when both present, winner model chip,
  Braintrust link icon when url present, finished-at as local time.
- Honest empty states: live+empty → "no completed runs yet — history starts
  with your first run"; replay modes → "run history is live-only (the replay
  has no database)" and NO fetch is made. Styles `.runs-panel`, `.runs-row`,
  `.runs-verdict` in the existing grammar.
- No reducer/types/event changes (fetch-local `useState` — deliberately
  outside the event stream; the sacred replay cannot be affected by
  construction).

Tests:

- CREATE `tests/test_history.py`:
  1. Roundtrip: `RunHistory(db_path=tmp_path/"h.db")` → record 3 →
     `recent()` newest-first, all fields typed, limit honored.
  2. Never-break-a-run: db_path pointing into an unwritable/garbage location
     (`"/dev/null/nope/h.db"`) → `record` returns None, no raise; `recent`
     → `[]`.
  3. Schema idempotence: two RunHistory instances on the same file both
     write; a pre-corrupted file (write junk bytes) → `recent` → `[]`.
  4. `RETRIAL_DB` env honored via `get_settings` (monkeypatch.setenv +
     default ctor).
- MODIFY `tests/test_server_endpoints.py`:
  1. `GET /runs` shape (monkeypatch `server_mod.HISTORY` to a stub or point
     RETRIAL_DB at tmp_path); limit clamped.
  2. End-to-end: drive the stubbed `/tournament` run to completion (existing
     pattern) with RETRIAL_DB at tmp_path → `/runs` shows one tournament row
     with the stub's verdict — proving the record call sits on the real run
     path.
  3. **Verdict-can-never-be-masked regression** (binds the placement rule):
     monkeypatch `server_mod.HISTORY` with a stub whose `record` RAISES
     (deliberately bypassing `_safe` — this tests the call-site guard, the
     second layer, in isolation); drive the stubbed `/tournament` to a
     FIXED result → the terminal `tournament_done` event carries
     `verdict="FIXED"` (not ERROR), exactly one terminal event is emitted,
     and the run thread exits cleanly. Same assertion for `/bisect` with a
     POLLUTER result. A history failure must cost only the history row,
     never the verdict.
- `py_compile scripts/live_smoke.py` added to the acceptance line; plus
  CREATE `tests/test_live_smoke_guard.py`: run
  `live_smoke`'s key-guard in-process (import module with monkeypatched
  cleared `DAYTONA_API_KEY` and patched `sys.exit` → asserts exit code 2
  BEFORE any client construction — patch `Daytona` to a bomb to prove it's
  never touched). Keeps the script honest without network.

### C4. Docs (PKG-C)

README: "Run history" (env `RETRIAL_DB`, `GET /runs`, the live-only UI
panel, gitignored db); "Live smoke" (manual workflow_dispatch only, secret
`DAYTONA_API_KEY`, spend caps, never on PRs); "PR receipts" one paragraph
(what the Statistical receipts section contains; wording rule: no claims
without data). Config table rows updated (RETRIAL_DB now landed).
ARCHITECTURE.md one-liner for history.py.

### PKG-C acceptance (no keys)

```bash
cd <repo>
.venv/bin/python -m py_compile engine/retrial/*.py scripts/live_smoke.py
.venv/bin/python -m pytest tests/ -q                     # green incl. prsmith/history/guard tests
DAYTONA_API_KEY= PYTHONPATH=engine .venv/bin/python scripts/live_smoke.py; test $? -eq 2   # offline fail-fast SKIP path
PYTHONPATH=engine .venv/bin/python -c "import retrial.server"   # HISTORY wiring imports clean
cd ui && npm run build && npm test && cd ..              # RunHistory compiles; sacred guard still green
git status 2>/dev/null || true                           # (orchestrator-only) .retrial/ must be gitignored
grep -n "workflow_dispatch" .github/workflows/live-smoke.yml   # manual-only trigger (smoke)
grep -n "Statistical receipts" engine/retrial/prsmith.py       # section present (smoke)
```

---

## Sequencing & cross-package invariants

1. **PKG-A first, strictly** — B and C read `get_settings()` (spend rate,
   RETRIAL_DB) and B renders the `preflight_done` state A plumbs. Within A:
   settings → refactor sweep → preflight → doctor → auth (each keeps the
   suite green before the next starts).
2. Event-name freeze: `preflight_done` and the `registry_snapshot.spend`
   payload shape above are final once PKG-A/PKG-B land — the TS union
   mirrors them with required fields.
3. Every package ends with: full `pytest tests/ -q` green, `cd ui && npm run
   build && npm test` green, and the F7b deep-equal guard passing WITHOUT
   the guard file having been edited.
4. The stale-bleed rule extended once more: sticky pool-level facts
   (`pool_degraded`, `preflight_done`) are re-seeded into the fresh buffer
   ONLY inside `_accept_run` under `_run_lock` — never from a background
   thread, never hand-rolled in an endpoint (A4; regression test A7).
5. Nothing in this plan calls a live API in tests or acceptance. Live code
   paths, reconciled (an earlier draft over-claimed "ONE implementation"):
   there are exactly TWO — (a) `preflight.live_fork_smoke`, shared verbatim
   by the server deep check (`RETRIAL_PREFLIGHT_LIVE=1`) and `doctor
   --live`; (b) `scripts/live_smoke.py`, an intentionally independent
   pool-level superset (ForkSandboxPool warm/lease + degrade + lineage —
   the production path, which the mini-cycle does not exercise; see C1 for
   why merging them would double spend for no evidence). The orchestrator's
   live verification MUST run both (a) via `doctor --live` and (b) via the
   script — passing one does not certify the other.
6. The orchestrator commits; implementers only edit files in this repo.

---

## Revision log — review round 1 (all findings addressed in place)

- Settings ValidationError boot crash → crash-proof `get_settings()`
  fallback preserving valid env vars + `settings_parse` check/banner (A1,
  A3.0, A7 tests, acceptance import-with-bad-env line).
- HISTORY.record verdict-masking → `finally` placement + call-site local
  guard + masked-verdict regression test (C3).
- Auth gate vs UI → explicitly scoped API/CLI-only in README/doctor + B4b
  401 surfacing at the four verified UI mutating fetch sites + vitest
  case. Partial rejection recorded in A6: the UI's 401 today is generic,
  not literally silent (PromoteGate.tsx:37, TournamentBoard.tsx:141-153).
- "ast scan binds all 3 places" over-claim → corrected (Python-side only);
  `reducer.test.ts` with `preflight_done` assertions pulled forward into
  PKG-A acceptance (intro bullet, A7, PKG-A acceptance).
- Cooperative live-smoke budget → per-call SDK timeouts where supported
  (create/exec, mirroring forkpool.py:150,170), residual risk documented,
  hard outer thread-join timeout for `doctor --live` (A3.5, A5).
- Invariant #5 vs C1 inconsistency → reconciled as two deliberate live
  paths, both required in live verification (C1 intro, invariant #5).
- SQLite contention → WAL + busy_timeout=250 PRAGMAs, documented sub-10ms
  reader bound (C3).
- Lifespan/TestClient gotcha → shared `lifespan_client` conftest fixture,
  mandatory for all new preflight/health tests (A7).
- `os._exit(3)` skipping teardown → best-effort 20s-capped `destroy_all`
  in the kill path before exit (C1.3/C1.6).
