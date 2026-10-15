"""Tests for redacting credential-shaped substrings out of arbitrary text,
and for the exception a failed run keeps on its row."""

from types import SimpleNamespace

import pytest

from app.error_utils import (
    TRACEBACK_CHARS,
    exception_detail,
    exception_summary,
    record_exception,
    redact_sensitive_params,
)


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


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:
        return caught


class TestExceptionDetail:
    def test_summary_names_where_type_message_and_frame(self):
        line = exception_summary(_raised(ValueError("bad   row\n  42")), "classify_donors")
        assert line.startswith("classify_donors: ValueError: bad row 42 (test_error_utils.py:")
        assert line.endswith("in _raised)")

    def test_credentials_are_redacted_in_summary_and_traceback(self):
        exc = _raised(RuntimeError("503 from https://api.open.fec.gov/v1/x?api_key=SECRET&page=2"))
        detail = exception_detail(exc)
        assert "SECRET" not in detail and "api_key=***&page=2" in detail

    def test_traceback_is_bounded_to_its_innermost_frames(self):
        exc = _raised(RuntimeError("x" * 20_000))
        detail = exception_detail(exc)
        trace = detail.split("\n\n", 1)[1]
        assert len(trace) <= TRACEBACK_CHARS + 1 and trace.startswith("…")
        assert len(detail.split("\n", 1)[0]) < 700

    def test_record_exception_keeps_earlier_failures_of_the_run(self):
        run = SimpleNamespace(error_detail=None)
        first = record_exception(run, _raised(KeyError("a")), "step_one")
        second = record_exception(run, _raised(OSError("disk")), "step_two")
        assert first.startswith("step_one: KeyError") and second.startswith("step_two: OSError: disk")
        assert run.error_detail.index("step_one") < run.error_detail.index("step_two")
        assert "Traceback (most recent call last)" in run.error_detail
