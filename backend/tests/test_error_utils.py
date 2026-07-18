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
    """safe_error_summary returns only the exception's type name — no
    message content at all. See error_utils.py's module docstring: CodeQL's
    taint tracking doesn't recognize a regex-redacted message as sanitized,
    only a value structurally independent of the exception's data does.
    """

    def test_returns_type_name_only(self):
        assert safe_error_summary(ValueError("bad input")) == "ValueError"

    def test_never_includes_embedded_api_key(self):
        e = RuntimeError("fetch failed: https://api.gov/x?api_key=SECRET123&y=1")
        assert "SECRET123" not in safe_error_summary(e)
        assert safe_error_summary(e) == "RuntimeError"

    def test_never_includes_sql_statement_text(self):
        e = ValueError("first line\nSELECT * FROM users WHERE token='abc'")
        assert "SELECT" not in safe_error_summary(e)

    def test_truncates_to_limit(self):
        class ReallyLongExceptionClassNameForTestingTruncationBehaviorHere(Exception):
            pass

        summary = safe_error_summary(
            ReallyLongExceptionClassNameForTestingTruncationBehaviorHere(), limit=10
        )
        assert len(summary) == 10

    def test_empty_message_still_returns_type_name(self):
        assert safe_error_summary(RuntimeError()) == "RuntimeError"
