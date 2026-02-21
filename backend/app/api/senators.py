from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import LeaderboardEntrySchema, SenatorSchema, StateCountSchema
from app.services.senator_service import (
    get_leaderboard,
    get_senator_by_id,
    get_senators_by_state,
    get_states_with_counts,
)

router = APIRouter()


@router.get("/config")
def get_config() -> dict:
    """Return all dynamic configuration for the frontend.

    Serves industries, platform categories, score weights, and policy areas
    so the frontend never needs to hardcode these values.
    """
    from app.config_definitions import (
        INDUSTRIES,
        PLATFORM_CATEGORIES,
        POLICY_AREAS,
        SCORE_WEIGHTS,
    )

    return {
        "scoreWeights": SCORE_WEIGHTS,
        "industries": INDUSTRIES,
        "platformCategories": PLATFORM_CATEGORIES,
        "policyAreas": POLICY_AREAS,
    }


@router.get("/senators/states", response_model=list[StateCountSchema])
def list_states(db: Session = Depends(get_db)) -> list[StateCountSchema]:
    """Return all states that have senator data, with counts."""
    return get_states_with_counts(db)


@router.get("/senators/leaderboard", response_model=list[LeaderboardEntrySchema])
def list_leaderboard(db: Session = Depends(get_db)) -> list[LeaderboardEntrySchema]:
    """Return all senators ranked by representation score."""
    return get_leaderboard(db)


@router.get("/senators/{senator_id}/highlights")
async def get_highlights(senator_id: str, db: Session = Depends(get_db)) -> dict:
    """Return LLM-generated data highlights for a senator (cached after first call)."""
    from app.pipeline.analyze.ollama_client import call_llm

    senator = get_senator_by_id(db, senator_id)
    if senator is None:
        raise HTTPException(status_code=404, detail="Senator not found")

    funding = senator.funding
    voting = senator.voting_record
    score = senator.representation_score

    pac_pct = round(
        (funding.total_from_pacs / funding.total_raised * 100)
        if funding.total_raised > 0
        else 0
    )
    top_donors_str = (
        ", ".join(f"{d.name} (${d.total:,.0f})" for d in funding.top_donors[:3])
        if funding.top_donors
        else "none on record"
    )
    from app.config_definitions import SCORE_WEIGHTS
    total_score = round(
        score.constituent_funding * SCORE_WEIGHTS["constituentFunding"]
        + score.independence_index * SCORE_WEIGHTS["independenceIndex"]
        + score.donor_diversity * SCORE_WEIGHTS["donorDiversity"]
        + score.promise_fulfillment * SCORE_WEIGHTS["promiseFulfillment"]
        + score.accountability * SCORE_WEIGHTS["accountability"]
    )
    kept = sum(1 for p in senator.campaign_promises if p.alignment == "kept")
    partial = sum(1 for p in senator.campaign_promises if p.alignment == "partial")
    broken = sum(1 for p in senator.campaign_promises if p.alignment == "broken")
    aligned_connections = sum(1 for m in senator.lobbying_matches if m.senator_vote_aligned)

    result = await call_llm(
        prompt_version="senator-highlights-v1",
        system_prompt="Political analyst. Return ONLY valid JSON, no markdown.",
        user_prompt=(
            f"Senator {senator.name} ({senator.party}-{senator.state}), "
            f"{senator.years_in_office} yrs in office.\n"
            f"FUNDING: ${funding.total_raised:,.0f} raised; {pac_pct}% from PACs; "
            f"{funding.small_donor_percentage:.0f}% small donors.\n"
            f"TOP DONORS: {top_donors_str}.\n"
            f"VOTES: {voting.total_votes} tracked; {voting.donor_aligned_votes} donor-aligned "
            f"of {voting.scoreable_votes} scoreable "
            f"({round(voting.donor_aligned_votes / max(voting.scoreable_votes, 1) * 100)}%), "
            f"{voting.donor_opposed_votes} went against donor interests.\n"
            f"REPRESENTATION SCORE: {total_score}/100 "
            f"(constituent funding: {score.constituent_funding:.0f}, "
            f"voting independence: {score.independence_index:.0f}, "
            f"donor diversity: {score.donor_diversity:.0f}, "
            f"promise fulfillment: {score.promise_fulfillment:.0f}, "
            f"accountability: {score.accountability:.0f}).\n"
            f"DONOR-VOTE CONNECTIONS: {len(senator.lobbying_matches)} found, "
            f"{aligned_connections} voted in donor's interest.\n"
            f"PLATFORM PROMISES: {kept} kept, {partial} partial, {broken} broken "
            f"of {len(senator.campaign_promises)} tracked.\n"
            f'Return JSON: {{"highlights":["<insight>","<insight>","<insight>"]}}\n'
            f"Generate 3-4 specific, punchy insights from this data. Focus on what is "
            f"surprising, notable, or concerning. Use plain English, reference specific numbers."
        ),
        cache_key={
            "senatorId": senator_id,
            "totalRaised": round(funding.total_raised),
            "pacPct": pac_pct,
            "totalVotes": voting.total_votes,
            "totalScore": total_score,
            "promiseCounts": f"{kept}/{partial}/{broken}",
            "v": 1,
        },
        db_session=db,
        max_tokens=400,
    )

    if not result or not isinstance(result.get("highlights"), list):
        return {"highlights": []}

    highlights = [
        str(h)[:300] for h in result["highlights"] if isinstance(h, str) and h.strip()
    ]
    return {"highlights": highlights[:5]}


@router.get("/senators/{senator_id}", response_model=SenatorSchema)
def get_senator(senator_id: str, db: Session = Depends(get_db)) -> SenatorSchema:
    """Return a single senator by ID."""
    result = get_senator_by_id(db, senator_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return result


@router.get("/senators", response_model=list[SenatorSchema])
def list_senators(
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> list[SenatorSchema]:
    """Return senators filtered by state."""
    return get_senators_by_state(db, state)
