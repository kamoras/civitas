"""President data pipeline — fetches live data and recalculates scores.

Targets presidents from Clinton (#42) onward where Federal Register
and BLS data are available. Older presidents keep their seed scores.
"""

import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.models import President
from app.pipeline.analyze.president_scorer import recalculate_president_scores
from app.pipeline.fetch.economic_data import fetch_jobs_for_president
from app.pipeline.fetch.federal_register import fetch_all_eo_data, fetch_all_rulemaking_stats

logger = logging.getLogger(__name__)

DYNAMIC_PRESIDENTS = [
    "clinton-42", "gwbush-43", "obama-44",
    "trump-45", "biden-46", "trump-47",
]


def _term_years(start: str, end: str | None) -> float:
    """Calculate term length in years."""
    s = datetime.strptime(start, "%Y-%m-%d")
    if end:
        e = datetime.strptime(end, "%Y-%m-%d")
    else:
        e = datetime.utcnow()
    return max((e - s).days / 365.25, 0.1)


async def run_president_pipeline(db: Session) -> dict:
    """Fetch live data and recalculate scores for recent presidents.

    Returns summary dict with counts.
    """
    logger.info("Starting president pipeline...")

    async with httpx.AsyncClient() as client:
        # 1. Fetch executive order data from Federal Register
        logger.info("Fetching executive order data from Federal Register...")
        eo_data = await fetch_all_eo_data(client)
        logger.info("EO data fetched for %d presidents", len(eo_data))

        # 2. Fetch employment data from BLS
        logger.info("Fetching employment data from BLS...")
        jobs_data: dict[str, float | None] = {}
        for pid in DYNAMIC_PRESIDENTS:
            jobs = await fetch_jobs_for_president(client, pid)
            if jobs is not None:
                jobs_data[pid] = jobs
                logger.info("  %s: %+.1fM jobs", pid, jobs)

        # 3. Fetch rulemaking stats from Federal Register
        logger.info("Fetching agency rulemaking data from Federal Register...")
        rulemaking_data = await fetch_all_rulemaking_stats(client)
        logger.info("Rulemaking data fetched for %d presidents", len(rulemaking_data))

    # 4. Update each president in the database
    updated = 0
    for pid in DYNAMIC_PRESIDENTS:
        president = db.query(President).filter(President.id == pid).first()
        if not president:
            logger.warning("President %s not found in database", pid)
            continue

        term_years = _term_years(president.term_start, president.term_end)

        live = {
            "eo_court_success_pct": president.eo_court_success_pct,
            "cabinet_turnover_pct": president.cabinet_turnover_pct,
            "gdp_growth_avg": president.gdp_growth_avg,
        }

        # Overlay live Federal Register EO count
        if pid in eo_data:
            live["eo_count"] = eo_data[pid]["eo_count"]
            president.eo_count = eo_data[pid]["eo_count"]

        # Overlay live BLS jobs data
        if pid in jobs_data:
            live["jobs_created_millions"] = jobs_data[pid]
            president.jobs_created_millions = jobs_data[pid]

        # Overlay live rulemaking data
        if pid in rulemaking_data:
            live["rulemaking_count"] = rulemaking_data[pid]["rulemaking_count"]
            live["rulemaking_finalized_pct"] = rulemaking_data[pid]["rulemaking_finalized_pct"]

        seed_scores = {
            "score_independence": president.score_independence,
            "score_follow_through": president.score_follow_through,
            "score_public_mandate": president.score_public_mandate,
            "score_effectiveness": president.score_effectiveness,
            "score_competence": president.score_competence,
            "score_agency_alignment": president.score_agency_alignment,
        }

        new_scores = recalculate_president_scores(
            pid, seed_scores, live, term_years,
        )

        president.score_competence = new_scores["score_competence"]
        president.score_effectiveness = new_scores["score_effectiveness"]
        president.score_agency_alignment = new_scores["score_agency_alignment"]
        president.updated_at = datetime.utcnow()
        updated += 1

        logger.info(
            "  %s: competence=%d effectiveness=%d agency=%d (eo=%s jobs=%s rules=%s)",
            pid,
            new_scores["score_competence"],
            new_scores["score_effectiveness"],
            new_scores["score_agency_alignment"],
            live.get("eo_count", "seed"),
            live.get("jobs_created_millions", "seed"),
            live.get("rulemaking_count", "seed"),
        )

    db.commit()
    logger.info("President pipeline complete: %d updated", updated)

    return {
        "updated": updated,
        "eo_data_count": len(eo_data),
        "jobs_data_count": len(jobs_data),
        "rulemaking_data_count": len(rulemaking_data),
    }
