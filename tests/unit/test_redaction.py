"""
Unit tests for audit log redaction.
"""

import pytest

from src.services.redaction import (
    DROPPED,
    REDACTED,
    redact,
    redact_details,
)


class TestSecretKeyRedaction:
    """Values under credential-shaped keys are masked."""

    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "apiKey",
            "APIKEY",
            "api-key",
            "X-Api-Key",
            "secret",
            "client_secret",
            "token",
            "access_token",
            "password",
            "passwd",
            "authorization",
            "Authorization",
            "credential",
            "bearer",
            "private_key",
            "session_id",
        ],
    )
    def test_secret_keys_are_redacted(self, key):
        result = redact({key: "super-secret-value"})

        assert result[key] == REDACTED
        assert "super-secret-value" not in str(result)

    def test_non_secret_keys_are_untouched(self):
        data = {
            "url": "https://catfact.ninja/fact",
            "method": "GET",
            "status_code": 200,
            "attempt": 2,
        }

        assert redact(data) == data

    def test_nested_secrets_are_redacted(self):
        data = {
            "request": {
                "url": "https://catfact.ninja/fact",
                "auth": {"api_key": "sk-12345"},
            }
        }

        result = redact(data)

        assert result["request"]["auth"] == REDACTED
        assert "sk-12345" not in str(result)

    def test_secrets_inside_lists_are_redacted(self):
        data = {"attempts": [{"token": "abc"}, {"token": "def"}]}

        result = redact(data)

        assert result["attempts"][0]["token"] == REDACTED
        assert result["attempts"][1]["token"] == REDACTED


class TestDroppedFields:
    """Whole fields that carry credentials are dropped, not walked."""

    @pytest.mark.parametrize("key", ["headers", "config", "step_config"])
    def test_credential_carrying_fields_are_dropped(self, key):
        data = {key: {"X-Custom-Auth-Header": "hunter2", "url": "https://x.test"}}

        result = redact(data)

        assert result[key] == DROPPED
        assert "hunter2" not in str(result)

    def test_dropping_is_case_insensitive(self):
        result = redact({"Headers": {"X-Secret-Thing": "value"}})

        assert result["Headers"] == DROPPED

    def test_dropped_field_catches_arbitrarily_named_secrets(self):
        """
        The point of dropping rather than masking: a caller can name an auth
        header anything, so no key pattern would reliably catch it.
        """
        data = {"headers": {"X-Totally-Innocent": "Bearer sk-live-999"}}

        result = redact(data)

        assert "sk-live-999" not in str(result)


class TestStructurePreservation:
    """Redaction should not mangle the shape of the data."""

    def test_scalars_pass_through(self):
        assert redact("plain string") == "plain string"
        assert redact(42) == 42
        assert redact(None) is None
        assert redact(True) is True

    def test_original_is_not_mutated(self):
        original = {"api_key": "secret", "url": "https://x.test"}
        snapshot = dict(original)

        redact(original)

        assert original == snapshot

    def test_deep_nesting_is_truncated(self):
        """A pathological structure is cut off rather than walked forever."""
        data = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "deep"}}}}}}}

        result = redact(data)

        assert "[TRUNCATED]" in str(result)


class TestRedactDetails:
    """The wrapper used by the logging helpers."""

    def test_empty_details_pass_through(self):
        assert redact_details({}) == {}
        assert redact_details(None) == {}

    def test_details_are_redacted(self):
        details = {"input_data": {"api_key": "sk-1"}, "attempt": 1}

        result = redact_details(details)

        assert result["input_data"]["api_key"] == REDACTED
        assert result["attempt"] == 1
