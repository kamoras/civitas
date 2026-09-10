"""Massachusetts's own official post-election archive
(electionstats.state.ma.us, "PD43+" — named for Public Document 43, the
Secretary of the Commonwealth's statutorily-required compilation of every
city/town clerk's certified return of votes) — a single-state deployment
(see state_candidates.py for why that earns a new module).

TWO real hops, both plain unauthenticated GETs against a classic
server-rendered site (no JS framework, no API — confirmed live: every
candidate name and per-town vote number is already present in the raw
HTML of a plain `httpx` GET, not loaded via a separate XHR call the way
a modern SPA would):

1. `/elections/search/office_id:{5|6}/year_from:{year}/year_to:{year}
   /stage:Primaries` — office_id 5 is "U.S. House", 6 is "U.S. Senate"
   (read off the site's own search form, never hardcoded from guessing).
   The site's own search page actually takes a query-string form of this
   same URL and 301-redirects it to this colon-separated path form; this
   module fetches the redirect target directly rather than depending on
   whatever httpx client the caller passes in following redirects.
   Every real federal primary for that year and office is one row; a
   race with no filed candidates (verified live: MA's real 2026 1st and
   7th Congressional Republican primaries) simply carries no
   `/elections/view/{id}/` link at all, so this discovery step naturally
   skips uncontested-with-nobody-filed primaries without any special
   case.
2. `/elections/view/{id}/` — the single page carrying BOTH the real
   candidate roster (in its `<thead>`, each column a `<th
   class="candidate_key_reference ... candidate-id-{id}">` whose `<a
   title="...">` gives the FULL display name, the abbreviated column
   header being a display-only surname) AND the real statewide vote
   totals (in its own `<tr class="total">` row, one `<td
   class="... number_{votes} ...">` per candidate column, in the SAME
   left-to-right order as the header). No per-town aggregation is
   needed here, unlike Vermont's per-town report shape — Massachusetts's
   own site already computes and publishes the statewide Totals row
   directly, verified live via cross-checking a hand-sum of the real
   6th Congressional District Democratic primary's 40 real cities/towns
   against this row's own printed total (matched exactly: 47,835 votes
   for the real winner).

The page's own `<title>` (e.g. "2026 U.S. House Democratic Primary 6th
Congressional District", or "2026 U.S. Senate Democratic Primary" for a
statewide contest with no district) is the party/district source — read
directly rather than reusing the shared parse_office(), since this
module already knows the OFFICE from its own office_id query parameter
and only needs the PARTY word and an optional district ordinal, a
narrower job than parse_office's general chamber-detection.

Candidate identity is the page's own numeric candidate id (embedded in
both the header's and the Totals row's own `candidate-id-{id}`/
`number_{votes}` CSS classes), NOT a name-matching problem: the header
and Totals row list candidates in the same order, cross-checked for
equal length before zipping (a mismatch — this module's own version of
Wyoming's Total-row/surname-row column-count guard — fails that contest
closed rather than risking a vote total silently attached to the wrong
candidate). The header scan is deliberately scoped to the `<thead>`
slice alone, never the whole page: the WINNING candidate's own Totals-
row `<td>` carries that same `candidate-id-{id}` class (verified live),
so scanning past `</thead>` risked the header regex's own non-greedy
title= search skipping past that row into unrelated page content.
Review caught this before it shipped — the trimmed test fixtures
happened to end right after the Totals row, which masked it entirely;
confirmed live against the real, untrimmed page that a full-page scan
finds the exact same 6 candidates for a small district today, but
nothing about that guarantees no `title=` attribute exists anywhere
downstream on every one of MA's real pages, which is exactly the class
of assumption this system's own review process exists to catch rather
than trust by inspection of one page.

The page's own chamber word ("U.S. House"/"U.S. Senate" in the title)
is also cross-checked against which `office_id` this page was
discovered under, alongside the existing year cross-check — a page
whose title doesn't match what was asked for is refused, not trusted.

Massachusetts nominates on a PLURALITY — no runoff mechanism for a
federal primary was found in this research pass — so
runoff_threshold_pct is null.

"PD43+" carries no live/rolling-count state to guard against by
construction — its own name identifies it as the state's statutorily-
required CERTIFIED compilation, and a race's detail page does not exist
on this site at all until that compilation is filed (verified live: the
real 2026 primary, held September 1, was already fully populated here
by September 8 with no separate "unofficial"/"preliminary" state
observed anywhere on the site). That claim was only ever checked
against a handful of already-final pages, never observed mid-count, so
it cannot positively rule out a future partial publication — which is
why, unlike Oregon's identically-reasoned single-final-publication
module, this one still keeps a real settle_days floor: `held` comes
from state_election_dates.primary_date(), the same national-calendar
cache NM's tabular discovery already falls back to for a vendor with no
date of its own. When that cache has no date for this state/cycle yet
(the common case locally; the weekly-refreshed FEC-calendar sync
populates it in production), the gate is simply skipped rather than
blocking confirmation on missing data it has no way to produce. A
single failed
race-page fetch skips only that race (logged) rather than aborting the
whole state's confirmation — MA's ~17 races are independently
discovered and fetched, unlike Mississippi's two co-published same-day
party pages, where one missing was itself evidence of a broken
discovery regex; here one page's outage says nothing about the other
16 already-parsed results. A run confirming nothing while at least one
fetch genuinely failed still reports fetch_failed rather than a healthy
empty state.

Verified live 2026-09-08 against the real 2026 primary: Edward J.
Markey (Senate D, real incumbent, real plurality winner of a 2-way
field), John Deaton (Senate R, unopposed), Richard E. Neal (CD1 D, real
incumbent), Gary J. Grossi (CD3 R, unopposed), Jake Auchincloss (CD4 D,
real incumbent), Dan Koh (CD6 D, real plurality winner of a real 6-way
field, 47,835 over runner-up Tram T. Nguyen's 34,324), Ayanna S. Pressley
(CD7 D, real incumbent, unopposed), Stephen F. Lynch (CD8 D, real
incumbent), Bill Keating (CD9 D, real incumbent) — the state's own real
2026 map has no contested Republican primary in most districts (real
plurality winners there are unopposed general-election long shots, not
incumbents).
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, resolve_confirmed_nominees, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.fetch.state_election_dates import primary_date
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_BASE_URL = "https://electionstats.state.ma.us"
_OFFICE_IDS = {"H": 5, "S": 6}
_OFFICE_CHAMBER_WORD = {"H": "House", "S": "Senate"}

_VIEW_LINK_RE = re.compile(r"elections/view/(\d+)")
_TITLE_RE = re.compile(
    r"<title>PD43\+ &raquo; (\d{4}) U\.S\. (House|Senate) ([A-Za-z][A-Za-z\s-]*?) Primary"
    r"(?:\s+(\d+)\w{2} Congressional District)?</title>",
)
_THEAD_RE = re.compile(r"<thead>(.*?)</thead>", re.DOTALL)
_CANDIDATE_HEADER_RE = re.compile(r'candidate-id-(\d+)"[^>]*>.*?title="([^"]+)"', re.DOTALL)
_TOTALS_ROW_RE = re.compile(r'<tr class="total">(.*?)</tr>', re.DOTALL)
_NUMBER_CLASS_RE = re.compile(r'class="[^"]*\bnumber_(\d+)\b')


async def _discover_election_ids(client: httpx.AsyncClient, state: str, office_id: int, year: int) -> list[str] | None:
    """Every real federal primary's own numeric election id for this
    office and year, deduplicated, or None on a genuine fetch failure --
    a race with no filed candidates has no view link at all and yields
    [], not a failure, but a search page that couldn't be fetched at all
    must not read the same as a healthy "nothing filed"."""
    # The site 301-redirects the query-string form of this URL to this
    # colon-separated path form -- used directly so discovery doesn't
    # depend on whatever httpx client the caller passes in following
    # redirects.
    url = f"{_BASE_URL}/elections/search/office_id:{office_id}/year_from:{year}/year_to:{year}/stage:Primaries"
    html = await fetch_text_with_retry(client, _rate_limiter, url, f"{state} elections search")
    if html is None:
        return None
    return list(dict.fromkeys(m.group(1) for m in _VIEW_LINK_RE.finditer(html)))


def _parse_election(html: str, election_id: str, year: int, office: str) -> tuple[int | None, str, list[tuple[str, int]]] | None:
    """(district, party, [(surname, votes), ...]) for one election's own
    detail page, or None if the page's title doesn't match the requested
    year/office (a defensive cross-check on the search hop's own filter,
    not trusted blindly) or carries no recognised party."""
    title_match = _TITLE_RE.search(html)
    if title_match is None:
        return None
    page_year, chamber_word, party_word, district_text = title_match.groups()
    if int(page_year) != year or chamber_word != _OFFICE_CHAMBER_WORD[office]:
        return None
    party = normalize_party(party_word)
    if party is None:
        return None
    district = int(district_text) if district_text else None

    # Scoped to <thead> only, never the whole page: the winning
    # candidate's own Totals-row <td> carries this SAME candidate-id
    # class (verified live), so scanning past </thead> risks the
    # non-greedy title= search skipping past that row into unrelated
    # page content (nav/footer) and either inflating the header count
    # (caught by the length guard below) or, worse, silently binding a
    # candidate's real id to the wrong text.
    thead_match = _THEAD_RE.search(html)
    if thead_match is None:
        logger.warning("MA results %s: election %s has no <thead>", office, election_id)
        return None
    header_pairs = _CANDIDATE_HEADER_RE.findall(thead_match.group(1))
    header_ids = [cid for cid, _name in header_pairs]
    names = dict(header_pairs)
    totals_match = _TOTALS_ROW_RE.search(html)
    if totals_match is None:
        logger.warning("MA results %s: election %s has no Totals row", office, election_id)
        return None
    votes = _NUMBER_CLASS_RE.findall(totals_match.group(1))
    if len(votes) != len(header_ids):
        # The Totals row's own candidate columns must line up 1:1 with the
        # header's -- a mismatch means this page's markup shape drifted
        # from what this module expects, and zipping them anyway risks
        # silently attaching a vote total to the wrong candidate (the
        # same fail-closed guard state_candidates_wy.py's own Total-row
        # check uses).
        logger.warning(
            "MA results %s: election %s has %d candidate columns but %d Totals values",
            office, election_id, len(header_ids), len(votes),
        )
        return None

    choices = []
    for cid, vote_text in zip(header_ids, votes):
        name = surname(names[cid])
        if name and vote_text.isdigit():
            choices.append((name, int(vote_text)))
    return district, party, choices


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    runoff_threshold_pct = source.get("runoff_threshold_pct")
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    held = primary_date(state, year)
    if held and not _settled(held, settle_days):
        return []

    by_group: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    any_fetch_failed = False
    for office, office_id in _OFFICE_IDS.items():
        election_ids = await _discover_election_ids(client, state, office_id, year)
        if election_ids is None:
            any_fetch_failed = True
            continue
        for election_id in election_ids:
            html = await fetch_text_with_retry(
                client, _rate_limiter, f"{_BASE_URL}/elections/view/{election_id}/", f"{state} election {election_id}",
            )
            if html is None:
                # Skip only THIS race, not the whole state: unlike
                # Mississippi's two co-published same-day party pages
                # (where one missing was itself evidence the discovery
                # regex broke), Massachusetts's ~17 race pages are
                # independently discovered and independently fetched --
                # one page's outage says nothing about whether the other
                # 16 already-parsed results are trustworthy. Tracked
                # below so a run that confirms NOTHING and also saw a
                # failure still reports fetch_failed rather than a
                # healthy empty state.
                logger.warning("MA results %s: election %s page fetch failed", office, election_id)
                any_fetch_failed = True
                continue
            parsed = _parse_election(html, election_id, year, office)
            if parsed is None:
                continue
            district, party, choices = parsed
            key = (office, district, party)
            if key in by_group:
                logger.warning("MA results: election %s duplicates an already-seen %s, keeping the first", election_id, key)
                continue
            by_group[key] = choices

    results = resolve_confirmed_nominees(by_group, runoff_threshold_pct)
    if not results and any_fetch_failed:
        return None
    return results
