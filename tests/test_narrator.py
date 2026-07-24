"""Narrator tests: script honesty, CBR duration math, and the never-raise contract.

The bulk of these guard the ONE property that makes an audio channel safe to
put on a stage: the narration cannot say anything the verifier did not measure.
`build_script` is pure, so every claim it makes is asserted here directly —
in particular that a clean confirmation round is voiced as a Wilson upper
bound and NEVER as "zero percent" (the project's non-negotiable statistics
rule, which an audio channel is just as capable of violating as the UI).

The network is never touched: `synthesize` is stubbed everywhere. The one live
check that mattered (that ElevenLabs actually returns a decodable mp3 for a v3
request) was run against the real API during integration and is not repeated
per-test — a unit suite that needs a paid sponsor API to pass is a broken suite.
"""
import pytest

from retrial.events import EVENT_TYPES, EventBus
from retrial.narrator import Narrator, _mp3_duration, build_script


def _flaky_result(**over):
    """A FIXED run: 48% original, three eliminated, one confirmed clean."""
    base = {
        "verdict": "FIXED",
        "orig_verdict": "FLAKY",
        "orig_flake_rate": 0.48,
        "detect": {"flake_rate": 0.48, "wilson_ci": [0.34, 0.62],
                   "trials": 50, "fails": 24},
        "hypotheses": [
            {"id": "h1", "cause_class": "order_dependency", "flake_rate": 0.0,
             "wilson_ci": [0.0, 0.088], "trials": 40, "fails": 0, "model": "glm-5p2"},
            {"id": "h2", "cause_class": "shared_state", "flake_rate": 0.42,
             "wilson_ci": [0.28, 0.58], "trials": 40, "fails": 17, "model": "glm-5p1"},
        ],
        "winner": {"id": "h1", "cause_class": "order_dependency",
                   "flake_rate": 0.0, "model": "glm-5p2"},
        "confirmation": {"flake_rate": 0.0, "wilson_ci": [0.0, 0.0876],
                         "trials": 50, "fails": 0},
    }
    base.update(over)
    return base


# --------------------------- the statistics law ---------------------------
def test_clean_confirmation_is_never_voiced_as_zero_percent():
    """0/50 is spoken as its Wilson upper bound. This is THE rule an audio
    channel is most likely to break, because "zero percent" is the natural
    English phrasing and it is a lie the board never tells."""
    script = build_script(_flaky_result(), "test_dict_order.py")
    assert "zero failures in 50 reruns" in script
    assert "at most 8.8 percent, at 95 percent confidence" in script
    assert "0 percent" not in script
    assert "zero percent" not in script


def test_every_spoken_rate_carries_its_interval():
    script = build_script(_flaky_result(), "test_dict_order.py")
    # the detect rate is voiced with its CI, not bare
    assert "48 percent flake rate" in script
    assert "95 percent confidence interval 34 to 62 percent" in script


def test_script_only_speaks_measured_numbers():
    """Trials/fails come from the dossier, so a changed measurement changes the
    words. Guards against a template that hardcodes demo numbers."""
    script = build_script(
        _flaky_result(detect={"flake_rate": 0.9, "wilson_ci": [0.8, 0.96],
                              "trials": 30, "fails": 27}),
        "test_first_key.py")
    assert "30 reruns" in script and "failed 27 times" in script
    assert "50 reruns" not in script.split("confirmation round")[0]


# --------------------------- the emotional arc ----------------------------
def test_eliminated_are_hesitant_and_the_winner_is_confident():
    script = build_script(_flaky_result(), "test_dict_order.py")
    assert "[hesitant] h2" in script          # the loser
    assert "[confident] But h1 held." in script
    assert "[triumphant]" in script
    assert "glm-5p2" in script                # honest model attribution


def _zero_rate_quarantine():
    """The shape that caught both bugs live (run3, 2026-07-25): every candidate
    measured 0.0 fails but on too few trials, so the CI upper stayed above the
    threshold and nothing could be confirmed."""
    return {
        "verdict": "QUARANTINE", "orig_verdict": "FLAKY", "orig_flake_rate": 0.5,
        "detect": {"flake_rate": 0.5, "wilson_ci": [0.22, 0.78], "trials": 8, "fails": 4},
        "hypotheses": [
            {"id": "h1", "cause_class": "order_dependency", "flake_rate": 0.0,
             "wilson_ci": [0.0, 0.32], "trials": 8, "fails": 0, "model": "glm-5p2"},
            {"id": "h2", "cause_class": "order_dependency", "flake_rate": 0.0,
             "wilson_ci": [0.0, 0.32], "trials": 8, "fails": 0, "model": "glm-5p1"},
        ],
        "winner": None, "confirmation": None,
    }


def test_a_measured_zero_is_never_spoken_as_one_hundred_percent():
    """REGRESSION (found live, not in review): `h.get("flake_rate") or 1.0`
    treats a measured 0.0 as falsy and substituted 1.0, so the narration
    announced "flaked 100 percent of the time" for candidates the board showed
    at 0%. A fabricated number is the single worst thing an audio channel can
    emit, since nobody can diff speech against the screen in real time."""
    script = build_script(_zero_rate_quarantine(), "test_dict_order.py")
    assert "100 percent" not in script


