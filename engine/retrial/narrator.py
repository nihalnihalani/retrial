"""Narrator: the flake autopsy as speech (ElevenLabs v3, OUTPUT only).

WHAT THIS IS: after a verdict is final, the run's evidence dossier is rendered
to a short spoken autopsy — hesitant over the hypotheses that were eliminated,
confident over the one that survived confirmation. It is the audio face of the
same numbers the board shows, and nothing else. Retrial never takes voice INPUT
(no microphone, no STT, no voice control); this module only ever writes an mp3.

THREE RULES THIS MODULE IS BUILT AROUND:

1. THE SCRIPT IS DERIVED, NOT GENERATED. The narration text is templated
   deterministically from the result dict — no LLM sits between the dossier and
   the words. An LLM narrator could hallucinate a number that contradicts the
   board mid-pitch; a template cannot. The only numbers spoken are numbers the
   verifier measured.

2. THE WILSON LAW APPLIES TO SPEECH TOO. A rate is never voiced bare. "0 of 50"
   is spoken as "at most 8.8 percent, at 95 percent confidence", never as
   "zero percent" — the same rule the UI and the PR body obey. See `_ci_phrase`.

3. IT CAN NEVER COST A VERDICT. Narration runs after `tournament_done`, off the
   run thread, and every public entry point swallows its own exceptions. A dead
   key, a 429, a network partition, a full disk — all degrade to "no audio",
   never to a failed run. `NARRATE` defaults to 0, so the demo path is
   byte-identical unless someone opts in.
"""
import re
import threading
import time
from pathlib import Path

import requests

from .settings import get_settings

_API_ROOT = "https://api.elevenlabs.io/v1"
# mp3_44100_128 is a CONSTANT-bitrate format, which is what makes `_mp3_duration`
# a two-line computation instead of a frame-walking parser. Changing this format
# silently invalidates that duration math — change both or neither.
_OUTPUT_FORMAT = "mp3_44100_128"
_BITRATE = 128_000
# Voice + model default to slugs VERIFIED live against this account on
# 2026-07-25 (`GET /v1/models`, `GET /v1/voices`) — the same "never hardcode an
# unverified slug" discipline the Fireworks model list follows. Both are
# env-overridable; neither is guessed.
_DEFAULT_VOICE = "XrExE9yKIg1WjnnlVkGX"   # Matilda — "knowledgable, professional"
_DEFAULT_MODEL = "eleven_v3"              # the only model with audio-tag support


def _pct(x):
    """0.4783 -> '48'. Rates are spoken as whole percents; the CI carries the
    precision, so a spoken '47.83 percent' would be false precision out loud."""
    return f"{round(x * 100)}"


def _ci_phrase(rate, ci, trials, fails):
    """Render a measured rate as a spoken clause that always carries its CI.

    This is the audio form of the project's non-negotiable statistics rule. A
    clean confirmation round is the case that matters: 0 fails does NOT become
    "zero percent" — it becomes the Wilson upper bound, which is the only
    honest thing 40-50 trials can support.
    """
    if ci:
        low, high = ci
    else:                                   # defensive: never voice a bare rate
        low, high = 0.0, 1.0
    if fails == 0:
        return (f"zero failures in {trials} reruns — "
                f"at most {high * 100:.1f} percent, at 95 percent confidence")
    return (f"{fails} failures in {trials} reruns — {_pct(rate)} percent, "
            f"95 percent confidence interval {_pct(low)} to {_pct(high)} percent")


def _spoken_name(test_name):
    """'test_dict_order.py' -> 'test dict order'. Underscores and the .py suffix
    are read aloud literally by TTS ('test underscore dict') without this."""
    return re.sub(r"[_\-]+", " ", Path(test_name).stem)


def _spoken_cause(cause_class):
    """'order_dependency' -> 'order dependency'."""
    if not cause_class:
        return "an unclassified cause"
    return re.sub(r"[_\-]+", " ", str(cause_class))


def _mp3_duration(data):
    """Seconds of audio, from CBR bitrate and payload size.

    Valid ONLY because we pin `_OUTPUT_FORMAT` to a constant-bitrate mp3. The
    ID3v2 tag is metadata, not audio, so its length is subtracted first — it is
    a syncsafe 32-bit size at bytes 6..9 (7 significant bits per byte), plus the
    10-byte header. Verified against ffprobe on a real response: computed 7.28s
    vs ffprobe 7.280s.
    """
    if not data:
        return 0.0
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        b = data[6:10]
        offset = 10 + ((b[0] & 0x7F) << 21 | (b[1] & 0x7F) << 14
                       | (b[2] & 0x7F) << 7 | (b[3] & 0x7F))
    return round(max(0, len(data) - offset) * 8 / _BITRATE, 2)


