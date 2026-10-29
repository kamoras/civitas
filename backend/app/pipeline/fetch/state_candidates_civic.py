"""Google Civic Information API (voterInfoQuery) as a state's candidate
source: the Voting Information Project's data, which election officials
supply to Democracy Works and Google serves. Where it holds a race, it is
the first source the sync reads (owner decision 2026-10-28: the crawler is
the fallback); where it holds none, the state's own readers answer.

**One precinct per question.** voterInfoQuery answers for the precinct an
address sits in, and takes "latitude,longitude" as the address: Google
answers with the nearest street address's ballot (measured 2026-10-28; a
city's name alone answers nothing). So every district is asked about at
points inside it, app/data/civic_query_points.json (the internal points of
its most populous Census blocks, scripts/build_civic_query_points.py), plus
any fixed public addresses the state's entry names (`address`,
`house_addresses`). Never a visitor's location (AGENTS.md §8): the same
points for everyone.

**The district is Google's, never the one asked about.** A point near a
line can sit in the next district, so each contest is filed under the
district its own label and `district.name` give ("Representative in
Congress" + "4th District"), read by the shared office parsers; a House
label with no number is at-large only in a state with one seat. Two answers
that disagree about a race's candidates drop that race this run.

**Coverage decides what it speaks for.** The answer covers the federal
races it names (the sync applies them as the certified ballot for exactly
those races), the statewide offices when its answers agreed on them, and
the legislative districts it read: a district no point answered for is
named in `unread_districts`, and its stored nominees stand (measured
2026-10-28: 147 of Michigan's 148 districts read). The sync takes the state
offices from it only when it names every statewide office already on file
(state_candidates._civic_speaks_for_state_offices). A state with no ballot behind its first points (Google
holds VIP data for some states only, and only near an election) is a
healthy empty answer, read in a few requests.

Answers are kept per point for a day in this process (`_answers`), so the
measures reader (ballot_measures_civic.py) and a second sync that day ask
nothing again. Data for a concluded election is removed from the API: what was read
stays in the database and the election archive.

The API key is merged into the request URL only, never the logged `url`.
"""

import logging
import re
import time

import httpx

from app.config import settings
from app.data_refresh import read as read_data
from app.pipeline.fetch.civic_info import CIVIC_BASE, _parse_contests
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    SourceRecords, ballot_list_party, clean_display_name, federal_record, normalize_party,
    parse_office, parse_state_leg_office, parse_statewide_office,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Google's quota is 60 queries a minute per project (measured 2026-10-28:
# 129 queries at 2.5 a second, then "Queries per minute per user").
_rate_limiter = RateLimiter(rps=0.9)

POINTS_FILE = "civic_query_points.json"
# Each point's answer, kept in this process for a day: the candidate sync
# and the measures reader in one Election run ask each point once. The
# pipeline process is one process, and the answers are the same for
# everyone (ballot data, never a visitor's).
CACHE_TTL_S = 20 * 3600
_answers: dict[str, tuple[float, list[dict]]] = {}
SOURCE_NAME = "the Voting Information Project (Google Civic Information API)"
# A state whose first few placed points carry no contests has no VIP ballot
# loaded. A point Google fails to place ("Failed to parse address", about
# half of them, measured 2026-10-29) is no answer and doesn't count: three
# such failures in a row had read Wisconsin, whose VIP ballot is loaded, as
# a state without one.
PROBE_POINTS = 3
# Points tried before a state none of whose points Google places reads as a
# failed fetch: at the measured rate twenty misses in a row is a broken
# geocoder, not a state, and an outage is never read as "no ballot".
PROBE_LIMIT = 20

_NO_DATA = object()
_UNPLACED = object()
_FETCH_FAILED = object()


class Unplaced(list):
    """No contests because Google couldn't place the point: an empty list
    to every reader, never an answer that the place has no ballot."""


