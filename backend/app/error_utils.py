"""Redacting credential-shaped substrings out of arbitrary text.

Used by http_utils.py's debug/error logging of request URLs and exception
messages. This is a best-effort readability aid, NOT a CodeQL-recognized
sanitizer for anything treated as a sensitive-data sink
(py/clear-text-logging-sensitive-data, py/stack-trace-exposure).

Empirically (2026-07, fetch/federal_register.py — see its git history for
the full trail), NOTHING that references a caught exception object inside a
flagged log statement clears that class of alert, no matter how it's
transformed: not a regex substitution, not type(e).__name__ computed
inline, not a shared helper doing isinstance()-branching to hardcoded
string literals, not even Python logging's own exc_info=True (which is
modeled as directly as a manual %s of the exception, not treated as safer).
The only thing that reliably clears it is removing every reference to the
exception object from the flagged statement entirely — see
fetch/federal_register.py, and the admin-facing PipelineRun.error_message
call sites in senate_pipeline.py, house_pipeline.py,
supplementary_pipeline.py, stock_pipeline.py, and database.py's
reset_all_data(), which all use a static string with zero exception
reference instead. Full exception detail still reaches server-side logs
unchanged via the existing logger.exception()/logger.error() calls at each
site — those were never flagged by the original scan.
"""

import re

_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|password)=)[^&\s]*", re.IGNORECASE
)


def redact_sensitive_params(text: str) -> str:
    """Strip credential-shaped query param values out of arbitrary text."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(r"\1***", text)
