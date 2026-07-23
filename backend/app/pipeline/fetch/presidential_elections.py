"""Fetch presidential election-margin data from UCSB's American
Presidency Project — the pre-polling-era proxy for Public Mandate.

Gallup-style approval polling doesn't exist before the mid-1930s
(presidential_approval.py covers Truman-33 onward, matching where UCSB's
own per-president approval pages start). For presidents who governed
before scientific polling existed, this platform's only honest path to a
genuinely computed (not hand-set) Public Mandate is the real, structured,
historical fact of how decisively they won election — the same "election
margins and historian consensus" framing this platform's About page
already used, now backed by an actual fetched dataset instead of a
hand-typed number.

Two sources, both scraped (no API/CSV exists for either):
  - "Presidential Election Margins of Victory" (1824-present): a single
    table with popular-vote and electoral-vote %/margin for every
    election. Popular vote is "nd" (no data) for the earliest few
    elections in this table (before it was uniformly tabulated) — falls
    back to electoral-vote margin in that case.
  - Per-year election pages (/statistics/elections/{year}), for the
    pre-1824 elections not in that table: 1789, 1792, 1796, 1800, 1804,
    1808, 1812, 1816, 1820. Electoral-vote only (no popular vote existed
    in the modern sense — many states' legislatures chose electors
    directly).

Five presidents (Tyler-10, fillmore-13, ajohnson-17, arthur-21, ford-38)
never won a presidential election in their own right — Tyler/Fillmore/
A.Johnson/Arthur succeeded via their predecessor's death, and Ford is the
only president never even elected vice president (appointed under the
25th Amendment after Agnew's resignation, then succeeded Nixon's
resignation). Neither data source has an entry for any of them, because
the underlying construct ("how much of a mandate did voters give this
president") genuinely doesn't apply the way it does for an elected
president. That's a real, structural absence, not a scraping gap;
callers should treat missing data for these five as "this dimension does
not apply," not as a fetch failure to retry.
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

MANDATES_URL = "https://www.presidency.ucsb.edu/statistics/data/presidential-election-mandates"
ELECTION_YEAR_URL = "https://www.presidency.ucsb.edu/statistics/elections/{year}"

# Pre-1824 elections not covered by the mandates table, mapped to the
# president_id of that election's winner. (1789/1792 both won by
# Washington; the rest are one election each.)
_PRE_1824_ELECTIONS: dict[int, str] = {
    1789: "washington-1", 1792: "washington-1",
    1796: "adams-2",
    1800: "jefferson-3", 1804: "jefferson-3",
    1808: "madison-4", 1812: "madison-4",
    1816: "monroe-5", 1820: "monroe-5",
}

# Mandates-table name -> president_id. Verified against a live fetch,
# 2026-07 — the table's own text, not derived/guessed.
_NAME_TO_ID: dict[str, str] = {
    "john quincy adams": "jqadams-6",
    "andrew jackson": "jackson-7",
    "martin van buren": "vanburen-8",
    "william henry harrison": "harrison-9",
    "james k. polk": "polk-11",
    "zachary taylor": "taylor-12",
    "franklin pierce": "pierce-14",
    "james buchanan": "buchanan-15",
    "abraham lincoln": "lincoln-16",
    "ulysses s. grant": "grant-18",
    "rutherford b. hayes": "hayes-19",
    "james garfield": "garfield-20",
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
    "gerald r. ford": "ford-38",  # not present (never won an election himself) — kept for clarity, harmless if unmatched
    "jimmy carter": "carter-39",
    "ronald reagan": "reagan-40",
    "george bush": "ghwbush-41",
    "william j. clinton": "clinton-42",
    "george w. bush": "gwbush-43",
    "barack obama": "obama-44",
    "donald j. trump (first term)": "trump-45",
    "joseph r. biden, jr.": "biden-46",
    "donald j. trump (second term)": "trump-47",
}

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "historical-elections"
_CACHE_MAX_AGE_HOURS = 24 * 30  # historical results never change

# Scale-consistency constants (2026-07, #218 review S3), fit on the 50
# elections in the mandates table where both figures exist — see the two
# call sites for the regression details. Refit alongside president_scorer's
# _PUBLIC_MANDATE_ELECTION_MARGIN_MEAN/STDEV if UCSB's table changes.
_ELECTORAL_TO_POPULAR_MARGIN_SLOPE = 0.211
_ELECTORAL_SHARE_TO_POPULAR_SLOPE = 0.3925
_ELECTORAL_SHARE_TO_POPULAR_INTERCEPT = -18.67


def _normalize_name(text: str) -> str:
    # Strips a trailing single-token party-code parenthetical like "(D)"
    # — deliberately does NOT match multi-word parentheticals like
    # Trump's "(First term)"/"(Second term)" in this table, which are
    # part of the name this platform needs to distinguish his two
    # non-consecutive terms and are kept as-is in _NAME_TO_ID's keys.
    text = re.sub(r"\([A-Za-z-]+\)\s*$", "", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def _to_float(text: str) -> float | None:
    text = text.strip().replace(",", "").replace("%", "")
    if not text or text.lower() in ("nd", "n/a", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_mandates_table(html: str) -> dict[str, list[float]]:
    """Returns president_id -> list of margin percentages (one per
    election that president won), preferring popular-vote margin,
    falling back to electoral-vote margin when popular vote is "nd"."""
    doc = lxml_html.fromstring(html)
    tables = doc.cssselect("table")
    if not tables:
        return {}
    result: dict[str, list[float]] = {}
    current_id: str | None = None
    for row in tables[0].cssselect("tbody tr"):
        cells = row.cssselect("td")
        if len(cells) < 6:
            continue
        name_text = cells[0].text_content().strip()
        if name_text:
            current_id = _NAME_TO_ID.get(_normalize_name(name_text))
        if current_id is None:
            continue
        # Column order (verified against a live fetch, 2026-07): President,
        # Election, Popular%, Popular Margin, Electoral%, Electoral Margin,
        # Electoral%-Popular%. Margin (not the raw %) is what indicates how
        # decisively a president won — cells[3]/cells[5], not cells[2]/[4].
        popular_margin = _to_float(cells[3].text_content())
        electoral_margin = _to_float(cells[5].text_content())
        # When popular vote is "nd" (the earliest elections in this
        # table, before it was uniformly tabulated), rescale the
        # electoral-vote margin onto the popular-margin scale instead of
        # mixing the raw value in (2026-07 fix, #218 review S3: the
        # electoral college exaggerates margins ~5x — tens of points vs.
        # single digits — so the raw fallback structurally inflated the
        # earliest presidents' Public Mandate). Rescale fit on the 50
        # elections in this same table where BOTH margins exist:
        # popular ≈ 0.211 x electoral (through-origin least squares,
        # R²=0.56; origin-anchored because a 0-margin election is a
        # 0-margin election on either scale).
        margin = (
            popular_margin
            if popular_margin is not None
            else (
                electoral_margin * _ELECTORAL_TO_POPULAR_MARGIN_SLOPE
                if electoral_margin is not None else None
            )
        )
        if margin is not None:
            result.setdefault(current_id, []).append(margin)
    return result


def _parse_election_year_page(html: str) -> float | None:
    """Returns the winner's electoral-vote percentage from a pre-1824
    per-year election page, or None if the table wasn't found in the
    expected shape.

    Column count is NOT consistent across these pages (verified live,
    2026-07): some list only a presidential nominee, others (e.g. 1820)
    also list a vice-presidential nominee in its own column, shifting
    every later column's fixed index. Reads whichever cell's text is
    formatted as a percentage ("N.N%") instead of trusting a fixed
    position — this is what caught the bug where a fixed cells[5] index
    silently picked up 1820's raw electoral vote COUNT (231) instead of
    its percentage (98.3%) on a page with the extra VP column.
    """
    doc = lxml_html.fromstring(html)
    tables = doc.cssselect("table")
    if not tables:
        return None
    best: float | None = None
    for row in tables[0].cssselect("tr"):
        for cell in row.cssselect("td"):
            text = cell.text_content().strip()
            if not text.endswith("%"):
                continue
            pct = _to_float(text)
            if pct is not None and (best is None or pct > best):
                best = pct
    return best


async def fetch_election_margins(
    client: httpx.AsyncClient, db: Session,
) -> dict[str, float]:
    """Fetch + parse election-margin data for every president who won at
    least one presidential election (Washington through the present,
    excluding the four who succeeded without ever winning one).

    Returns president_id -> average margin percentage across that
    president's own election win(s). Empty dict (never None) on total
    failure."""
    cache_key = "all-presidents"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return cached["data"]

    margins_by_id: dict[str, list[float]] = {}
    mandates_table_ok = False

    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", MANDATES_URL, log_label="UCSB election mandates",
    )
    if resp is not None and resp.status_code == 200:
        try:
            margins_by_id = _parse_mandates_table(resp.text)
            mandates_table_ok = True
        except Exception:
            logger.exception("Failed to parse UCSB election-mandates table")
    else:
        logger.warning("Failed to fetch UCSB election-mandates table (%s)", MANDATES_URL)

    for year, pid in _PRE_1824_ELECTIONS.items():
        url = ELECTION_YEAR_URL.format(year=year)
        resp = await fetch_with_retry(
            client, _RATE_LIMITER, "GET", url, log_label=f"UCSB election {year}",
        )
        if resp is None or resp.status_code != 200:
            logger.warning("Failed to fetch UCSB %d election page (%s)", year, url)
            continue
        try:
            pct = _parse_election_year_page(resp.text)
        except Exception:
            logger.exception("Failed to parse UCSB %d election page", year)
            continue
        if pct is not None:
            # Pre-1824 pages report the winner's raw electoral vote SHARE,
            # not a margin vs. the runner-up. Map share onto the popular-
            # margin scale via the relationship fit on the 50 mandates-
            # table elections where both electoral share and popular
            # margin exist: popular ≈ 0.3925 x share − 18.67 (least
            # squares, R²=0.52). Replaces the previous ad-hoc `pct − 55.0`
            # heuristic (2026-07, #218 review S3), which had no empirical
            # basis and produced values on yet a third scale — e.g.
            # Monroe's 98.3% share mapped to +43.3 under the heuristic
            # (double any real popular margin ever recorded) vs. +19.9
            # under the fitted line.
            margins_by_id.setdefault(pid, []).append(
                _ELECTORAL_SHARE_TO_POPULAR_SLOPE * pct
                + _ELECTORAL_SHARE_TO_POPULAR_INTERCEPT
            )

    if not margins_by_id:
        logger.warning("No election-margin data fetched for any president this run")
        return {}

    result = {pid: sum(vals) / len(vals) for pid, vals in margins_by_id.items()}
    if mandates_table_ok:
        api_cache_set(db, _CACHE_TIER, cache_key, {"data": result})
    else:
        # 2026-07 (#218 review S1): the mandates table covers 1824-present
        # (the vast majority of presidents); only total failure used to
        # skip caching, so a mandates-table outage with the nine pre-1824
        # year-pages still succeeding cached a 5-president dataset for 30
        # days, nulling Public Mandate for every post-1824 pre-Truman
        # president for a month. Still return this run's real (if
        # partial) data — B2's fallback-to-stored-value in
        # president_pipeline.py covers anyone missing here — just don't
        # lock it into the cache; next run retries the mandates table.
        logger.warning("UCSB election-mandates table fetch failed this run — not caching partial results")
    return result
