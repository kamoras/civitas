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

Optional, each because a live state needed it:
  discovery.index_url + index_regex  one hop first, to the page that links the
                                     file (Virginia: index -> "{year} November
                                     Federal Offices" page -> xlsx)
  format.surname_column omitted      the name is one printed column; the
                                     surname is its last word (Colorado)
  format.status_column/status_values keep only these statuses ("Qualified")
  format.exclude                     {column: value} rows to drop — Colorado
                                     lists declared write-ins, who are not
                                     printed on the ballot

Only the columns named here are read. Virginia's list carries every
candidate's campaign email, phone and street address beside the ballot
fields; this platform has no reason to hold them.

Rows repeat (Virginia prints each candidate once per locality), so records
are deduplicated.
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
    surname,
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
    codes = {" ".join(str(k).split()).upper(): v for k, v in (fmt.get("office_codes") or {}).items()}
    statuses = {str(v).strip().upper() for v in fmt.get("status_values") or []}
    exclude = {col: str(val).strip().upper() for col, val in (fmt.get("exclude") or {}).items()}
    records: dict[tuple, dict] = {}
    for row in rows:
        if statuses and str(row.get(fmt["status_column"]) or "").strip().upper() not in statuses:
            continue
        if any(str(row.get(col) or "").strip().upper() == val for col, val in exclude.items()):
            continue
        office = codes.get(" ".join(str(row.get(fmt["office_column"]) or "").split()).upper())
        if office not in ("S", "H"):
            continue
        district = None
        if office == "H":
            digits = "".join(ch for ch in str(row.get(fmt["district_column"]) or "") if ch.isdigit())
            district = int(digits) if digits else 0
        display = clean_display_name(
            " ".join(str(row.get(col) or "").strip() for col in fmt["name_columns"])
        )
        last = (
            str(row.get(fmt["surname_column"]) or "").strip()
            if fmt.get("surname_column") else (surname(display) or "")
        )
        if not last:
            continue
        party_label = str(row.get(fmt["party_column"]) or "").strip()
        records[(office, district, display.lower())] = {
            "office": office,
            "district": district,
            # A certified ballot: "Independent"/"Unenrolled" is an entry.
            "party": normalize_party(party_label, ballot_list=True),
            "last_name": last,
            "display_name": display,
            "party_label": party_label,
        }
    return list(records.values())


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    discovery = source.get("discovery") or {}
    fmt = source.get("format") or {}
    if not (discovery.get("page_url") or discovery.get("index_url")) or not discovery.get("link_regex"):
        logger.warning("%s certified_table source needs discovery.page_url and link_regex", state)
        return None
    missing = [k for k in ("office_column", "office_codes", "district_column", "party_column",
                           "name_columns") if not fmt.get(k)]
    if missing:
        logger.warning("%s certified_table format is missing %s", state, missing)
        return None

    page_url = discovery.get("page_url")
    if discovery.get("index_url") and discovery.get("index_regex"):
        page_url = await discover_certification_link(
            client, _rate_limiter, discovery["index_url"], discovery["index_regex"], year, state,
        )
        if page_url is None:
            return None
    url = await discover_certification_link(
        client, _rate_limiter, page_url, discovery["link_regex"], year, state,
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
