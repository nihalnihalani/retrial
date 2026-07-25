"""Settings: the single typed env surface for the whole engine.

WHY THIS MODULE EXISTS: env reads used to be scattered across ~14 modules
(pool, forkpool, bisect, cli, server, diagnosis, coordinator, genome, ledger,
registry, prsmith, ...), each hand-parsing `os.environ.get(...)` with its own
default. That is where config drift and silent-degrade bugs hide. This module
is the one place the environment is read and typed, validated once, and
documented once (README "Configuration reference").

THREE BEHAVIOR-PRESERVATION RULES (load-bearing — do not "simplify" them away):

1. ENV VAR NAMES ARE FROZEN. Field names are the lowercase of the env var,
   no prefix, `case_sensitive=False` (pydantic-settings default) —
   `retrial_pool_backend` <-> `RETRIAL_POOL_BACKEND`, `max_trials` <->
   `MAX_TRIALS`. No renames, no new names for existing knobs. This refactor
   changes WHERE the string is parsed, never the name and never the meaning.

2. `get_settings()` CONSTRUCTS A FRESH `Settings()` ON EVERY CALL (cheap; none
   of these sites are hot paths). This preserves today's read-at-use-site
   semantics and keeps every existing `monkeypatch.setenv` test working. Do
   NOT cache/singleton it.

3. READ-SITE TIMING IS PRESERVED EXACTLY. Values read at ctor time today stay
   ctor-time (RETRIAL_MAX_FORKS, registry rings), values read at call time
   stay call-time (RETRIAL_FORK_SNAPSHOT, RETRIAL_POOL_BACKEND in make_pool,
   the /bisect gate). The refactor changes WHERE the string is parsed, never
   WHEN the env is consulted.

FLAG SEMANTICS ARE PRESERVED BYTE-FOR-BYTE: today's flags are
`os.environ.get(X, default) != "0"`, so `PRSMITH=""` is TRUTHY. pydantic's bool
parser would silently change that, so flags are stored as raw `str` fields and
exposed as `*_on` properties comparing `!= "0"`.

NOT MIGRATED (out of scope, by design): `scripts/calibrate_seeds.py` and
`scripts/certify_fallback.py` are standalone live-side scripts, not part of
`engine/retrial/`; the A7 enforcement scan covers `engine/retrial/*.py` only.
"""
from pathlib import Path

from dotenv import load_dotenv
from pydantic import PrivateAttr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]
# At import: keep os.environ populated for the Daytona SDK (which reads
# DAYTONA_API_KEY itself). Settings' env_file does NOT export to os.environ, so
# this one load_dotenv is kept HERE and dropped from pool.py/forkpool.py — the
# single place the .env is loaded for the whole engine.
load_dotenv(_REPO_ROOT / ".env")


