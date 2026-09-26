"""A state's official canvass of its partisan primary, published as a
summary PDF in which the state itself marks each contest's winner.

Wisconsin is the live case. Every page of elections.wi.gov answers a
server request with a bot challenge, which is why the state sat on
`google_civic` — but the Commission's document files are served from an
open path, and the certified canvass (WEC Canvass Reporting System,
"Canvass Results for 2026 Partisan Primary", generated 2026-08-27) is one
of them. It reads, per page:

    Office REPRESENTATIVE IN CONGRESS DISTRICT 1 Total Votes: 122,554
    Party: Republican Total Votes: 51,119
    Winner 50,915 99.6% Bryan Steil Republican
    204 .4%
    SCATTERING

The state's own "Winner" mark names the nominee, so nothing is derived
from vote totals here — the concern that keeps results PDFs out of this
pipeline (a name separated from its number) does not arise when the one
line that matters carries the winner mark, the count and the name
together. A "Winner" line with no name (text extraction does drop one now
and then) names nobody rather than the next name down the page; a
SCATTERING winner (write-ins totalled, a party with no candidate) is not
a person.

This is primary results, so it names party nominees and cannot see an
independent — the state stays general_ballot_complete: false, and the
page says "nominees", not "confirmed".

The file is found from the primary date (the FEC calendar supplies it):
`url_templates` are tried in order with {year}, {primary_month} and
{primary_date} filled in. Wisconsin renamed this file between cycles, so
a template is a best guess for the next one, not a promise; when none
resolves the source returns None and the state's `fallback` source runs.
"""

import logging
import re
from datetime import date
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.fetch.state_candidates_common import (
    federal_record,
    normalize_party,
    parse_office,
)
from app.pipeline.fetch.state_election_dates import primary_date
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_OFFICE_RE = re.compile(r"^Office\s+(.+?)(?:\s+Total Votes:.*)?$")
_PARTY_RE = re.compile(r"^Party:\s+(.+?)(?:\s+Total Votes:.*|\s+[\d,]+)?$")
_WINNER_RE = re.compile(r"^Winner\s+[\d,]+\s+[\d.]+%\s*(?P<rest>.*)$")
# The candidate's party printed after the name, in the report's own words.
_PARTY_WORDS = frozenset({
    "republican", "democrat", "democratic", "libertarian", "green", "wisconsin",
    "constitution", "independent", "party",
})


def _winner_name(rest: str) -> str | None:
    words = rest.split()
    while words and words[-1].lower() in _PARTY_WORDS:
        words.pop()
    name = " ".join(words).strip()
    if not name or name.upper() == "SCATTERING" or len(name.split()) < 2:
        return None
    return name


def parse_canvass(lines: list[str]) -> list[dict]:
    """One record per (federal office, district, party) the state marks a
    named winner for."""
    records: dict[tuple, dict] = {}
    office: tuple[str, int | None] | None = None
    party: str | None = None
    for raw in lines:
        line = raw.strip()
        m = _OFFICE_RE.match(line)
        if m:
            office = parse_office(m.group(1))
            party = None
            continue
        m = _PARTY_RE.match(line)
        if m:
            # A minor party's heading repeats the office: "Party: REPRESENTATIVE
            # IN CONGRESS DISTRICT 1 - Constitution".
            label = m.group(1).rsplit(" - ", 1)[-1]
            party = normalize_party(label)
            continue
        m = _WINNER_RE.match(line)
        if not m or office is None or party is None:
            continue
        name = _winner_name(m.group("rest"))
        if name is None:
            continue
        key = (office[0], office[1], party)
        if key in records:
            continue  # a page break repeats the office heading
        record = federal_record(office[0], office[1], party, name)
        if record:
            records[key] = record
    return list(records.values())


def _lines(pdf_bytes: bytes) -> list[str]:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return [ln for page in pdf.pages for ln in (page.extract_text() or "").splitlines()]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    templates = source.get("url_templates") or []
    held = primary_date(state, year)
    if not templates or not held:
        logger.info("%s canvass: no url_templates or no primary date for %d", state, year)
        return None
    day = date.fromisoformat(held)
    for template in templates:
        url = template.format(year=year, primary_month=day.strftime("%B"), primary_date=held)
        payload = await fetch_bytes_with_retry(
            client, _rate_limiter, url, f"{state} primary canvass {year}", retry_on_4xx=False,
        )
        if not payload or not payload.startswith(b"%PDF"):
            continue
        records = parse_canvass(_lines(payload))
        if records:
            logger.info("%s canvass %s: %d federal nominees", state, url, len(records))
            return records
        logger.warning("%s canvass %s parsed no federal winner", state, url)
    return None
