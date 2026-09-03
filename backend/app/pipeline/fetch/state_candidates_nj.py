"""New Jersey's confirmed-general-election-candidate strategy — the
Division of Elections' own "Certification of General Election Nominees"
PDF (one of potentially many per-state strategies in state_candidates.py;
see that module for the shared contract).

A stable, evergreen url_pattern, verified across 2 real cycles (2024,
2026, both 200): nj.gov/state/elections/assets/pdf/election-results/
{year}/{year}-certification-of-general-election-nominees_us-senate_us-
house.pdf. This is the state's own POST-primary confirmed nominee list
(dated 07/27/2026, "For GENERAL ELECTION 11/03/2026" — a genuinely
different, later document from the pre-primary
"{year}-certification-of-primary-nominees.pdf", which only lists who
filed to RUN in the primary, not who won it).

Column-based, not narrative text: verified via word x0 positions (not
by parsing flowing extract_text(), which is fragile here) that this is
a real tabular report — "Name" column starts x0≈14, "Party" x0≈266,
"County" x0≈321. Each real candidate's name and party sit on the SAME
row, in those two columns; every other row on the page repeats that
same candidate's party once per county the district touches (an
artifact of NJ printing ballots per-county), landing in the County/
Slogan columns (x0≈321/384) with NOTHING in the Name column — which is
exactly how this module tells a real candidate row from 20 repeats of
the same one. A text-only parse (matching on ALL-CAPS "COUNTYNAME
PARTYNAME" lines) was tried and rejected: an independent candidate's
own chosen slogan ("AFFORDABILITY, ACCOUNTABILITY, PEOPLE") is
mixed-case on their own candidate row but printed ALL-CAPS on their
county-repeat rows too, so casing alone doesn't reliably distinguish
a real row from a repeat — x0 position does, unambiguously.

Independent/slogan candidates (no recognized party) are correctly
skipped rather than guessed at — normalize_party returns None for a
free-text slogan, and this module never treats "ran under a slogan" as
a party.
"""

import io
import logging
import re

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL_PATTERN = (
    "https://www.nj.gov/state/elections/assets/pdf/election-results/"
    "{year}/{year}-certification-of-general-election-nominees_us-senate_us-house.pdf"
)

_rate_limiter = RateLimiter(rps=1.0)

# NJ has 12 congressional districts — bounded to what a real map could
# ever need, not open-ended (a stray ordinal word elsewhere on the page
# must never be read as a 13th+ district).
_ORDINALS = {
    "First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6,
    "Seventh": 7, "Eighth": 8, "Ninth": 9, "Tenth": 10, "Eleventh": 11, "Twelfth": 12,
}

# Column boundaries read off the real document's own header row ("Name"
# x0=14, "Address" x0=135, "Party" x0=266, "County" x0=321) — a candidate
# row has real content in both ranges; a county-repeat row only ever has
# content at x0>=321.
_NAME_COL_MAX_X = 130.0
_PARTY_COL_MIN_X = 260.0
_PARTY_COL_MAX_X = 320.0

_SENATE_HEADER_RE = re.compile(r"Candidates\s+for\s+US\s+Senate", re.IGNORECASE)
_HOUSE_HEADER_RE = re.compile(r"Candidates\s+for\s+House\s+of\s+Representatives", re.IGNORECASE)
_DISTRICT_RE = re.compile(
    r"^(" + "|".join(_ORDINALS) + r")\s+Congressional\s+District:", re.IGNORECASE,
)


def _rows_by_top(words: list[dict]) -> list[list[dict]]:
    """Words grouped by row (rounded `top`), in row order — this is a
    clean, computer-generated tabular report (not narrative prose like
    some states' guides), so simple top-rounding is enough; no baseline-
    drift calibration needed."""
    by_top: dict[int, list[dict]] = {}
    for w in words:
        by_top.setdefault(round(w["top"]), []).append(w)
    return [by_top[t] for t in sorted(by_top)]


def _parse_page(words: list[dict], office: str | None, district: int | None) -> tuple[list[dict], str | None, int | None]:
    """(results, office, district) — office/district carry across pages
    (a district's candidates can spill onto the next page), so the
    caller threads them through every page in document order."""
    header_text = " ".join(w["text"] for w in words[:15])
    if _SENATE_HEADER_RE.search(header_text):
        office, district = "S", None
    elif _HOUSE_HEADER_RE.search(header_text):
        office = "H"

    results = []
    for row in _rows_by_top(words):
        row_text = " ".join(w["text"] for w in row)
        if office == "H":
            m = _DISTRICT_RE.match(row_text)
            if m:
                district = _ORDINALS[m.group(1)]
                continue
        if office is None:
            continue

        name_words = [w for w in row if w["x0"] < _NAME_COL_MAX_X]
        party_words = [w for w in row if _PARTY_COL_MIN_X <= w["x0"] < _PARTY_COL_MAX_X]
        if not name_words or not party_words:
            continue
        if name_words[0]["text"] == "Name":  # the column header row itself
            continue
        if office == "H" and district is None:
            continue

        name = " ".join(w["text"] for w in name_words if w["text"] != "*")
        party = normalize_party(" ".join(w["text"] for w in party_words))
        if party is None:
            continue  # independent/slogan candidate — never guessed at
        last_name = surname(name)
        if not last_name:
            continue
        results.append({"office": office, "district": district, "party": party, "last_name": last_name})

    return results, office, district


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is NJ-only by construction
) -> list[dict] | None:
    """Every confirmed general-election federal nominee NJ has certified
    for `year`, or None on a fetch/parse failure. Each item: {"office",
    "district", "party", "last_name"}."""
    url = URL_PATTERN.format(year=year)
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0,
        log_label=f"NJ certification of general nominees {year}", headers=BROWSER_HEADERS,
    )
    if resp is None:
        return None

    try:
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            office: str | None = None
            district: int | None = None
            results: list[dict] = []
            for page in pdf.pages:
                words = page.extract_words()
                if not words:
                    continue
                page_results, office, district = _parse_page(words, office, district)
                results.extend(page_results)
    except Exception:
        logger.exception("NJ certification PDF for %d failed to parse", year)
        return None

    return results