def build_script(result, test_name):
    """Turn a coordinator result dict into v3 narration text. Pure and testable.

    Audio tags (`[serious]`, `[hesitant]`, ...) are v3's emotional-direction
    markers: they steer delivery and are not spoken. The emotional arc is
    dictated by the evidence — hesitant over eliminations, confident only where
    a confirmation round actually earned it.
    """
    verdict = result.get("verdict")
    orig_verdict = result.get("orig_verdict")
    detect = result.get("detect") or {}
    name = _spoken_name(test_name)

    # --- the non-flaky gates: no tournament ran, so there is no race to narrate.
    # Each mirrors the detect-gate's terminal reason exactly.
    if orig_verdict == "ALWAYS_FAILING":
        return (f"[serious] Autopsy for {name}. This test did not flake — it "
                f"failed every single one of {detect.get('trials', 0)} reruns. "
                f"[flat] That is not a flaky test, that is a regression. "
                f"[serious] No tournament was run. Fix the code, not the test.")
    if orig_verdict == "STABLE":
        return (f"[calm] Autopsy for {name}. Across {detect.get('trials', 0)} "
                f"reruns on the swarm, this test never failed. "
                f"[confident] It is already stable. Nothing to fix.")
    if orig_verdict == "INCONCLUSIVE":
        return (f"[hesitant] Autopsy for {name}. After "
                f"{detect.get('trials', 0)} reruns, the confidence interval is "
                f"still too wide to call this test flaky or stable. "
                f"[serious] The honest verdict is: inconclusive. More trials needed.")
    if verdict == "ERROR" or orig_verdict == "ERROR":
        return (f"[serious] The run for {name} did not complete. "
                f"No verdict was reached, and nothing was shipped.")

    # --- the flaky path: detect established a real flake rate, so a tournament ran.
    orig_rate = result.get("orig_flake_rate")
    orig_rate = 0.0 if orig_rate is None else orig_rate
    parts = [
        f"[serious] Flake autopsy. Subject: {name}. "
        f"[curious] Under {detect.get('trials', 0)} reruns across the sandbox "
        f"swarm, this test failed {detect.get('fails', 0)} times — a "
        f"{_pct(orig_rate)} percent flake rate, 95 percent confidence interval "
        f"{_pct((detect.get('wilson_ci') or [0, 1])[0])} to "
        f"{_pct((detect.get('wilson_ci') or [0, 1])[1])} percent. "
        f"[flat] It was not broken. It was lying."
    ]

    hypotheses = result.get("hypotheses") or []
    winner = result.get("winner")
    winner_id = (winner or {}).get("id")
    losers = [h for h in hypotheses if h.get("id") != winner_id]

    if hypotheses:
        parts.append(
            f"[curious] {len(hypotheses)} competing "
            f"{'hypothesis' if len(hypotheses) == 1 else 'hypotheses'} entered "
            f"the tournament, each re-verified across the swarm."
        )
    for h in losers[:3]:                     # cap: past three, the arc drags
        # A loser is voiced WITH its interval, because "still 0 percent.
        # Eliminated." is both incoherent and a bare-rate violation: a candidate
        # with zero observed failures is eliminated for CI WIDTH, not for a high
        # rate, and the narration has to say which.
        hi = (h.get("wilson_ci") or [0.0, 1.0])[1]
        if h.get("fails") == 0:
            parts.append(
                f"[hesitant] {h.get('id')}, {_spoken_cause(h.get('cause_class'))} — "
                f"clean on {h.get('trials', 0)} reruns, but the interval still "
                f"reaches {hi * 100:.0f} percent. Not proven. Eliminated."
            )
        else:
            parts.append(
                f"[hesitant] {h.get('id')}, {_spoken_cause(h.get('cause_class'))} — "
                f"still {_pct(h.get('flake_rate'))} percent. Eliminated."
            )

    if verdict == "FIXED" and winner:
        confirm = result.get("confirmation") or {}
        model = winner.get("model") or "the panel"
        parts.append(
            f"[confident] But {winner.get('id')} held. "
            f"{_spoken_cause(winner.get('cause_class'))}, authored by {model}. "
            f"On a fresh confirmation round: "
            f"{_ci_phrase(confirm.get('flake_rate', 0), confirm.get('wilson_ci'), confirm.get('trials', 0), confirm.get('fails', 0))}."
        )
        parts.append(
            f"[triumphant] Verdict: fixed. From {_pct(orig_rate)} percent, "
            f"down to noise — and proven, not asserted."
        )
    else:
        # `or 1.0` here once turned a MEASURED 0.0 into 1.0 (0.0 is falsy) and the
        # narration announced "100 percent" for a candidate the board showed at
        # 0% — a fabricated number, the one thing this module must never do.
        # Every rate lookup below is an explicit None check for that reason.
        rated = [h for h in hypotheses if h.get("flake_rate") is not None]
        best = min(rated, key=lambda h: h["flake_rate"]) if rated else None
        parts.append(
            "[hesitant] No hypothesis stabilized the test."
            + (f" The closest candidate: "
               f"{_ci_phrase(best['flake_rate'], best.get('wilson_ci'), best.get('trials', 0), best.get('fails', 0))}"
               f" — not tight enough to ship." if best is not None else "")
        )
        parts.append(
            "[serious] Verdict: quarantine — with the full evidence dossier "
            "attached. The run does not dead-end, and nothing unproven ships."
        )
    return " ".join(parts)


