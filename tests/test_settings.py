"""Typed Settings: defaults, env roundtrips, flag semantics, empty-string
normalization, the problems() coherence matrix, the resolved-chain logic, the
crash-proof parse-failure fallback, AND the two enforcement scans (no direct
os.environ reads and no bare Settings() construction outside settings.py)."""
import ast
from pathlib import Path

import pytest

from retrial import settings as sm
from retrial.settings import Settings, get_settings

_ENGINE_DIR = Path(sm.__file__).resolve().parent

# Every env var this module owns — cleared before a defaults probe so a stray
# ambient var can't make the defaults test lie.
_ALL_ENV = [
    "DAYTONA_API_KEY", "DAYTONA_TARGET", "RETRIAL_FORK_TARGET",
    "RETRIAL_FORK_SNAPSHOT", "RETRIAL_FORK_BOOTSTRAP_CMD", "RETRIAL_POOL_BACKEND",
    "RETRIAL_MAX_FORKS", "AUTO_DELETE_MIN", "MAX_TRIALS", "CONC",
    "TOURNAMENT_CONC", "THRESHOLD", "ISOLATION", "PREWARM", "HERMETIC_PREWARM",
    "PRSMITH", "PROMOTE_GATE", "HERMETIC", "LEDGER", "RETRIAL_PREFLIGHT_LIVE",
    "FIREWORKS_API_KEY", "FIREWORKS_MODELS", "BRAINTRUST_API_KEY", "RETRIAL_REPO",
    "GENOME_PATH", "RETRIAL_EXEC_HISTORY", "RETRIAL_DESTROYED_RETAIN",
    "RETRIAL_PREVIEW_PORT", "RETRIAL_AUTH_TOKEN",
    "RETRIAL_EST_RATE_PER_SANDBOX_HOUR", "RETRIAL_DB", "HOST", "PORT",
]


@pytest.fixture()
def clean_env(monkeypatch):
    for k in _ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_defaults(clean_env):
    s = Settings()
    assert s.retrial_pool_backend == "snapshot"
    assert s.retrial_fork_snapshot == "daytona-vm-small"
    assert s.retrial_max_forks == 64
    assert s.auto_delete_min == 60
    assert s.retrial_destroyed_retain == 50
    assert s.retrial_exec_history == 20
    assert s.max_trials is None and s.conc is None and s.threshold is None
    assert s.retrial_auth_token is None
    assert s.retrial_est_rate_per_sandbox_hour is None
    assert s.host == "127.0.0.1" and s.port == 8000


def test_env_override_roundtrip_one_of_each_type(clean_env):
    clean_env.setenv("RETRIAL_FORK_SNAPSHOT", "vm-big")          # str
    clean_env.setenv("RETRIAL_MAX_FORKS", "8")                   # int
    clean_env.setenv("RETRIAL_EST_RATE_PER_SANDBOX_HOUR", "0.5")  # float
    clean_env.setenv("PROMOTE_GATE", "0")                        # flag
    clean_env.setenv("DAYTONA_API_KEY", "sk-xyz")               # optional
    s = get_settings()
    assert s.retrial_fork_snapshot == "vm-big"
    assert s.retrial_max_forks == 8
    assert s.retrial_est_rate_per_sandbox_hour == 0.5
    assert s.promote_gate_on is False
    assert s.daytona_api_key == "sk-xyz"


def test_flag_semantics_pinned(clean_env):
    # PRSMITH="" is TRUTHY under the old `!= "0"` rule — pin it forever.
    clean_env.setenv("PRSMITH", "")
    assert get_settings().prsmith_on is True
    clean_env.setenv("PROMOTE_GATE", "0")
    assert get_settings().promote_gate_on is False
    clean_env.delenv("PROMOTE_GATE", raising=False)
    assert get_settings().promote_gate_on is True   # documented default ON
    clean_env.delenv("PRSMITH", raising=False)
    assert get_settings().prsmith_on is False        # documented default OFF


def test_empty_string_normalization(clean_env):
    clean_env.setenv("DAYTONA_TARGET", "")
    s = get_settings()
    assert s.daytona_target is None
    assert s.resolved_pool_target() == "us"


