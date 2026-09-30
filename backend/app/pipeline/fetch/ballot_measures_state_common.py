"""Small shared pieces for the per-state ballot-measure readers added for
AL, AR, FL, KY, MD, NC, SC, TN, TX and WV (ballot_measures_<st>.py).

Each of those states publishes its certified statewide measures in its
own shape — a landing page of per-measure PDFs, one HTML page, a
database query form, a one-page PDF report — so each still has its own
parse function, built against that state's real document (the same
reason ballot_measures_pdf.py gives for not writing one parser that
guesses a layout). What they share is only plumbing, collected here so
ten modules don't carry ten copies of it:

- `election_day(year)`: the November general-election date the reader
  must find named in the state's own document. Several readers REFUSE a
  document that doesn't name it — a page that still shows last cycle's
  measures would otherwise be stored as this cycle's ballot.
- `long_date(day)`: that date written the way state documents print it
  ("November 3, 2026"), for that check.
- `get_text` / `get_bytes` / `pdf_text`: the rate-limited fetch and
  pdfplumber text extraction every reader needs.

Nothing here reads or rewrites ballot content.
"""

import io
import logging
import re
from datetime import date

import httpx
import pdfplumber

from app.election_calendar import next_election_day
from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.http_utils import (
    BROWSER_HEADERS,
    fetch_bytes_with_retry,
    fetch_json_with_retry,
    fetch_text_with_retry,
    fetch_with_retry,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

# BROWSER_HEADERS with the User-Agent's trailing "(+<contact email>)" comment
# dropped (still ending "Civitas/1.0"). Akamai-fronted state sites refuse
# that bracketed crawler-contact form with a 403 and serve the same
# request without it: michigan.gov (ballot_measures_mi.py) and nh.gov —
# www.sos.nh.gov and mm.nh.gov both measured 403 with it, 200 without,
# 2026-09-28.
HEADERS_NO_CONTACT = {
    **BROWSER_HEADERS,
    "User-Agent": re.sub(r"\s*\(\+[^)]*\)\s*$", "", BROWSER_HEADERS["User-Agent"]),
}


def election_day(year: int) -> date:
    """The statutory November general-election day in `year` (2 U.S.C.
    §7) — the same rule the election pipeline keys every measure on."""
    return next_election_day(date(year, 1, 1))


def long_date(day: date) -> str:
    """"November 3, 2026" — no zero padding, as state documents print it."""
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def names_date(text: str | None, day: date) -> bool:
    """Whether `text` names `day` in long form, tolerating the line break
    a PDF puts anywhere inside it ("November 3,\\n2026")."""
    return long_date(day) in (clean_text(text) or "")


async def get_text(client: httpx.AsyncClient, url: str, label: str, **kwargs) -> str | None:
    return await fetch_text_with_retry(client, _rate_limiter, url, label, **kwargs)


async def get_text_or_missing(
    client: httpx.AsyncClient, url: str, label: str, *, headers: dict = BROWSER_HEADERS,
) -> tuple[str | None, bool]:
    """(body, missing): body None with missing True is a 404 (the page
    isn't there); with missing False, any other failure."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, log_label=label, headers=headers,
        expected_statuses=(404,),
    )
    if resp is None:
        return None, False
    if resp.status_code == 404:
        return None, True
    return resp.text, False


async def get_text_unless_missing(
    client: httpx.AsyncClient, url: str, label: str, awaited: str, *, deadline_applies: bool = True,
) -> str | None:
    """get_text for a page a state creates only when it has something to
    post: a 404 there raises NotYetPublished(`awaited`) — not yet covered,
    no alert — while any other failure is still None (ingest_failed). Use
    it only where the address is the state's own per-election convention
    and its absence is known to mean "not posted". `deadline_applies` is
    passed through to NotYetPublished (see its docstring)."""
    text, missing = await get_text_or_missing(client, url, label)
    if missing:
        raise NotYetPublished(awaited, deadline_applies=deadline_applies)
    return text


async def get_bytes(client: httpx.AsyncClient, url: str, label: str, **kwargs) -> bytes | None:
    return await fetch_bytes_with_retry(client, _rate_limiter, url, label, **kwargs)


async def get_json(client: httpx.AsyncClient, url: str, label: str) -> dict | list | None:
    return await fetch_json_with_retry(client, _rate_limiter, url, label)


async def post_text(client: httpx.AsyncClient, url: str, label: str, **request_kwargs) -> str | None:
    """A form POST (data=/files=) returning the body, or None. Two of these
    states (FL, SC) publish their certified list only as the answer to
    their own search form."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "POST", url, log_label=label, headers=BROWSER_HEADERS,
        **request_kwargs,
    )
    return resp.text if resp is not None else None


def pdf_text(raw: bytes) -> str:
    """Every page's extract_text(), newline-joined — the same reduction
    ballot_measures_va.py uses, so parsers (and their fixtures) work on
    plain text rather than binary PDFs."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def pdf_pages(raw: bytes) -> list[str]:
    """Each page's extract_text(), one string per page — for a reader
    that needs page boundaries (running headers and footers to drop)."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]
