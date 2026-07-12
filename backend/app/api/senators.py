from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db

from app.services.senator_service import (
    get_leaderboard,
    get_senator_by_id,
    get_senator_votes,
    get_senators_by_state,
    get_states_with_counts,
)

router = APIRouter()


def _cached_json(data, max_age: int = 300) -> JSONResponse:
    """Wrap data in a JSONResponse with Cache-Control headers."""
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


@router.get("/config")
def get_config() -> JSONResponse:
    """Return all dynamic configuration for the frontend.

    Serves industries, platform categories, score weights, and policy areas
    so the frontend never needs to hardcode these values.
    """
    from app.config_definitions import (
        INDUSTRIES,
        PLATFORM_CATEGORIES,
        POLICY_AREAS,
        PRESIDENT_SCORE_WEIGHTS,
        SCORE_WEIGHTS,
    )

    return _cached_json({
        "scoreWeights": SCORE_WEIGHTS,
        "presidentScoreWeights": PRESIDENT_SCORE_WEIGHTS,
        "industries": INDUSTRIES,
        "platformCategories": PLATFORM_CATEGORIES,
        "policyAreas": POLICY_AREAS,
    }, max_age=3600)


@router.get("/senators/states")
def list_states(db: Session = Depends(get_db)) -> JSONResponse:
    """Return all states that have senator data, with counts."""
    data = get_states_with_counts(db)
    return _cached_json([s.model_dump(by_alias=True) for s in data], max_age=300)


@router.get("/senators/leaderboard")
def list_leaderboard(db: Session = Depends(get_db)) -> JSONResponse:
    """Return all senators ranked by representation score."""
    data = get_leaderboard(db)
    return _cached_json([e.model_dump(by_alias=True) for e in data], max_age=300)


