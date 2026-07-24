"""RunHistory tests: SQLite roundtrip, the never-break-a-run contract (a write
into an unwritable/garbage path is a swallowed no-op, a read of a
missing/corrupt db is []), schema idempotence across instances, and RETRIAL_DB
resolution through get_settings. No server, no network."""
from retrial.history import RunHistory


def test_roundtrip_newest_first_and_limit(tmp_path):
    h = RunHistory(db_path=str(tmp_path / "h.db"))
    h.record("tournament", "test_a.py", "FIXED", orig_flake_rate=0.4,
             final_flake_rate=0.0, winner_model="qwen",
             braintrust_url="https://bt/x", started_at=1.0, finished_at=10.0)
    h.record("bisect", "suite_b", "POLLUTER:test_x.py", orig_flake_rate=0.1,
             final_flake_rate=0.5, started_at=2.0, finished_at=20.0)
    h.record("tournament", "test_c.py", "QUARANTINE", started_at=3.0,
             finished_at=30.0)

    rows = h.recent()
    assert [r["test_name"] for r in rows] == ["test_c.py", "suite_b", "test_a.py"]
    top = rows[0]
    # All declared columns present and correctly typed.
    for k in ("id", "kind", "test_name", "verdict", "orig_flake_rate",
              "final_flake_rate", "winner_model", "braintrust_url",
              "started_at", "finished_at"):
        assert k in top
    assert rows[2]["kind"] == "tournament"
    assert rows[2]["winner_model"] == "qwen"
    assert isinstance(rows[2]["orig_flake_rate"], float)
    # Limit honored.
    assert len(h.recent(limit=2)) == 2


def test_record_never_raises_on_unwritable_path():
    # /dev/null is a file: mkdir of a child path fails, and @_safe swallows it.
    h = RunHistory(db_path="/dev/null/nope/h.db")
    assert h.record("tournament", "t.py", "FIXED") is None   # no raise
    assert h.recent() == []                                   # missing db => []


def test_schema_idempotent_across_instances_and_corrupt_file(tmp_path):
    db = str(tmp_path / "shared.db")
    RunHistory(db_path=db).record("tournament", "one.py", "FIXED",
                                  finished_at=1.0)
    RunHistory(db_path=db).record("bisect", "two", "INCONCLUSIVE",
                                  finished_at=2.0)
    assert len(RunHistory(db_path=db).recent()) == 2

    # A pre-corrupted (non-sqlite) file reads as [], never raises.
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database, just noise \x00\x01\x02")
    assert RunHistory(db_path=str(junk)).recent() == []


def test_retrial_db_env_honored_by_default_ctor(tmp_path, monkeypatch):
    env_db = tmp_path / "env.db"
    monkeypatch.setenv("RETRIAL_DB", str(env_db))
    h = RunHistory()   # db_path None -> resolved from get_settings at call time
    h.record("tournament", "envtest.py", "FIXED", finished_at=5.0)
    assert env_db.exists()
    rows = h.recent()
    assert len(rows) == 1 and rows[0]["test_name"] == "envtest.py"
