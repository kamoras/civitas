"""The certified November ballot from a Secretary of State "voter portal"
results site that stages the general election's candidate list before
any vote is cast.

Louisiana is the live case. It sat on `google_civic` all cycle as a
"jungle primary, nothing to fetch until November 3" state. In 2026 the
U.S. Senate race used closed party primaries (May 16, runoff June 27),
both official on this portal, while the U.S. House returned to the open
November primary after Louisiana v. Callais (election_rules.json) — but
the better source sits beside them: the portal already stages the 11/03/2026 election with
its candidate list, and that list IS the November ballot. It carries the
Libertarian and no-party candidates a primary-results reading cannot
see, and it needs no reconstruction from vote totals at all.

Shape (read from the portal's own graphicalresults bundle, not guessed):
  {data_url}?blob=ElectionDates.htm
      -> every election the portal knows, MM/DD/YYYY
  {data_url}?blob={yyyymmdd}/RacesCandidates_Multiparish.htm
      -> each race's SpecificTitle and Choice[].Desc = "Name (PARTY)"

The election is chosen by DATE — the statutory federal general election
day for the cycle — never by id, so the next cycle needs no edit.
"""

import logging
import re

import httpx

from app.pipeline.fetch.election_results import (
    ContestCount,
    FeedUnreadable,
    StateCount,
    is_special_contest,
)
from app.pipeline.fetch.fec import general_election_day
from app.pipeline.fetch.http_utils import fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    normalize_party,
    parse_office,
    surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

# "Jane Doe (REP)" — the party code is the trailing parenthetical.
# A nickname in quotes or a middle parenthetical stays in the name.
_DESC_RE = re.compile(r"^(?P<name>.*\S)\s*\((?P<party>[^()]+)\)\s*$")


def _as_list(value) -> list:
    # The portal serialises a one-element array as the bare object.
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    data_url = source.get("data_url")
    if not data_url:
        logger.warning("%s voterportal source has no data_url", state)
        return None

    dates = await fetch_json_with_retry(
        client, _rate_limiter, f"{data_url}?blob=ElectionDates.htm", f"{state} portal election dates",
    )
    if not isinstance(dates, dict):
        return None
    general = general_election_day(year).strftime("%m/%d/%Y")
    listed = {d.get("ElectionDate") for d in _as_list((dates.get("Dates") or {}).get("Date"))}
    if general not in listed:
        # Not staged yet — an honest "cannot say", not "nobody is running".
        logger.info("%s portal has not staged the %s general yet", state, general)
        return None

    folder = general_election_day(year).strftime("%Y%m%d")
    races = await fetch_json_with_retry(
        client, _rate_limiter,
        f"{data_url}?blob={folder}/RacesCandidates_Multiparish.htm",
        f"{state} portal general-election candidates",
    )
    if not isinstance(races, dict):
        return None

    records: list[dict] = []
    for race in _as_list((races.get("Races") or {}).get("Race")):
        parsed = parse_office(race.get("SpecificTitle") or "")
        if not parsed:
            continue
        office, district = parsed
        for choice in _as_list(race.get("Choice")):
            m = _DESC_RE.match((choice.get("Desc") or "").strip())
            if not m:
                continue
            display = clean_display_name(m.group("name"))
            last = surname(display)
            if not last:
                continue
            records.append({
                "office": office,
                "district": district,
                # A certified ballot: "NOPTY" is an ordinary independent.
                "party": normalize_party(m.group("party"), ballot_list=True),
                "last_name": last,
                "display_name": display,
                "party_label": m.group("party").strip(),
            })

    if not records:
        # The general is staged but carries no federal contest — the
        # title format changed, or this is the wrong election. Loud.
        logger.warning("%s portal general election has no parseable federal contest", state)
        return None
    logger.info("%s portal: %d federal candidates on the %s ballot", state, len(records), general)
    return records