class Narrator:
    """Renders a run's verdict to speech. Never raises into the caller."""

    def __init__(self, bus=None, out_dir=None, api_key=None,
                 voice_id=None, model_id=None):
        s = get_settings()
        self.bus = bus
        self._key = api_key if api_key is not None else s.elevenlabs_api_key
        self.voice_id = voice_id or s.elevenlabs_voice_id or _DEFAULT_VOICE
        self.model_id = model_id or s.elevenlabs_model_id or _DEFAULT_MODEL
        self.out_dir = Path(out_dir) if out_dir else narration_dir()
        self.enabled = s.narrate_on

    @property
    def available(self):
        """True when narration is both switched on AND has a key to use.

        Two separate conditions on purpose: NARRATE=1 with no key is a
        misconfiguration worth reporting in preflight, not a silent no-op.
        """
        return bool(self.enabled and self._key)

    def _emit(self, event_type, payload):
        if self.bus is not None:
            try:
                self.bus.emit(event_type, payload)
            except Exception:
                pass

    def synthesize(self, script, timeout=120):
        """POST the script to ElevenLabs; return mp3 bytes. Raises on failure —
        callers that must not fail (narrate) wrap this."""
        r = requests.post(
            f"{_API_ROOT}/text-to-speech/{self.voice_id}",
            headers={"xi-api-key": self._key, "Content-Type": "application/json"},
            params={"output_format": _OUTPUT_FORMAT},
            json={"text": script, "model_id": self.model_id},
            timeout=timeout,
        )
        if not r.ok:
            raise RuntimeError(f"elevenlabs {r.status_code}: {r.text[:200]}")
        return r.content

    def narrate(self, result, test_name, run_id):
        """Render, synthesize, persist, and announce. Returns the payload dict
        on success, None on ANY failure or when narration is off.

        The broad `except Exception` is deliberate and is the whole point of the
        module's third rule: this runs after the verdict is already final and
        published, so there is no failure here worth propagating to a caller.
        """
        if not self.available:
            return None
        try:
            script = build_script(result, test_name)
            t0 = time.monotonic()
            audio = self.synthesize(script)
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{run_id}.mp3"
            path.write_bytes(audio)
            payload = {
                "run_id": run_id,
                "url": f"/narration/{run_id}",
                "duration_s": _mp3_duration(audio),
                "synth_s": round(time.monotonic() - t0, 2),
                "bytes": len(audio),
                "script": script,
                "voice_id": self.voice_id,
                "model_id": self.model_id,
            }
            self._emit("narration_ready", payload)
            return payload
        except Exception:
            return None

    def narrate_async(self, result, test_name, run_id):
        """Fire narration on its own daemon thread.

        Called from the run thread's tail. TTS is a multi-second network round
        trip; doing it inline would park the run thread (and the pool reset
        behind it) on a sponsor API. The verdict is already on the wire by the
        time this starts, so nothing waits on it.
        """
        if not self.available:
            return None
        t = threading.Thread(
            target=self.narrate, args=(result, test_name, run_id), daemon=True)
        t.start()
        return t


def narration_dir():
    """Where mp3s land: `.retrial/narration/` (already gitignored)."""
    return Path(__file__).resolve().parents[2] / ".retrial" / "narration"
