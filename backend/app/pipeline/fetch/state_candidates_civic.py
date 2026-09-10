"""Google Civic Information API (voterInfoQuery) as a confirmed-candidate
source for states with no state-specific pipeline at all (MI, NY, OH, WI,
NV, SC, MO, OK, DE, LA, RI, NH) — a single national source, not a per-
state vendor, so one module serves all of them the same way `tabular`
serves every Enhanced Voting state.

**SENATE ONLY, deliberately.** voterInfoQuery resolves to the ONE
precinct the given address sits in — one address per state can only ever
honestly cover that state's statewide Senate race plus whichever single
House district happens to contain it. Real House coverage needs one
hand-curated address PER CONGRESSIONAL DISTRICT (~97 across these 12
states), the same town_directory.json-style curation scaled way up — a
real, separate, larger effort this module doesn't attempt.
`civic_info.py`'s own `_parse_candidate_contest` doesn't even read a
contest's district field today, and `state_candidates_common.parse_office`
requires chamber wording and a district number in the SAME string, which
a bare House `office` label ("U.S. Representative") never carries on its
own — so this only ever asks `parse_office` about `office` strings that
resolve unambiguously to a Senate seat, `("S", None)`, and anything else
(a House contest that happens to be at this address, a state office) is
silently out of scope, not an error.

**Never a visitor's address** — same discipline civic_info.py/
town_directory.json already establish: this module only ever sends one
fixed, publicly-known address per state (that state's own capitol,
configured in state_candidate_sources.json's `address` key), the same
input for every visitor regardless of where they actually live, so
nothing visitor-specific ever leaves the server.

TWO-HOP discovery, both plain GETs, reusing civic_info.py's own
`_parse_contests`/`CIVIC_BASE` for the response schema (verified against
Google's public Discovery Document there — same schema, no reason to
duplicate the parsing):

1. `elections.electionQuery` (no address needed) — every election
   Google's index currently carries, matched by `ocdDivisionId` (this
   state's own division id, or the bare country-level one, in case a
   national general ever gets listed that way instead of per-state) and
   `electionDay` falling in `year`'s real November window. **No match at
   all is today's actual live reality for every state** (verified
   2026-09-10 against production's real key: the index currently lists
   only a permanent test entry plus Delaware's and Rhode Island's real
   September PRIMARIES — nothing for the November general yet, for
   anyone) — so no match returns `[]`, healthy, never a failure.
2. `voterinfo` with that election's own id passed EXPLICITLY, plus this
   state's configured address. Explicit-id lookup is what actually
   works: passing no electionId at all currently 400s "Election unknown"
   for every address tried, live-verified the same day — auto-select
   finds nothing right now. Even with the real, currently-listed
   Delaware primary's own real id, `contests` came back MISSING
   entirely, 5 days before that primary — contest-level data can lag
   well behind an election merely being listed, possibly until very
   close to, or on, election day itself. So a request that succeeds but
   carries no contests is ALSO `[]`, not a failure — matches that real,
   directly-observed shape, not a guess.

Only a genuine network/JSON failure at either hop returns `None`
(fetch_failed) — every "nothing here yet" shape above is `[]` (healthy,
0 confirmed), which matters because until Google populates real November
data, `[]` is the correct answer for literally every state, every night.

Party is deliberately NOT required to normalize for a candidate to be
kept, unlike every vote-counting strategy elsewhere in this system
(which skip an unrecognized party to avoid mis-bucketing raw VOTE COUNTS
into the wrong pile). Nothing here counts votes — Civic's roster is
already Google's own resolved answer to "who's on this ballot" — so
there's no bucketing risk, and dropping an independent/minor-party
candidate would defeat the actual point of adding this source: catching
exactly the candidates who reach November without ever running in a
primary, which every primary-derived source in this system structurally
cannot see.

The API key is injected into a separately-built request URL, never the
`url` value fetch_with_retry logs on every request/retry/failure — same
credential-safety split congress.py's own Congress.gov caller uses, for
the identical reason (httpx's `params=` replaces rather than merges an
existing query string, so the key can't just be added via that kwarg
without risking it landing in a logged value some other way).
"""

import logging

import httpx

from app.config import settings
from app.pipeline.fetch.civic_info import CIVIC_BASE, _parse_contests
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)


def _matches_state(election: dict, state: str) -> bool:
    ocd = str(election.get("ocdDivisionId") or "")
    return ocd in (
        f"ocd-division/country:us/state:{state.lower()}",
        "ocd-division/country:us",
    )


def _matches_november(election: dict, year: int) -> bool:
    day = str(election.get("electionDay") or "")
    # The real federal general is always in November; a permanent test
    # entry (Google's own "VIP Test Election", live-verified to sit far
    # in the future) and any of this state's own off-cycle/local/special
    # elections in the same index must not be mistaken for it.
    return day.startswith(f"{year}-11")


async def _get_json(client: httpx.AsyncClient, url: str, params: dict, label: str) -> dict | list | None:
    full_url = str(httpx.URL(url).copy_merge_params({**params, "key": settings.GOOGLE_CIVIC_API_KEY}))
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, request_url=full_url, log_label=label,
    )
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("%s did not return valid JSON", label)
        return None


# Sentinel distinguishing "the elections list genuinely failed to fetch or
# parse" (a real problem -- state_candidates.py must report fetch_failed,
# not a healthy empty night) from "fetched fine, nothing for this state/
# year matched" (healthy -- today's actual reality for every state).
# Collapsing both into one `None` return was a real bug caught before
# this shipped: it would have silently reported every state's fetch
# failure as a healthy [], every single night.
_FETCH_FAILED = object()


async def _current_general_election_id(
    client: httpx.AsyncClient, state: str, year: int,
) -> str | None | object:
    payload = await _get_json(client, f"{CIVIC_BASE}/elections", {}, f"Civic elections index ({state})")
    if payload is None:
        return _FETCH_FAILED
    elections = payload.get("elections") if isinstance(payload, dict) else None
    if not isinstance(elections, list):
        return _FETCH_FAILED
    matches = [
        e for e in elections
        if isinstance(e, dict) and _matches_state(e, state) and _matches_november(e, year)
    ]
    return str(matches[0]["id"]) if matches else None


def is_configured() -> bool:
    return bool(settings.GOOGLE_CIVIC_API_KEY)


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    if not is_configured():
        return None

    address = source.get("address")
    if not address:
        logger.error("google_civic source for %s has no configured address", state)
        return None

    election_id = await _current_general_election_id(client, state, year)
    if election_id is _FETCH_FAILED:
        return None
    if election_id is None:
        return []  # not listed yet this cycle -- healthy, today's reality everywhere

    payload = await _get_json(
        client, f"{CIVIC_BASE}/voterinfo", {"electionId": election_id, "address": address},
        f"Civic voterinfo ({state})",
    )
    if payload is None:
        return None  # genuine fetch/parse failure
    if not isinstance(payload, dict):
        return []  # a 200 with an unexpected shape reads the same as "no contests yet"

    results: list[dict] = []
    for contest in _parse_contests(payload):
        if contest.get("kind") != "contest":
            continue
        office_district = parse_office(contest.get("office") or "")
        if office_district != ("S", None):
            continue  # Senate only -- see module docstring
        for cand in contest.get("candidates") or []:
            last_name = surname(cand.get("name") or "")
            if not last_name:
                continue
            results.append({
                "office": "S",
                "district": None,
                "party": normalize_party(cand.get("party") or ""),
                "last_name": last_name,
            })
    return results
