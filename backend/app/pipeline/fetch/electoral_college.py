"""Every presidential election's electoral vote, from the National Archives.

The Office of the Federal Register keeps a page per election
(archives.gov/electoral-college/<year>, listed on its results page): the
president it names, that candidate's electoral votes, the main opponent's,
and the total of electors. Read for the president page's elections chart,
beside each election's popular-vote margin (the American Presidency
Project's mandates table, which the president pipeline already reads; no
federal source tabulates the popular vote for every election).

Which president an election belongs to is read from the roster's own term
dates, not from names: the presidency in office a week after the
inauguration day that followed the election (March 4 until 1933, January
20 from 1937, under the 20th Amendment; the week clears a Sunday shift).
The archives' page names the electoral-vote leader, which in 1824 was not
the person the House of Representatives elected; the office holder's
electoral votes are then the main opponent's, and the election is marked
decided by the House (as it is when the leaders tied, in 1800).

Not scored. Stored in api_cache (`stored_elections`); an outage keeps the
last stored set.
"""

import html as html_lib
import logging
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import President
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.fetch.presidential_elections import MANDATES_URL, popular_margin_by_year
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

RESULTS_URL = "https://www.archives.gov/electoral-college/results"
YEAR_URL = "https://www.archives.gov/electoral-college/{year}"
_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "electoral-college"
_CACHE_KEY = "elections"
_CACHE_TTL_HOURS = 24 * 30
_NO_EXPIRY_HOURS = 24 * 365 * 100
_TAG = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S)
# A page whose figures don't parse is skipped; a year count this far under
# the results page's list means the layout changed, and nothing is stored.
_MIN_SHARE_PARSED = 0.9


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG.sub(" ", page)))


def parse_year_page(page: str) -> dict | None:
    """{president, winnerEv, opponent, opponentEv, electors} from one
    election's page, or None when the figures aren't there."""
    t = _text(page)
    t = t[t.find("Electoral College Results", t.find("Contact")):]
    pres = re.search(r"President (.+?) \[", t)
    opp = re.search(r"(?:Main Opponent|Opponents) (.+?)(?: \[| \(|;)", t)
    # Footnote marks ("Electoral Vote*", "286*", "176 (175)*") and a range
    # ("(71, excluding Georgia; ...") are read past: the first figure counts.
    ev = re.search(r"Electoral Vote\*? Winner: (\d+) Main Opponent: \(?(\d+)", t)
    total = re.search(r"Total/Majority: (\d+)", t)
    if not (pres and ev and total):
        return None
    return {
        "president": pres.group(1).strip(),
        "opponent": opp.group(1).strip() if opp else None,
        "winnerEv": int(ev.group(1)),
        "opponentEv": int(ev.group(2)),
        "electors": int(total.group(1)),
    }


def _surname(name: str) -> str:
    words = re.sub(r",?\s+(jr|sr)\.?$", "", name.strip(), flags=re.I).split()
    return words[-1].lower().strip(".,") if words else ""


def inauguration_check_date(year: int) -> date:
    """A week after the inauguration day following a `year` election."""
    day = date(year + 1, 3, 4) if year + 1 < 1937 else date(year + 1, 1, 20)
    return day + timedelta(days=7)


def holder(presidents: list[President], year: int) -> President | None:
    when = inauguration_check_date(year).isoformat()
    return next((p for p in presidents if p.term_start <= when and (p.term_end is None or when < p.term_end)),
                None)


def assign(results: dict[int, dict], presidents: list[President], margins: dict[int, float]) -> list[dict]:
    """One row per election, with the office holder it put in office."""
    rows = []
    for year in sorted(results):
        r, p = results[year], holder(presidents, year)
        if p is None:
            continue
        named = _surname(p.name) in r["president"].lower()
        ev = r["winnerEv"] if named else r["opponentEv"]
        rows.append({
            "year": year, "presidentId": p.id, "president": p.name,
            "ev": ev, "electors": r["electors"], "evShare": round(100 * ev / r["electors"], 1),
            "popularMargin": margins.get(year),
            "decidedByHouse": (not named) or r["winnerEv"] == r["opponentEv"],
        })
    return rows


async def fetch_elections(db: Session) -> list[dict] | None:
    """Every election's row (see `assign`), or None when the archives can't
    be read (the last stored set is kept)."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_TTL_HOURS)
    if cached is not None:
        return cached["elections"]
    index = await fetch_with_retry_requests(_RATE_LIMITER, "GET", RESULTS_URL, log_label="NARA electoral results")
    if index is None or index.status_code != 200:
        logger.warning("NARA electoral college results could not be read")
        return None
    years = sorted({int(y) for y in re.findall(r'href="(?:https://www\.archives\.gov)?/electoral-college/(\d{4})"',
                                               index.text)})
    results: dict[int, dict] = {}
    for year in years:
        resp = await fetch_with_retry_requests(_RATE_LIMITER, "GET", YEAR_URL.format(year=year),
                                               log_label=f"NARA electoral {year}")
        parsed = parse_year_page(resp.text) if resp is not None and resp.status_code == 200 else None
        if parsed:
            results[year] = parsed
        else:
            logger.warning("NARA electoral college page for %d did not parse", year)
    if not years or len(results) < _MIN_SHARE_PARSED * len(years):
        logger.warning("Only %d of %d NARA election pages parsed; keeping the last stored set", len(results),
                       len(years))
        return None
    margins: dict[int, float] = {}
    resp = await fetch_with_retry_requests(_RATE_LIMITER, "GET", MANDATES_URL, log_label="UCSB election mandates")
    if resp is not None and resp.status_code == 200:
        margins = popular_margin_by_year(resp.text)
    rows = assign(results, db.query(President).all(), margins)
    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"elections": rows})
    return rows


def stored_elections(db: Session) -> list[dict]:
    """The last stored rows, whatever their age; empty when none."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_NO_EXPIRY_HOURS)
    return cached["elections"] if cached else []
