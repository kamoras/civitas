"""President data pipeline — fetches live data and recalculates scores.

Live-data recalculation targets presidents from Clinton (#42) onward
where Federal Register and BLS data are available. Older presidents keep
their seed scores. Score-history snapshotting (_record_president_snapshots,
2026-07) covers every president regardless — same as senators/reps, a
historical president's unchanging score still gets a daily row so trend
charts have a continuous line, not gaps.
"""

import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.models import President, ScoreSnapshot
from app.pipeline.analyze.president_scorer import (
    PRESIDENT_ALGORITHM_VERSION,
    compute_president_overall_score,
    recalculate_president_scores,
)
from app.pipeline.fetch.economic_data import (
    fetch_jobs_for_president,
    fetch_gdp_by_year,
    fetch_gdp_for_president,
)
from app.pipeline.fetch.federal_register import fetch_all_eo_data, fetch_all_rulemaking_stats
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

DYNAMIC_PRESIDENTS = [
    "clinton-42", "gwbush-43", "obama-44",
    "trump-45", "biden-46", "trump-47",
]

# Presidents for whom BLS employment data and FRED GDP data are available
# but Federal Register / EO data are not.  Only score_effectiveness is
# recalculated; all other sub-scores remain as seed values.
# Data availability: BLS payroll series from 1939; FRED GDP from 1947.
# References: Blinder & Watson (2016, AER 106(4)) for year-1 exclusion.
ECONOMICS_ONLY_PRESIDENTS = [
    "eisenhower-34", "jfk-35", "lbj-36", "nixon-37",
    "ford-38", "carter-39", "reagan-40", "ghwbush-41",
]