def test_a_zero_fail_loser_is_eliminated_on_interval_width_not_a_bare_rate():
    """REGRESSION: losers were voiced as "still 0 percent. Eliminated." — both a
    bare-rate violation and incoherent, since a clean candidate is eliminated
    for CI WIDTH. The narration must say which."""
    script = build_script(_zero_rate_quarantine(), "test_dict_order.py")
    assert "still 0 percent" not in script
    assert "clean on 8 reruns, but the interval still reaches 32 percent" in script
    assert "Not proven. Eliminated." in script


def test_the_closest_candidate_carries_its_interval():
    script = build_script(_zero_rate_quarantine(), "test_dict_order.py")
    assert "zero failures in 8 reruns — at most 32.0 percent" in script
    assert "not tight enough to ship" in script


def test_quarantine_never_claims_a_fix():
    r = _flaky_result(verdict="QUARANTINE", winner=None, confirmation=None)
    script = build_script(r, "test_dict_order.py")
    assert "quarantine" in script.lower()
    assert "[triumphant]" not in script
    assert "fixed" not in script.lower()


# ------------------------- the non-flaky detect gates ---------------------
def test_always_failing_is_narrated_as_a_regression_not_a_flake():
    r = {"verdict": "REGRESSION", "orig_verdict": "ALWAYS_FAILING",
         "detect": {"trials": 40, "fails": 40, "flake_rate": 1.0,
                    "wilson_ci": [0.91, 1.0]}}
    script = build_script(r, "test_always_fails.py")
    assert "regression" in script.lower()
    assert "Fix the code, not the test." in script
    assert "tournament" in script.lower() and "No tournament was run" in script


@pytest.mark.parametrize("orig,expect", [
    ("STABLE", "already stable"),
    ("INCONCLUSIVE", "inconclusive"),
])
def test_other_baseline_gates_have_their_own_scripts(orig, expect):
    r = {"verdict": orig, "orig_verdict": orig,
         "detect": {"trials": 40, "fails": 0, "flake_rate": 0.0,
                    "wilson_ci": [0.0, 0.088]}}
    assert expect in build_script(r, "test_x.py").lower()


def test_underscores_are_not_read_aloud():
    script = build_script(_flaky_result(), "test_dict_order.py")
    assert "test dict order" in script
    assert "test_dict_order" not in script
    assert ".py" not in script


# ----------------------------- duration math ------------------------------
def test_mp3_duration_from_cbr_payload():
    """128 kbit/s CBR => 16000 bytes per second of audio."""
    assert _mp3_duration(b"\xff\xfb" + b"\x00" * (16_000 * 3 - 2)) == 3.0


def test_mp3_duration_excludes_the_id3_tag():
    """The ID3v2 size is syncsafe (7 bits/byte); counting it as audio is how
    this math drifts from ffprobe. 0x7F -> 127 bytes of tag + 10 header."""
    tag = b"ID3\x04\x00\x00" + b"\x00\x00\x00\x7f"
    audio = b"\x00" * 16_000
    assert _mp3_duration(tag + b"\x00" * 127 + audio) == 1.0


def test_mp3_duration_of_nothing_is_zero():
    assert _mp3_duration(b"") == 0.0


# --------------------------- the never-raise contract ---------------------
def test_narrate_is_a_noop_without_a_key(monkeypatch, tmp_path):
    """NARRATE=1 so the MISSING KEY is the only thing making this unavailable —
    otherwise the assertion passes for the wrong reason (the flag) and would
    keep passing even if the key check were deleted."""
    monkeypatch.setenv("NARRATE", "1")
    n = Narrator(api_key="", out_dir=tmp_path)
    assert n.available is False
    assert n.narrate(_flaky_result(), "t.py", "run-1") is None
    assert list(tmp_path.iterdir()) == []


def test_narrate_is_a_noop_when_switched_off(monkeypatch):
    monkeypatch.setenv("NARRATE", "0")
    n = Narrator(api_key="key-present")
    assert n.available is False


def test_a_dead_api_costs_the_audio_and_nothing_else(monkeypatch, tmp_path):
    """The whole point of the module's third rule: narration runs AFTER the
    verdict is published, so an ElevenLabs outage must degrade to silence."""
    monkeypatch.setenv("NARRATE", "1")
    n = Narrator(api_key="k", out_dir=tmp_path)
    monkeypatch.setattr(n, "synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("elevenlabs 429")))
    assert n.available is True
    assert n.narrate(_flaky_result(), "t.py", "run-1") is None   # no raise
    assert list(tmp_path.iterdir()) == []                        # no half-written file


def test_successful_narration_writes_the_mp3_and_announces_it(monkeypatch, tmp_path):
    monkeypatch.setenv("NARRATE", "1")
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    n = Narrator(bus=bus, api_key="k", out_dir=tmp_path)
    monkeypatch.setattr(n, "synthesize", lambda *a, **k: b"\xff\xfb" + b"\x00" * 15_998)

    payload = n.narrate(_flaky_result(), "test_dict_order.py", "run-abc")

    assert (tmp_path / "run-abc.mp3").read_bytes()[:2] == b"\xff\xfb"
    assert payload["url"] == "/narration/run-abc"
    assert payload["duration_s"] == 1.0
    assert payload["voice_id"] and payload["model_id"]
    assert [e["type"] for e in seen] == ["narration_ready"]
    # the transcript ships with the event so the UI can show what was said
    assert "test dict order" in payload["script"]


def test_narration_ready_is_a_registered_event_type():
    """Belt-and-braces next to the ast scan in test_events.py: the UI union and
    this registry must both know the type or the frame is silently dropped."""
    assert "narration_ready" in EVENT_TYPES
