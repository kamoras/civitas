"""Montana's own election-night-reporting site (electionresults.mt.gov) —
a single-state deployment, so this is a vendor module in the same sense
state_candidates_pa.py is, not a per-state fetcher (see state_candidates.py
for why that distinction is the whole design).

ONE call, no discovery step needed: `resultsSW.aspx?type=FED&map=CTY`
with NO `?eid=` param always serves the CURRENT election. An explicit
`eid` can point at a STALE past one instead — verified live 2026-09-06:
`eid=450002785`, found in the site's own "County by County" results-
archive link, actually returned 2024 General data, not the 2026
Primary. Because of that, the page's own election title ("2026 Primary
Election - June 2, 2026") is parsed and cross-checked against the
requested `year` before anything on the page is trusted — a page that
hasn't rolled over to the requested cycle yet is a real "not published
yet" miss, not a stale confirm.

Clean server-rendered HTML: one `<div class="wrapper-inside
wrapper-border">` block per (office, party) contest. Montana runs
ordinary single-party primaries, so a Senate or House race with N
parties on the ballot is N separate blocks, never a mixed-party block —
confirmed live (2026 Senate: 3 blocks, R/D/L). Each candidate's own row
still carries its own party label rather than trusting the block, in
case that ever isn't true. The office label ("UNITED STATES SENATOR" /
"UNITED STATES REPRESENTATIVE 1ST CONGRESSIONAL") already matches
state_candidates_common.parse_office, and the party label
("Republican"/"Democrat"/"Libertarian"/"Green") already matches
normalize_party — zero new parsing needed in the shared module.

Montana nominates by plurality — no federal-primary runoff exists in
state law — so `runoff_threshold_pct` is null.

No certification flag is published anywhere on the results page itself
(same shape as AR/CT/TN/FL in this system), so `settle_days` is the
only freshness gate, left at this system's DEFAULT_SETTLE_DAYS (30)
rather than a state-specific override: Montana's own 2026 Primary
Reconciliation Report (sosmt.gov/elections/results/) was still being
updated as late as 7/17/2026 — 45 days after the June 2 primary — which
argues for the wider default over AR/CT's tighter 21-day override, not
a specific statutory deadline (none found in the time available for
this research pass, so this is a deliberately conservative default, not
a cited certification date).

Verified live 2026-09-06 against the real, certified 2026 primary (page
dated "Results last updated: 6/29/2026", 100% precincts reporting):
Kurt Alme (Senate R, real plurality winner of a 3-way field, 76.1%),
Alani Bankhead (Senate D, real plurality winner of a 5-way field,
43.6%), Aaron Flint (CD1 R, real plurality winner of a 4-way field),
Sam Forstag (CD1 D, real plurality winner of a 4-way field), Troy
Downing (CD2 R, unopposed), Brian J Miller (CD2 D, real plurality
winner of a 3-way field).
"""

import logging
import re
from datetime import datetime

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE = "https://electionresults.mt.gov"
_rate_limiter = RateLimiter(rps=1.0)

# "Primary Election - June 2, 2026" -- the site's own page title, read
# fresh every call rather than trusted from a static date registry
# (Montana isn't in data/state_election_dates.json), so this is also
# what _settled gates freshness on. The year comes from the date itself,
# not a separate "2026" heading the same title box also carries (a
# sibling <h1>, not part of this string) -- one source, not two that
# could disagree.
_TITLE_RE = re.compile(
    r"Primary\s+Election\s*-\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
)


async def _fetch_html(client: httpx.AsyncClient, url: str, label: str) -> str | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0, log_label=label, headers=BROWSER_HEADERS,
    )
    return resp.text if resp is not None else None


def _page_election(html: str) -> tuple[int, str] | None:
    """(year, ISO held-date) from the page's own title, or None if the
    title isn't in the expected shape at all (a real site redesign, not
    a wrong-cycle miss -- that case is handled by the caller comparing
    the returned year against what it asked for)."""
    m = _TITLE_RE.search(html)
    if not m:
        return None
    try:
        held = datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return None
    return held.year, held.isoformat()


def _contests(html: str) -> list[tuple[str, int | None, list[tuple[str, str, int]]]]:
    """(office, district, [(surname, party_code, votes), ...]) for every
    federal contest block on the page."""
    tree = lxml_html.fromstring(html)
    contests = []
    for wrapper in tree.xpath(
        '//div[contains(concat(" ", normalize-space(@class), " "), " wrapper-inside ")]',
    ):
        headers = wrapper.xpath('.//div[contains(@class, "display-results-box-a")]/h1')
        if not headers:
            continue
        office_district = parse_office(headers[0].text_content().strip())
        if office_district is None:
            continue
        office, district = office_district

        candidates = []
        for section in wrapper.xpath(
            './/div[contains(concat(" ", normalize-space(@class), " "), " section group ")]',
        ):
            name_el = section.xpath('.//div[contains(@class, "display-results-box-d")]/h1')
            party_el = section.xpath('.//div[contains(@class, "display-results-box-d")]/h2')
            votes_el = section.xpath('.//div[contains(@class, "display-results-box-f")]/h1')
            if not (name_el and party_el and votes_el):
                continue  # the "total votes" row has no display-results-box-d at all
            party = normalize_party(party_el[0].text_content().strip())
            votes_text = votes_el[0].text_content().strip().replace(",", "")
            name = surname(name_el[0].text_content().strip())
            if party is None or not name or not votes_text.isdigit():
                continue
            candidates.append((name, party, int(votes_text)))
        if candidates:
            contests.append((office, district, candidates))
    return contests


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is MT-only by construction
) -> list[dict] | None:
    html = await _fetch_html(client, f"{BASE}/resultsSW.aspx?type=FED&map=CTY", f"MT federal results {year}")
    if html is None:
        return None

    election = _page_election(html)
    if election is None:
        logger.warning("MT results page title didn't match the expected shape")
        return None
    page_year, held_on = election
    if page_year != year:
        logger.info("MT results page is for %d, not the requested %d -- not yet rolled over", page_year, year)
        return []

    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    if not _settled(held_on, settle_days):
        return []

    by_party: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for office, district, candidates in _contests(html):
        for name, party, votes in candidates:
            by_party.setdefault((office, district, party), []).append((name, votes))

    results = []
    for (office, district, party), choices in by_party.items():
        won = pick_nominee(choices, runoff_threshold_pct=None)
        if won:
            results.append({"office": office, "district": district, "party": party, "last_name": won[0]})
    return results
