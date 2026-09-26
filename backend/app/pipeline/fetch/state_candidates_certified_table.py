"""A state's certified general-election candidate list, published as a
spreadsheet (xlsx or csv) — one row per candidate on the November ballot.

Maine is the live case, and the reason this exists. Maine's adapter read
the June primary's official results and confirmed Graham Platner as the
Democratic Senate nominee. He was: he won with 77.7%. He then withdrew on
2026-07-10 and the party nominated Troy Jackson at a convention on July
25. Primary results cannot see that, and they never will — the same way
South Carolina's results named Lindsey Graham after a special primary had
replaced him. The Secretary of State's "2026 General Candidate List" is
the ballot as certified, replacement included, so it is read instead.

Every state-specific detail is configuration: where the list is linked
from, which columns hold what, and how the state codes its federal
offices (Maine writes "US" for Senate and "CG" for Congress, which no
label parser should be taught to guess).

    "discovery": {"page_url": ..., "link_regex": "...{year}..."},
    "format": {
        "office_column": "Office",
        "office_codes": {"US": "S", "CG": "H"},
        "district_column": "Dist",
        "party_column": "Party",
        "surname_column": "Last Name",
        "name_columns": ["First Name", "Middle Name", "Last Name", "Suffix"]
    }
"""

import csv
import io
import logging

import httpx

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    discover_certification_link,
    normalize_party,
)
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)


def _rows(payload: bytes, url: str) -> list[dict] | None:
    if url.lower().split("?")[0].endswith(".csv"):
        try:
            return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error):
            return None
    return _xlsx_rows(payload)


def parse_certified_rows(rows: list[dict], fmt: dict) -> list[dict]:
    """Federal candidate records from the list's rows."""
    codes = {str(k).strip().upper(): v for k, v in (fmt.get("office_codes") or {}).items()}
    records = []
    for row in rows:
        office = codes.get(str(row.get(fmt["office_column"]) or "").strip().upper())
        if office not in ("S", "H"):
            continue
        district = None
        if office == "H":
            digits = "".join(ch for ch in str(row.get(fmt["district_column"]) or "") if ch.isdigit())
            district = int(digits) if digits else 0
        last = str(row.get(fmt["surname_column"]) or "").strip()
        if not last:
            continue
        display = clean_display_name(
            " ".join(str(row.get(col) or "").strip() for col in fmt["name_columns"])
        )
        party_label = str(row.get(fmt["party_column"]) or "").strip()
        records.append({
            "office": office,
            "district": district,
            # A certified ballot: "Independent"/"Unenrolled" is an entry.
            "party": normalize_party(party_label, ballot_list=True),
            "last_name": last,
            "display_name": display,
            "party_label": party_label,
        })
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    discovery = source.get("discovery") or {}
    fmt = source.get("format") or {}
    if not discovery.get("page_url") or not discovery.get("link_regex"):
        logger.warning("%s certified_table source needs discovery.page_url and link_regex", state)
        return None
    missing = [k for k in ("office_column", "office_codes", "district_column", "party_column",
                           "surname_column", "name_columns") if not fmt.get(k)]
    if missing:
        logger.warning("%s certified_table format is missing %s", state, missing)
        return None

    url = await discover_certification_link(
        client, _rate_limiter, discovery["page_url"], discovery["link_regex"], year, state,
    )
    if url is None:
        return None
    payload = await fetch_bytes_with_retry(client, _rate_limiter, url, f"{state} certified list {year}")
    if payload is None:
        return None
    rows = _rows(payload, url)
    if not rows:
        logger.warning("%s certified list %s did not parse", state, url)
        return None
    records = parse_certified_rows(rows, fmt)
    if not records:
        logger.warning("%s certified list %s has no federal candidate — columns or codes changed?", state, url)
        return None
    logger.info("%s certified list: %d federal candidates", state, len(records))
    return records
