"""Redacting credential-shaped substrings out of arbitrary text.

Used by http_utils.py's debug/error logging of request URLs and exception
messages. This is NOT sufficient on its own for anything CodeQL treats as a
sensitive-data sink (py/clear-text-logging-sensitive-data,
py/stack-trace-exposure): its taint tracking doesn't recognize a regex
substitution as a sanitizer, and conservatively taints the return value of
*any* function called with a tainted argument (an exception object, a
credential-bearing string) regardless of what the function body actually
does with it. Fields that reach an admin-visible API response
(PipelineRun.error_message and similar) use `type(e).__name__` computed
inline at the call site instead — see senate_pipeline.py, house_pipeline.py,
supplementary_pipeline.py, stock_pipeline.py, database.py's
reset_all_data(). type(e).__name__ isn't derived from the exception's
message/args at all, so it's a different taint source as far as CodeQL is
concerned, and reliably clears the alert where a wrapped helper call did not.
"""

import re

_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|password)=)[^&\s]*", re.IGNORECASE
)


def redact_sensitive_params(text: str) -> str:
    """Strip credential-shaped query param values out of arbitrary text."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(r"\1***", text)
