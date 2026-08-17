"""Midterm-elections API — candidate rosters, race detail, and PVI
(2026-07). Plain camelCase dicts, same convention as api/action.py's
existing /action/elections endpoint (that endpoint is unchanged; this is
a separate, fuller namespace for the new candidate-research feature)."""

import json
import logging
import pathlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.api.public import RateLimit
from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.election_calendar import next_election_day
from app.http_client import make_async_client
from app.models import Candidate, Race, RaceCoverageItem, Representative, Senator
from app.pipeline.analyze.score_calculator import (
    compute_overall_score,
    get_district_pvi_map,
    get_pvi_meta,
    get_state_pvi_map,
)
from app.pipeline.election_pipeline import STATES_WITH_FEDERAL_RACES, current_election_cycle
from app.pipeline.fetch.state_candidate_sources import source_for_state
from app.pipeline.fetch.state_election_dates import primary_date
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_COUNTY_CROSSWALK_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "data" / "county_district_crosswalk.json"
)
_district_counties_cache: dict[str, list[str]] | None = None


def _district_counties() -> dict[str, list[str]]:
    """"ST-N" -> sorted county names (a "(part)" suffix means that county
    also has population in another district) — lets a voter who doesn't
    know their district number recognize their county instead. District
    boundaries only change after redistricting (once a decade), so this
    is a static bundled file, not an auto-refreshed one like
    district_pvi.json. Empty dict (never a guess) if the file is
    missing."""
    global _district_counties_cache
    if _district_counties_cache is None:
        try:
            data = json.loads(_COUNTY_CROSSWALK_PATH.read_text())
            _district_counties_cache = data["districts"]
        except Exception:
            logger.exception("county_district_crosswalk.json unavailable")
            _district_counties_cache = {}
    return _district_counties_cache

router = APIRouter(prefix="/elections")


def _pvi_for_race(race: Race, state_pvi: dict, district_pvi: dict) -> tuple[int | None, str | None]:
    """(pvi, level) where level says which map the number came from —
    "district" or "state". A House race falling back to the statewide
    number is a materially different claim (a D+19 urban district in a
    red state is nothing like its state's lean), so the fallback is
    FLAGGED for the frontend to label rather than silently blended
    (2026-07 review F7)."""
    if race.office == "H":
        key = f"{race.state}-{race.district if race.district is not None else 0}"
        if key in district_pvi:
            return district_pvi[key], "district"
    pvi = state_pvi.get(race.state)
    return pvi, ("state" if pvi is not None else None)


def _iso_utc(dt) -> str | None:
    """Serialize a stored naive-UTC datetime with an explicit Z suffix —
    an offset-less ISO string gets parsed as LOCAL time by JS Date
    (2026-07 review: coverage timestamps displayed shifted by the
    viewer's UTC offset). Same reasoning as main.py's PROCESS_STARTED_AT
    keeping its explicit +00:00 in an exposed field."""
    if dt is None:
        return None
    iso = dt.isoformat()
    return iso if ("+" in iso or iso.endswith("Z")) else iso + "Z"


def _coverage_item(item: RaceCoverageItem) -> dict:
    return {
        "id": item.id,
        "sourceType": item.source_type,
        "sourceName": item.source_name,
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "author": item.author,
        "publishedAt": _iso_utc(item.published_at),
    }


def _candidate_summary(cand: Candidate) -> dict:
    return {
        "id": cand.id,
        "name": cand.name,
        "party": cand.party,
        "incumbentChallenge": cand.incumbent_challenge,
        "hasRaisedFunds": cand.has_raised_funds,
        "candidateStatus": cand.candidate_status,
        "contributions": cand.contributions,
        "cashOnHand": cand.cash_on_hand,
        # Null = never synced. The frontend renders "awaiting FEC sync"
        # for null vs. real figures with an as-of date — a candidate whose
        # refresh turn hasn't come up must not read as "$0 raised"
        # (2026-07 review F10).
        "lastFinancialsSync": _iso_utc(cand.last_financials_sync),
    }


