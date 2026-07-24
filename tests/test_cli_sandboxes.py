"""CLI `sandboxes` / `reap` tests — the thin HTTP clients of the running engine.

`fetch` is injected so the formatting + exit-code logic is exercised without a
live server: canned JSON for the happy paths, a raised URLError for the
engine-unreachable path, and a 409 body for the run-active refusal."""
import argparse
import json
import urllib.error

from retrial.cli import _cmd_sandboxes, _cmd_reap


_SNAP = {
    "sandboxes": [
        {"id": "root-abc123def456", "role": "root", "backend": "fork",
         "state": "warm", "parent_id": None, "created_ts": 1.0,
         "updated_ts": 5.0, "exec_count": 0, "current_cmd": None},
        {"id": "clone-9", "role": "trial-clone", "backend": "fork",
         "state": "running-cmd", "parent_id": "ckpt-1", "created_ts": 2.0,
         "updated_ts": 5.0, "exec_count": 7,
         "current_cmd": "python3 /tmp/seed.py"},
    ],
    "counts": {"live": 2, "total_ever": 9, "destroyed": 7},
    "lineage": {"ckpt-1": ["clone-9"]},
    "est_resources": {"live_sandboxes": 2, "note": "count-based estimate"},
}


def _args(**kw):
    ns = argparse.Namespace(url="http://localhost:8000", json=False, force=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_sandboxes_table_lists_rows_and_totals(capsys):
    def fetch(method, url, timeout=10):
        assert method == "GET" and url.endswith("/sandboxes")
        return 200, _SNAP

    rc = _cmd_sandboxes(_args(), fetch=fetch)
    assert rc == 0
    out = capsys.readouterr().out
    # Header + both rows (id/role/state present) + the totals line.
    assert "ROLE" in out and "CURRENT_CMD" in out
    assert "root" in out and "trial-clone" in out
    assert "running-cmd" in out
    assert "clone-9" in out
    assert "live 2 · total-ever 9 · destroyed 7" in out


def test_sandboxes_json_round_trips(capsys):
    def fetch(method, url, timeout=10):
        return 200, _SNAP

    rc = _cmd_sandboxes(_args(json=True), fetch=fetch)
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == _SNAP


def test_sandboxes_unreachable_exits_2(capsys):
    def fetch(method, url, timeout=10):
        raise urllib.error.URLError("Connection refused")

    rc = _cmd_sandboxes(_args(), fetch=fetch)
    assert rc == 2
    err = capsys.readouterr().err
    assert "not reachable" in err and "uvicorn retrial.server:app" in err


def test_reap_success_reports_count(capsys):
    def fetch(method, url, timeout=10):
        assert method == "POST"
        return 200, {"status": "destroyed", "count": 5, "forced": False,
                     "bisector_cancelled": False, "live": 0, "total_ever": 5,
                     "destroyed": 5}

    rc = _cmd_reap(_args(), fetch=fetch)
    assert rc == 0
    assert "destroyed 5 sandboxes" in capsys.readouterr().out


def test_reap_force_passes_query_param():
    seen = {}

    def fetch(method, url, timeout=10):
        seen["url"] = url
        return 200, {"count": 1, "bisector_cancelled": True}

    _cmd_reap(_args(force=True), fetch=fetch)
    assert seen["url"].endswith("/sandboxes/destroy_all?force=1")


def test_reap_409_exits_1(capsys):
    def fetch(method, url, timeout=10):
        return 409, {"detail": "a run is active — pass ?force=1 to cancel it and reap"}

    rc = _cmd_reap(_args(), fetch=fetch)
    assert rc == 1
    assert "a run is active" in capsys.readouterr().err


def test_reap_unreachable_exits_2(capsys):
    def fetch(method, url, timeout=10):
        raise urllib.error.URLError("Connection refused")

    rc = _cmd_reap(_args(), fetch=fetch)
    assert rc == 2
    assert "not reachable" in capsys.readouterr().err
