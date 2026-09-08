import re
from typing import Any

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|secret|password|passwd|authorization|access[_-]?token|"
    r"refresh[_-]?token|connection[_-]?string|credential|email|phone)", re.I
)
_TEXT_PATTERNS = [
    re.compile(r"\bBearer\s+[^\s\"'<>]+", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*[^\s,;]+", re.I),
]


def sanitize(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """Return a redacted JSON-shaped copy. Not a general PII/DLP guarantee.

    Call before persistence/export; pass known credential values via secrets.
    Synthetic customer IDs are intentionally preserved for evaluation.
    Dictionary keys are redacted too. Collisions raise a generic ValueError;
    callers must reject the input, not persist the original or partially sanitize it.
    The [REDACTED] marker is reserved and preserved across repeated calls.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe_key = sanitize(str(key), secrets=secrets)
            if safe_key in result:
                raise ValueError("Redaction would produce duplicate dictionary keys")
            result[safe_key] = "[REDACTED]" if _SECRET_KEY.search(str(key)) else sanitize(item, secrets=secrets)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        # Never rescan a replacement marker, including when a secret is its substring.
        for pattern in _TEXT_PATTERNS:
            value = "[REDACTED]".join(pattern.sub("[REDACTED]", part) for part in value.split("[REDACTED]"))
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = "[REDACTED]".join(part.replace(secret, "[REDACTED]") for part in value.split("[REDACTED]"))
    return value