def is_configured() -> bool:
    return bool(settings.GOOGLE_CIVIC_API_KEY)


def _matches_state(election: dict, state: str) -> bool:
    ocd = str(election.get("ocdDivisionId") or "")
    return ocd in (f"ocd-division/country:us/state:{state.lower()}", "ocd-division/country:us")


def _matches_november(election: dict, year: int) -> bool:
    # The federal general is in November; Google's permanent test entries
    # and a state's off-cycle elections in the same index are not it.
    return str(election.get("electionDay") or "").startswith(f"{year}-11")


async def _get_json(client: httpx.AsyncClient, url: str, params: dict, label: str,
                    expected_statuses: tuple[int, ...] = ()):
    """The parsed body, `_NO_DATA` for an expected status (404: "No
    information for this address", Google's answer for a point it has no
    ballot for), `_UNPLACED` for a 400 (Google couldn't place the address),
    or None on a failed fetch."""
    full_url = str(httpx.URL(url).copy_merge_params({**params, "key": settings.GOOGLE_CIVIC_API_KEY}))
    resp = await fetch_with_retry(client, _rate_limiter, "GET", url, request_url=full_url, log_label=label,
                                  expected_statuses=expected_statuses)
    if resp is None:
        return None
    if resp.status_code in expected_statuses:
        return _UNPLACED if resp.status_code == 400 else _NO_DATA
    try:
        return resp.json()
    except ValueError:
        logger.warning("%s did not return valid JSON", label)
        return None


async def general_election_id(client: httpx.AsyncClient, state: str, year: int):
    """The November general's id in Google's index, None when not listed,
    or _FETCH_FAILED."""
    payload = await _get_json(client, f"{CIVIC_BASE}/elections", {}, f"Civic elections index ({state})")
    elections = payload.get("elections") if isinstance(payload, dict) else None
    if not isinstance(elections, list):
        return _FETCH_FAILED
    matches = [e for e in elections if isinstance(e, dict) and _matches_state(e, state) and _matches_november(e, year)]
    return str(matches[0]["id"]) if matches else None


async def contests_at(client: httpx.AsyncClient, election_id: str, where: str, label: str) -> list[dict] | None:
    """The parsed contests on the ballot at `where` (an address, or
    "lat,lon"), [] for none, an empty `Unplaced` when Google couldn't place
    it, None on a failed fetch. Kept a day."""
    key = f"{election_id}|{where}"
    kept = _answers.get(key)
    if kept and time.monotonic() - kept[0] < CACHE_TTL_S:
        return kept[1]
    payload = await _get_json(client, f"{CIVIC_BASE}/voterinfo", {"electionId": election_id, "address": where},
                              label, expected_statuses=(400, 404))
    if payload is None:
        return None
    if payload is _UNPLACED:
        contests = Unplaced()
    else:
        contests = [] if payload is _NO_DATA or not isinstance(payload, dict) else _parse_contests(payload)
    _answers[key] = (time.monotonic(), contests)
    return contests


def _label(contest: dict) -> str:
    """The contest's office label with its district's name after it, the
    way a printed ballot reads ("Representative in Congress 4th District")."""
    office = (contest.get("office") or "").strip()
    district = ((contest.get("district") or {}).get("name") or "").strip()
    return f"{office} {district}".strip() if district and district.lower() not in office.lower() else office


def _federal(contest: dict, one_seat: bool) -> tuple[str, int | None] | None:
    """("S", None) or ("H", n) for a federal contest, else None."""
    office = (contest.get("office") or "").strip()
    scope = (contest.get("district") or {}).get("scope")
    # The label with its district's name first: "Representative in
    # Congress" alone reads as an at-large seat, "... 4th District" as the 4th.
    parsed = parse_office(_label(contest)) or parse_office(office)
    if parsed is None and scope == "congressional":
        # Virginia's "Member, House of Representatives (9th District)": the
        # label alone is a state chamber's words, Google's scope says
        # which house it is.
        number = re.search(r"\d+", _label(contest))
        parsed = ("H", int(number.group())) if number else None
    if parsed and parsed[0] == "H" and parsed[1] is None and not one_seat:
        return None  # no number, and not an at-large state: refused, never guessed
    return parsed