@router.get("/senators/{senator_id}/highlights")
async def get_highlights(senator_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return data-driven highlights for a senator — no LLM, pure data."""
    senator = get_senator_by_id(db, senator_id)
    if senator is None:
        raise HTTPException(status_code=404, detail="Senator not found")

    highlights = _build_highlights(senator)
    return _cached_json({"highlights": highlights[:5]}, max_age=120)


def _build_highlights(senator) -> list[str]:
    """Generate factual, data-driven insights from senator records."""
    funding = senator.funding
    voting = senator.voting_record
    score = senator.representation_score
    hints: list[tuple[int, str]] = []  # (priority, text)

    total = funding.total_raised
    small_pct = funding.small_donor_percentage or 0
    pac_total = funding.total_from_pacs or 0
    pac_pct_raw = pac_total / total * 100 if total > 0 else 0.0
    pac_pct_str = "<1" if 0 < pac_pct_raw < 1 else f"{pac_pct_raw:.0f}"

    # --- Funding highlights ---
    if small_pct >= 50:
        hints.append((10, (
            f"Grassroots funded: {small_pct:.0f}% of {senator.name}'s "
            f"${total / 1e6:.1f}M raised comes from small donors (under $200), "
            f"suggesting broad constituent support."
        )))
    elif small_pct < 15 and total > 0:
        small_str = "<1" if 0 < small_pct < 1 else f"{small_pct:.0f}"
        hints.append((10, (
            f"Only {small_str}% of {senator.name}'s "
            f"${total / 1e6:.1f}M came from small donors — "
            f"the vast majority flows from large donors and organizations."
        )))

    if pac_pct_raw > 40:
        hints.append((9, (
            f"PAC-heavy: {pac_pct_str}% of funding (${pac_total:,.0f}) "
            f"comes from political action committees."
        )))
    elif pac_pct_raw < 5 and total > 500_000:
        hints.append((9, (
            f"Virtually PAC-free: Only ${pac_total:,.0f} "
            f"({pac_pct_str}%) came from PACs — an unusually low amount."
        )))

    # Top industry donor
    industry_donors = [
        d for d in funding.top_donors
        if d.type not in ("CandidateAffiliated",) and d.industry not in (
            "POLITICAL", "SMALL_DONORS", "LARGE_INDIVIDUAL", "OTHER"
        )
    ]
    if industry_donors:
        top = industry_donors[0]
        hints.append((5, (
            f"Largest industry donor: {top.name} "
            f"(${top.total:,.0f}, {top.industry.replace('_', ' ').title()})."
        )))

    # --- Voting highlights ---
    # (Donor-alignment voting highlights removed: VotingRecord no longer has
    # scoreable_votes, donor_aligned_votes, or donor_opposed_votes.)

    # --- Lobbying matches ---
    matches = senator.lobbying_matches or []
    aligned_matches = sum(1 for m in matches if m.senator_vote_aligned)
    if len(matches) > 3 and aligned_matches > 2:
        hints.append((7, (
            f"Found {len(matches)} donor-vote connections where a major donor's "
            f"industry overlaps with legislation — {aligned_matches} votes went "
            f"the donor's way."
        )))
    elif len(matches) == 0:
        hints.append((3, (
            "No direct donor-vote industry connections detected in tracked legislation."
        )))

    # --- Promise fulfillment ---
    promises = senator.campaign_promises or []
    kept = sum(1 for p in promises if p.alignment == "kept")
    broken = sum(1 for p in promises if p.alignment == "broken")
    if len(promises) > 0:
        if kept > 0 and broken == 0:
            hints.append((6, (
                f"Platform follow-through: {kept} of {len(promises)} tracked "
                f"campaign promises rated as kept, with none broken."
            )))
        elif broken > kept and len(promises) >= 3:
            hints.append((6, (
                f"Promise gap: {broken} campaign promises rated as broken "
                f"versus only {kept} kept out of {len(promises)} tracked."
            )))

    # --- Overall score ---
    from app.config_definitions import SCORE_WEIGHTS
    total_score = round(
        score.funding_independence * SCORE_WEIGHTS["fundingIndependence"]
        + score.promise_persistence * SCORE_WEIGHTS["promisePersistence"]
        + score.independent_voting * SCORE_WEIGHTS["independentVoting"]
        + score.funding_diversity * SCORE_WEIGHTS["fundingDiversity"]
        + score.legislative_effectiveness * SCORE_WEIGHTS["legislativeEffectiveness"]
    )
    if total_score >= 80:
        hints.append((2, (
            f"Overall representation score: {total_score}/100 — "
            f"strong marks across funding transparency, voting independence, "
            f"and promise fulfillment."
        )))
    elif total_score <= 40:
        hints.append((2, (
            f"Overall representation score: {total_score}/100 — "
            f"significant concerns across funding sources, voting patterns, "
            f"or promise fulfillment."
        )))

    hints.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in hints]


@router.get("/senators/{senator_id}/history")
def get_senator_history(senator_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return historical score snapshots for a senator."""
    from app.models import ScoreSnapshot
    snapshots = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.entity_type == "senator", ScoreSnapshot.entity_id == senator_id)
        .order_by(ScoreSnapshot.date)
        .all()
    )
    return _cached_json({
        "snapshots": [
            {
                "date": s.date,
                "overallScore": round(s.overall_score, 1),
                "algorithmVersion": s.algorithm_version,
                "scores": {
                    "fundingIndependence": round(s.score_1, 1),
                    "promisePersistence": round(s.score_2, 1),
                    "independentVoting": round(s.score_3, 1),
                    "fundingDiversity": round(s.score_4, 1),
                    "legislativeEffectiveness": round(s.score_5, 1),
                },
            }
            for s in snapshots
        ]
    }, max_age=3600)


@router.get("/senators/{senator_id}/votes")
def get_votes(
    senator_id: str,
    category: str = Query("recent", pattern="^(recent|key)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=1, le=100),
    filter: str = Query("all", pattern="^(all|yea|nay|against-party)$"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return paginated votes for a senator."""
    result = get_senator_votes(db, senator_id, category, page, per_page, filter)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("/senators/{senator_id}")
def get_senator(senator_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return a single senator by ID."""
    result = get_senator_by_id(db, senator_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("/senators")
def list_senators(
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return senators filtered by state."""
    data = get_senators_by_state(db, state)
    return _cached_json([s.model_dump(by_alias=True) for s in data], max_age=120)
