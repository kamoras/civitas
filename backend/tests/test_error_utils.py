"""Tests for redacting credential-shaped substrings out of arbitrary text
and classifying exceptions into a fixed, credential-free label set.
"""

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app.error_utils import classify_exception, redact_sensitive_params


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


class TestClassifyException:
    """classify_exception returns one of a fixed set of hardcoded labels —
    never data from the exception's own type name or message. See
    error_utils.py's module docstring for why type(e).__name__ alone isn't
    enough (it's still flagged by CodeQL's taint tracking).
    """

    def test_http_status_error(self):
        request = httpx.Request("GET", "https://api.gov/x?api_key=SECRET")
        response = httpx.Response(500, request=request)
        e = httpx.HTTPStatusError("boom", request=request, response=response)
        assert classify_exception(e) == "HTTPStatusError"

    def test_timeout_exception(self):
        assert classify_exception(httpx.ConnectTimeout("timed out")) == "Timeout"

    def test_sqlalchemy_error(self):
        e = OperationalError("SELECT * FROM users WHERE token='abc'", {}, Exception())
        assert classify_exception(e) == "DatabaseError"

    def test_value_error(self):
        assert classify_exception(ValueError("bad input")) == "ValueError"

    def test_key_error(self):
        assert classify_exception(KeyError("missing")) == "DataShapeError"

    def test_unrecognized_exception_falls_back(self):
        class SomeCustomError(Exception):
            pass

        assert classify_exception(SomeCustomError("secret detail")) == "OtherError"

    def test_never_leaks_message_content(self):
        e = RuntimeError("https://api.gov/x?api_key=SECRET123")
        result = classify_exception(e)
        assert "SECRET123" not in result
        assert "api.gov" not in result