# Google's scopes for a contest no state office has (VIP's district types).
_LOCAL_SCOPES = frozenset({"countywide", "citywide", "township", "ward", "schoolBoard", "cityCouncil",
                           "countyCouncil", "judicial", "special"})


def _party_label(printed: str | None) -> str | None:
    """A printed party name in VIP's capitals ("U.S. TAXPAYERS") as the
    state's lists write it ("U.S. Taxpayers")."""
    if not printed or not printed.isupper():
        return printed
    return " ".join(w if "." in w else w.capitalize() for w in printed.split())


def _state_records(contest: dict) -> list[dict]:
    """The statewide or legislative records a contest makes, [] for any
    other contest (local, judicial, a measure).

    Only the label WITH its district's name is parsed: the office alone
    reads Jackson's city "Treasurer" (district "JACKSON CITY") as the
    state treasurer, and with it the shared locality gate refuses it."""
    if (contest.get("district") or {}).get("scope") in _LOCAL_SCOPES:
        return []
    label = _label(contest)
    statewide = parse_statewide_office(label)
    leg = None if statewide else parse_state_leg_office(label)
    if statewide is None and leg is None:
        return []
    out = []
    for cand in contest.get("candidates") or []:
        name = clean_display_name(cand.get("name") or "")
        if statewide and statewide[0] == "governor" and "/" in name:
            # A ticket printed "Governor/Lieutenant Governor" names both
            # "A/B"; the state's lists write it "A and B".
            name = " and ".join(part.strip() for part in name.split("/") if part.strip())
        party = ballot_list_party(cand.get("party") or "") if cand.get("party") else None
        if not name or party is None:
            continue
        code, printed = party
        if statewide:
            record = {"office": statewide[0], "district": statewide[1], "party": code, "last_name": name}
        else:
            record = {"office": leg[0], "district": leg[1], "party": code, "last_name": name}
            if leg[2] is not None:
                record["seat"] = leg[2]
        if printed:
            record["party_label"] = _party_label(printed)
        out.append(record)
    return out


def _race_key(record: dict) -> tuple:
    return (record["office"], record["district"], record.get("seat"))


class _Reading:
    """What every answer for one state said, race by race."""

    def __init__(self, one_seat: bool):
        self.one_seat = one_seat
        self.races: dict[tuple, list[dict]] = {}
        self.conflicts: set[tuple] = set()
        self.seen: set[str] = set()  # district keys answered for, as the points file keys them
        self.answers = 0
        self.unread: list[str] = []

    def add(self, contests: list[dict], state: str) -> None:
        if contests:
            self.answers += 1
        for contest in contests:
            if contest.get("kind") != "contest":
                continue
            federal = _federal(contest, self.one_seat)
            if federal:
                office, district = federal
                records = [r for c in contest.get("candidates") or []
                           if (r := federal_record(office, district, normalize_party(c.get("party") or ""),
                                                   c.get("name") or ""))]
                if office == "H":
                    self.seen.add(f"{state}-H-{district or 0}")
            else:
                records = _state_records(contest)
                for r in records:
                    if r["office"] in ("upper", "lower"):
                        self.seen.add(f"{state}-{r['office']}-{r['district']}")
            for key in {_race_key(r) for r in records}:
                these = sorted((r["last_name"], r["party"] or "") for r in records if _race_key(r) == key)
                if key in self.races:
                    if sorted((r["last_name"], r["party"] or "") for r in self.races[key]) != these:
                        self.conflicts.add(key)
                else:
                    self.races[key] = [r for r in records if _race_key(r) == key]

    def records(self) -> list[dict]:
        if self.conflicts:
            logger.warning("Civic answers disagreed about %d race(s); left out this run: %s",
                           len(self.conflicts), sorted(self.conflicts)[:10])
        return [r for key, rs in self.races.items() if key not in self.conflicts for r in rs]