class Settings(BaseSettings):
    """Typed, validated view of every engine env var. Never construct directly
    outside this module — use `get_settings()`, which can never raise."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Set by get_settings() when it falls back on a malformed env var, so a
    # type-coercion failure becomes a LOUD `settings_parse` problem instead of a
    # boot crash (see get_settings + problems()).
    _parse_error: str | None = PrivateAttr(default=None)

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
    narrate: str = "0"
    # --- integrations ---
    fireworks_api_key: str | None = None
    fireworks_models: str = ""
    braintrust_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    # Slugs VERIFIED live 2026-07-25 against GET /v1/models and /v1/voices; the
    # narrator's module defaults are the same two values. Overridable, never guessed.
    elevenlabs_voice_id: str | None = None
    elevenlabs_model_id: str | None = None
    retrial_repo: str | None = None
    genome_path: str | None = None
    # --- observatory ---
    retrial_exec_history: int = 20
    retrial_destroyed_retain: int = 50
    retrial_preview_port: int = 8080
    # Sandbox Observatory previews. Default OFF. Sandboxes stay PRIVATE — the
    # link is a signed, 1h-expiring preview URL (create_signed_preview_url), not
    # a public sandbox. Still opt-in: anyone holding the link can read the served
    # page for its lifetime, and the served directory is writable by the code
    # under test.
    retrial_preview: str = "0"
    # Comma-separated origins allowed to make cross-origin requests. Empty =
    # localhost dev origins only. Never "*": see the CORS block in server.py.
    retrial_allowed_origins: str = ""
    # --- new in this plan ---
    retrial_auth_token: str | None = None                      # T1-5
    retrial_est_rate_per_sandbox_hour: float | None = None     # T1-4 (None = no $ estimate)
    retrial_db: str = str(_REPO_ROOT / ".retrial" / "history.db")  # T2-4
    # --- server bind ---
    host: str = "127.0.0.1"
    port: int = 8000

    # Empty-string normalization: `FOO=` in a .env means unset, not "". Applied
    # to the optional fields so `DAYTONA_TARGET=` resolves to the default region
    # rather than trying to talk to a region named "".
    @field_validator(
        "daytona_api_key", "daytona_target", "retrial_fork_target",
        "fireworks_api_key", "braintrust_api_key", "retrial_repo",
        "genome_path", "retrial_auth_token", "max_trials", "conc",
        "threshold", "retrial_est_rate_per_sandbox_hour",
        "elevenlabs_api_key", "elevenlabs_voice_id", "elevenlabs_model_id",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # -- flag properties (byte-identical to the old `!= "0"` rule) -------
    @property
    def prsmith_on(self) -> bool:
        return self.prsmith != "0"

    @property
    def promote_gate_on(self) -> bool:
        return self.promote_gate != "0"

    @property
    def hermetic_on(self) -> bool:
        return self.hermetic != "0"

    @property
    def ledger_on(self) -> bool:
        return self.ledger != "0"

    @property
    def preflight_live_on(self) -> bool:
        return self.retrial_preflight_live != "0"

    @property
    def narrate_on(self) -> bool:
        return self.narrate != "0"

    @property
    def preview_on(self) -> bool:
        """Serve a shareable Observatory preview from every pool sandbox.

        Note this one is `== "1"`, not the `!= "0"` convention used by the other
        flags: it publishes sandboxes to anyone holding the URL, so an empty or
        malformed value must mean OFF, never ON."""
        return self.retrial_preview == "1"

    # -- resolved chains (one place each) -------------------------------
    def resolved_fork_target(self) -> str:
        """forkpool region chain: RETRIAL_FORK_TARGET -> DAYTONA_TARGET ->
        'us-east-1' (fork VMs are verified only in us-east-1)."""
        return self.retrial_fork_target or self.daytona_target or "us-east-1"

    def resolved_pool_target(self) -> str:
        """snapshot pool / bisect region chain: DAYTONA_TARGET -> 'us'."""
        return self.daytona_target or "us"

    # -- coherence problems (never raised — the server must boot) -------
    def problems(self) -> list[dict]:
        """Config problems as `{name, status: warn|fail, detail}` entries, used
        by preflight.config_checks and the doctor CLI. NEVER raised: a failing
        config surfaces as a LOUD banner + doctor FAIL, never a boot crash."""
        out: list[dict] = []
        # settings_parse FIRST: a type-coercion failure that get_settings()
        # recovered from (defaults substituted) — the loud surface for the class
        # of failures the coherence checks below can never see.
        if self._parse_error:
            out.append({
                "name": "settings_parse", "status": "fail",
                "detail": "malformed env var(s) — defaults substituted for: "
                          + self._parse_error,
            })
        if self.daytona_api_key is None:
            out.append({"name": "daytona_api_key", "status": "fail",
                        "detail": "no DAYTONA_API_KEY in env or .env"})
        backend = self.retrial_pool_backend.lower()
        if backend not in ("fork", "snapshot"):
            out.append({"name": "pool_backend", "status": "fail",
                        "detail": f"invalid RETRIAL_POOL_BACKEND={self.retrial_pool_backend!r} "
                                  f"(expected fork|snapshot)"})
        if backend == "fork":
            target = self.resolved_fork_target()
            if target == "us":
                out.append({"name": "fork_region", "status": "fail",
                            "detail": "resolved fork region is 'us' — the container-region "
                                      "default; fork VMs verified only in us-east-1 "
                                      "(forkpool.py, commit 156f98a)"})
            elif target != "us-east-1":
                out.append({"name": "fork_region", "status": "warn",
                            "detail": f"fork region {target!r}: fork snapshots verified only "
                                      "in us-east-1; misconfig degrades silently to the "
                                      "snapshot pool"})
            if not self.retrial_fork_snapshot:
                out.append({"name": "fork_snapshot", "status": "fail",
                            "detail": "RETRIAL_FORK_SNAPSHOT is empty — the container default "
                                      "snapshot rejects _experimental_fork"})
            if self.retrial_max_forks <= 0:
                out.append({"name": "max_forks", "status": "warn",
                            "detail": f"RETRIAL_MAX_FORKS={self.retrial_max_forks} <= 0 "
                                      "disables forking"})
        # NARRATE=1 with no key would degrade to silence with no other signal —
        # exactly the "silent degrade" class this module exists to make loud.
        if self.narrate_on and not self.elevenlabs_api_key:
            out.append({"name": "narration", "status": "warn",
                        "detail": "NARRATE=1 but ELEVENLABS_API_KEY is unset — "
                                  "the verdict autopsy will be silently skipped"})
        return out


def get_settings() -> Settings:
    """The ONLY constructor anyone calls — it can never raise.

    pydantic-core raises ValidationError at CONSTRUCTION on a malformed value
    (e.g. MAX_TRIALS=abc), and server.py constructs settings at import time; a
    naive `Settings()` there would kill boot before lifespan/preflight ever run,
    violating the charter ("server must still boot for replay demos"). So on a
    ValidationError we substitute the DEFAULT only for the offending fields —
    every VALID env var is preserved (explicit kwargs override the env source in
    pydantic-settings) — and record `_parse_error` so the typo becomes a RED
    `settings_parse` check (loud banner + doctor FAIL), never a boot crash.
    """
    try:
        return Settings()
    except ValidationError as e:
        bad = {err["loc"][0] for err in e.errors() if err.get("loc")}
        overrides = {f: Settings.model_fields[f].default
                     for f in bad if f in Settings.model_fields}
        try:
            s = Settings(**overrides)
        except ValidationError:
            # Pathological (a default is itself invalid, or a non-field loc):
            # fall to pure defaults, still without raising.
            s = Settings.model_construct()
        s._parse_error = "; ".join(
            f"{err['loc'][0] if err.get('loc') else '?'}={err.get('input')!r}: {err['msg']}"
            for err in e.errors())[:200]
        return s
