"""voice.py — ElevenLabs "flake autopsy" narration (OUTPUT ONLY, disclosed).

Turns a completed tournament result into a short spoken verdict and writes an
mp3 the server serves at /voice/<name>. Every number in the script is read from
the real result — never invented — to honor the project's honesty rules.

Design constraints (match CLAUDE.md):
  - OUTPUT only. This module never takes voice INPUT and never listens.
  - Best-effort: a synthesis failure returns None and never raises into the run.
  - Opt-in via env VOICE (default off) so a normal run adds no latency or credit.
  - Disclosed: the narration is a post-verdict summary, not a "live" voice.

The default model (eleven_flash_v2_5) does not support inline audio tags, so the
script is plain prose — bracketed emotion tags would otherwise be read aloud.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

import httpx

# mp3s land here; server.py serves this directory read-only at GET /voice/<name>.
VOICE_DIR = Path(__file__).resolve().parents[2] / "voice_out"
_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{vid}"
_DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"          # ElevenLabs "George" (from .env)
_DEFAULT_MODEL = "eleven_flash_v2_5"


def _pct(x) -> str:
    try:
        return f"{round(float(x) * 100)} percent"
    except (TypeError, ValueError):
        return "an unknown"


def build_script(result: dict, test_name: str) -> str:
    """Compose an honest one-paragraph autopsy from the real verdict + numbers."""
    verdict = result.get("verdict")
    orig = result.get("orig_flake_rate", result.get("orig_verdict"))
    name = re.sub(r"\.py$", "", test_name or "the test")

    if verdict == "FIXED":
        winner = result.get("winner") or {}
        conf = result.get("confirmation") or {}
        model = (winner.get("model") or "a frontier model").split("/")[-1]
        cause = (winner.get("cause_class") or "a root cause").replace("_", " ")
        final = conf.get("flake_rate", 0.0)
        return (
            f"Autopsy for {name}. The unmodified test failed {_pct(orig)} of the time "
            f"across a fresh sandbox swarm. Its green runs were lying. "
            f"Four models raced competing theories. {model} won, diagnosing the root cause: {cause}. "
            f"Its fix was re-tried from scratch and confirmed at {_pct(final)} failures. "
            f"Verdict: fixed, with a Braintrust receipt to prove it."
        )
    if verdict == "QUARANTINE":
        best = (result.get("hypotheses") or [{}])
        return (
            f"Autopsy for {name}. The test flaked {_pct(orig)} of the time. "
            f"Every proposed fix was re-tried across the swarm, and none drove the "
            f"failure rate below the threshold with confidence. "
            f"Verdict: quarantine, with an evidence dossier. No fix was faked to look green."
        )
    ov = result.get("orig_verdict")
    if ov == "ALWAYS_FAILING":
        return (
            f"Autopsy for {name}. It did not flake. It failed every single time. "
            f"That is a regression, not a flake. Fix the code, not the test. No tournament was run."
        )
    return (
        f"Autopsy for {name}. The baseline was not confidently flaky, so no fix "
        f"tournament was run. Retrial reports the evidence honestly and stops."
    )


def narrate(result: dict, test_name: str, bus=None, api_key: str | None = None,
            voice_id: str | None = None, model_id: str | None = None,
            timeout: int = 60) -> dict | None:
    """Synthesize the autopsy to an mp3 and (if bus) emit voice_ready. Returns
    {"file", "url", "text"} or None on any failure. Never raises."""
    try:
        api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return None
        voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", _DEFAULT_VOICE)
        model_id = model_id or os.environ.get("ELEVENLABS_MODEL_ID", _DEFAULT_MODEL)
        text = build_script(result, test_name)
        r = httpx.post(
            _TTS_URL.format(vid=voice_id),
            headers={"xi-api-key": api_key, "accept": "audio/mpeg",
                     "content-type": "application/json"},
            json={"text": text, "model_id": model_id,
                  "voice_settings": {"stability": 0.4, "similarity_boost": 0.75}},
            timeout=timeout,
        )
        if r.status_code != 200 or not r.content:
            return None
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"autopsy-{uuid.uuid4().hex[:8]}.mp3"
        (VOICE_DIR / fname).write_bytes(r.content)
        payload = {"file": fname, "url": f"/voice/{fname}", "text": text,
                   "verdict": result.get("verdict")}
        if bus is not None:
            bus.emit("voice_ready", payload)
        return payload
    except Exception:
        return None  # narration is a bonus; never break the run
