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
only freshness gate. Set to 45 in state_candidate_sources.json — not
this system's DEFAULT_SETTLE_DAYS (30), and wider than AR/CT's 21 —
because Montana's own 2026 Primary Reconciliation Report
(sosmt.gov/elections/results/) was still being updated as late as
7/17/2026, exactly 45 days after the June 2 primary: real, observed
evidence of how long this state's own count kept moving, not a cited
statutory deadline (none found in the time available for this research
pass, so 45 is the directly-observed number itself, not a rounder or
more conservative one built on top of it).

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

# The same no-eid URL this module reads also serves the November
# general once Montana's site rolls over to it -- a well-formed page,
# not a redesign. Recognised explicitly so that transition reads as
# "nothing to confirm from this stage" (like a primary not yet
# published) rather than a fetch failure that would otherwise fire on
# every run indefinitely, forever re-warning about a page that is
# working exactly as intended.
_GENERAL_RE = re.compile(r"General\s+Election", re.IGNORECASE)

# ENR platforms commonly render a "Write-In" tally as its own row with
# the same markup as a real candidate -- confirmed elsewhere in this
# system as a real shape to guard against (state_candidates_tn.py,
# state_candidates_canvass_xml.py). Not seen on Montana's real 2026
# federal contests (no write-in drew enough votes to appear), so this
# is a defensive guard against an untested-but-plausible shape, not a
# fixture-driven fix.
_WRITE_IN_RE = re.compile(r"write.?in", re.IGNORECASE)


def _xpath_class(name: str) -> str:
    """Whole-token class-match XPath predicate for `name` -- bare
    `contains(@class, "foo")` would also match a future sibling class
    like "foo-detail", silently grabbing the wrong element. Applied to
    every class check in this module, not just the outer wrapper query
    (which needed this from the start since "wrapper-inside" is itself
    a substring of nothing else today, but a vendor CSS change adding
    e.g. "wrapper-inside-mobile" would otherwise silently double-match),
    so a future vendor CSS change can't reintroduce the gap in just one
    of them."""
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


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
    for wrapper in tree.xpath(f"//div[{_xpath_class('wrapper-inside')}]"):
        headers = wrapper.xpath(f'.//div[{_xpath_class("display-results-box-a")}]/h1')
        if not headers:
            continue
        office_district = parse_office(headers[0].text_content().strip())
        if office_district is None:
            continue
        office, district = office_district

        candidates = []
        for section in wrapper.xpath(f'.//div[{_xpath_class("section")} and {_xpath_class("group")}]'):
            name_el = section.xpath(f'.//div[{_xpath_class("display-results-box-d")}]/h1')
            party_el = section.xpath(f'.//div[{_xpath_class("display-results-box-d")}]/h2')
            votes_el = section.xpath(f'.//div[{_xpath_class("display-results-box-f")}]/h1')
            if not (name_el and party_el and votes_el):
                continue  # the "total votes" row has no display-results-box-d at all
            raw_name = name_el[0].text_content().strip()
            if _WRITE_IN_RE.search(raw_name):
                continue
            party = normalize_party(party_el[0].text_content().strip())
            votes_text = votes_el[0].text_content().strip().replace(",", "")
            name = surname(raw_name)
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
        if _GENERAL_RE.search(html):
            # The site rolled over to the general on this same no-eid
            # URL, exactly as its own docstring says it will -- a
            # healthy "nothing to confirm from this stage" outcome, not
            # a failure. Without this branch, every run after rollover
            # would log a fetch-failed warning forever for a page that
            # is working exactly as intended.
            logger.info("MT results page has moved on to the general election -- nothing to confirm here")
            return []
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
