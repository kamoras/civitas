"""Redacting credential-shaped substrings out of arbitrary text, and
recording a failed pipeline run's exception on its run row.

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
fetch/federal_register.py and database.py's reset_all_data(), which use a
static string with zero exception reference instead.

A run's ``error_message`` stays such a static string: the public
``/api/pipeline/status`` returns it. The exception itself goes to the
run's ``error_detail`` column (``record_exception``), which only the
admin API returns. Until 2026-10 the pipelines kept nothing but the static
string and the server log, and a deploy replaces the container and its log:
a failed Senate run's cause could not be recovered the same day.
"""

import re
import traceback

_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|password)=)[^&\s]*", re.IGNORECASE
)


def redact_sensitive_params(text: str) -> str:
    """Strip credential-shaped query param values out of arbitrary text."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(r"\1***", text)


# How much of a traceback a run row keeps: its innermost frames, which name
# where it raised. Enough for the call chain through a pipeline phase; a
# run row is not a log.
TRACEBACK_CHARS = 4096


def exception_summary(exc: BaseException, where: str | None = None) -> str:
    """One line: ``where: Type: message (file:line in function)``, the
    innermost frame being where it raised, credential parameters redacted.
    What an alert carries."""
    frames = traceback.extract_tb(exc.__traceback__)
    at = f" ({frames[-1].filename.rsplit('/', 1)[-1]}:{frames[-1].lineno} in {frames[-1].name})" if frames else ""
    message = " ".join(str(exc).split())[:500]
    return redact_sensitive_params(
        f"{where + ': ' if where else ''}{type(exc).__name__}{': ' + message if message else ''}{at}"
    )


def exception_detail(exc: BaseException, where: str | None = None) -> str:
    """exception_summary's line, then the last TRACEBACK_CHARS of the
    traceback, credential parameters redacted."""
    trace = "".join(traceback.format_exception(exc))
    if len(trace) > TRACEBACK_CHARS:
        trace = "…" + trace[-TRACEBACK_CHARS:]
    return f"{exception_summary(exc, where)}\n\n{redact_sensitive_params(trace).rstrip()}"


def record_exception(run, exc: BaseException, where: str | None = None) -> str:
    """Add ``exc`` to ``run.error_detail`` (after anything earlier in the
    same run, such as a step that failed before it) and return its summary
    line. The caller commits."""
    detail = exception_detail(exc, where)
    earlier = getattr(run, "error_detail", None)
    run.error_detail = f"{earlier}\n\n{detail}" if earlier else detail
    return detail.split("\n", 1)[0]