def _confirmed_or_all(candidates: list[Candidate]) -> list[Candidate]:
    """If a registered state source (state_candidate_sources.json /
    state_candidates.py) has confirmed any candidate in this race as an
    actual general-election nominee, return ONLY confirmed candidates — an
    FEC filer who lost their primary/runoff isn't a real ballot option
    (2026-08 fix: TX's Senate race listed 19 FEC filers as if all still
    running, months after the real primary/runoff resolved). A race with
    no confirmed data at all (not yet covered, or genuinely pre-primary)
    returns every active FEC filer, unchanged from before this existed.

    Failing that, a race whose state publishes a candidate FILING list
    falls back to whoever is actually on that state's PRIMARY ballot,
    which is the best answer available for the months before a primary
    happens — an FEC filer who never filed with the state is not a ballot
    option either. Deliberately the weaker rule and only reached when no
    nominee is confirmed: being on a primary ballot says nothing about
    surviving it.

    Shared by every endpoint that lists a race's candidates
    (_race_summary, _race_full, race_detail) — the bug this guards
    against previously resurfaced via race_detail even after _race_full
    was fixed, since a race's full candidate list is reachable from more
    than one route."""
    if any(c.confirmed_general for c in candidates):
        return [c for c in candidates if c.confirmed_general]
    if any(c.on_primary_ballot for c in candidates):
        return [c for c in candidates if c.on_primary_ballot]
    return candidates


def _candidate_source(candidates: list[Candidate], state: str) -> str:
    """WHICH of _confirmed_or_all's three answers a race's list is, so the
    page can say so instead of presenting three quite different things as
    one list. Computed here rather than in the frontend, which must not
    re-derive what the filter already decided.

    "confirmed"  — the state has named its whole November ballot, minor
                   parties included.
    "nominees"   — the state has confirmed nominees, but only from PRIMARY
                   results, which structurally cannot see a Libertarian,
                   Green or independent candidate who never ran in one. The
                   list is real and incomplete, and saying so is the
                   difference between a short ballot and a wrong one.
    "primary"    — no nominee yet, but the state lists these as being on
                   its primary ballot.
    "filers"     — nobody has confirmed anything for this race, so this is
                   every active FEC filer, some of whom may never appear on
                   a ballot.
    """
    if any(c.confirmed_general for c in candidates):
        source = source_for_state(state) or {}
        return "confirmed" if source.get("general_ballot_complete") else "nominees"
    if any(c.on_primary_ballot for c in candidates):
        return "primary"
    return "filers"


def _race_summary(race: Race, state_pvi: dict, district_pvi: dict) -> dict:
    candidates = sorted(
        _confirmed_or_all(race.candidates),
        key=lambda c: (c.cash_on_hand or 0.0),
        reverse=True,
    )
    top_candidates = candidates[:2]
    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    return {
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": pvi,
        "pviLevel": pvi_level,
        "candidateCount": len(candidates),
        "topCandidates": [_candidate_summary(c) for c in top_candidates],
    }


def _last_name_matches(last_name: str, full_name: str) -> bool:
    """True if `last_name` (FEC's — possibly multi-word, e.g. "van
    hollen") exactly matches the TRAILING tokens of `full_name`.
    Deliberately token-exact rather than a raw substring check: a
    substring match would let "lee" match "leeman" by coincidence,
    which is exactly the kind of wrong-person attribution
    _incumbent_link's docstring warns against. Token-trailing (not
    single-last-token) so multi-word surnames like "Van Hollen" still
    match against a full name of "Chris Van Hollen"."""
    cand_tokens = last_name.split()
    name_tokens = full_name.lower().split()
    return bool(cand_tokens) and name_tokens[-len(cand_tokens):] == cand_tokens


