"""North Carolina's certified general-election results, read for the
certified-results sync (live_results/certified.py).

The State Board of Elections lists them under "Certified Election Results"
on its historical results page, one link per election ("2024 Nov 05
Election - Certified Results by Contest (PDF)"), to a file kept in the
folder of the Board meeting that certified it
(State_Board_Meeting_Docs/2024-11-26/Canvass/..., verified 2026-11-04). The
section says these come after the county canvasses and the post-election
audits; the folder's date is the canvass meeting's, and is read as the
certification date. Before the Board certifies, the section lists no link
for the election.

The PDF's first page is the signed certificate, an image; the tables follow,
one row per choice under its contest (the contest's name only on its first
row, and again where a page break falls inside it): Contest, Choice, Party,
then Total Votes and its breakdown by voting method. A choice with no party
is a write-in ("Write-In (Miscellaneous)", "Shiva Ayyadurai (Write-In)"),
counted in the contest's total and no one's.
"""

import io
import logging
import re
from datetime import date

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import rows as _clustered_rows
from app.pipeline.fetch.http_utils import FETCH_HEADERS
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

RESULTS_PAGE = "https://www.ncsbe.gov/results-data/election-results/historical-election-results-data"
_LINK_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_MEETING_RE = re.compile(r"State_Board_Meeting_Docs/(\d{4})-(\d{2})-(\d{2})/")
# The table's columns, from its own header row ("Contest" x0 20, "Choice"
# 325, "Party" 446, "Total Votes" 496 on 2024's).
_CHOICE_X, _PARTY_X, _TOTAL_X = 320.0, 440.0, 490.0
_FIGURE_RE = re.compile(r"^[\d,]+$")


def link_text(election_day: date) -> str:
    """"2024 Nov 05 Election - Certified Results by Contest": the section's
    own wording for the election's certified results."""
    return f"{election_day.year} {election_day.strftime('%b')} {election_day.day:02d} Election - Certified Results by Contest"


def certified_link(page: str, election_day: date) -> tuple[str, date] | None:
    """(url, certified on) for the election's certified results, or None."""
    want = link_text(election_day)
    for href, text in _LINK_RE.findall(page):
        if want in " ".join(re.sub(r"<[^>]+>|&nbsp;", " ", text).split()):
            found = _MEETING_RE.search(href)
            if found:
                return href, date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    return None


def general_contests(pages: list[list[dict]]) -> list:
    """Every federal contest in the certified results, from each page's words."""
    from app.pipeline.fetch.election_results import ContestCount

    contests: dict[tuple[str, int | None], dict] = {}
    current: tuple[str, int | None] | None = None
    for words in pages:
        clustered = _clustered_rows(words)
        for row_id in sorted(clustered):
            row = sorted(clustered[row_id], key=lambda w: w["x0"])
            label = " ".join(w["text"] for w in row if w["x0"] < _CHOICE_X)
            if label == "Contest" or "Results by Contest" in label or label.startswith("Page "):
                continue
            choice = " ".join(w["text"] for w in row if _CHOICE_X <= w["x0"] < _PARTY_X)
            party = " ".join(w["text"] for w in row if _PARTY_X <= w["x0"] < _TOTAL_X)
            figures = [w["text"] for w in row if w["x0"] >= _TOTAL_X and _FIGURE_RE.match(w["text"])]
            if label:
                found = parse_office(label)
                current = found if found else None
            if current is None or not choice or not figures:
                continue  # another office's row, or a wrapped "(Write-In)"
            votes = int(figures[0].replace(",", ""))
            contest = contests.setdefault(current, {"candidates": [], "other": 0})
            if party:
                contest["candidates"].append((choice, normalize_party(party, ballot_list=True), votes))
            else:
                contest["other"] += votes
    return [ContestCount(office=o, district=d, candidates=c["candidates"],
                         total_votes=sum(v for _, _, v in c["candidates"]) + c["other"])
            for (o, d), c in contests.items() if c["candidates"]]


async def _get(client: httpx.AsyncClient, url: str):
    await _rate_limiter.acquire()
    return await client.get(url, headers=FETCH_HEADERS, timeout=90.0, follow_redirects=True)


async def fetch_certified_general(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """North Carolina's certified federal count for `election_day`'s general,
    or None while the Board lists no certified results for it."""
    from app.pipeline.fetch.election_results import FeedUnreadable, StateCount

    try:
        page = await _get(client, RESULTS_PAGE)
        page.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedUnreadable(f"{state} results page could not be read") from exc
    found = certified_link(page.text, election_day)
    if found is None:
        return None
    url, on = found
    try:
        pdf = await _get(client, url)
        pdf.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedUnreadable(f"{state} certified results could not be fetched") from exc
    with pdfplumber.open(io.BytesIO(pdf.content)) as doc:
        contests = general_contests([p.extract_words() for p in doc.pages])
    if not contests:
        raise FeedUnreadable(f"{state} certified results held no federal contest this reader could read")
    return StateCount(source_name=source.get("source_name") or "North Carolina State Board of Elections",
                      page_url=url, official=True, unit_label="counties", contests=contests, certified_on=on)
