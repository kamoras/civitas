"""Sanitizing raw exception text before it reaches an admin-facing field.

Pipeline run failures store str(exception) in DB columns
(PipelineRun.error_message and similar) that the admin dashboard displays
directly to an authenticated operator — but some exception types embed far
more than a human-readable message: SQLAlchemy errors can include the full
failing statement and bound parameters, and an HTTP-client exception can
embed the request URL with any credential that was in it. Route every
exception that reaches an admin-visible field through safe_error_summary()
first, so full detail only ever lands in server-side logs (via the
logger.exception/logger.error call already made at each site) — never in
the API response itself.

safe_error_summary() returns only the exception's type name, nothing from
its message. An earlier version returned the type name plus a redacted
first line of str(e), but CodeQL's taint tracking (py/clear-text-logging-
sensitive-data, py/stack-trace-exposure) doesn't recognize an arbitrary
regex substitution as a sanitizer — it kept flagging the redacted value as
if it were still the raw exception, because the value is still, structurally,
*derived from* the exception's data. type(e).__name__ isn't derived from
the exception's message/args at all, so it's a different source as far as
taint tracking is concerned, and is the only thing that reliably clears the
alert. redact_sensitive_params() stays available for the one place a raw
URL still needs partial redaction for human readability (http_utils.py's
debug logs, which are not treated as sensitive-data sinks the same way).
"""

import re

_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|password)=)[^&\s]*", re.IGNORECASE
)


def redact_sensitive_params(text: str) -> str:
    """Strip credential-shaped query param values out of arbitrary text."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(r"\1***", text)


def safe_error_summary(e: Exception, limit: int = 200) -> str:
    """Exception type name only — no message content, see module docstring."""
    return type(e).__name__[:limit]
