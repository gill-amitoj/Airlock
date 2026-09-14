"""
Redaction for audit log details.

Execution logs are durable and readable through the API, so anything written
into them is effectively published. Step configs carry request headers, and
headers carry API keys - so the details dict passed to a log call is a realistic
place for a credential to leak.

Two rules are applied:

1. Values under key-shaped names (api_key, token, secret, ...) are masked.
2. Whole fields that exist to carry credentials - request headers, and full step
   configs - are dropped rather than masked field-by-field, since a caller can
   name a header anything at all.
"""

import re
from typing import Any

REDACTED = "[REDACTED]"
DROPPED = "[DROPPED]"

# Key names that indicate a credential. Matched as a substring, case-insensitively,
# so api_key / apiKey / X-Api-Key / access_token all hit.
SECRET_KEY_PATTERN = re.compile(
    r"(api[-_]?key|secret|token|password|passwd|authorization|auth|credential|bearer|private[-_]?key|session[-_]?id)",
    re.IGNORECASE,
)

# Fields dropped wholesale - their contents are attacker- or user-shaped and can
# hold credentials under arbitrary key names.
DROP_KEYS = frozenset({"headers", "config", "step_config"})

# How deep to walk before giving up, so a pathological structure cannot hang a log write.
MAX_DEPTH = 6


def _is_secret_key(key: Any) -> bool:
    """Check whether a mapping key looks like it names a credential."""
    return isinstance(key, str) and bool(SECRET_KEY_PATTERN.search(key))


def redact(value: Any, _depth: int = 0) -> Any:
    """
    Return a copy of `value` with credential-shaped data removed.

    Walks dicts and lists recursively. Non-container values are returned as-is;
    the decision is always made from the key that points at a value, never from
    the value itself.
    """
    if _depth >= MAX_DEPTH:
        return "[TRUNCATED]"

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in DROP_KEYS:
                result[key] = DROPPED
            elif _is_secret_key(key):
                result[key] = REDACTED
            else:
                result[key] = redact(item, _depth + 1)
        return result

    if isinstance(value, (list, tuple)):
        return [redact(item, _depth + 1) for item in value]

    return value


def redact_details(details: dict) -> dict:
    """
    Redact a log `details` mapping.

    Convenience wrapper used by the logging helpers in ExecutionService and
    WorkflowOrchestrator so both apply identical rules.
    """
    if not details:
        return details or {}
    return redact(details)
