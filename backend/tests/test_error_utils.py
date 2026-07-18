"""Tests for redacting credential-shaped substrings out of arbitrary text."""

import pytest

from app.error_utils import redact_sensitive_params


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
