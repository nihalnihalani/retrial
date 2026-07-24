"""scripts/live_smoke.py fail-fast guard, exercised in-process (no network).

The script SKIPs with exit code 2 when DAYTONA_API_KEY is absent, and it must
do so BEFORE constructing any Daytona client — so a keyless CI/dispatch never
touches the SDK. We bomb the Daytona ctor to prove the SKIP path never reaches
it."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_live_smoke():
    path = ROOT / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skip_exit_2_without_key_never_touches_sdk(monkeypatch):
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

    from retrial import forkpool as forkpool_mod

    class Bomb:
        def __init__(self, *a, **k):
            raise AssertionError("Daytona must NOT be constructed on the SKIP path")

    monkeypatch.setattr(forkpool_mod, "Daytona", Bomb)

    mod = _load_live_smoke()
    # No key => SKIP with exit code 2, before any SDK construction (Bomb unfired).
    assert mod.main() == 2
