"""Fetch + parse presidential approval polling from UCSB's American
Presidency Project (presidency.ucsb.edu).

Replaces the Gallup dependency this platform's Public Mandate dimension
used to lean on for its "modern presidents" claim — Gallup itself ended
presidential approval tracking entirely in Feb 2026 after 88 years.
FiveThirtyEight's approval CSV (the obvious alternative) is also dead —
its dedicated data site now 302s to abcnews.go.com/politics. UCSB's
American Presidency Project is the one still-live, reputable source found
(2026-07 research): each president gets a per-term page with a real HTML
data table, still updated for the sitting president (most recent Trump
2nd-term row observed during development: poll dated 07/08-07/13/2026,
sourced "ipsos-wapo" — UCSB has already adapted to aggregate AP-NORC/
CNN-SSRS/Marist/Verasight/Pew/etc. in place of the discontinued Gallup
feed, rather than going stale).

No API or CSV/JSON export exists — this scrapes the rendered HTML table,
same risk class as the Wikipedia-fallback scrape already in congress.py's
fetch_senator_platform_text. Table markup is real but not perfectly
uniform across the ~90 years of pages this project maintains: some rows
wrap cell values in a <p> tag, others put text directly in the <td> —
.text_content() handles both. A trailing all-blank row (template
artifact) is also observed on at least one live page and must be skipped.

URL slugs are NOT reliably derivable from a president's name (middle
initials, "2nd-term" suffixes for repeat presidents, inconsistent
formatting) — hardcoded per-president below, each verified live against
presidency.ucsb.edu during development (2026-07). Only presidents with
real polling-era coverage are included (Truman #33 onward, matching this
platform's existing "modern presidents" framing) — pre-Truman presidents
have no live source and keep their seed Public Mandate value.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.presidency.ucsb.edu/statistics/data"

# Verified live (200 OK, real approval table present) against
# presidency.ucsb.edu, 2026-07. Presidents not listed here (pre-Truman)
# have no UCSB polling-era page and keep their seed Public Mandate value.
PRESIDENT_APPROVAL_SLUGS: dict[str, str] = {
    "truman-33": "harry-s-truman-public-approval",
    "eisenhower-34": "dwight-d-eisenhower-public-approval",
    "jfk-35": "john-f-kennedy-public-approval",
    "lbj-36": "lyndon-b-johnson-public-approval",
    "nixon-37": "richard-m-nixon-public-approval",
    "ford-38": "gerald-r-ford-public-approval",
    "carter-39": "jimmy-carter-public-approval",
    "reagan-40": "ronald-reagan-public-approval",
    "ghwbush-41": "george-bush-public-approval",
    "clinton-42": "william-j-clinton-public-approval",
    "gwbush-43": "george-w-bush-public-approval",
    "obama-44": "barack-obama-public-approval",
    "trump-45": "donald-j-trump-public-approval",
    "biden-46": "joseph-r-biden-public-approval",
    "trump-47": "donald-j-trump-2nd-term-public-approval",
}

# UCSB doesn't publish a documented rate limit; this is a courteous
# default for a nonprofit academic site rather than a fitted value (same
# posture as this file's peers when no documented limit exists).
_RATE_LIMITER = RateLimiter(rps=1.0)

_CACHE_TIER = "presidential-approval"
_CACHE_MAX_AGE_HOURS = 20  # a bit under the ~24h pipeline cadence


@dataclass
class ApprovalPoll:
    start_date: str  # MM/DD/YYYY, as published
    end_date: str
    approving: float | None
    disapproving: float | None
    unsure: float | None
    source: str | None


def _cell_text(td) -> str:
    return (td.text_content() or "").strip()


def _cell_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_approval_table(html: str) -> list[ApprovalPoll]:
    """Parse UCSB's approval-data table into poll records, oldest first.

    Column order (verified against several live pages, 2026-07): Start
    date, End Date, Approving, Disapproving, Unsure/NoData, [blank
    spacer], then either Source directly (older single-president pages)
    or Republicans/Independents/Democrats Approving + Source (newer
    multi-term pages, e.g. Trump's 2nd-term page). Only the first 5
    columns + a best-effort Source (last non-blank cell) are extracted —
    the by-party breakdown isn't used by this platform's formula and is
    frequently blank even when present as a column.
    """
    doc = lxml_html.fromstring(html)
    tables = doc.cssselect("table")
    if not tables:
        return []
    rows = tables[0].cssselect("tbody tr")

    polls: list[ApprovalPoll] = []
    for row in rows:
        cells = row.cssselect("td")
        if len(cells) < 5:
            continue
        start = _cell_text(cells[0])
        end = _cell_text(cells[1])
        if not start or not end:
            continue  # trailing blank template row, or a malformed one
        approving = _cell_float(_cell_text(cells[2]))
        disapproving = _cell_float(_cell_text(cells[3]))
        unsure = _cell_float(_cell_text(cells[4]))
        if approving is None and disapproving is None:
            continue  # header-ish or otherwise unusable row
        source_text = _cell_text(cells[-1])
        source = source_text if source_text and _cell_float(source_text) is None else None
        polls.append(ApprovalPoll(
            start_date=start, end_date=end,
            approving=approving, disapproving=disapproving, unsure=unsure,
            source=source,
        ))

    # Row order is NOT consistent across UCSB's pages — verified live,
    # 2026-07: Trump's 1st-term page lists newest-first, his 2nd-term page
    # lists oldest-first. Sorting by parsed start_date (rather than
    # trusting either assumption or blindly reversing) is correct
    # regardless of which convention a given page happens to use. Any row
    # with an unparseable date sorts first (datetime.min) rather than
    # raising and discarding the whole page.
    def _sort_key(p: ApprovalPoll) -> datetime:
        try:
            return datetime.strptime(p.start_date, "%m/%d/%Y")
        except ValueError:
            return datetime.min

    polls.sort(key=_sort_key)
    return polls


async def fetch_president_approval_history(
    client: httpx.AsyncClient, db: Session, president_id: str,
) -> list[ApprovalPoll] | None:
    """Fetch + parse a president's full approval-poll history from UCSB.

    Returns None if this president has no known UCSB page (pre-Truman) or
    the fetch/parse failed — never an empty-but-successful list conflated
    with "no data source", so callers can tell "not applicable" from
    "temporarily unavailable"."""
    slug = PRESIDENT_APPROVAL_SLUGS.get(president_id)
    if slug is None:
        return None

    cache_key = f"approval-{president_id}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return [ApprovalPoll(**p) for p in cached["polls"]]

    url = f"{BASE_URL}/{slug}"
    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", url,
        log_label="UCSB approval",
    )
    if resp is None or resp.status_code != 200:
        # url (not president_id) identifies which president in these
        # messages — a bare id like "obama-44" reads as a person
        # identifier to CodeQL's clear-text-logging heuristic even though
        # it's a public internal slug; the URL conveys the same info
        # without that false-positive class (see error_utils.py's
        # docstring on this codebase's prior fights with the same query).
        logger.warning("Failed to fetch UCSB approval data (%s)", url)
        return None

    try:
        polls = _parse_approval_table(resp.text)
    except Exception:
        logger.exception("Failed to parse UCSB approval table (%s)", url)
        return None

    if not polls:
        logger.warning("UCSB approval page parsed to zero rows — page structure may have changed (%s)", url)
        return None

    api_cache_set(db, _CACHE_TIER, cache_key, {
        "polls": [
            {
                "start_date": p.start_date, "end_date": p.end_date,
                "approving": p.approving, "disapproving": p.disapproving,
                "unsure": p.unsure, "source": p.source,
            }
            for p in polls
        ],
    })
    return polls