def _incumbent_link(
    cand: Candidate, race: Race, reps_by_district: dict[int, Representative], senators: list[Senator],
) -> dict | None:
    """{id, score} for this candidate's matching Senator/Representative
    scorecard row, or None — only ever populated for a real, uniquely-
    identified match; never a guess (a wrong match here would attribute
    one member's voting record to a different person on the ballot).

    House matches on the exact (state, district) key via `reps_by_district`
    — no ambiguity possible, since a district has exactly one
    representative. Senate has no seat-class field to key on (Senator only
    stores `state`, and a state has two), so it matches on state + last
    name against `senators` (pre-filtered to this race's state), checked
    UNIQUE before trusting it — the only real disambiguator available
    given the schema, and safe because two senators from the same state
    sharing a last name is not a real scenario this needs to handle
    "close enough". Both lookups are precomputed once per state_ballot
    call (not queried per-candidate here) — see that function.
    """
    if cand.incumbent_challenge != "I":
        return None
    # FEC's name field is "LAST, FIRST MIDDLE ..." (see fec.py's
    # _fec_first_name) — the last name is everything before the comma.
    last_name = cand.name.split(",")[0].strip().lower()
    if not last_name:
        return None

    if race.office == "H":
        rep = reps_by_district.get(race.district or 0)
        if rep and _last_name_matches(last_name, rep.name):
            return {"id": rep.id, "score": compute_overall_score(rep)}
        return None

    if race.office == "S":
        matches = [s for s in senators if _last_name_matches(last_name, s.name)]
        if len(matches) == 1:
            return {"id": matches[0].id, "score": compute_overall_score(matches[0])}
    return None


def _race_full(
    race: Race, state_pvi: dict, district_pvi: dict,
    reps_by_district: dict[int, Representative], senators: list[Senator],
) -> dict:
    """Same shape as race_detail's response, minus coverage — this backs
    the per-state ballot view, which needs every candidate (not just the
    top-2-by-funds _race_summary uses for the map/directory) but not the
    news feed, which stays one click away on the existing race-detail
    page. Confirmed-general filtering (see _confirmed_or_all) applies
    here too, same as race_detail."""
    candidates = sorted(_confirmed_or_all(race.candidates), key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    counties = None
    if race.office == "H":
        key = f"{race.state}-{race.district if race.district is not None else 0}"
        counties = _district_counties().get(key)
    return {
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": pvi,
        "pviLevel": pvi_level,
        "counties": counties,
        "candidateSource": _candidate_source(race.candidates, race.state),
        "candidates": [
            {**_candidate_summary(c), "incumbentRecord": _incumbent_link(c, race, reps_by_district, senators)}
            for c in candidates
        ],
    }


# How many state-wide coverage items the ballot page's top-of-page teaser
# shows. STATE_COVERAGE_QUERY_LIMIT is deliberately larger than this: a
# state with many races can have the same story matched under more than
# one race (see _state_coverage's dedup), so the raw query needs headroom
# above the post-dedup count actually shown.
STATE_COVERAGE_LIMIT = 20
STATE_COVERAGE_QUERY_LIMIT = 100


def _state_coverage(db: Session, races: list[Race]) -> list[dict]:
    """Every race's coverage for this state, newest first, deduplicated
    by URL and capped to STATE_COVERAGE_LIMIT — backs the ballot page's
    top-of-page coverage feed (front and center, not one click away on a
    per-race page, per 2026-08 review). A single story can match
    candidates in two different races within the same state (e.g. an
    article about both the Senate race and a House race) — the
    RaceCoverageItem table's uniqueness is per (race_id, url), so the
    same url can legitimately appear as separate rows across races;
    deduped here so the reader doesn't see one headline twice. Each item
    carries a `race` sub-object (id/office/district) so the frontend can
    label which race it's about via lib/elections.ts's raceBadgeLabel()
    — not a second copy of that formatting logic.

    The two (or more) rows for a deduplicated story share the same
    published_at/fetched_at (same article, ingested in the same pass),
    so the `.id` tiebreaker decides — deterministically, not whichever
    row SQL happens to return first for a tied sort key — which race's
    badge is shown for it."""
    races_by_id = {r.id: r for r in races}
    if not races_by_id:
        return []
    rows = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.race_id.in_(races_by_id.keys()))
        .order_by(
            RaceCoverageItem.published_at.desc().nullslast(),
            RaceCoverageItem.fetched_at.desc(),
            RaceCoverageItem.id.asc(),
        )
        .limit(STATE_COVERAGE_QUERY_LIMIT)
        .all()
    )
    seen_urls: set[str] = set()
    coverage = []
    for item in rows:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        race = races_by_id[item.race_id]
        coverage.append({
            **_coverage_item(item),
            "race": {"id": race.id, "office": race.office, "district": race.district},
        })
        if len(coverage) >= STATE_COVERAGE_LIMIT:
            break
    return coverage