def test_problems_matrix(clean_env):
    # fork + DAYTONA_TARGET=us -> fail fork_region
    clean_env.setenv("RETRIAL_POOL_BACKEND", "fork")
    clean_env.setenv("DAYTONA_TARGET", "us")
    clean_env.setenv("DAYTONA_API_KEY", "k")
    names = {p["name"]: p for p in get_settings().problems()}
    assert names["fork_region"]["status"] == "fail"

    # fork + us-east-1 + key -> no fork problems
    clean_env.setenv("DAYTONA_TARGET", "us-east-1")
    probs = {p["name"] for p in get_settings().problems()}
    assert "fork_region" not in probs and "fork_snapshot" not in probs

    # snapshot backend -> no fork checks at all
    clean_env.setenv("RETRIAL_POOL_BACKEND", "snapshot")
    probs = {p["name"] for p in get_settings().problems()}
    assert "fork_region" not in probs and "fork_snapshot" not in probs


def test_resolved_fork_target_chain(clean_env):
    clean_env.setenv("RETRIAL_FORK_TARGET", "eu")
    clean_env.setenv("DAYTONA_TARGET", "us")
    assert get_settings().resolved_fork_target() == "eu"       # fork_target wins
    clean_env.delenv("RETRIAL_FORK_TARGET", raising=False)
    assert get_settings().resolved_fork_target() == "us"       # then daytona_target
    clean_env.delenv("DAYTONA_TARGET", raising=False)
    assert get_settings().resolved_fork_target() == "us-east-1"  # then the default


def test_parse_failure_fallback_preserves_valid_env(clean_env):
    # A typo'd numeric must NOT crash and must NOT discard a valid sibling var.
    clean_env.setenv("MAX_TRIALS", "abc")
    clean_env.setenv("RETRIAL_POOL_BACKEND", "fork")
    clean_env.setenv("DAYTONA_API_KEY", "k")
    s = get_settings()                       # must not raise
    assert s.max_trials is None              # default substituted for the bad one
    assert s.retrial_pool_backend == "fork"  # valid env preserved through fallback
    assert s._parse_error and "max_trials" in s._parse_error
    probs = s.problems()
    assert probs[0]["name"] == "settings_parse" and probs[0]["status"] == "fail"

    from retrial.preflight import run_preflight
    res = run_preflight(live=False)
    assert res["ok"] is False
    assert any(c["name"] == "settings_parse" and c["status"] == "fail"
               for c in res["checks"])


def test_parse_failure_pathological_double_fault(clean_env):
    # Even if BOTH Settings() and Settings(**overrides) blow up, get_settings
    # falls to model_construct defaults and never raises.
    RealSettings = sm.Settings
    try:
        RealSettings(retrial_max_forks="notanint")
        raise AssertionError("expected ValidationError")
    except sm.ValidationError as e:
        err = e

    class FakeSettings(RealSettings):
        def __init__(self, **kw):
            raise err

    clean_env.setattr(sm, "Settings", FakeSettings)
    s = sm.get_settings()                    # must not raise
    assert s is not None
    assert s._parse_error is not None


def test_no_direct_environ_reads_outside_settings():
    """THE ENFORCEMENT SCAN: grep is not evidence. ast-walk every
    engine/retrial/*.py (except settings.py) and fail on any `os.environ`
    attribute node (covers .get, [], `in`) or any bare `Settings(` call —
    everyone must go through the crash-proof get_settings()."""
    offenders_env = []
    offenders_ctor = []
    for py in sorted(_ENGINE_DIR.glob("*.py")):
        if py.name == "settings.py":
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "environ"
                    and isinstance(node.value, ast.Name) and node.value.id == "os"):
                offenders_env.append(f"{py.name}:{node.lineno}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Settings"):
                offenders_ctor.append(f"{py.name}:{node.lineno}")
    assert not offenders_env, f"os.environ read outside settings.py: {offenders_env}"
    assert not offenders_ctor, f"bare Settings() outside settings.py: {offenders_ctor}"
