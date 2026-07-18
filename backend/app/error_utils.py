"""Sanitizing exception info before it reaches an admin-facing field or log.

Two independent tools here, for two different problems:

redact_sensitive_params() strips credential-shaped query param values out of
arbitrary text (used by http_utils.py's debug/error logging of request URLs
and exception messages). This is a best-effort readability aid, NOT a
CodeQL-recognized sanitizer — its taint tracking (py/clear-text-logging-
sensitive-data, py/stack-trace-exposure) doesn't recognize a regex
substitution as clearing taint on a value derived from a tainted source.

classify_exception() is what actually clears those alerts for fields that
reach an admin-visible API response (PipelineRun.error_message and
similar — see senate_pipeline.py, house_pipeline.py,
supplementary_pipeline.py, stock_pipeline.py, database.py's
reset_all_data(), fetch/federal_register.py). Every branch returns a string
literal typed directly in source; the exception `e` is used only in
isinstance() checks — a control-flow condition, not a data-flow source for
the return value — so there is no dataflow edge from `e` to what actually
gets logged or stored. This is deliberately different from an earlier,
failed approach: type(e).__name__, even computed inline with no wrapping
function call and carrying no message content at all, was still flagged.
CodeQL doesn't treat type()/.__name__ as clearing taint on a caught
exception variable; only a value chosen from a small, fixed set of literals
(this isinstance-branch pattern) reliably does.
"""

import re

import httpx
from sqlalchemy.exc import SQLAlchemyError

_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|password)=)[^&\s]*", re.IGNORECASE
)


def redact_sensitive_params(text: str) -> str:
    """Strip credential-shaped query param values out of arbitrary text."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(r"\1***", text)


def classify_exception(e: Exception) -> str:
    """One of a fixed set of hardcoded labels — never data from the
    exception's own type name or message. See module docstring.
    """
    if isinstance(e, httpx.HTTPStatusError):
        return "HTTPStatusError"
    if isinstance(e, httpx.TimeoutException):
        return "Timeout"
    if isinstance(e, httpx.TransportError):
        return "TransportError"
    if isinstance(e, httpx.RequestError):
        return "RequestError"
    if isinstance(e, SQLAlchemyError):
        return "DatabaseError"
    if isinstance(e, TimeoutError):
        return "Timeout"
    if isinstance(e, ConnectionError):
        return "ConnectionError"
    if isinstance(e, PermissionError):
        return "PermissionError"
    if isinstance(e, (KeyError, IndexError, AttributeError)):
        return "DataShapeError"
    if isinstance(e, (ValueError, TypeError)):
        return "ValueError"
    if isinstance(e, OSError):
        return "OSError"
    return "OtherError"
