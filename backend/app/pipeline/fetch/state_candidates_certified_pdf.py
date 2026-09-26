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
"""

import logging
import re
from io import BytesIO
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    normalize_party,
    parse_office,
    surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_SECTION_RE = re.compile(r"^([A-Z][A-Z ]+?) CANDIDATES$")
_OFFICE_RE = re.compile(r"^For (.+)$")
_DISTRICT_RE = re.compile(r"^District (\d+),\s*(.+)$")


def parse_certification(lines: list[str]) -> list[dict]:
    """Federal candidates from the certification's text lines."""
    records: list[dict] = []
    party: str | None = None
    in_party_section = False
    office: tuple[str, int | None] | None = None
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
            continue
        header = _OFFICE_RE.match(line)
        if header:
            office = parse_office(header.group(1))
            continue
        if not in_party_section or office is None:
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

    page = await fetch_text_with_retry(client, _rate_limiter, page_url, f"{state} certification page")
    if page is None:
        return None
    links = {m.group(1) for m in re.finditer(link_regex.replace("{year}", str(year)), page)}
    if len(links) != 1:
        # Not certified yet (none), or more than one candidate document —
        # either way nothing to confirm from without guessing.
        logger.info("%s certification page links %d %d certifications", state, len(links), year)
        return None
    pdf_url = urljoin(page_url, links.pop())

    pdf_bytes = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, f"{state} certification {year}")
    if pdf_bytes is None:
        return None
    records = parse_certification(_lines(pdf_bytes))
    if not records:
        logger.warning("%s certification %s parsed no federal candidate", state, pdf_url)
        return None
    logger.info("%s certification: %d federal candidates", state, len(records))
    return records
