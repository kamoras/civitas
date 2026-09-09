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
candidate).

Massachusetts nominates on a PLURALITY — no runoff mechanism for a
federal primary was found in this research pass — so
runoff_threshold_pct is null.

No settle_days/date gate exists here, unlike every other module in this
system: "PD43+" is a POST-hoc historical archive, not a live
election-night results feed — its own name identifies it as the state's
statutorily-required CERTIFIED compilation, and a race's detail page
does not exist on this site at all until that compilation is filed
(verified live: the real 2026 primary, held September 1, was already
fully populated here by September 8 with no separate
"unofficial"/"preliminary" state observed anywhere on the site). The
mere existence of a populated detail page — a Total Votes Cast greater
than zero — IS the certification signal, the same reasoning this
system's Oregon module already uses for its own single, final PDF
publication.

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
from app.pipeline.fetch.state_candidates_common import normalize_party, pick_nominee, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_BASE_URL = "https://electionstats.state.ma.us"
_OFFICE_IDS = {"H": 5, "S": 6}

_VIEW_LINK_RE = re.compile(r"elections/view/(\d+)")
_TITLE_RE = re.compile(
    r"<title>PD43\+ &raquo; (\d{4}) U\.S\. (?:House|Senate) ([A-Za-z][A-Za-z\s-]*?) Primary"
    r"(?:\s+(\d+)\w{2} Congressional District)?</title>",
)
_CANDIDATE_HEADER_RE = re.compile(r'candidate-id-(\d+)"[^>]*>.*?title="([^"]+)"', re.DOTALL)
_TOTALS_ROW_RE = re.compile(r'<tr class="total">(.*?)</tr>', re.DOTALL)
_NUMBER_CLASS_RE = re.compile(r'class="[^"]*\bnumber_(\d+)\b')


async def _discover_election_ids(client: httpx.AsyncClient, state: str, office_id: int, year: int) -> list[str]:
    """Every real federal primary's own numeric election id for this
    office and year, deduplicated. A race with no filed candidates has
    no view link at all -- not a failure, just nothing to iterate."""
    # The site 301-redirects the query-string form of this URL to this
    # colon-separated path form -- used directly so discovery doesn't
    # depend on whatever httpx client the caller passes in following
    # redirects.
    url = f"{_BASE_URL}/elections/search/office_id:{office_id}/year_from:{year}/year_to:{year}/stage:Primaries"
    html = await fetch_text_with_retry(client, _rate_limiter, url, f"{state} elections search")
    if html is None:
        return []
    seen: dict[str, None] = {}
    for m in _VIEW_LINK_RE.finditer(html):
        seen.setdefault(m.group(1), None)
    return list(seen)


def _parse_election(html: str, election_id: str, year: int, office: str) -> tuple[int | None, str, list[tuple[str, int]]] | None:
    """(district, party, [(surname, votes), ...]) for one election's own
    detail page, or None if the page's title doesn't match the requested
    year/office (a defensive cross-check on the search hop's own filter,
    not trusted blindly) or carries no recognised party."""
    title_match = _TITLE_RE.search(html)
    if title_match is None:
        return None
    page_year, party_word, district_text = title_match.groups()
    if int(page_year) != year:
        return None
    party = normalize_party(party_word)
    if party is None:
        return None
    district = int(district_text) if district_text else None

    header_ids = [cid for cid, _name in _CANDIDATE_HEADER_RE.findall(html)]
    names = dict(_CANDIDATE_HEADER_RE.findall(html))
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
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is MA-only by construction
) -> list[dict] | None:
    runoff_threshold_pct = source.get("runoff_threshold_pct")
    by_group: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for office, office_id in _OFFICE_IDS.items():
        election_ids = await _discover_election_ids(client, state, office_id, year)
        for election_id in election_ids:
            html = await fetch_text_with_retry(
                client, _rate_limiter, f"{_BASE_URL}/elections/view/{election_id}/", f"{state} election {election_id}",
            )
            if html is None:
                # A hard failure, not a per-race skip: silently confirming
                # only whichever races happened to fetch would drop the
                # others' real candidates from the page with no signal
                # anything went wrong, the same class of gap this system's
                # Mississippi module was fixed to avoid when only one of
                # its two party pages fetched successfully.
                return None
            parsed = _parse_election(html, election_id, year, office)
            if parsed is None:
                continue
            district, party, choices = parsed
            by_group.setdefault((office, district, party), choices)

    results = []
    for (office, district, party), choices in by_group.items():
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if won:
            results.append({"office": office, "district": district, "party": party, "last_name": won[0]})
    return results
