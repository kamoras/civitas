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
from urllib.parse import unquote, urljoin, urlparse

import httpx
import pdfplumber
from lxml import html as lxml_html

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


async def get_bytes_unless_missing(
    client: httpx.AsyncClient, url: str, label: str, awaited: str, *, deadline_applies: bool = True,
) -> bytes | None:
    """get_bytes for a document at the state's own per-election address: a
    404 raises NotYetPublished(`awaited`), any other failure is None —
    the bytes counterpart of get_text_unless_missing."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, log_label=label, headers=BROWSER_HEADERS,
        expected_statuses=(404,),
    )
    if resp is None:
        return None
    if resp.status_code == 404:
        raise NotYetPublished(awaited, deadline_applies=deadline_applies)
    return resp.content


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


def pdf_text_flow(raw: bytes) -> str:
    """pdf_text in the PDF's own text order (use_text_flow) rather than
    pdfplumber's left-to-right reading of each line: an OCR text layer
    whose word boxes overlap (Utah's scanned certifications) otherwise
    interleaves the letters of neighbouring words."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text(use_text_flow=True) or "" for page in pdf.pages)


def pdf_pages(raw: bytes) -> list[str]:
    """Each page's extract_text(), one string per page — for a reader
    that needs page boundaries (running headers and footers to drop)."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def same_site(host: str, start_host: str) -> bool:
    """`host` is the starting host or one of its subdomains. A state's site
    spreads over subdomains of its own name — Colorado's Legislature lists
    the 2026 Blue Book on leg.colorado.gov but publishes it from
    content.leg.colorado.gov — and those are the same publisher. A sibling
    or parent (the Secretary of State's sos.state.co.us, colorado.gov) is
    not: a subdomain of the start, never the other way round."""
    host, start_host = host.lower().removeprefix("www."), start_host.lower().removeprefix("www.")
    return host == start_host or host.endswith("." + start_host)


# Pages a county's election page links that the search also opens: the
# year's election-information page, a news item announcing the booklet.
_REPUBLISHED_HOP_PAGES = 6


def _page_links(page_html: str) -> list[tuple[str, str]]:
    tree = lxml_html.fromstring(page_html)
    return [(a.get("href").strip(), " ".join(a.text_content().split())) for a in tree.xpath("//a[@href]")]


def _names(href: str, text: str, year: int, words: tuple[str, ...]) -> bool:
    haystack = unquote(f"{href} {text}").lower().replace("-", " ").replace("_", " ")
    return str(year) in haystack and all(w in haystack for w in words)


async def republished_candidates(
    client: httpx.AsyncClient, landing_url: str, year: int, words: tuple[str, ...],
) -> list[str] | None:
    """Every link naming `year` and all of `words`, on a county election
    office's page and on the same-site pages it links that name `year`
    (one hop). Candidates only: which, if any, is the state's own document
    is the reader's to decide by reading it (each reader's cover and
    contents checks), never by its link. None when the county page itself
    couldn't be fetched.

    County offices file the state's booklet wherever their own site puts
    documents — Augusta-Richmond's is under a news item, at a
    /DocumentCenter/View/ address with no ".pdf" — so no link shape is
    assumed."""
    landing = await get_text(client, landing_url, f"republished copy page {landing_url}")
    if landing is None:
        return None
    start_host = urlparse(landing_url).netloc
    pages = [(landing_url, landing)]
    for href, text in _page_links(landing):
        url = urljoin(landing_url, href)
        if len(pages) > _REPUBLISHED_HOP_PAGES:
            break
        if (
            same_site(urlparse(url).netloc, start_host) and _names(href, text, year, ())
            and url not in {u for u, _ in pages}
        ):
            html = await get_text(client, url, f"republished copy page {url}")
            if html is not None:
                pages.append((url, html))
    candidates: list[str] = []
    for page_url, html in pages:
        for href, text in _page_links(html):
            url = urljoin(page_url, href)
            if _names(href, text, year, words) and url not in candidates:
                candidates.append(url)
    return candidates

