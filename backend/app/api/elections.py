"""Midterm-elections API — candidate rosters, race detail, and PVI
(2026-07). Plain camelCase dicts, same convention as api/action.py's
existing /action/elections endpoint (that endpoint is unchanged; this is
a separate, fuller namespace for the new candidate-research feature)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.election_calendar import (
    CLASS_I_STATES,
    CLASS_II_STATES,
    CLASS_III_STATES,
    next_election_day,
)
from app.models import BallotMeasure, Candidate, MeasureCoverage, Race, RaceCoverageItem
from app.pipeline.analyze.score_calculator import (
    get_district_pvi_map,
    get_pvi_meta,
    get_state_pvi_map,
)
from app.pipeline.election_pipeline import current_election_cycle
from app.pipeline.fetch.ballot_lookup import lookup_for_state
from app.time_utils import utcnow

# The 50 states, from the same class sets election_pipeline.py derives its
# roster filter from — one source for "which jurisdictions hold federal
# elections", not a second hand-typed list that can drift from it.
STATE_CODES = CLASS_I_STATES | CLASS_II_STATES | CLASS_III_STATES

# DC is a valid BALLOT jurisdiction even though it has no voting member of
# Congress and is deliberately absent from the candidate roster (see
# election_pipeline.STATES_WITH_FEDERAL_RACES). It votes on statewide
# initiatives, and — decisively — the frontend's own map renders DC as a
# clickable, keyboard-focusable region, so refusing it here would 404 a
# link the site itself hands the user. The territories are not included:
# the map doesn't render them, so nothing links there.
BALLOT_STATE_CODES = STATE_CODES | {"DC"}

logger = logging.getLogger(__name__)

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
        },
        max_age=CACHE_TTL_LIST_S,
    )


def _measure_json(measure) -> dict:
    """One measure, with everything needed to read it honestly.

    Every text field here is verbatim from `sourceName` — nothing on this
    endpoint is model-generated (see BallotMeasure's docstring). `yesMeans`
    /`noMeans` are null whenever the source publishes no such framing,
    rather than inferred: the intuitive inference is inverted on a veto
    referendum, where approving RETAINS the law under challenge.
    """
    return {
        "id": measure.id,
        "state": measure.state,
        "electionDate": measure.election_date,
        "electionType": measure.election_type,
        "number": measure.number,
        "title": measure.title,
        "measureType": measure.measure_type,
        "origin": measure.origin,
        "status": measure.status,
        "officialTitle": measure.official_title,
        "officialSummary": measure.official_summary,
        "fiscalImpact": measure.fiscal_impact,
        "yesMeans": measure.yes_means,
        "noMeans": measure.no_means,
        "titleAuthority": measure.title_authority,
        "fiscalAuthority": measure.fiscal_authority,
        "sourceName": measure.source_name,
        "sourceUrl": measure.source_url,
        "asOf": _iso_utc(measure.as_of),
    }


@router.get("/states/{state}/ballot")
def state_ballot(state: str, db: Session = Depends(get_db)):
    """The statewide portion of one state's ballot: federal races on file
    plus statewide ballot measures, with an explicit account of what is
    NOT here.

    Scope is deliberately narrow and named in the payload. A ballot is
    defined per ballot style, not per state (precinct splits mean one
    county can print dozens), so U.S. House district contests, state
    legislative districts, county/municipal offices, judicial questions
    and local measures cannot be shown on a state page without misstating
    somebody's ballot. `omits` carries that list so the frontend renders
    the limitation as content rather than a footnote, and
    `officialLookup` is the route to the rest.

    `measureCoverage` is the field that keeps an empty list honest:
    "confirmed_none" (the source says this state has none) and
    "not_yet_covered" / "ingest_failed" (we don't know) are different
    claims, and rendering them identically would tell a voter in a state
    with 17 amendments that there is nothing to research.
    """
    state = state.upper()
    if state not in BALLOT_STATE_CODES:
        raise HTTPException(status_code=404, detail="Unknown state")

    cycle = current_election_cycle()
    election_day = next_election_day(utcnow().date()).isoformat()

    races = (
        db.query(Race)
        .filter(Race.cycle_year == cycle, Race.state == state)
        .options(selectinload(Race.candidates))
        .all()
    )
    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()

    measures = (
        db.query(BallotMeasure)
        .filter(BallotMeasure.state == state)
        .order_by(BallotMeasure.election_date, BallotMeasure.number)
        .all()
    )
    coverage = (
        db.query(MeasureCoverage)
        .filter(
            MeasureCoverage.state == state,
            MeasureCoverage.election_date == election_day,
        )
        .first()
    )

    senate_races = [r for r in races if r.office == "S"]
    house_races = sorted(
        (r for r in races if r.office == "H"),
        key=lambda r: (r.district if r.district is not None else 0),
    )

    return cached_json({
        "state": state,
        "cycleYear": cycle,
        # The federal general is the only date derivable from statute
        # (2 U.S.C. §7). Primaries are party-specific and set by each
        # state on ~50 different dates, so they are NOT covered here and
        # `omits` says so — rather than this page quietly implying that
        # the November ballot is the next one a visitor will see.
        "electionDate": election_day,
        "electionType": "general",
        "statePvi": state_pvi.get(state),
        "senateRaces": [_race_summary(r, state_pvi, district_pvi) for r in senate_races],
        "houseRaces": [_race_summary(r, state_pvi, district_pvi) for r in house_races],
        "measures": [_measure_json(m) for m in measures],
        "measureCoverage": {
            "status": coverage.status if coverage else MeasureCoverage.NOT_YET_COVERED,
            "sourceName": coverage.source_name if coverage else None,
            "checkedAt": _iso_utc(coverage.checked_at) if coverage else None,
        },
        "officialLookup": lookup_for_state(state),
        "omits": [
            "Governor and other statewide executive contests",
            "State legislative districts",
            "Judicial contests and retention questions",
            "County and municipal offices",
            "Local ballot measures",
            "Primary and runoff ballots",
        ] + ([
            # DC elects a Delegate with no floor vote, so it has no race in
            # the FEC-derived roster at all. Saying that is better than a
            # page that just looks empty.
            "DC's Delegate to the House (non-voting) is not covered here",
        ] if state == "DC" else []),
    }, max_age=CACHE_TTL_DETAIL_S)


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
        "coverage": [
            {
                "id": item.id,
                "sourceType": item.source_type,
                "sourceName": item.source_name,
                "title": item.title,
                "url": item.url,
                "summary": item.summary,
                "author": item.author,
                "publishedAt": _iso_utc(item.published_at),
            }
            for item in coverage
        ],
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
