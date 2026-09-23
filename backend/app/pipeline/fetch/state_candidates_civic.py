"""Google Civic Information API (voterInfoQuery) as a confirmed-candidate
source for states with no state-specific pipeline at all (NY, OH, WI, NV,
SC, MO, OK, DE, LA, RI) — a single national source, not a per-state
vendor, so one module serves all of them the same way `tabular` serves
every Enhanced Voting state.

**SENATE is always covered, via one statewide address (`source["address"]`
— that state's own capitol, same fixed-public-building discipline
town_directory.json already establishes: never a visitor's address, the
same input for every visitor regardless of where they actually live).**
voterInfoQuery resolves to the ONE precinct a given address sits in, so
that single address also happens to cover whichever ONE House district
contains it — real, full House coverage needs a SEPARATE address PER
CONGRESSIONAL DISTRICT, configured per state as `source["house_addresses"]`
(`{"1": "...", "2": "...", ...}`, keyed by district number as a string).
This is genuinely more curation work (~13 addresses for Michigan alone,
each independently verified — see below) than the single capitol address
Senate needs, so `house_addresses` starts empty for most states and is
filled in one state at a time, same town_directory.json-style "hand-
curated, spot-checked, shouldn't grow without a human looking at each
address" discipline. A state with no `house_addresses` entry simply gets
Senate-only confirmation, exactly as before — not an error, not a gap
this module needs to apologize for in its own output.

**Every address, for every district, must be independently VERIFIED
before it's trusted** — not assumed from a member's residence or a
district's largest city. Live-verified building this for Michigan
(2026-09-11): starting from each district's own current member's
residence city (Wikipedia's own current, cited "Member (Residence)"
table) and geocoding each candidate address via the SAME free Census
Bureau geocoder this codebase's own `/geocode` endpoint already uses,
**3 of the first 13 addresses tried resolved to the WRONG district**
(Rep. Moolenaar's own Midland office → district 8, not 2; Rep. Dingell's
own Dearborn office → district 12, not 6; Rep. McClain's own Shelby
Township office → district 10, not 9 — all three real, current district-
office addresses that simply sit just outside their own member's
redrawn 2022 district lines). This is exactly why an address here is
never trusted on the strength of "it's a real, current official
address" alone — every single one must independently geocode to the
district it's configured for before it's added.

Once a House contest is actually returned by voterinfo for a queried
address, the SAME discipline applies again: Google's own `district.id`
(schema-guaranteed, `district.scope == "congressional"` — verified
against the Discovery Document, see civic_info.py's own
`_parse_candidate_contest`) must match the district the address was
configured for, or the contest is refused outright rather than
confirmed under a number that might be wrong. A bare House office label
("U.S. Representative") carries no district number of its own in free
text, so this is the only reliable source for one — `parse_office` is
still used first, only to confirm the contest is a House race at all.

TWO-HOP discovery, both plain GETs, reusing civic_info.py's own
`_parse_contests`/`CIVIC_BASE` for the response schema (verified against
Google's public Discovery Document there — same schema, no reason to
duplicate the parsing):

1. `elections.electionQuery` (no address needed) — every election
   Google's index currently carries, matched by `ocdDivisionId` (this
   state's own division id, or the bare country-level one, in case a
   national general ever gets listed that way instead of per-state) and
   `electionDay` falling in `year`'s real November window. No match at
   all returns `[]`, healthy, never a failure — which was every state's
   live reality until 2026-09-23, when the index first carried the real
   general: one country-level entry, `id` 12000, "2026 General Midterm
   Election", 2026-11-03. That is matched by the bare country-level
   `ocdDivisionId` branch above, which existed for exactly this case
   and is no longer hypothetical.
2. `voterinfo` with that election's own id passed EXPLICITLY, plus one
   configured address — called once for the state's own Senate address,
   then again for each configured `house_addresses` entry (the election
   id lookup itself is shared, not repeated per address). Explicit-id
   lookup is what actually works: passing no electionId at all currently 400s "Election unknown"
   for every address tried, live-verified the same day — auto-select
   finds nothing right now. Even with the real, currently-listed
   Delaware primary's own real id, `contests` came back MISSING
   entirely, 5 days before that primary — contest-level data can lag
   well behind an election merely being listed, possibly until very
   close to, or on, election day itself. So a request that succeeds but
   carries no contests is ALSO `[]`, not a failure — matches that real,
   directly-observed shape, not a guess. The same hop's HTTP 404 "No
   information for this address" means the same thing and is handled
   the same way (see `_NO_DATA`): on 2026-09-23, with the general now
   listed, 71 of this file's 80 configured addresses answered exactly
   that, every state capitol among them. Only LA's answered 200, and
   even that one carried no `contests` key.

Only a genuine network/JSON failure at either hop returns `None`
(fetch_failed) — every "nothing here yet" shape above is `[]` (healthy,
0 confirmed), which matters because until Google populates real November
data, `[]` is the correct answer for literally every state, every night.
That distinction is not theoretical: the morning the general was first
listed, the second hop started answering 404 for real, and because 4xx
is retried by default those 404s reported 8 states as fetch_failed
before this was fixed.

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


# Google's own normal answer for an address whose ballot it has not
# published yet: HTTP 404 "No information for this address". Verified
# live on 2026-09-23, the morning after Google first listed the November
# 3 general at all -- 71 of this file's 80 configured addresses answered
# exactly that, including every state capitol. It is a real, expected
# miss, NOT a failure, and must not read as one: because the default
# retry path treats any 4xx as transient, that first morning reported
# fetch_failed for 8 states (3 attempts each) rather than a healthy
# empty night. `_FETCH_FAILED` below draws the same line one level up,
# for the elections index.
_NO_DATA = object()


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict, label: str,
    expected_statuses: tuple[int, ...] = (),
) -> dict | list | None | object:
    """The parsed body, `_NO_DATA` for one of `expected_statuses`, or
    `None` on a genuine fetch/parse failure."""
    full_url = str(httpx.URL(url).copy_merge_params({**params, "key": settings.GOOGLE_CIVIC_API_KEY}))
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, request_url=full_url, log_label=label,
        expected_statuses=expected_statuses,
    )
    if resp is None:
        return None
    if resp.status_code in expected_statuses:
        return _NO_DATA
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


async def _voterinfo_contests(
    client: httpx.AsyncClient, election_id: str, address: str, label: str,
) -> list[dict] | None:
    """Parsed real contests for one address, or `None` on a genuine
    fetch/parse failure. An empty list covers "fetched fine, no contests
    published yet" (a 200 carrying no `contests`, or the 404 `_NO_DATA`
    describes) and "a 200 with an unexpected shape" — see module
    docstring for why all of those read as healthy, not a failure."""
    payload = await _get_json(
        client, f"{CIVIC_BASE}/voterinfo", {"electionId": election_id, "address": address}, label,
        expected_statuses=(404,),
    )
    if payload is None:
        return None
    if payload is _NO_DATA or not isinstance(payload, dict):
        return []
    return _parse_contests(payload)


def _contest_candidates(contest: dict, office: str, district: int | None) -> list[dict]:
    results = []
    for cand in contest.get("candidates") or []:
        last_name = surname(cand.get("name") or "")
        if not last_name:
            continue
        results.append({
            "office": office,
            "district": district,
            "party": normalize_party(cand.get("party") or ""),
            "last_name": last_name,
        })
    return results


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    if not is_configured():
        return None

    address = source.get("address")
    if not address:
        logger.error("google_civic source for %s has no configured address", state)
        return None
    house_addresses: dict = source.get("house_addresses") or {}

    election_id = await _current_general_election_id(client, state, year)
    if election_id is _FETCH_FAILED:
        return None
    if election_id is None:
        return []  # not listed yet this cycle -- healthy, today's reality everywhere

    results: list[dict] = []

    senate_contests = await _voterinfo_contests(client, election_id, address, f"Civic voterinfo ({state})")
    if senate_contests is None:
        return None  # genuine fetch/parse failure
    for contest in senate_contests:
        if contest.get("kind") != "contest":
            continue
        if parse_office(contest.get("office") or "") != ("S", None):
            continue  # Senate only from the statewide address
        results.extend(_contest_candidates(contest, "S", None))

    for district_str, house_address in house_addresses.items():
        try:
            expected_district = int(district_str)
        except (TypeError, ValueError):
            logger.error("google_civic source for %s has a non-numeric house_addresses key %r", state, district_str)
            continue

        contests = await _voterinfo_contests(
            client, election_id, house_address, f"Civic voterinfo ({state}-{expected_district})",
        )
        if contests is None:
            return None  # genuine fetch/parse failure
        for contest in contests:
            if contest.get("kind") != "contest":
                continue
            office_district = parse_office(contest.get("office") or "")
            if office_district is None or office_district[0] != "H":
                continue  # not a House contest at all

            # The schema-guaranteed district id is the only number this
            # module trusts -- a bare office label carries none of its
            # own, and even when the label text DOES carry a number
            # (rare), a mismatch against Civic's own structured answer
            # is treated as untrustworthy, not resolved in the text's
            # favor. Refuses outright — never confirms a candidate under
            # a district number that isn't independently confirmed —
            # rather than guessing which of two disagreeing signals to
            # trust (see module docstring: 3 of 13 real, current
            # Michigan addresses resolved to the WRONG district on
            # first try, which is exactly the failure mode this guards
            # against happening again silently).
            district_info = contest.get("district") or {}
            reported_district = None
            if district_info.get("scope") == "congressional":
                try:
                    reported_district = int(district_info.get("id") or "")
                except (TypeError, ValueError):
                    reported_district = None
            if reported_district != expected_district:
                logger.warning(
                    "Civic voterinfo for %s district %s reported district %s instead of the "
                    "expected one -- refusing rather than guessing",
                    state, expected_district, reported_district,
                )
                continue
            results.extend(_contest_candidates(contest, "H", expected_district))

    return results
