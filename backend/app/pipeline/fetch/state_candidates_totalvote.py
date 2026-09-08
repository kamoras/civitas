"""The "TotalVote" election-night-reporting platform (SOE Software /
KNOWiNK family) — confirmed live 2026-09 running BOTH Montana's
(electionresults.mt.gov) and Nebraska's (electionresults.nebraska.gov)
own state results sites: identical `resultsSW.aspx?type=X&map=Y` URLs,
identical `wrapper-inside wrapper-border` block-per-contest markup, and
the literal string "totalvote" in Nebraska's page source. One vendor
module shared by every state on it, matching this system's existing
multi-state-vendor precedent (state_candidates_tabular.py,
state_candidates_canvass_xml.py) rather than a per-state file — the
whole reason this file was renamed off of `state_candidates_mt.py` the
moment a second state on the same platform turned up.

ONE call per configured query, no discovery step needed: a `map`/`type`
pair with NO `?eid=` param always serves the CURRENT election. An
explicit `eid` can point at a STALE past one instead — verified live
2026-09-06 on Montana: `eid=450002785`, found in the site's own "County
by County" results-archive link, actually returned 2024 General data,
not the 2026 Primary. Because of that, every fetched page's own election
title is parsed and cross-checked against the requested `year` before
anything on it is trusted — a page that hasn't rolled over to the
requested cycle yet is a real "not published yet" miss, not a stale
confirm.

Montana bundles Senate and House under one "FED" query (`queries: [{type:
FED, map: CTY}]`); Nebraska splits them across two — Senate lives on the
"SW" (statewide) query alongside non-federal offices like Governor, and
House lives on its own "CG" (congressional-district) query — so `queries`
is a list, fetched and merged together. state_candidates_common.parse_office
already returns None for the non-federal offices mixed into Nebraska's SW
page ("For Governor", "For Secretary of State"), so no extra filtering is
needed to ignore them.

Clean server-rendered HTML: one `<div class="wrapper-inside
wrapper-border">` block per (office, party) contest on every state seen
so far. Both states run ordinary single-party primaries, so a race with N
parties on the ballot is N separate blocks, never a mixed-party block —
confirmed live (MT 2026 Senate: 3 blocks R/D/L; NE 2026 Senate: 3 blocks
R/D/Legal-Marijuana-Now). Each candidate's own row still carries its own
party label rather than trusting the block, in case that ever isn't true.
Office labels ("UNITED STATES SENATOR" / "For United States Senator - 6
Year Term", "UNITED STATES REPRESENTATIVE 1ST CONGRESSIONAL DISTRICT" /
"For Representative in Congress - 2 Year Term - District 01") already
match state_candidates_common.parse_office, and party labels
("Republican"/"Democrat"/"Libertarian"/"Green") already match
normalize_party, on both states — zero new parsing needed in the shared
module. A party normalize_party doesn't recognise (Nebraska's real 2026
ballot carried "Legal Marijuana Now" candidates in three federal
contests) is silently dropped, matching every other vendor module in
this system — not a gap specific to this one.

Both states nominate by plurality — no federal-primary runoff in either
state's law — so `runoff_threshold_pct` is null for both.

Neither state's results page publishes a certification flag: both keep
showing a static "Unofficial Results" heading indefinitely — confirmed
live on Nebraska, which was STILL labelled "Unofficial Results" on
2026-09-07, almost four months after its May 12 primary — so that text
is boilerplate, not a real signal, and `settle_days` is the only
freshness gate (same shape as AR/CT/TN/FL in this system). Set to 45 in
state_candidate_sources.json for both — not this system's
DEFAULT_SETTLE_DAYS (30), and wider than AR/CT's 21 — because Montana's
own 2026 Primary Reconciliation Report (sosmt.gov/elections/results/) was
still being updated as late as 7/17/2026, exactly 45 days after the June
2 primary: real, observed evidence of how long this vendor's count kept
moving on at least one of its states, not a cited statutory deadline.

Verified live 2026-09-06/07 against each state's real, certified 2026
primary:

Montana (page dated "Results last updated: 6/29/2026", 100% precincts
reporting): Kurt Alme (Senate R, real plurality winner of a 3-way field,
76.1%), Alani Bankhead (Senate D, real plurality winner of a 5-way field,
43.6%), Aaron Flint (CD1 R, real plurality winner of a 4-way field), Sam
Forstag (CD1 D, real plurality winner of a 4-way field), Troy Downing
(CD2 R, unopposed), Brian J Miller (CD2 D, real plurality winner of a
3-way field).

Nebraska: Pete Ricketts (Senate R, real plurality winner of a 5-way
field, 160,547 votes), Cindy Burbank (Senate D, real plurality winner of
a 2-way field, 110,210 votes), Mike Flood (CD1 R, unopposed), Chris
Backemeyer (CD1 D, real plurality winner of a 2-way field, 26,523 over
19,508), Nik Sandman (CD1 L, unopposed), Brinker Harding (CD2 R,
unopposed), Denise Powell (CD2 D, real plurality winner of a close 7-way
field, 22,516 over runner-up John Cavanaugh's 21,115), Eric Michael
Foreman (CD2 L, unopposed), Adrian Smith (CD3 R, real plurality winner of
a 2-way field, 60,549 over 33,020), Becky Kelly Stille (CD3 D,
unopposed).
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

_rate_limiter = RateLimiter(rps=1.0)

# "Primary Election - June 2, 2026" (Montana) / "Primary Election May 12,
# 2026" (Nebraska, no hyphen) -- each site's own page title, read fresh
# every call rather than trusted from a static date registry, so this is
# also what _settled gates freshness on. The year comes from the date
# itself, not a separate "2026" heading the same title box also carries
# (a sibling <h1>, not part of this string) -- one source, not two that
# could disagree.
_TITLE_RE = re.compile(
    r"Primary\s+Election\s*(?:-\s*)?([A-Za-z]+\s+\d{1,2},\s+\d{4})",
)

# The same no-eid URL this module reads also serves the November general
# once a state's site rolls over to it -- a well-formed page, not a
# redesign. Recognised explicitly so that transition reads as "nothing to
# confirm from this stage" (like a primary not yet published) rather than
# a fetch failure that would otherwise fire on every run indefinitely,
# forever re-warning about a page that is working exactly as intended.
_GENERAL_RE = re.compile(r"General\s+Election", re.IGNORECASE)

# ENR platforms commonly render a "Write-In" tally as its own row with
# the same markup as a real candidate -- confirmed elsewhere in this
# system as a real shape to guard against (state_candidates_tn.py,
# state_candidates_canvass_xml.py). Not seen on either state's real 2026
# federal contests (no write-in drew enough votes to appear), so this is
# a defensive guard against an untested-but-plausible shape, not a
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
    federal contest block on the page. Non-federal contests mixed onto
    the same page (Nebraska's "SW" query also carries Governor and other
    state offices) are dropped here via parse_office returning None."""
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
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    base_url = source["base_url"]
    queries = source["queries"]

    pages = []
    for query in queries:
        url = f"{base_url}/resultsSW.aspx?type={query['type']}&map={query['map']}"
        html = await _fetch_html(client, url, f"{state} results {query['type']} {year}")
        if html is None:
            return None
        pages.append(html)

    election = _page_election(pages[0])
    if election is None:
        if _GENERAL_RE.search(pages[0]):
            # The site rolled over to the general on this same no-eid
            # URL, exactly as this module's own docstring says it will --
            # a healthy "nothing to confirm from this stage" outcome, not
            # a failure. Without this branch, every run after rollover
            # would log a fetch-failed warning forever for a page that
            # is working exactly as intended.
            logger.info("%s results page has moved on to the general election -- nothing to confirm here", state)
            return []
        logger.warning("%s results page title didn't match the expected shape", state)
        return None
    page_year, held_on = election
    if page_year != year:
        logger.info("%s results page is for %d, not the requested %d -- not yet rolled over", state, page_year, year)
        return []

    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    if not _settled(held_on, settle_days):
        return []

    by_party: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for html in pages:
        for office, district, candidates in _contests(html):
            for name, party, votes in candidates:
                by_party.setdefault((office, district, party), []).append((name, votes))

    results = []
    for (office, district, party), choices in by_party.items():
        won = pick_nominee(choices, runoff_threshold_pct=None)
        if won:
            results.append({"office": office, "district": district, "party": party, "last_name": won[0]})
    return results
