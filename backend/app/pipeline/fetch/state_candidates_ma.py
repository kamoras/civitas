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

THE STATE'S OWN WINNER MARK is a second gate after the vote count. PD43+
tags the nominee's Totals cell `winner`, and a named write-in who
topped the named field is not always one: the real 2026 5th
Congressional District Republican primary lists Walter Grochowski and
the 1st Governor's Council District Republican primary lists Mary
Catherine Dormer (write-in, 455 votes against 1,212 "All Others"), and
the archive marks neither as having won. Picked on votes alone, both
were published as nominees. A pick the state has not marked names
nobody.

STATEWIDE OFFICES (`statewide_offices: true`). The same archive holds
every office on the primary ballot, found by ONE more search with no
office filter (`/elections/search/year_from:{year}/year_to:{year}
/stage:Primaries`, 510 rows in 2026), whose rows name each contest's
Office and District in the site's own words. Each row's label goes
through parse_statewide_office: a District of "Statewide" is carried
into it as the qualifier ("Statewide Auditor" -- the archive prints the
office bare), and any other district as "{district} District", which is
how the Governor's Council's eight seats read ("Governor's Council 3rd
District"). County offices (Register of Probate, County Treasurer,
Sheriff), District Attorneys and the legislature are refused by the
same gates they are everywhere else. The legislature is deliberately
not read: Massachusetts names its districts ("1st Barnstable", "Norfolk,
Worcester & Middlesex") rather than numbering them, and the shared seat
parser would fold every "1st <county>" district into one district "1".
The page keeps "State legislative districts" in its omissions.

Each statewide page is resolved exactly like a federal one (Totals row,
plurality, the state's own winner mark, the same settle gate), with the
whole printed name kept. A statewide search or page that cannot be
fetched or read fails the whole run rather than dropping that office,
because a missing office under the opt-in renders as "not on this
ballot".

Verified live 2026-09-08 against the 2026 primary: every federal contest
resolves to its certified winner, including incumbents, unopposed
nominees and the plurality winner of a six-way field. Most districts on
the state's map had no contested Republican primary.
"""

import html as html_lib
import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import (
    runoff_threshold,
    clean_display_name,
    normalize_party,
    parse_statewide_office,
    resolve_confirmed_nominees,
    surname,
)
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
_TOTALS_CELL_RE = re.compile(r'class="([^"]*\bnumber_\d+\b[^"]*)"')
_NUMBER_CLASS_RE = re.compile(r"\bnumber_(\d+)\b")
_WINNER_CLASS_RE = re.compile(r"\bwinner\b")

# One row of the all-offices search: its election id, then the Year,
# Office, District and Stage cells in the site's own words.
_SEARCH_ROW_RE = re.compile(
    r'<tr id="election-id-(\d+)" class="election_item[^"]*">(.*?)END tr#election-id', re.DOTALL,
)
_CELL_RE = re.compile(r"<td[^>]*>([^<]*)</td>")
_STAGE_RE = re.compile(r"^([A-Za-z][A-Za-z\s-]*?) Primary$")
_ANY_TITLE_RE = re.compile(r"<title>PD43\+ &raquo; (\d{4}) (.+?)</title>")


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


def _parse_election(
    html: str, election_id: str, year: int, office: str,
) -> tuple[int | None, str, list[tuple[str, int]], set[str]] | None:
    """(district, party, [(name, votes), ...], {names the state marks as
    the winner}) for one election's own detail page, or None if the
    page's title doesn't match the requested year/office (a defensive
    cross-check on the search hop's own filter, not trusted blindly) or
    carries no recognised party."""
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
    parsed = _parse_candidates(html, election_id, office)
    if parsed is None:
        return None
    return district, party, *parsed


def _parse_candidates(
    html: str, election_id: str, label: str,
) -> tuple[list[tuple[str, int]], set[str]] | None:
    """([(name, votes), ...], {marked winners}) off one detail page's
    header and Totals row, or None when the two do not line up."""
    # Scoped to <thead> only, never the whole page: the winning
    # candidate's own Totals-row <td> carries this SAME candidate-id
    # class (verified live), so scanning past </thead> risks the
    # non-greedy title= search skipping past that row into unrelated
    # page content (nav/footer) and either inflating the header count
    # (caught by the length guard below) or, worse, silently binding a
    # candidate's real id to the wrong text.
    thead_match = _THEAD_RE.search(html)
    if thead_match is None:
        logger.warning("MA results %s: election %s has no <thead>", label, election_id)
        return None
    header_pairs = _CANDIDATE_HEADER_RE.findall(thead_match.group(1))
    header_ids = [cid for cid, _name in header_pairs]
    names = dict(header_pairs)
    totals_match = _TOTALS_ROW_RE.search(html)
    if totals_match is None:
        logger.warning("MA results %s: election %s has no Totals row", label, election_id)
        return None
    cells = _TOTALS_CELL_RE.findall(totals_match.group(1))
    votes = [_NUMBER_CLASS_RE.search(c).group(1) for c in cells]
    if len(votes) != len(header_ids):
        # The Totals row's own candidate columns must line up 1:1 with the
        # header's -- a mismatch means this page's markup shape drifted
        # from what this module expects, and zipping them anyway risks
        # silently attaching a vote total to the wrong candidate (the
        # same fail-closed guard state_candidates_wy.py's own Total-row
        # check uses).
        logger.warning(
            "MA results %s: election %s has %d candidate columns but %d Totals values",
            label, election_id, len(header_ids), len(votes),
        )
        return None

    choices = []
    marked = set()
    for cid, vote_text, cell in zip(header_ids, votes, cells):
        # The whole name travels with the votes; the resolver reduces the
        # winner to a surname and keeps the printed name beside it.
        if surname(names[cid]) and vote_text.isdigit():
            choices.append((names[cid], int(vote_text)))
            if _WINNER_CLASS_RE.search(cell):
                marked.add(names[cid])
    return choices, marked


def _resolve(
    groups: dict[tuple, tuple[list[tuple[str, int]], set[str]]],
    runoff_threshold_pct: float | None,
    reduce_name,
) -> list[dict]:
    """The shared plurality pick, then the state's own winner mark: a pick
    the archive does not mark as the winner names nobody (see the module
    docstring's write-in cases)."""
    results = []
    for key, (choices, marked) in groups.items():
        results.extend(resolve_confirmed_nominees(
            {key: choices}, runoff_threshold_pct,
            name_transform=lambda name, marked=marked: reduce_name(name) if name in marked else None,
        ))
    return results


def _statewide_label(office: str, district: str) -> str:
    """The contest label parse_statewide_office reads, built from the
    search row's own Office and District cells."""
    if district == "Statewide":
        return f"Statewide {office}"
    return f"{office} {district} District"


def _search_rows(page: str, year: int) -> list[tuple[str, str, str, str, str]]:
    """(election id, office, district, party code, party as printed) for
    every regular party
    primary row of the all-offices search that links a results page."""
    rows = []
    for election_id, block in _SEARCH_ROW_RE.findall(page):
        if f"elections/view/{election_id}/" not in block:
            continue  # "No Candidates": nothing filed, no page
        cells = [re.sub(r"\s+", " ", html_lib.unescape(c)).strip() for c in _CELL_RE.findall(block)[:4]]
        if len(cells) < 4 or cells[0] != str(year):
            continue
        stage = _STAGE_RE.match(cells[3])
        # A SPECIAL primary fills an unexpired term in a separate
        # contest; it is not this ballot's regular race for the office.
        if not stage or "special" in stage.group(1).lower():
            continue
        party = normalize_party(stage.group(1))
        if party is None:
            continue
        rows.append((election_id, cells[1], cells[2], party, stage.group(1)))
    return rows


def _title_matches(page: str, year: int, office: str, district: str, party_word: str) -> bool:
    """The detail page's own title names the same year, office, party and
    district as the search row that linked it."""
    match = _ANY_TITLE_RE.search(page)
    if match is None or int(match.group(1)) != year:
        return False
    title = re.sub(r"\s+", " ", html_lib.unescape(match.group(2))).strip()
    expected = f"{office} {party_word} Primary"
    if district != "Statewide":
        expected += f" {district} District"
    return title == expected


async def _fetch_statewide(
    client: httpx.AsyncClient, state: str, year: int, runoff_threshold_pct: float | None,
) -> list[dict] | None:
    """Every statewide-office primary winner, or None when the search or
    any statewide page could not be fetched or read."""
    url = f"{_BASE_URL}/elections/search/year_from:{year}/year_to:{year}/stage:Primaries"
    page = await fetch_text_with_retry(client, _rate_limiter, url, f"{state} all-offices search")
    if page is None:
        return None
    groups: dict[tuple, tuple[list[tuple[str, int]], set[str]]] = {}
    for election_id, office_text, district_text, party, party_word in _search_rows(page, year):
        parsed_office = parse_statewide_office(_statewide_label(office_text, district_text))
        if parsed_office is None:
            continue
        code, seat = parsed_office
        detail = await fetch_text_with_retry(
            client, _rate_limiter, f"{_BASE_URL}/elections/view/{election_id}/", f"{state} election {election_id}",
        )
        if detail is None:
            logger.warning("MA statewide: election %s page fetch failed", election_id)
            return None
        if not _title_matches(detail, year, office_text, district_text, party_word):
            logger.warning("MA statewide: election %s title does not match its search row", election_id)
            return None
        parsed = _parse_candidates(detail, election_id, office_text)
        if parsed is None:
            return None
        key = (code, seat, party)
        if key in groups:
            logger.warning("MA statewide: election %s duplicates %s", election_id, key)
            return None
        groups[key] = parsed
    return _resolve(groups, runoff_threshold_pct, clean_display_name)


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    runoff_threshold_pct = runoff_threshold(source)
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    held = primary_date(state, year)
    if held and not _settled(held, settle_days):
        return []

    by_group: dict[tuple[str, int | None, str], tuple[list[tuple[str, int]], set[str]]] = {}
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
            district, party, choices, marked = parsed
            key = (office, district, party)
            if key in by_group:
                logger.warning("MA results: election %s duplicates an already-seen %s, keeping the first", election_id, key)
                continue
            by_group[key] = (choices, marked)

    results = _resolve(by_group, runoff_threshold_pct, surname)
    if not results and any_fetch_failed:
        return None
    if source.get("statewide_offices"):
        statewide = await _fetch_statewide(client, state, year, runoff_threshold_pct)
        if statewide is None:
            # A partial or missing statewide list would be synced as the
            # whole truth and delete (or never show) real nominees.
            return None
        results.extend(statewide)
    return results
