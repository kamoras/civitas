"""Midterm-elections API — candidate rosters, race detail, and PVI
(2026-07). Plain camelCase dicts, same convention as api/action.py's
existing /action/elections endpoint (that endpoint is unchanged; this is
a separate, fuller namespace for the new candidate-research feature)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze.score_calculator import get_district_pvi_map, get_state_pvi_map

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/elections")


def _pvi_for_race(race: Race, state_pvi: dict, district_pvi: dict) -> int | None:
    if race.office == "H":
        key = f"{race.state}-{race.district if race.district is not None else 0}"
        if key in district_pvi:
            return district_pvi[key]
    return state_pvi.get(race.state)


def _candidate_summary(cand: Candidate) -> dict:
    return {
        "id": cand.id,
        "name": cand.name,
        "party": cand.party,
        "incumbentChallenge": cand.incumbent_challenge,
        "hasRaisedFunds": cand.has_raised_funds,
        "contributions": cand.contributions,
        "cashOnHand": cand.cash_on_hand,
    }


def _race_summary(race: Race, state_pvi: dict, district_pvi: dict) -> dict:
    candidates = sorted(
        race.candidates,
        key=lambda c: (c.cash_on_hand or 0.0),
        reverse=True,
    )
    top_candidates = candidates[:2]
    return {
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": _pvi_for_race(race, state_pvi, district_pvi),
        "candidateCount": len(candidates),
        "topCandidates": [_candidate_summary(c) for c in top_candidates],
    }


@router.get("/races")
def list_races(db: Session = Depends(get_db)):
    """All races for the current cycle, with PVI and top-2-by-funds
    candidates — backs the map + directory."""
    races = db.query(Race).all()
    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    data = [_race_summary(r, state_pvi, district_pvi) for r in races]
    return cached_json(data, max_age=CACHE_TTL_LIST_S)


@router.get("/pvi")
def pvi_map():
    """State + district PVI maps (positive = R lean, negative = D lean) —
    already computed for internal scoring (score_calculator.py), exposed
    publicly here for the first time to color the elections map."""
    return cached_json(
        {"states": get_state_pvi_map(), "districts": get_district_pvi_map()},
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

    return cached_json({
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": _pvi_for_race(race, state_pvi, district_pvi),
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
                "publishedAt": item.published_at.isoformat() if item.published_at else None,
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
        "lastFinancialsSync": cand.last_financials_sync.isoformat() if cand.last_financials_sync else None,
        "race": {
            "id": race.id,
            "office": race.office,
            "state": race.state,
            "district": race.district,
        } if race else None,
    }, max_age=CACHE_TTL_DETAIL_S)
