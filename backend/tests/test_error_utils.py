"""Tests for exception sanitization before it reaches an admin-facing field."""

import pytest

from app.error_utils import redact_sensitive_params, safe_error_summary


class TestRedactSensitiveParams:
    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param(
                "https://api.congress.gov/v3/bill?api_key=abc123&format=json",
                "https://api.congress.gov/v3/bill?api_key=***&format=json",
                id="api_key_redacted",
            ),
            pytest.param(
                "https://x.gov/y?token=secretvalue",
                "https://x.gov/y?token=***",
                id="token_redacted",
            ),
            pytest.param(
                "https://x.gov/y?page=1&limit=10",
                "https://x.gov/y?page=1&limit=10",
                id="no_sensitive_params_unchanged",
            ),
            pytest.param(
                "plain error message with no url",
                "plain error message with no url",
                id="plain_text_unchanged",
            ),
        ],
    )
    def test_redact_sensitive_params(self, text, expected):
        assert redact_sensitive_params(text) == expected


class TestSafeErrorSummary:
    def test_includes_exception_type_and_message(self):
        summary = safe_error_summary(ValueError("bad input"))
        assert summary == "ValueError: bad input"

    def test_redacts_embedded_api_key(self):
        e = RuntimeError("fetch failed: https://api.gov/x?api_key=SECRET123&y=1")
        summary = safe_error_summary(e)
        assert "SECRET123" not in summary
        assert "***" in summary

    def test_drops_lines_after_the_first(self):
        e = ValueError("first line\nSELECT * FROM users WHERE token='abc'")
        summary = safe_error_summary(e)
        assert "SELECT" not in summary
        assert summary == "ValueError: first line"

    def test_truncates_to_limit(self):
        e = ValueError("x" * 500)
        summary = safe_error_summary(e, limit=50)
        assert len(summary) == 50

    def test_empty_message_falls_back_to_type_name(self):
        assert safe_error_summary(RuntimeError()) == "RuntimeError"
