"""Fetch executive-order counts for every president (Washington onward)
from UCSB's American Presidency Project.

Replaces Federal Register as the source for Competence's EO-activity-rate
component. Federal Register's own machine-readable presidential-document
coverage is a hard wall at 1994 (Clinton onward) — real for that source,
but it left every earlier president with no live EO-rate signal at all.
UCSB maintains a single table ("Executive Orders by President, Average
Per Years in Office") covering Washington through the current term in one
place, actively updated (observed during development: "Current averages
through July 20, 2026" — i.e. current within the current term, not just
historical). One fetch replaces the Federal-Register-only, 1994-plus
coverage with the full presidency.

No API/CSV — scrapes the rendered HTML table, same risk class as
presidential_approval.py's UCSB scrape.
"""

import logging
import re

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://www.presidency.ucsb.edu/statistics/data/executive-orders"

# UCSB's table lists presidents by full name + party abbreviation, not
# this platform's id scheme, and splits non-consecutive-term presidents
# (Cleveland, Trump) into separate "- I"/"- II" rows matching this
# platform's own id split (cleveland-22/cleveland-24, trump-45/trump-47).
# Verified against a live fetch, 2026-07 — table text is the left side,
# checked with .startswith() so the trailing "(D)"/"(R)"/etc. doesn't need
# to be matched exactly.
NAME_TO_ID: dict[str, str] = {
    "george washington": "washington-1",
    "john adams": "adams-2",
    "thomas jefferson": "jefferson-3",
    "james madison": "madison-4",
    "james monroe": "monroe-5",
    "john quincy adams": "jqadams-6",
    "andrew jackson": "jackson-7",
    "martin van buren": "vanburen-8",
    "william henry harrison": "harrison-9",
    "john tyler": "tyler-10",
    "james k. polk": "polk-11",
    "zachary taylor": "taylor-12",
    "millard fillmore": "fillmore-13",
    "franklin pierce": "pierce-14",
    "james buchanan": "buchanan-15",
    "abraham lincoln": "lincoln-16",
    "andrew johnson": "ajohnson-17",
    "ulysses s. grant": "grant-18",
    "rutherford b. hayes": "hayes-19",
    "james garfield": "garfield-20",
    "chester arthur": "arthur-21",
    "grover cleveland - i": "cleveland-22",
    "benjamin harrison": "bharrison-23",
    "grover cleveland - ii": "cleveland-24",
    "william mckinley": "mckinley-25",
    "theodore roosevelt": "troosevelt-26",
    "william howard taft": "taft-27",
    "woodrow wilson": "wilson-28",
    "warren g. harding": "harding-29",
    "calvin coolidge": "coolidge-30",
    "herbert hoover": "hoover-31",
    "franklin d. roosevelt": "fdr-32",
    "harry s. truman": "truman-33",
    "dwight d. eisenhower": "eisenhower-34",
    "john f. kennedy": "jfk-35",
    "lyndon b. johnson": "lbj-36",
    "richard nixon": "nixon-37",
    "gerald r. ford": "ford-38",
    "jimmy carter": "carter-39",
    "ronald reagan": "reagan-40",
    "george bush": "ghwbush-41",
    "william j. clinton": "clinton-42",
    "george w. bush": "gwbush-43",
    "barack obama": "obama-44",
    "donald j. trump - i": "trump-45",
    "joseph r. biden, jr.": "biden-46",
    "donald j. trump - ii": "trump-47",
}

# Other sources reference a handful of these same presidents with a
# fuller middle name/initial than NAME_TO_ID's keys (themselves sourced
# from UCSB's EO table, whose own name text is inconsistent about this).
# Reused by resolve_president_id below rather than letting every new
# fetcher (presidential_roster.py, cspan_historians_survey.py) build its
# own separate alias table for the identical mismatches.
_NAME_ALIASES: dict[str, str] = {
    "james a garfield": "james garfield",
    "chester a arthur": "chester arthur",
    "george h w bush": "george bush",
    "richard m nixon": "richard nixon",
}