@router.get("/states/{state}")
def state_ballot(state: str, db: Session = Depends(get_db)):
    """Every federal (Senate + House) race on `state`'s ballot this
    cycle, full candidate lists — backs the ballot-centric per-state
    page. State-level ballot measures and local races are NOT part of
    this response; they're a separate, later feature."""
    state = state.upper()
    if state not in STATES_WITH_FEDERAL_RACES:
        raise HTTPException(status_code=404, detail="Not a state with federal races")

    races = (
        db.query(Race)
        .filter(Race.state == state, Race.cycle_year == current_election_cycle())
        .options(selectinload(Race.candidates))
        .all()
    )
    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    # Fetched once per request, not once per incumbent candidate — a
    # state can have up to ~50 House races, and querying Representative/
    # Senator inside _incumbent_link per candidate would be exactly the
    # N+1 shape the .candidates selectinload above already exists to
    # avoid for a different relationship.
    reps_by_district = {
        r.district: r for r in db.query(Representative).filter(Representative.state == state).all()
    }
    senators = db.query(Senator).filter(Senator.state == state, Senator.is_current).all()
    full = [_race_full(r, state_pvi, district_pvi, reps_by_district, senators) for r in races]
    senate_races = [r for r in full if r["office"] == "S"]
    house_races = sorted(
        (r for r in full if r["office"] == "H"),
        key=lambda r: r["district"] if r["district"] is not None else -1,
    )

    return cached_json({
        "state": state,
        "cycleYear": current_election_cycle(),
        "electionDate": next_election_day(utcnow().date()).isoformat(),
        # Read from this state's own election feed (state_election_dates.py),
        # never a calendar maintained here — null for a state whose source
        # doesn't date itself, which is the honest answer.
        "primaryDate": primary_date(state, current_election_cycle()),
        "statePvi": state_pvi.get(state),
        "senateRaces": senate_races,
        "houseRaces": house_races,
        "coverage": _state_coverage(db, races),
    }, max_age=CACHE_TTL_LIST_S)


@router.get("/races")
def list_races(db: Session = Depends(get_db)):
    """All races for the current cycle, with PVI and top-2-by-funds
    candidates — backs the map + directory."""
    races = (
        db.query(Race)
        # Filter matches the docstring's contract — harmless while only
        # one cycle exists, load-bearing the day a second cycle syncs.
        .filter(Race.cycle_year == current_election_cycle())
        # ~470 races each lazy-loading .candidates is an N+1 of ~500
        # queries per request on a Pi — batch them.
        .options(selectinload(Race.candidates))
        .all()
    )
    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    data = [_race_summary(r, state_pvi, district_pvi) for r in races]
    return cached_json(data, max_age=CACHE_TTL_LIST_S)


