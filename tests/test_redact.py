"""Tests for secret redaction utilities."""

from __future__ import annotations

import pytest

from oporch.redact import redact_secrets


class TestRedactSecrets:
    """redact_secrets strips API-key-shaped strings, leaves normal text."""

    def test_normal_text_unchanged(self) -> None:
        text = "This is a normal log message about WU-001 completing."
        assert redact_secrets(text) == text

    def test_bearer_token_redacted(self) -> None:
        text = "Authorization: Bearer sk-abc123def456ghi789jkl012mno345pqr678"
        result = redact_secrets(text)
        assert "sk-abc123" not in result
        assert "[REDACTED]" in result

    def test_sk_prefix_key_redacted(self) -> None:
        text = "Using key sk-abcdefghijklmnopqrstuvwxyz1234567890 for auth"
        result = redact_secrets(text)
        assert "sk-abcdef" not in result
        assert "[REDACTED]" in result

    def test_api_key_prefix_redacted(self) -> None:
        text = "Config: api-keyABCDEFGHIJKLMNOPQRSTUVWXYZ for service"
        result = redact_secrets(text)
        assert "api-keyABC" not in result
        assert "[REDACTED]" in result

    def test_long_hex_string_redacted(self) -> None:
        hex_key = "a" * 40
        text = f"Secret: {hex_key}"
        result = redact_secrets(text)
        assert hex_key not in result
        assert "[REDACTED]" in result

    def test_aws_key_redacted(self) -> None:
        text = "aws_access_key_id=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED]" in result

    def test_api_key_equals_format_redacted(self) -> None:
        text = 'api_key=my-super-secret-value'
        result = redact_secrets(text)
        assert "my-super-secret" not in result
        assert "[REDACTED]" in result

    def test_short_strings_not_redacted(self) -> None:
        text = "short id abc123 and status OK"
        assert redact_secrets(text) == text

    def test_work_unit_ids_not_redacted(self) -> None:
        text = "WU-001 depends on WU-002, status PENDING"
        assert redact_secrets(text) == text

    def test_file_paths_not_redacted(self) -> None:
        text = "Modified src/oporch/cli.py and tests/test_config.py"
        assert redact_secrets(text) == text

    def test_multiple_secrets_all_redacted(self) -> None:
        text = (
            "key1: sk-aaaabbbbccccddddeeeeffffgggg1234 "
            "key2: token-xxxxyyyyzzzzwwwwuuuuv1234567890"
        )
        result = redact_secrets(text)
        assert "sk-aaaa" not in result
        assert "token-xxxx" not in result
        assert result.count("[REDACTED]") >= 2

    def test_empty_string(self) -> None:
        assert redact_secrets("") == ""

    def test_secret_in_json_structure(self) -> None:
        text = '{"api_key": "sk-testkey12345678901234567890abcdef", "status": "ok"}'
        result = redact_secrets(text)
        assert "sk-testkey" not in result
        assert '"status": "ok"' in result