# Periods stripped from both sides at lookup time: NAME_TO_ID's own keys
# retain them ("james k. polk"), but not every source renders a period
# after a middle initial (e.g. presidential_roster.py's "Harry S Truman").
_NAME_LOOKUP: dict[str, str] = {k.replace(".", ""): v for k, v in NAME_TO_ID.items()}


def resolve_president_id(name: str) -> str | None:
    """Resolve a plain president name (case/whitespace/period differences
    handled here; term-disambiguating suffixes like "- I"/"- II" must
    already be applied by the caller, since only the caller knows its own
    source's convention for non-consecutive terms) to this platform's id
    scheme. Shared by every fetcher that references presidents by name
    rather than a pre-existing id."""
    normalized = re.sub(r"\s+", " ", name.replace(".", "").strip().lower())
    normalized = _NAME_ALIASES.get(normalized, normalized)
    return _NAME_LOOKUP.get(normalized)


_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "historical-eo"
_CACHE_KEY = "all-presidents"
_CACHE_MAX_AGE_HOURS = 20


_PARTY_TAG = re.compile(r"\(([A-Za-z-]+)\)\s*$")


def _normalize_name(text: str) -> str:
    # Strip a trailing "(D)"/"(R)"/"(D-R)"/"(F)"/"(W)" party tag and
    # collapse whitespace so table text matches NAME_TO_ID's keys.
    text = _PARTY_TAG.sub("", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def _extract_party(text: str) -> str | None:
    """Pulls the party abbreviation UCSB embeds in the same cell as the
    name (e.g. "George Washington (F)" -> "F") — this table's party tag
    is the only place this fetcher needs to look; presidential_roster.py
    reuses this rather than fetching party from a second source."""
    m = _PARTY_TAG.search(text.strip())
    return m.group(1) if m else None


def _parse_eo_table(html: str) -> dict[str, dict]:
    """Returns president_id -> {"total_orders": int, "avg_per_year": float,
    "years_in_office": float, "party": str | None}."""
    doc = lxml_html.fromstring(html)
    tables = doc.cssselect("table")
    result: dict[str, dict] = {}
    for table in tables:
        for row in table.cssselect("tbody tr"):
            cells = row.cssselect("td")
            if len(cells) < 5:
                continue
            name_raw = cells[0].text_content().strip()
            if not name_raw:
                continue
            pid = NAME_TO_ID.get(_normalize_name(name_raw))
            if pid is None:
                continue

            def _num(cell) -> float | None:
                text = cell.text_content().strip().replace(",", "")
                try:
                    return float(text)
                except ValueError:
                    return None

            total = _num(cells[2])
            avg_per_year = _num(cells[3])
            years = _num(cells[4])
            if total is None or years is None:
                continue
            result[pid] = {
                "total_orders": int(total),
                "avg_per_year": avg_per_year,
                "years_in_office": years,
                "party": _extract_party(name_raw),
            }
        if result:
            break  # the EO table is the first (and only) real data table on the page
    return result


async def fetch_historical_eo_counts(
    client: httpx.AsyncClient, db: Session,
) -> dict[str, dict]:
    """Fetch + parse EO counts for every president in one request.

    Returns an empty dict (never None) on total failure — callers should
    treat "no data this run" as "keep whatever was already stored," same
    as every other fetch function's failure posture in this pipeline."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return cached["data"]

    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", URL, log_label="UCSB executive orders",
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch UCSB executive-orders table (%s)", URL)
        return {}

    try:
        data = _parse_eo_table(resp.text)
    except Exception:
        logger.exception("Failed to parse UCSB executive-orders table")
        return {}

    if not data:
        logger.warning("UCSB executive-orders table parsed to zero rows — page structure may have changed")
        return {}

    unmatched_expected = set(NAME_TO_ID.values()) - set(data.keys())
    if unmatched_expected:
        logger.warning(
            "UCSB executive-orders table: %d expected president(s) not matched: %s",
            len(unmatched_expected), sorted(unmatched_expected),
        )

    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"data": data})
    return data
