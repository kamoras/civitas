"""Shared helpers for the Oyez API clients.

Both supreme_court.py (case decisions for the explore store) and
justice_votes.py (per-justice vote records) fetch from api.oyez.org and had
independent, verbatim copies of the base URL and the Unix-timestamp date
parser. This is the one canonical copy, mirroring how house_record.py reuses
congressional_record._strip_html.
"""

import re
from datetime import UTC, datetime

OYEZ_BASE = "https://api.oyez.org"

_HTML_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Remove HTML tags from Oyez question/description fields."""
    if not text:
        return ""
    return _HTML_TAG.sub("", text).strip()


def unix_to_date(ts: int | float | None) -> str:
    """Convert a Unix timestamp to YYYY-MM-DD (empty string if unparseable)."""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return ""