# ---------------------------------------------------------------------------
# Election-night count (fetch_general_results), read 2026-11-09.
#
# The portal's graphicalresults bundle loads each election from two files in
# the election's date folder and merges them by choice id:
#   {folder}/RacesCandidates_Multiparish.htm  each race's title and choices
#   {folder}/Votes_Multiparish.htm            each race's PrecinctsReporting,
#       PrecinctsExpected and, per choice id, VoteTotal (and an Outcome)
# The election list (ElectionDates.htm) carries ResultsOfficial ("0" for the
# 2026 general on 2026-11-09), read as the count's official flag.
#
# The Outcome field ("Elected", "Defeated", "Runoff") is the portal's own
# reading of an unofficial count and is not read: Civitas calls no race,
# and the runoff a House seat owes without a majority is
# election_rules.json's, read by the projection and certification rules. The reporting
# figure is precincts reporting of precincts expected, worded as precincts
# with results, since nothing says a reporting precinct is complete; the
# early and absentee units the file counts separately are not added to it.
# The files state no total beyond the candidates' (Louisiana counts no
# write-ins), and their VersionDateTime reads 1900-01-01, so neither is
# read. The Secretary of State certifies by the 14th day after the election
# (certification_deadlines.json).


def _votes(raw) -> int | None:
    raw = str(raw if raw is not None else "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


def general_contests(races: dict, votes: dict) -> list:
    """The federal contests of one election's two files, merged by id."""
    counted = {str(r.get("ID")): r for r in _as_list((votes.get("Races") or {}).get("Race"))}
    out = []
    for race in _as_list((races.get("Races") or {}).get("Race")):
        title = race.get("SpecificTitle") or ""
        parsed = parse_office(title)
        tally = counted.get(str(race.get("ID")))
        if parsed is None or tally is None:
            continue
        totals = {str(c.get("ID")): _votes(c.get("VoteTotal")) for c in _as_list(tally.get("Choice"))}
        candidates = []
        for choice in _as_list(race.get("Choice")):
            m = _DESC_RE.match((choice.get("Desc") or "").strip())
            got = totals.get(str(choice.get("ID")))
            if not m or got is None:
                continue
            candidates.append((clean_display_name(m.group("name")),
                               normalize_party(m.group("party"), ballot_list=True), got))
        out.append(ContestCount(
            office=parsed[0], district=parsed[1], candidates=candidates,
            reporting_units=_votes(tally.get("PrecinctsReporting")),
            total_units=_votes(tally.get("PrecinctsExpected")) or None,
            is_special=is_special_contest(title),
        ))
    return out


async def fetch_general_results(client: httpx.AsyncClient, election_day, state: str, source: dict):
    """The portal's count for `election_day`, or None when its election
    list holds no election on that date."""
    data_url = source.get("data_url")
    if not data_url:
        raise FeedUnreadable(f"{state} voterportal source has no data_url")
    dates = await fetch_json_with_retry(
        client, _rate_limiter, f"{data_url}?blob=ElectionDates.htm", f"{state} portal election dates",
    )
    if not isinstance(dates, dict):
        raise FeedUnreadable(f"{state} portal election list could not be read")
    day = election_day.strftime("%m/%d/%Y")
    listed = [d for d in _as_list((dates.get("Dates") or {}).get("Date")) if d.get("ElectionDate") == day]
    if not listed:
        return None
    folder = election_day.strftime("%Y%m%d")
    races = await fetch_json_with_retry(
        client, _rate_limiter, f"{data_url}?blob={folder}/RacesCandidates_Multiparish.htm",
        f"{state} portal races",
    )
    votes = await fetch_json_with_retry(
        client, _rate_limiter, f"{data_url}?blob={folder}/Votes_Multiparish.htm", f"{state} portal votes",
    )
    if not isinstance(races, dict) or not isinstance(votes, dict):
        raise FeedUnreadable(f"{state} portal results for {day} could not be read")
    return StateCount(
        source_name=source.get("source_name") or f"{state} Secretary of State (election results)",
        page_url=source.get("page_url") or "https://voterportal.sos.la.gov/graphical",
        official=all(str(d.get("ResultsOfficial")) == "1" for d in listed),
        unit_label="precincts", units_complete=False, contests=general_contests(races, votes),
    )
