import json

import pytest

from goldenloop_eval import sanitize


@pytest.mark.parametrize("key,secrets", [
    ("person@example.com", ()), ("prefix-opaque-value", ("opaque-value",)),
    ("Bearer abc123", ()), ("sk-abcdefgh", ()), ("api_key=opaque", ()),
])
def test_sensitive_dictionary_keys_are_redacted_and_idempotent(key, secrets):
    original = {"nested": [{key: "value"}]}
    safe = sanitize(original, secrets=secrets)
    assert key not in json.dumps(safe)
    assert sanitize(safe, secrets=secrets) == safe
    assert original == {"nested": [{key: "value"}]}


@pytest.mark.parametrize("original,secrets", [
    ({"one@example.com": 1, "two@example.com": 2}, ()),
    ({"opaque": 1, "[REDACTED]": 2}, ("opaque",)),
    ({"first": 1, "second": 2}, ("first", "second")),
    ({"nested": [{"one@example.com": 1, "two@example.com": 2}]}, ()),
    ({1: "number", "1": "string"}, ()),
])
def test_dictionary_key_collisions_reject_without_leaking(original, secrets):
    with pytest.raises(ValueError) as caught:
        sanitize(original, secrets=secrets)
    assert str(caught.value) == "Redaction would produce duplicate dictionary keys"


@pytest.mark.parametrize("secrets", [("REDACTED",), ("a", "abc"), ("[",), ("opaque", "REDACTED")])
def test_replacement_markers_are_reserved_and_never_rescanned(secrets):
    original = {"[REDACTED]": "[REDACTED] Bearer abc a@example.com opaque abc REDACTED"}
    safe = sanitize(original, secrets=secrets)
    assert sanitize(safe, secrets=secrets) == safe
