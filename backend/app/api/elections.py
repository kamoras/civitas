"""Midterm-elections API — candidate rosters, race detail, and PVI
(2026-07). Plain camelCase dicts, same convention as api/action.py's
existing /action/elections endpoint (that endpoint is unchanged; this is
a separate, fuller namespace for the new candidate-research feature)."""

import json
import logging
import pathlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.election_calendar import next_election_day
from app.models import Candidate, Race, RaceCoverageItem, Representative, Senator
from app.pipeline.analyze.score_calculator import (
    compute_overall_score,
    get_district_pvi_map,
    get_pvi_meta,
    get_state_pvi_map,
)
from app.pipeline.election_pipeline import STATES_WITH_FEDERAL_RACES, current_election_cycle
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


def _race_summary(race: Race, state_pvi: dict, district_pvi: dict) -> dict:
    candidates = sorted(
        race.candidates,
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
    page."""
    candidates = sorted(race.candidates, key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
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
    """Full race detail: all candidates, financials, coverage feed."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    candidates = sorted(race.candidates, key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
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