def _term_years(start: str, end: str | None) -> float:
    """Calculate term length in years."""
    s = datetime.strptime(start, "%Y-%m-%d")
    if end:
        e = datetime.strptime(end, "%Y-%m-%d")
    else:
        e = utcnow()
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
        all_economic_presidents = DYNAMIC_PRESIDENTS + ECONOMICS_ONLY_PRESIDENTS
        for pid in all_economic_presidents:
            jobs = await fetch_jobs_for_president(client, pid)
            if jobs is not None:
                jobs_data[pid] = jobs
                logger.info("  %s: %+.1fM jobs", pid, jobs)

        # 3. Fetch annual GDP from FRED for year-1-excluded adjustment.
        # Blinder & Watson (2016) show the first year's GDP reflects the
        # prior administration; excluding it gives a fairer attribution.
        logger.info("Fetching annual GDP data from FRED...")
        gdp_by_year = await fetch_gdp_by_year(client) or {}
        gdp_adj_data: dict[str, float] = {}
        gdp_full_data: dict[str, float] = {}
        for pid in all_economic_presidents:
            gdp_full, gdp_adj = await fetch_gdp_for_president(client, pid, gdp_by_year)
            if gdp_adj is not None:
                gdp_adj_data[pid] = gdp_adj
                logger.info("  %s: GDP full=%.1f%% adjusted(yr1-excl)=%.1f%%",
                            pid, gdp_full or 0.0, gdp_adj)
            elif gdp_full is not None:
                gdp_full_data[pid] = gdp_full

        # 4. Fetch rulemaking stats from Federal Register
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

        # eo_court_success_pct and cabinet_turnover_pct are NOT included
        # here: no fetch function anywhere populates them from a live
        # source (there is no structured, machine-readable API for EO
        # litigation outcomes or cabinet tenure this pipeline uses) — they
        # are one-time editorial estimates set in the seed data and never
        # updated. Passing the stored value back in as "live" would make
        # calc_competence's court-success (40% weight) and cabinet-
        # stability (30% weight) components look freshly computed on
        # every run when they're actually frozen opinion (2026-07 audit).
        # Omitting them here lets calc_competence's existing
        # missing-component fallback correctly blend that 70% of weight
        # with seed_score instead — the same honest "seed" treatment
        # already applied to Independence, Follow-Through, and Public
        # Mandate.
        live = {
            "gdp_growth_avg": president.gdp_growth_avg,
            # Year-1-excluded GDP average (Blinder & Watson 2016)
            "gdp_growth_adjusted": gdp_adj_data.get(pid),
        }
        # Persisted (not just kept in this local dict) so the on-demand
        # score-breakdown endpoint can recompute calc_effectiveness's exact
        # inputs later without a live re-fetch from FRED.
        president.gdp_growth_adjusted = live["gdp_growth_adjusted"]

        # Overlay live Federal Register EO count
        if pid in eo_data:
            live["eo_count"] = eo_data[pid]["eo_count"]
            president.eo_count = eo_data[pid]["eo_count"]

        # Overlay live BLS jobs data
        if pid in jobs_data:
            live["jobs_created_millions"] = jobs_data[pid]
            president.jobs_created_millions = jobs_data[pid]

        # Overlay live rulemaking data — persisted for the same on-demand
        # breakdown-recompute reason as gdp_growth_adjusted above.
        if pid in rulemaking_data:
            live["rulemaking_count"] = rulemaking_data[pid]["rulemaking_count"]
            live["rulemaking_finalized_pct"] = rulemaking_data[pid]["rulemaking_finalized_pct"]
            president.rulemaking_count = rulemaking_data[pid]["rulemaking_count"]
            president.rulemaking_finalized_pct = rulemaking_data[pid]["rulemaking_finalized_pct"]

        seed_scores = {
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
        president.updated_at = utcnow()
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

    # Update economics-only presidents (Eisenhower through GHW Bush).
    # Only recalculates score_effectiveness from GDP + jobs; all other
    # sub-scores remain as seed values.
    econ_updated = 0
    for pid in ECONOMICS_ONLY_PRESIDENTS:
        president = db.query(President).filter(President.id == pid).first()
        if not president:
            continue

        term_years = _term_years(president.term_start, president.term_end)
        gdp_adj = gdp_adj_data.get(pid)
        gdp_full = gdp_full_data.get(pid, president.gdp_growth_avg)
        jobs = jobs_data.get(pid, president.jobs_created_millions)

        if gdp_adj is None and gdp_full is None and jobs is None:
            continue

        from app.pipeline.analyze.president_scorer import calc_effectiveness
        new_eff = calc_effectiveness(
            jobs_created_millions=jobs,
            gdp_growth_avg=gdp_full,
            term_years=term_years,
            seed_score=president.score_effectiveness,
            gdp_growth_adjusted=gdp_adj,
        )
        president.score_effectiveness = new_eff
        if jobs is not None:
            president.jobs_created_millions = jobs
        if gdp_full is not None and president.gdp_growth_avg is None:
            president.gdp_growth_avg = gdp_full
        president.gdp_growth_adjusted = gdp_adj
        president.updated_at = utcnow()
        econ_updated += 1
        logger.info("  %s: effectiveness=%d (gdp_adj=%.1f%% jobs=%.1fM)",
                    pid, new_eff, gdp_adj or 0.0, jobs or 0.0)

    db.commit()
    logger.info("President pipeline complete: %d dynamic + %d economics-only updated",
                updated, econ_updated)

    _record_president_snapshots(db)

    return {
        "updated": updated + econ_updated,
        "eo_data_count": len(eo_data),
        "jobs_data_count": len(jobs_data),
        "rulemaking_data_count": len(rulemaking_data),
    }


def _record_president_snapshots(db: Session) -> None:
    """Snapshot today's scores for every president so we can compute trends.

    ScoreSnapshot (models.py) is a generic table already shared by
    senators ("senator") and representatives ("representative") — this is
    the first writer for "president". Runs for every president, not just
    DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS: even a historical
    president whose scores never change still gets a daily row, same as
    how senators/reps are snapshotted regardless of whether their score
    happened to move that day — the trend chart needs a continuous line,
    not gaps wherever nothing changed.

    Per-row upsert (not delete-then-insert): mirrors house_pipeline.py's
    _record_rep_snapshots rather than senate_pipeline.py's
    _record_score_snapshots, which briefly deletes the day's rows before
    reinserting — an upsert never leaves the table without today's data
    mid-write.
    """
    today = utcnow().date().isoformat()
    presidents = db.query(President).all()
    count = 0
    for p in presidents:
        overall = compute_president_overall_score(p)
        existing = (
            db.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.entity_type == "president",
                ScoreSnapshot.entity_id == p.id,
                ScoreSnapshot.date == today,
            )
            .first()
        )
        if existing:
            existing.overall_score = overall
            existing.score_1 = p.score_public_mandate
            existing.score_2 = p.score_effectiveness
            existing.score_3 = p.score_competence
            existing.score_4 = p.score_agency_alignment
        else:
            db.add(ScoreSnapshot(
                entity_type="president",
                entity_id=p.id,
                date=today,
                overall_score=overall,
                score_1=p.score_public_mandate,
                score_2=p.score_effectiveness,
                score_3=p.score_competence,
                score_4=p.score_agency_alignment,
                algorithm_version=PRESIDENT_ALGORITHM_VERSION,
            ))
            count += 1
    db.commit()
    logger.info("Recorded %d new president score snapshots (%d total presidents)", count, len(presidents))
