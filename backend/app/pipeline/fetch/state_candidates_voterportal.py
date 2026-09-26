"""The certified November ballot from a Secretary of State "voter portal"
results site that stages the general election's candidate list before
any vote is cast.

Louisiana is the live case. It sat on `google_civic` all cycle as a
"jungle primary, nothing to fetch until November 3" state — true until
2026, when Louisiana moved Congress to CLOSED party primaries (May 16,
runoff June 27). Both rounds are on this portal, official, with a
per-candidate Outcome ("Advances" / "Defeated") — but the better source
sits beside them: the portal already stages the 11/03/2026 election with
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

# "Julia Letlow (REP)" — the party code is the trailing parenthetical.
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
            })

    if not records:
        # The general is staged but carries no federal contest — the
        # title format changed, or this is the wrong election. Loud.
        logger.warning("%s portal general election has no parseable federal contest", state)
        return None
    logger.info("%s portal: %d federal candidates on the %s ballot", state, len(records), general)
    return records
