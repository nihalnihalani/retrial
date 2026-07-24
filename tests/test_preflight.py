"""Preflight: config_checks/run_preflight verdicts, and the shared live fork
smoke exercised against self-contained fakes (call ordering, leaf-first
teardown, cooperative budget abort, and honest fork-failure)."""
from types import SimpleNamespace

import pytest

from retrial.preflight import config_checks, live_fork_smoke, run_preflight


# ------------------------------ smoke fakes ------------------------------
class _Proc:
    def __init__(self, result=""):
        self.result = result
        self.execs = []

    def exec(self, cmd, timeout=None):
        self.execs.append((cmd, timeout))
        return SimpleNamespace(result=self.result)


class _Clone:
    def __init__(self, cid, result="42"):
        self.id = cid
        self.process = _Proc(result)
        self.pause_calls = 0

    def pause(self):
        self.pause_calls += 1


class _Ckpt:
    def __init__(self, cid):
        self.id = cid
        self.process = _Proc()
        self.start_calls = 0
        self.pause_calls = 0
        self.fork_calls = 0
        self._n = 0

    def start(self):
        self.start_calls += 1

    def pause(self):
        self.pause_calls += 1

    def _experimental_fork(self, name=None):
        self.fork_calls += 1
        self._n += 1
        return _Clone(f"{self.id}-clone-{self._n}")


class _Root:
    def __init__(self, rid, root_fork_fails=False):
        self.id = rid
        self.process = _Proc()
        self.fork_calls = 0
        self._fail = root_fork_fails
        self._n = 0

    def _experimental_fork(self, name=None):
        self.fork_calls += 1
        if self._fail:
            raise RuntimeError("root fork blew up")
        self._n += 1
        return _Ckpt(f"{self.id}-ckpt-{self._n}")


class SmokeClient:
    """Mocked Daytona client tailored to the preflight fork cycle. `deleted`
    records ids in client-deletion order (the leaf-first assertion reads it)."""

    def __init__(self, root_fork_fails=False):
        self.create_calls = 0
        self.deleted = []
        self.registry = {}
        self._root_fork_fails = root_fork_fails

    def create(self, params, timeout=None):
        self.create_calls += 1
        r = _Root(f"root-{self.create_calls}", root_fork_fails=self._root_fork_fails)
        self.registry[r.id] = r
        return r

    def get(self, sid):
        return self.registry.get(sid) or SimpleNamespace(id=sid)

    def delete(self, sb):
        self.deleted.append(getattr(sb, "id", str(sb)))


@pytest.fixture()
def keyed_env(monkeypatch):
    for k in ("DAYTONA_API_KEY", "DAYTONA_TARGET", "RETRIAL_FORK_TARGET",
              "RETRIAL_POOL_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ------------------------------ config checks ------------------------------
def test_no_key_fails(keyed_env):
    keyed_env.setenv("RETRIAL_POOL_BACKEND", "snapshot")
    checks = config_checks()
    dk = [c for c in checks if c["name"] == "daytona_api_key"][0]
    assert dk["status"] == "fail"
    assert run_preflight(live=False)["ok"] is False


def test_key_snapshot_ok_and_fork_region_fail(keyed_env):
    keyed_env.setenv("DAYTONA_API_KEY", "k")
    keyed_env.setenv("RETRIAL_POOL_BACKEND", "snapshot")
    res = run_preflight(live=False)
    assert res["ok"] is True
    assert res["live_checked"] is False and res["timings"] is None

    keyed_env.setenv("RETRIAL_POOL_BACKEND", "fork")
    keyed_env.setenv("DAYTONA_TARGET", "us")
    checks = config_checks()
    fr = [c for c in checks if c["name"] == "fork_region"][0]
    assert fr["status"] == "fail"
    assert run_preflight(live=False)["ok"] is False


def test_settings_parse_is_first_check(keyed_env):
    checks = config_checks()
    assert checks[0]["name"] == "settings_parse"


# ------------------------------ live smoke ------------------------------
def test_live_smoke_happy_path_ordering():
    client = SmokeClient()
    res = live_fork_smoke(client=client, budget_s=180)
    assert res["ok"] is True
    for k in ("create_s", "checkpoint_s", "fork_s", "exec_s",
              "teardown_s", "total_s"):
        assert k in res["timings"]
    # leaf-first teardown: clone, then checkpoint, then root.
    assert len(client.deleted) == 3
    assert "clone" in client.deleted[0]
    assert client.deleted[1].endswith("ckpt-1")
    assert client.deleted[2] == "root-1"


def test_live_smoke_budget_abort(monkeypatch):
    class Clock:
        def __init__(self):
            self.n = 0

        def __call__(self):
            self.n += 1
            # first three reads (t0, create-start, create-end) at 0; the
            # over-budget check after create jumps past the budget.
            return 0.0 if self.n <= 3 else 1e6

    monkeypatch.setattr("retrial.preflight.monotonic", Clock())
    client = SmokeClient()
    res = live_fork_smoke(client=client, budget_s=10)
    assert res["ok"] is False
    assert "budget" in res["reason"]
    # teardown still ran on what existed (only the root by then).
    assert client.deleted == ["root-1"]


def test_live_smoke_fork_failure_still_tears_down():
    client = SmokeClient(root_fork_fails=True)
    res = live_fork_smoke(client=client, budget_s=180)
    assert res["ok"] is False
    assert "fork" in res["reason"].lower()
    assert client.deleted == ["root-1"]   # root deleted despite the failure
