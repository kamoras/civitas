"""Who is on a state's PRIMARY ballot, and when that primary is — from
the candidate FILING list states publish months before anyone votes.

Everything else in this feature answers the question after the fact: a
results file says who won a primary, so a state is only accurate once its
primary is over. For most of a cycle that leaves the ballot page showing
every active FEC filer, including people who never filed with the state at
all. A filing list is the answer for that window, and it is the same
question one step earlier — which of these FEC filers is really on a
ballot.

It is deliberately WEAKER than a confirmed nominee and never replaces one:
being on the primary ballot says nothing about surviving it. Once a state
confirms nominees, those win (see api/elections.py's _confirmed_or_all).

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

import httpx

from app.pipeline.fetch.state_candidates_common import (
    normalize_party, office_from_columns, parse_office, surname,
)

logger = logging.getLogger(__name__)


def _iso(raw: str) -> str | None:
    """A filing list's own election date, as ISO. Two formats appear
    live: "03/03/2026" (North Carolina) and "2026-03-03"."""
    from datetime import date

    text = (raw or "").strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime

            return datetime.strptime(text[:10], pattern).date().isoformat()
        except ValueError:
            continue
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    return None


async def fetch_primary_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> tuple[list[dict], str | None] | None:
    """Everyone on `state`'s primary ballot for `year`, plus the primary's
    DATE, or None on a fetch/parse failure.

    Each record: {"office", "district", "party", "last_name"} — the same
    shape the results adapters emit, matched against FEC rows by
    state_candidates.py rather than here.

    The date returned is the earliest federal election date in the file
    that isn't the general: a filing list carries both, because candidates
    who face no primary (an unaffiliated candidate in North Carolina)
    file straight for November.
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
    date_col = fmt.get("election_date_column")
    office_spec = fmt.get("house_from_columns")

    records: dict[tuple, dict] = {}
    dates: Counter = Counter()
    for row in rows:
        parsed = office_from_columns(row, office_spec) or parse_office(_cell(row, contest_col))
        if parsed is None:
            continue
        office, district = parsed
        # The party whose primary they filed in. A filing with no party is
        # a candidate who faces no primary at all (an unaffiliated
        # candidate files straight for November), which is a real ballot
        # fact but not a primary one, so it is skipped here rather than
        # guessed into somebody's primary.
        party = normalize_party(_cell(row, party_col)) if party_col else None
        if party is None:
            continue
        last_name = surname(_cell(row, choice_col))
        if not last_name:
            continue
        if date_col:
            held = _iso(_cell(row, date_col))
            if held:
                dates[held] += 1
        # One row per county per candidate: a filing list is a set of
        # people, not a tally.
        records[(office, district, party, last_name.lower())] = {
            "office": office, "district": district,
            "party": party, "last_name": last_name,
        }

    # The primary is the earliest federal election date in the file — the
    # later one is November, where the no-primary candidates filed.
    primary_date = min(dates) if dates else None
    return list(records.values()), primary_date
