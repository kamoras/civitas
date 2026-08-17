"""Who is on a state's ballot — primary and general — and when its primary
is, from the candidate FILING list states publish months before anyone
votes.

Everything else in this feature answers the question after the fact: a
results file says who won a primary, so a state is only accurate once its
primary is over. For most of a cycle that leaves the ballot page showing
every active FEC filer, including people who never filed with the state at
all. A filing list is the answer for that window, and it is the same
question one step earlier — which of these FEC filers is really on a
ballot.

A PRIMARY filing is deliberately weaker than a confirmed nominee and never
replaces one: being on a primary ballot says nothing about surviving it.
Once a state confirms nominees, those win (api/elections.py's
_confirmed_or_all).

A GENERAL filing is the opposite — it is the state naming its November
ballot outright, which is a better answer than deriving nominees from
primary results, and the only answer for a candidate who never appears in
a primary at all. That is not an edge case: Libertarian and Green
candidates reach November without any primary, so results-derived
confirmation cannot see them, and a race with any confirmed candidate
shows only confirmed candidates — so they were being dropped from the page
entirely.

Shape, verified live against North Carolina's real 2026 filing list on
2026-08-17 (dl.ncsbe.gov, 8,300 rows, one per county per candidate): a
filing row carries the contest, the candidate, the party whose primary
they filed in, and the DATE of the election they filed for. That date is
the second thing this module exists to read — a state's primary date is
not derivable from any statute the way the November general is
(election_calendar.py), and here the state states it outright.

Rows repeat per county, so records are deduplicated: a filing list is a
set of people, not a tally.

Nothing here parses a name, an office or a party itself — that is all
state_candidates_common.py, the same code the results adapters use, so a
label that works in one works in the other.
"""

import logging
from collections import Counter
from datetime import date, datetime

import httpx

from app.election_calendar import next_election_day
from app.pipeline.fetch.state_candidates_common import (
    normalize_party, office_from_columns, parse_office, surname,
)

logger = logging.getLogger(__name__)


def _iso(raw: str) -> str | None:
    """A filing list's own election date, as ISO. Two formats appear
    live: "03/03/2026" (North Carolina) and "2026-03-03"."""
    text = (raw or "").strip()[:10]
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


async def fetch_ballot_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> dict | None:
    """Everyone `state` lists on a ballot for `year`:

        {"primary": [...], "general": [...], "primary_date": iso|None}

    or None on a fetch/parse failure. Each record is the usual {"office",
    "district", "party", "last_name"}, matched against FEC rows by
    state_candidates.py rather than here.

    The two lists are split by the election each filing names, which needs
    no configuration: the general is the statutory federal election day
    (election_calendar.py), and anything earlier is that state's primary.

    Reading the GENERAL list matters as much as the primary one, because a
    nominee is not the whole ballot. Libertarian and Green candidates
    reach November without ever appearing in a primary, so a confirmation
    derived from primary RESULTS cannot see them — and since a race with
    any confirmed candidate shows only confirmed candidates, they were
    being dropped from the page entirely. North Carolina's real 2026 file
    is the proof: 42 federal candidates on its general ballot, of which 15
    are Libertarian or Green.
    """
    from app.pipeline.fetch.state_candidates_tabular import (
        MAX_DOWNLOAD_BYTES, _cell, _discover_urls, _get, _rows,
    )

    st = state.upper()
    filings = source.get("filings") or {}
    fmt = filings.get("format") or {}
    stages = await _discover_urls(client, st, year, filings.get("discovery") or {})
    urls = [s["url"] for s in stages if s.get("url")]
    if not urls:
        logger.warning("No %d candidate filing list discoverable for %s", year, st)
        return None

    resp = await _get(client, urls[0], f"{st} candidate filings")
    if resp is None or len(resp.content) > MAX_DOWNLOAD_BYTES:
        return None
    rows = _rows(resp.content, fmt)
    if not rows:
        logger.warning("No parsable rows in the candidate filing list for %s", st)
        return None

    contest_col = fmt.get("contest_column") or "contest_name"
    choice_col = fmt.get("choice_column") or "name_on_ballot"
    party_col = fmt.get("party_column")
    # Where a state records the primary a candidate ran in separately from
    # the candidate's OWN party, the second column is what a
    # general-election filing carries — North Carolina leaves
    # party_contest empty for November and puts LIB/GRE in
    # party_candidate.
    own_party_col = fmt.get("candidate_party_column")
    date_col = fmt.get("election_date_column")
    office_spec = fmt.get("house_from_columns")
    general_day = next_election_day(date(year, 1, 1)).isoformat()

    primary: dict[tuple, dict] = {}
    general: dict[tuple, dict] = {}
    dates: Counter = Counter()
    for row in rows:
        parsed = office_from_columns(row, office_spec) or parse_office(_cell(row, contest_col))
        if parsed is None:
            continue
        office, district = parsed
        last_name = surname(_cell(row, choice_col))
        if not last_name:
            continue
        held = _iso(_cell(row, date_col)) if date_col else None
        is_general = held == general_day
        # For a primary filing the party IS the contest — which primary
        # they are in. For a general filing there is no primary to name,
        # so it is the candidate's own party.
        party = normalize_party(_cell(row, party_col)) if party_col else None
        if party is None and own_party_col:
            party = normalize_party(_cell(row, own_party_col))
        if party is None:
            # An unaffiliated candidate belongs to no party and runs in no
            # primary. Real, and kept for the general (where the ballot
            # lists them) with an empty party the matcher falls back on
            # surname for — never guessed into somebody's primary.
            if not is_general:
                continue
            party = ""
        if held and not is_general:
            dates[held] += 1
        # One row per county per candidate: a filing list is a set of
        # people, not a tally.
        into = general if is_general else primary
        into[(office, district, party, last_name.lower())] = {
            "office": office, "district": district,
            "party": party, "last_name": last_name,
        }

    return {
        "primary": list(primary.values()),
        "general": list(general.values()),
        # The primary is the earliest non-general federal election date the
        # file names.
        "primary_date": min(dates) if dates else None,
    }