@router.get("/pvi")
def pvi_map():
    """State + district PVI maps (positive = R lean, negative = D lean) —
    already computed for internal scoring (score_calculator.py), exposed
    publicly here with their provenance metadata (source, method, election
    window, as-of date) so the frontend can label what the number is and
    is not (2026-07 review F7)."""
    return cached_json(
        {
            "states": get_state_pvi_map(),
            "districts": get_district_pvi_map(),
            "meta": get_pvi_meta(),
            # Lets the /elections directory page show "{cycleYear} MIDTERM
            # ELECTIONS" from the same fetch it already makes for map
            # coloring, instead of a second fetch of every race just to
            # read one field off the first result.
            "cycleYear": current_election_cycle(),
        },
        max_age=CACHE_TTL_LIST_S,
    )


@router.get("/races/{race_id}")
def race_detail(race_id: str, db: Session = Depends(get_db)):
    """Full race detail: candidates (confirmed nominees only where known,
    see _confirmed_or_all), financials, coverage feed."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    candidates = sorted(_confirmed_or_all(race.candidates), key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
    coverage = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.race_id == race_id)
        .order_by(RaceCoverageItem.published_at.desc().nullslast(), RaceCoverageItem.fetched_at.desc())
        .limit(50)
        .all()
    )

    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    return cached_json({
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": pvi,
        "pviLevel": pvi_level,
        "candidates": [_candidate_summary(c) for c in candidates],
        "coverage": [_coverage_item(item) for item in coverage],
    }, max_age=CACHE_TTL_DETAIL_S)


@router.get("/candidates/{candidate_id}")
def candidate_detail(candidate_id: str, db: Session = Depends(get_db)):
    """Single candidate profile, with its parent race's identity."""
    cand = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if cand is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    race = cand.race
    return cached_json({
        **_candidate_summary(cand),
        "disbursements": cand.disbursements,
        "individualItemizedContributions": cand.individual_itemized_contributions,
        "race": {
            "id": race.id,
            "office": race.office,
            "state": race.state,
            "district": race.district,
        } if race else None,
    }, max_age=CACHE_TTL_DETAIL_S)


_CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


@router.get("/geocode")
async def geocode_address(address: str, _rl: RateLimit):
    """State + House district for a US mailing address, via the Census
    Bureau's free, no-key geocoder — lets the ballot page auto-select a
    visitor's district instead of requiring the manual dropdown (2026-08,
    address collection scoped explicitly: optional, resolve-only, never
    stored). The address is passed straight through to Census and never
    logged, cached, or persisted here — only the resolved state/district
    numbers are returned.

    Rate-limited (same RateLimit as qa.py's LLM-calling /ask) — this is
    the only /elections endpoint that makes a per-request outbound call
    to a third party rather than just querying the local DB, so it's the
    one route in this file an unauthenticated caller could otherwise use
    to hammer Census on Civitas's behalf for free.

    {"state": None, "district": None} for an address Census can't match
    or that doesn't resolve to a congressional district (e.g. outside the
    US) — never a guess. A malformed/empty address is a 400, not a
    silent null result, so the frontend can tell "you typed something
    Census rejected" apart from "a real address with no match"."""
    address = (address or "").strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")
    if len(address) > 200:
        raise HTTPException(status_code=400, detail="address is too long")

    try:
        async with make_async_client(timeout=15.0) as client:
            response = await client.get(
                _CENSUS_GEOCODER_URL,
                params={
                    "address": address,
                    "benchmark": "Public_AR_Current",
                    "vintage": "Current_Current",
                    "layers": "54",
                    "format": "json",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        # Never log `address` itself (a real visitor-entered street
        # address) — only that the lookup failed.
        logger.exception("Census geocode lookup failed")
        raise HTTPException(status_code=502, detail="Could not resolve that address right now.")

    matches = (payload.get("result") or {}).get("addressMatches") or []
    if not matches:
        return {"state": None, "district": None}

    match = matches[0]
    state = (match.get("addressComponents") or {}).get("state")
    districts = (match.get("geographies") or {}).get("119th Congressional Districts") or []
    if not state or not districts:
        return {"state": None, "district": None}

    cd = districts[0].get("CD119")
    if cd is None or not str(cd).isdigit():
        return {"state": None, "district": None}

    return {"state": state, "district": int(cd)}
