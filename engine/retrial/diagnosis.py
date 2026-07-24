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
import re
import threading

from .settings import get_settings

BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_MODELS = ["accounts/fireworks/models/glm-5p2"]  # "p", not "." — verified slug

CAUSE_CLASSES = (
    "order_dependency",
    "shared_state",
    "timing",
    "race_condition",
    "external_dep",
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _models_from_env():
    raw = get_settings().fireworks_models.strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(DEFAULT_MODELS)


def _build_messages(test_code, test_name, log_tail, cause_hint, context=None):
    """Prompt one model for a single competing root-cause hypothesis + fix.

    `context` (optional) is an infrastructure finding — e.g. a hermetic detect
    result — that narrows the search (e.g. env_independent rules out external_dep).
    """
    system = (
        "You are a flaky-test root-cause analyst. A flaky test passes sometimes "
        "and fails sometimes on identical code. Diagnose ONE root cause and return "
        "a corrected version of the file. Respond with a strict JSON object only."
    )
    context_block = f"\nInfrastructure finding: {context}\n" if context else ""
    user = f"""Flaky test file `{test_name}`:
```python
{test_code}
```

Sample run log (may be empty):
```
{log_tail or "(no log provided)"}
```
{context_block}
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


def _parse_hypothesis(content, hid, fallback_cause):
    """Parse a model's JSON response into a hypothesis dict. Pure + defensive:
    tolerates fenced/leading junk and coerces cause_class to the enum. Never
    raises.

    Crucially it does NOT substitute the original code when the model returned no
    usable patch — that would race a non-answer at baseline flake and let the UI
    claim a model "produced a theory" when it did not. Instead the result carries
    a `status`: "ok" (a real patch) or "no_valid_patch" (unparseable / empty), and
    the raw response is preserved for honesty."""
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
    explanation = str(data.get("explanation") or "").strip()
    patched = data.get("patched_code") or data.get("patchedCode")
    valid = isinstance(patched, str) and patched.strip() != ""

    return {
        "id": hid,
        "cause_class": cause,
        "explanation": explanation,
        "patched_code": patched if valid else None,
        "status": "ok" if valid else "no_valid_patch",
        "raw_response": content,
    }


def diagnose(test_code, test_name, log_tail="", n=4, models=None,
             api_key=None, base_url=BASE_URL, client=None, context=None):
    """Return N competing hypotheses [{id, cause_class, explanation, patched_code}].

    Models are round-robined across the N hypotheses for diversity. Requires a
    Fireworks key (or an injected OpenAI-compatible `client`, used by tests).
    `context` (optional) is an infrastructure finding fed into every prompt.
    """
    models = models or _models_from_env()
    api_key = api_key or get_settings().fireworks_api_key

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
        messages = _build_messages(test_code, test_name, log_tail, cause_hint, context)
        raws = []
        h = None
        # One initial attempt + ONE retry if the model returned no parseable patch
        # (free-form prose despite json_object, or a truncated response).
        for _attempt in range(2):
            try:
                content = _complete(client, model, messages)
            except Exception:
                content = None
            raws.append(content)
            h = _parse_hypothesis(content, hid, fallback_cause=cause_hint)
            if h["status"] == "ok":
                break
        h["model"] = model            # which model generated this hypothesis (for the genome)
        h["raw_responses"] = raws     # keep every raw response — artifact honesty
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
            temperature=0.7, max_tokens=4096)
    except Exception:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.7, max_tokens=2048)
    return resp.choices[0].message.content


class DiagnosisEngine:
    """Object wrapper bundling default models/key for repeated diagnosis calls."""

    def __init__(self, models=None, api_key=None, base_url=BASE_URL):
        self.models = models or _models_from_env()
        self.api_key = api_key or get_settings().fireworks_api_key
        self.base_url = base_url

    @property
    def available(self):
        return bool(self.api_key)

    def diagnose(self, test_code, test_name, log_tail="", n=4):
        return diagnose(test_code, test_name, log_tail, n=n, models=self.models,
                        api_key=self.api_key, base_url=self.base_url)
