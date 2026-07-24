"""_parse_hypothesis tests — pure JSON-wrangling, no Fireworks. The honesty
contract matters most: a model that returned nothing usable is reported as
no_valid_patch, never silently substituted with the original code."""
import json

from retrial.diagnosis import CAUSE_CLASSES, _parse_hypothesis

VALID = {
    "cause_class": "race_condition",
    "explanation": "two threads mutate the counter without a lock",
    "patched_code": "import sys\nassert 1 == 1\nsys.exit(0)\n",
}


def test_valid_json_parses_ok():
    h = _parse_hypothesis(json.dumps(VALID), "h1", fallback_cause="timing")
    assert h["status"] == "ok"
    assert h["id"] == "h1"
    assert h["cause_class"] == "race_condition"
    assert h["patched_code"] == VALID["patched_code"]
    assert h["explanation"] == VALID["explanation"]


def test_fenced_json_with_leading_junk_is_extracted():
    content = "Sure! Here's my analysis:\n```json\n" + json.dumps(VALID) + "\n```"
    h = _parse_hypothesis(content, "h2", fallback_cause="timing")
    assert h["status"] == "ok"
    assert h["cause_class"] == "race_condition"


def test_garbage_is_no_valid_patch_never_a_substitute():
    for content in (None, "", "I think it's a race condition, good luck!",
                    '{"cause_class": "timing"}',          # no patched_code
                    '{"patched_code": ""}',               # empty patch
                    '{"patched_code": 42}'):              # non-string patch
        h = _parse_hypothesis(content, "h3", fallback_cause="shared_state")
        assert h["status"] == "no_valid_patch"
        # The load-bearing honesty rule: NEVER substitute the original code.
        assert h["patched_code"] is None
        assert h["raw_response"] == content               # raw kept for audit


def test_unknown_cause_class_coerced_to_fallback():
    data = dict(VALID, cause_class="cosmic_rays")
    h = _parse_hypothesis(json.dumps(data), "h4", fallback_cause="order_dependency")
    assert h["cause_class"] == "order_dependency"
    assert h["cause_class"] in CAUSE_CLASSES
    assert h["status"] == "ok"                            # patch still usable


def test_camel_case_patched_code_accepted():
    data = {"cause_class": "timing", "explanation": "x",
            "patchedCode": "import sys\nsys.exit(0)\n"}
    h = _parse_hypothesis(json.dumps(data), "h5", fallback_cause="timing")
    assert h["status"] == "ok"
    assert h["patched_code"] == data["patchedCode"]


def test_never_raises_on_adversarial_input():
    for content in ("{" * 500, "}{", '{"a": {"b": ]}', "\x00\x01", "null", "[]"):
        h = _parse_hypothesis(content, "h6", fallback_cause="timing")
        assert h["status"] in ("ok", "no_valid_patch")
        assert h["id"] == "h6"