def _points(state: str) -> dict[str, list]:
    doc = read_data(POINTS_FILE) or {}
    prefix = f"{state.upper()}-"
    return {k: v for k, v in (doc.get("points") or {}).items() if k.startswith(prefix)}


async def read_state(client: httpx.AsyncClient, year: int, state: str, source: dict | None = None):
    """(_Reading, election id) for `state`, None on a failed fetch, or
    (None, None) when Google lists no general for it."""
    source = source or {}
    election_id = await general_election_id(client, state, year)
    if election_id is _FETCH_FAILED:
        return None
    if election_id is None:
        return None, None
    points = _points(state)
    one_seat = sum(1 for k in points if k.startswith(f"{state}-H-")) == 1
    reading = _Reading(one_seat)
    # Fixed public addresses first, then each district's points, the
    # legislative districts' first (each answer names its House and state
    # Senate districts too), skipping a district an earlier answer named.
    fixed = [a for a in [source.get("address"), *(source.get("house_addresses") or {}).values()] if a]
    order = sorted(points, key=lambda k: ({"lower": 0, "upper": 1, "H": 2}.get(k.split("-")[1], 3), k))
    asked = tried = 0
    for where in fixed:
        contests = await contests_at(client, election_id, where, f"Civic voterinfo ({state})")
        if contests is None:
            return None
        asked += not isinstance(contests, Unplaced)
        reading.add(contests, state)
    for district in order:
        if district in reading.seen:
            continue
        for lat, lon in points[district]:
            if asked >= PROBE_POINTS and reading.answers == 0:
                return reading, election_id  # no VIP ballot loaded for this state
            if tried >= PROBE_LIMIT and asked == 0:
                logger.warning("Civic placed none of %d points for %s", tried, state)
                return None
            tried += 1
            contests = await contests_at(client, election_id, f"{lat},{lon}", f"Civic voterinfo ({district})")
            if contests is None:
                return None
            asked += not isinstance(contests, Unplaced)
            reading.add(contests, state)
            if district in reading.seen:
                break
    reading.unread = sorted(d for d in points if d.split("-")[1] in ("upper", "lower") and d not in reading.seen)
    return reading, election_id


async def fetch_confirmed_candidates(client: httpx.AsyncClient, year: int, state: str, source: dict):
    """The state's VIP ballot as candidate records (see the module
    docstring), [] when Google holds none, None on a failed fetch."""
    if not is_configured():
        return None
    read = await read_state(client, year, state, source)
    if read is None:
        return None
    reading, _ = read
    if reading is None or reading.answers == 0:
        return []
    records = reading.records()
    has_statewide = any(r["office"] not in ("S", "H", "upper", "lower") for r in records)
    if not has_statewide or any(k[0] not in ("S", "H", "upper", "lower") for k in reading.conflicts):
        # The statewide offices are on every ballot in the state: an answer
        # without them, or answers disagreeing about one, is no read of them.
        return SourceRecords([r for r in records if r["office"] in ("S", "H")], state_offices_incomplete=True,
                             ballot_list=True)
    out = SourceRecords(records, ballot_list=True)
    # The legislative districts no point answered for (or whose answers
    # disagreed): the sync leaves their stored nominees as they are.
    out.unread_districts = frozenset(
        tuple(d.split("-")[1:]) for d in reading.unread
    ) | frozenset((k[0], k[1]) for k in reading.conflicts if k[0] in ("upper", "lower"))
    if out.unread_districts:
        logger.info("Civic: %s's legislative districts not read this run, kept as stored: %s",
                    state, sorted(out.unread_districts)[:20])
    return out
