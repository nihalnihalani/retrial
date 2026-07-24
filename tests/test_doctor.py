"""`retrial doctor`: PASS/WARN/FAIL rendering, exit codes (warns never fail),
--json round-trip, and --live forwarding — all via an injected preflight_fn so
no network is touched."""
import json
from types import SimpleNamespace

from retrial.cli import _cmd_doctor


def _args(live=False, as_json=False):
    return SimpleNamespace(live=live, json=as_json)


def _canned(ok, checks, timings=None, live_checked=False):
    return {"ok": ok, "live_checked": live_checked, "checks": checks,
            "timings": timings}


def test_pass_exit_zero(capsys):
    res = _canned(True, [{"name": "daytona_api_key", "status": "pass",
                          "detail": "present"}])
    rc = _cmd_doctor(_args(), preflight_fn=lambda live: res)
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS  daytona_api_key" in out
    assert "doctor: OK" in out


def test_warn_does_not_fail(capsys):
    res = _canned(True, [{"name": "auth", "status": "warn",
                          "detail": "RETRIAL_AUTH_TOKEN set"}])
    rc = _cmd_doctor(_args(), preflight_fn=lambda live: res)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN  auth" in out
    assert "doctor: OK" in out


def test_fail_exit_one(capsys):
    res = _canned(False, [{"name": "daytona_api_key", "status": "fail",
                           "detail": "missing"}])
    rc = _cmd_doctor(_args(), preflight_fn=lambda live: res)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL  daytona_api_key" in out
    assert "doctor: FAILED (1 failing checks)" in out


def test_json_round_trips(capsys):
    res = _canned(False, [{"name": "x", "status": "fail", "detail": "d"}])
    rc = _cmd_doctor(_args(as_json=True), preflight_fn=lambda live: res)
    out = capsys.readouterr().out
    assert rc == 1
    assert json.loads(out) == res


def test_live_flag_forwarded(capsys):
    seen = {}

    def fake(live):
        seen["live"] = live
        return _canned(True, [{"name": "live_smoke", "status": "pass",
                               "detail": "ok"}],
                       timings={"create_s": 1.0, "total_s": 2.0},
                       live_checked=True)

    rc = _cmd_doctor(_args(live=True), preflight_fn=fake)
    out = capsys.readouterr().out
    assert rc == 0
    assert seen.get("live") is True
    assert "timings:" in out and "total 2.0s" in out
