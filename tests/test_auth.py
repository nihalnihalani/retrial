"""Optional bearer auth gate (RETRIAL_AUTH_TOKEN). Unset (default) => behavior
identical to today, no 401s anywhere. Set => the five mutating endpoints 401
without a valid Bearer token and fall through to their NORMAL status with one;
read-only endpoints and /ws stay open in both modes."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


# Each mutating endpoint + a deterministic NORMAL (non-auth) status reached with
# a valid/absent token: tournament 404 (seed missing), bisect 400 (snapshot
# backend, no fork), promote 404 (nothing pending), destroy_all 200 (no run),
# destroy_one 404 (unknown id).
MUTATING = {
    "tournament": 404,
    "bisect": 400,
    "promote": 404,
    "destroy_all": 200,
    "destroy_one": 404,
}


def _call(client, name, headers=None):
    h = headers or {}
    if name == "tournament":
        return client.post("/tournament",
                           json={"seed_path": "seeds/__no_such_seed__.py"}, headers=h)
    if name == "bisect":
        return client.post("/bisect",
                           json={"suite_dir": "seeds/__no_such_suite__"}, headers=h)
    if name == "promote":
        return client.post("/promote", json={"approve": True}, headers=h)
    if name == "destroy_all":
        return client.post("/sandboxes/destroy_all", headers=h)
    if name == "destroy_one":
        return client.delete("/sandboxes/__unknown__", headers=h)
    raise AssertionError(name)


@pytest.fixture()
def server(monkeypatch):
    from retrial import server as server_mod
    monkeypatch.delenv("RETRIAL_AUTH_TOKEN", raising=False)
    # Snapshot backend so /bisect returns its own 400 (not fork) — the auth gate
    # sits IN FRONT of that, so a bad token still 401s first.
    monkeypatch.setenv("RETRIAL_POOL_BACKEND", "snapshot")
    with server_mod._run_lock:
        server_mod._running.update(active=False, test_name=None)
        server_mod._pending["promotion"] = None
    return server_mod, TestClient(server_mod.app)


@pytest.mark.parametrize("name,expected", list(MUTATING.items()))
def test_token_unset_behaves_as_today(server, name, expected):
    server_mod, client = server
    r = _call(client, name)
    assert r.status_code == expected            # never 401 when unset


@pytest.mark.parametrize("name,expected", list(MUTATING.items()))
def test_token_set_gates_each_mutating_endpoint(server, name, expected, monkeypatch):
    server_mod, client = server
    monkeypatch.setenv("RETRIAL_AUTH_TOKEN", "s3cret")
    # No header -> 401.
    assert _call(client, name).status_code == 401
    # Wrong token -> 401.
    assert _call(client, name,
                 {"Authorization": "Bearer nope"}).status_code == 401
    # Correct token -> the endpoint's own normal status (auth must not mask it).
    assert _call(client, name,
                 {"Authorization": "Bearer s3cret"}).status_code == expected


def test_read_only_endpoints_stay_open_with_token(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setenv("RETRIAL_AUTH_TOKEN", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/sandboxes").status_code == 200
    assert client.get("/preflight").status_code == 200
    assert client.get("/genome").status_code == 200


def test_ws_stays_open_with_token(server, monkeypatch):
    server_mod, client = server
    monkeypatch.setenv("RETRIAL_AUTH_TOKEN", "s3cret")
    with client.websocket_connect("/ws"):
        pass   # connecting at all proves /ws is not gated
