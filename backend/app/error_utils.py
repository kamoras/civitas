"""Sanitizing raw exception text before it reaches an admin-facing field.

Pipeline run failures store str(exception) in DB columns
(PipelineRun.error_message and similar) that the admin dashboard displays
directly to an authenticated operator — but some exception types embed far
more than a human-readable message: SQLAlchemy errors can include the full
failing statement and bound parameters, and an HTTP-client exception can
embed the request URL with any credential that was in it before
http_utils.redact_url() had a chance to run. Route every exception that
reaches an admin-visible field through safe_error_summary() first, so full
detail only ever lands in server-side logs (via the logger.exception/
logger.error call already made at each site) — never in the API response
itself.
"""

import re

_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|password)=)[^&\s]*", re.IGNORECASE
)


def redact_sensitive_params(text: str) -> str:
    """Strip credential-shaped query param values out of arbitrary text."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(r"\1***", text)


def safe_error_summary(e: Exception, limit: int = 200) -> str:
    """Short, credential-free description of an exception for admin UI display.

    Deliberately drops everything after the first line (where a
    SQLAlchemy statement/parameter dump would appear) and redacts any
    credential-shaped query param before truncating.
    """
    first_line = str(e).splitlines()[0] if str(e) else ""
    redacted = redact_sensitive_params(first_line)
    text = f"{type(e).__name__}: {redacted}" if redacted else type(e).__name__
    return text[:limit]
