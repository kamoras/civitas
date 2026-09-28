"""The Secretary of State's certification of general-election candidates,
published as a PDF (Missouri's "Certification of Candidates and Party
Emblems").

Missouri sat on `google_civic` all cycle. Its election-night site
(enr.sos.mo.gov) is behind a bot challenge, but it does not matter: on
2026-08-25 the Secretary of State certified the November ballot to every
county election authority, and that document is linked from the SOS's
own results page. It is the ballot itself — Republican, Democratic,
Libertarian and independent candidates for every office — so it answers
`confirmed_general` directly.

The one concern with any PDF source here is text extraction separating a
name from its number; that is why results PDFs are not read this way.
This document has no vote counts. Its text is a plain sequence:

    REPUBLICAN CANDIDATES          <- a party section
    For U.S. Representative        <- an office within it
    District 1, Paul Berry III     <- one line per candidate
    ...
    INDEPENDENT CANDIDATES
    JUDICIAL CANDIDATES            <- non-partisan: parsing stops mattering

so every candidate line is read under the nearest party and office
header above it. The link is found by a regex carrying `{year}`, so the
next cycle's certification is picked up without an edit.

With the source's `statewide_offices` opt-in, the same party sections'
state offices are read too — "For State Auditor" (Missouri's only
executive office in 2026, one candidate per party) and "For State
Senator" / "For State Representative", each followed by the same
"District N, Name" lines as the U.S. House. Every name listed is on the
November ballot, so every one is a record, exactly as for the federal
offices; nothing is resolved from votes because there are none. The
header is handed to the shared gates (parse_statewide_office, then
parse_state_leg_office) with the line's district restated as "District
N", so the gates make every decision. "For Circuit Judge" appears under
the party sections too and is refused by both gates; judicial contests
are a separate claim (`judicial_offices`) this strategy does not make.
"""

import logging
import re
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    discover_certification_link,
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_SECTION_RE = re.compile(r"^([A-Z][A-Z ]+?) CANDIDATES$")
_OFFICE_RE = re.compile(r"^For (.+)$")
_DISTRICT_RE = re.compile(r"^District (\d+),\s*(.+)$")


def _state_record(header: str, line: str, party: str | None) -> dict | None:
    """The statewide-executive or legislative record one candidate line
    under `header` makes, or None when the gates refuse the pair."""
    district_line = _DISTRICT_RE.match(line)
    label = f"{header} District {district_line.group(1)}" if district_line else header
    name = clean_display_name(district_line.group(2) if district_line else line)
    if not name:
        return None
    statewide = parse_statewide_office(label)
    if statewide is not None:
        office, seat = statewide
        if district_line and seat is None:
            # A district beside an office that has none ("Secretary of
            # State, District 3") is a line this does not understand.
            return None
        return {"office": office, "district": seat, "party": party, "last_name": name}
    if not district_line:
        # Every legislative seat is printed "District N, Name"; a bare
        # line under one is the next block's prose.
        return None
    leg = parse_state_leg_office(label)
    if leg is None:
        return None
    chamber, district, seat = leg
    record = {"office": chamber, "district": district, "party": party, "last_name": name}
    if seat is not None:
        record["seat"] = seat
    return record


def parse_certification(lines: list[str], state_offices: bool = False) -> list[dict]:
    """Federal candidates from the certification's text lines — and,
    with `state_offices`, the statewide-executive and legislative ones
    (see the module docstring)."""
    records: list[dict] = []
    party: str | None = None
    in_party_section = False
    office: tuple[str, int | None] | None = None
    # The header of a state office being read, or None.
    state_header: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line.isdigit():  # blank, or a page number
            continue
        section = _SECTION_RE.match(line)
        if section:
            # "JUDICIAL CANDIDATES" is a section with no party at all —
            # nothing after it is a partisan federal nominee.
            party = normalize_party(section.group(1), ballot_list=True)
            in_party_section = party is not None
            office = None
            state_header = None
            continue
        header = _OFFICE_RE.match(line)
        if header:
            office = parse_office(header.group(1))
            state_header = None
            if office is None and state_offices:
                # Whether it is a state office at all is decided line by
                # line in _state_record, where the district is known.
                state_header = header.group(1)
            continue
        if not in_party_section:
            continue
        if state_header is not None:
            record = _state_record(state_header, line, party)
            if record is None:
                # Not a candidate line for this office: the block ended.
                state_header = None
                continue
            records.append(record)
            if record["office"] not in ("upper", "lower") and record["district"] is None:
                # One candidate per party for a single-seat statewide
                # office, as for the U.S. Senate below.
                state_header = None
            continue
        if office is None:
            continue
        chamber, at_large = office
        district_line = _DISTRICT_RE.match(line)
        if chamber == "H" and not district_line:
            # Every House line is "District N, Name"; anything else under a
            # House header is the next block's prose, not a candidate.
            office = None
            continue
        district = int(district_line.group(1)) if district_line else at_large
        display = clean_display_name(district_line.group(2) if district_line else line)
        last = surname(display)
        if not last:
            continue
        records.append({
            "office": chamber,
            "district": district,
            "party": party,
            "last_name": last,
            "display_name": display,
        })
        if chamber == "S":
            # One Senate candidate per party per seat; the next line is
            # the next office's header or another section.
            office = None
    return records


def _lines(pdf_bytes: bytes) -> list[str]:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return [ln for page in pdf.pages for ln in (page.extract_text() or "").splitlines()]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    page_url = source.get("page_url")
    link_regex = source.get("link_regex")
    if not page_url or not link_regex:
        logger.warning("%s certified_pdf source needs page_url and link_regex", state)
        return None

    pdf_url = await discover_certification_link(client, _rate_limiter, page_url, link_regex, year, state)
    if pdf_url is None:
        return None

    pdf_bytes = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, f"{state} certification {year}")
    if pdf_bytes is None:
        return None
    records = parse_certification(_lines(pdf_bytes), bool(source.get("statewide_offices")))
    if not any(r["office"] in ("S", "H") for r in records):
        logger.warning("%s certification %s parsed no federal candidate", state, pdf_url)
        return None
    logger.info(
        "%s certification: %d candidates (%d federal)", state, len(records),
        sum(r["office"] in ("S", "H") for r in records),
    )
    return records
