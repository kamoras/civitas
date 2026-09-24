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

from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
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

# Scale-consistency fits (2026-07, #218 review S3): the electoral college
# exaggerates margins, so electoral-only figures are rescaled onto the
# popular-margin scale. The fits are re-estimated on every fetch from the
# elections in the mandates table where BOTH figures exist (_fit_scales) —
# they were hand-typed (0.211; 0.3925 x share - 18.67) from a one-off fit
# of those same ~50 rows (AGENTS.md §3a). Fewer usable rows than this means
# the table's shape changed; rescaling is skipped rather than guessed.
_MIN_FIT_ROWS = 20


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


def _mandates_rows(html: str) -> list[tuple[str, float | None, float | None, float | None]]:
    """(president_id, popular_margin, electoral_pct, electoral_margin) per
    election row of UCSB's mandates table."""
    doc = lxml_html.fromstring(html)
    tables = doc.cssselect("table")
    if not tables:
        return []
    rows = []
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
        # decisively a president won.
        rows.append((
            current_id,
            _to_float(cells[3].text_content()),
            _to_float(cells[4].text_content()),
            _to_float(cells[5].text_content()),
        ))
    return rows


def _fit_scales(rows) -> dict | None:
    """Least-squares maps from electoral figures onto the popular-margin
    scale, fit on rows where both exist:

    - margin_slope: popular_margin ≈ slope x electoral_margin, through the
      origin (a 0-margin election is a 0-margin election on either scale);
    - share_slope / share_intercept: popular_margin ≈ a + b x electoral
      share, for pre-1824 pages that report only the winner's share.

    None when fewer than _MIN_FIT_ROWS rows have both."""
    pairs = [(pm, ep, em) for _, pm, ep, em in rows if pm is not None and ep is not None and em is not None]
    if len(pairs) < _MIN_FIT_ROWS:
        return None
    sxx = sum(em * em for _, _, em in pairs)
    margin_slope = sum(pm * em for pm, _, em in pairs) / sxx if sxx else 0.0
    n = len(pairs)
    mx = sum(ep for _, ep, _ in pairs) / n
    my = sum(pm for pm, _, _ in pairs) / n
    vx = sum((ep - mx) ** 2 for _, ep, _ in pairs)
    share_slope = sum((ep - mx) * (pm - my) for pm, ep, _ in pairs) / vx if vx else 0.0
    return {
        "margin_slope": margin_slope,
        "share_slope": share_slope,
        "share_intercept": my - share_slope * mx,
        "n": n,
    }


def _parse_mandates_table(html: str) -> tuple[dict[str, list[float]], dict | None]:
    """Returns (president_id -> list of margin percentages, one per election
    that president won, and the scale fits). Popular-vote margin is used
    where it exists; where it's "nd" (the earliest elections in the table)
    the electoral-vote margin is rescaled onto the popular scale instead of
    mixed in raw — the electoral college exaggerates margins ~5x, which
    structurally inflated the earliest presidents' Public Mandate (#218
    review S3). With no usable fit, those elections are left out."""
    rows = _mandates_rows(html)
    fits = _fit_scales(rows)
    if fits is None:
        logger.warning("Too few mandates rows with both popular and electoral figures to fit a rescale")
    result: dict[str, list[float]] = {}
    for pid, popular_margin, _, electoral_margin in rows:
        if popular_margin is not None:
            margin = popular_margin
        elif electoral_margin is not None and fits is not None:
            margin = electoral_margin * fits["margin_slope"]
        else:
            continue
        result.setdefault(pid, []).append(margin)
    return result, fits


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


async def fetch_election_margins(db: Session) -> dict[str, float]:
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
    fits: dict | None = None
    mandates_table_ok = False

    resp = await fetch_with_retry_requests(
        _RATE_LIMITER, "GET", MANDATES_URL, log_label="UCSB election mandates",
    )
    if resp is not None and resp.status_code == 200:
        try:
            margins_by_id, fits = _parse_mandates_table(resp.text)
            mandates_table_ok = fits is not None
        except Exception:
            logger.exception("Failed to parse UCSB election-mandates table")
    else:
        logger.warning("Failed to fetch UCSB election-mandates table (%s)", MANDATES_URL)

    for year, pid in _PRE_1824_ELECTIONS.items():
        if fits is None:
            # The share->margin map is fit from the mandates table; without
            # it these pages can't be put on the same scale.
            break
        url = ELECTION_YEAR_URL.format(year=year)
        resp = await fetch_with_retry_requests(
            _RATE_LIMITER, "GET", url, log_label=f"UCSB election {year}",
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
            # margin scale via the line fit on the mandates-table elections
            # where both exist (_fit_scales; 2026-07 fit: 0.3925 x share −
            # 18.67, R²=0.52). Replaced an ad-hoc `pct − 55.0` heuristic
            # (#218 review S3) that mapped Monroe's 98.3% share to +43.3 —
            # double any real popular margin ever recorded — vs. +19.9.
            margins_by_id.setdefault(pid, []).append(
                fits["share_slope"] * pct + fits["share_intercept"]
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
