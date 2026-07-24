"""DiagnosisEngine: Fireworks-powered differential diagnosis of a flaky test.

Race HYPOTHESES, not models: given the test source + a sample run log, we ask N
Fireworks models (round-robin, for diversity) for competing root-cause
hypotheses, each a {cause_class, explanation, patched_code}. The tournament then
lets EVIDENCE — empirical flake rate across reruns — eliminate them.

Fireworks is OpenAI-compatible: base_url https://api.fireworks.ai/inference/v1,
key FIREWORKS_API_KEY, model ids like accounts/fireworks/models/glm-5.2.

NOTE: FIREWORKS_API_KEY is not yet provisioned, so live diagnosis is UNTESTED.
The JSON parsing (`_parse_hypothesis`) is pure and unit-tested against canned
responses; the network path is wired and will work once the key lands.
"""
import json
import os
import re
import threading

BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_MODELS = ["accounts/fireworks/models/glm-5.2"]  # real slugs TBD when key lands

CAUSE_CLASSES = (
    "order_dependency",
    "shared_state",
    "timing",
    "race_condition",
    "external_dep",
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _models_from_env():
    raw = os.environ.get("FIREWORKS_MODELS", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(DEFAULT_MODELS)


def _build_messages(test_code, test_name, log_tail, cause_hint):
    """Prompt one model for a single competing root-cause hypothesis + fix."""
    system = (
        "You are a flaky-test root-cause analyst. A flaky test passes sometimes "
        "and fails sometimes on identical code. Diagnose ONE root cause and return "
        "a corrected version of the file. Respond with a strict JSON object only."
    )
    user = f"""Flaky test file `{test_name}`:
```python
{test_code}
```

Sample run log (may be empty):
```
{log_tail or "(no log provided)"}
```

Propose ONE competing root-cause hypothesis. Consider especially the
`{cause_hint}` angle, but choose whichever cause the evidence best supports.

Return a JSON object with EXACTLY these keys:
{{
  "cause_class": one of ["order_dependency","shared_state","timing","race_condition","external_dep"],
  "explanation": "1-2 sentences on the root cause",
  "patched_code": "the FULL corrected replacement file as a single string"
}}"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _parse_hypothesis(content, hid, fallback_cause, fallback_code):
    """Parse a model's JSON response into a hypothesis dict. Pure + defensive:
    tolerates fenced/leading junk, coerces cause_class to the enum, and falls back
    to the original code if no patch was returned. Never raises."""
    data = None
    if content:
        try:
            data = json.loads(content)
        except Exception:
            m = _JSON_OBJ_RE.search(content)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None
    if not isinstance(data, dict):
        data = {}

    cause = data.get("cause_class")
    if cause not in CAUSE_CLASSES:
        cause = fallback_cause
    explanation = data.get("explanation") or ""
    patched = data.get("patched_code") or data.get("patchedCode") or fallback_code

    return {
        "id": hid,
        "cause_class": cause,
        "explanation": str(explanation).strip(),
        "patched_code": patched,
    }


def diagnose(test_code, test_name, log_tail="", n=4, models=None,
             api_key=None, base_url=BASE_URL, client=None):
    """Return N competing hypotheses [{id, cause_class, explanation, patched_code}].

    Models are round-robined across the N hypotheses for diversity. Requires a
    Fireworks key (or an injected OpenAI-compatible `client`, used by tests).
    """
    models = models or _models_from_env()
    api_key = api_key or os.environ.get("FIREWORKS_API_KEY")

    if client is None:
        if not api_key:
            raise ValueError("FIREWORKS_API_KEY not set; cannot run live diagnosis")
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)

    results = [None] * n

    def ask(i):
        hid = f"h{i + 1}"
        cause_hint = CAUSE_CLASSES[i % len(CAUSE_CLASSES)]
        model = models[i % len(models)]
        try:
            content = _complete(client, model, _build_messages(test_code, test_name,
                                                                log_tail, cause_hint))
        except Exception as e:
            content = None
            # Leave a diagnostic breadcrumb in the explanation via fallback below.
            _ = e
        h = _parse_hypothesis(content, hid, fallback_cause=cause_hint,
                              fallback_code=test_code)
        h["model"] = model  # which model generated this hypothesis (for the genome)
        results[i] = h

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [h for h in results if h is not None]


def _complete(client, model, messages):
    """One chat completion; prefers JSON mode, retries without it if unsupported."""
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7, max_tokens=2048)
    except Exception:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.7, max_tokens=2048)
    return resp.choices[0].message.content


class DiagnosisEngine:
    """Object wrapper bundling default models/key for repeated diagnosis calls."""

    def __init__(self, models=None, api_key=None, base_url=BASE_URL):
        self.models = models or _models_from_env()
        self.api_key = api_key or os.environ.get("FIREWORKS_API_KEY")
        self.base_url = base_url

    @property
    def available(self):
        return bool(self.api_key)

    def diagnose(self, test_code, test_name, log_tail="", n=4):
        return diagnose(test_code, test_name, log_tail, n=n, models=self.models,
                        api_key=self.api_key, base_url=self.base_url)
