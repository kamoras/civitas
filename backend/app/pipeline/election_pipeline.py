"""Candidate roster + fundraising + coverage ingestion for federal
election cycles (2026-07, midterm-elections feature).

Independent pipeline: no data dependency on Senate/House/President's own
runs, same reasoning as supplementary_pipeline.py's own extraction from
senate_pipeline.py. Phases:

  1. Roster sync — every declared candidate for the cycle (bulk FEC fetch,
     not a per-race lookup — see fetch.fec.fetch_all_candidates) upserted
     into Race/Candidate rows, per-candidate fault isolation.
  2. Financial refresh — FEC's per-candidate totals endpoint is rate-
     limited to 1 request/4 sec and there are ~6,900 candidates in the
     2026 cycle, so this is prioritized (incumbents first, then active
     fundraisers, then everyone else) and watermarked (last_financials_
     sync), refreshing a bounded batch per run rather than blocking for
     hours on a single pass.
  3. Coverage ingestion — matches already-fetched RSS articles (Action
     Center's own news_feeds.py fetch, not re-fetched here) and new
     Bluesky search results to races by candidate-name string match.
  4. Bluesky posting — one grounded, source-backed sentence per notable
     coverage item, reusing bluesky_poster.py's existing LLM+grounding
     pattern.
  5. Snapshot — daily fundraising snapshot per candidate for trend charts,
     via the same shared ScoreSnapshot table senators/reps/presidents use.
"""

import logging
import time

import httpx
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Candidate, ElectionPipelineRun, PipelineStatus, Race, ScoreSnapshot
from app.pipeline.fetch.fec import fetch_all_candidates, fetch_candidate_financials
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# The 2026 midterm cycle. A future cycle's fetcher work is the same shape;
# bumping this (and the FEC candidates.py cycle param) is the only change
# needed to point the whole pipeline at the next election.
CURRENT_ELECTION_CYCLE = 2026

ELECTION_PIPELINE_STEPS = [
    ("roster_sync",        "roster",   "Sync candidate roster"),
    ("financial_refresh",  "financial", "Refresh candidate financials"),
    ("coverage_ingestion", "coverage", "Ingest race coverage"),
    ("bluesky_posting",    "posting",  "Post race coverage updates"),
    ("snapshot",           "snapshot", "Snapshot candidate fundraising"),
]

# Candidates refreshed per run at FEC's 0.25 req/s rate limit — 500 candidates
# is ~33 minutes, a small slice of ~6,900 total candidates, so the full set
# cycles through over multiple nightly runs rather than one multi-hour pass.
FINANCIALS_BATCH_SIZE = 500

_tracker = PipelineRunTracker()


def is_election_pipeline_running() -> bool:
    return _tracker.is_running


def election_pipeline_age():
    """Wall-clock age of the in-process election pipeline run, or None when idle."""
    return _tracker.age


def _race_id(cycle: int, office: str, state: str, district: int | None) -> str:
    if office == "S":
        return f"{cycle}-SEN-{state}"
    return f"{cycle}-HOUSE-{state}-{district if district is not None else 0}"


def _sync_roster(db: Session, cycle: int, candidates_raw: list[dict]) -> int:
    """Upsert Race + Candidate rows from raw FEC candidate records.

    Per-candidate try/except so one malformed record doesn't blank the
    rest of the sync — same fault-isolation shape as president_pipeline.py's
    per-president score loop.
    """
    synced = 0
    for raw in candidates_raw:
        try:
            candidate_id = raw.get("candidate_id")
            state = raw.get("state")
            office = raw.get("office")
            if not candidate_id or not state or office not in ("H", "S"):
                continue
            district = raw.get("district_number") if office == "H" else None
            race_id = _race_id(cycle, office, state, district)

            race = db.query(Race).filter(Race.id == race_id).first()
            if race is None:
                race = Race(
                    id=race_id, cycle_year=cycle, office=office,
                    state=state, district=district,
                )
                db.add(race)

            cand = db.query(Candidate).filter(Candidate.id == candidate_id).first()
            if cand is None:
                cand = Candidate(id=candidate_id, race_id=race_id)
                db.add(cand)
            cand.race_id = race_id
            cand.name = raw.get("name") or ""
            cand.party = raw.get("party") or "UNK"
            cand.incumbent_challenge = raw.get("incumbent_challenge")
            cand.has_raised_funds = bool(raw.get("has_raised_funds"))
            synced += 1
        except Exception:
            logger.exception(
                "Failed to sync candidate %s — skipping", raw.get("candidate_id"),
            )
    if synced:
        db.commit()
    return synced


def _prioritize_for_financial_refresh(db: Session, limit: int) -> list[Candidate]:
    """Never-synced candidates first, then oldest-synced first; within each
    group, incumbents before active fundraisers before everyone else."""
    priority = case(
        (Candidate.incumbent_challenge == "I", 0),
        (Candidate.has_raised_funds.is_(True), 1),
        else_=2,
    )
    return (
        db.query(Candidate)
        .order_by(
            Candidate.last_financials_sync.is_(None).desc(),
            priority,
            Candidate.last_financials_sync.asc(),
        )
        .limit(limit)
        .all()
    )


async def _refresh_financials(db: Session, client: httpx.AsyncClient, batch_size: int) -> int:
    candidates = _prioritize_for_financial_refresh(db, batch_size)
    refreshed = 0
    for cand in candidates:
        try:
            totals = await fetch_candidate_financials(client, db, cand.id)
            if totals:
                latest = totals[0]
                cand.contributions = latest.get("contributions")
                cand.disbursements = latest.get("disbursements")
                cand.cash_on_hand = latest.get("last_cash_on_hand_end_period")
                cand.individual_itemized_contributions = latest.get(
                    "individual_itemized_contributions",
                )
            cand.last_financials_sync = utcnow()
            refreshed += 1
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Financial refresh failed for candidate %s — leaving existing values", cand.id,
            )
    return refreshed


def _snapshot_candidates(db: Session) -> int:
    """Daily fundraising snapshot per candidate, via the same shared
    ScoreSnapshot table senators/reps/presidents already use for trend
    charts. overall_score holds cash_on_hand — a fundraising figure, not
    an evaluative score; ScoreSnapshot's shape is reused as-is rather than
    adding a parallel table for one more "value over time per entity" case.
    """
    today = utcnow().strftime("%Y-%m-%d")
    db.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == "candidate",
        ScoreSnapshot.date == today,
    ).delete()

    candidates = db.query(Candidate).filter(Candidate.cash_on_hand.isnot(None)).all()
    for cand in candidates:
        db.add(ScoreSnapshot(
            entity_type="candidate",
            entity_id=cand.id,
            date=today,
            overall_score=cand.cash_on_hand or 0.0,
            score_1=cand.contributions or 0.0,
            score_2=cand.disbursements or 0.0,
        ))
    db.commit()
    return len(candidates)


async def run_election_pipeline(cycle: int = CURRENT_ELECTION_CYCLE) -> dict:
    """Sync candidate rosters, refresh a prioritized batch of financials,
    ingest race coverage, post grounded Bluesky updates, and snapshot
    fundraising. Returns a summary dict with counts."""
    db = SessionLocal()

    run = acquire_pipeline_lock(db, ElectionPipelineRun, STALE_PIPELINE_TIMEOUT)
    if run is None:
        logger.warning("Election pipeline already running in another process — skipping")
        db.close()
        return {"status": "skipped", "reason": "already_running"}

    _tracker.start()
    start_time = time.time()
    progress = ProgressTracker(run, ELECTION_PIPELINE_STEPS, db, start_time)

    try:
        logger.info("=== ELECTION PIPELINE START (cycle %d) ===", cycle)

        async with httpx.AsyncClient() as client:
            run.current_phase = "roster"
            db.commit()
            logger.info("--- Election: ROSTER SYNC ---")
            progress.begin("roster_sync")
            try:
                house_raw = await fetch_all_candidates(client, db, cycle, "H")
                senate_raw = await fetch_all_candidates(client, db, cycle, "S")
                synced = _sync_roster(db, cycle, house_raw + senate_raw)
                run.candidates_synced = synced
                logger.info("Synced %d candidates", synced)
                progress.complete("roster_sync", detail=f"{synced} candidates")
            except Exception:
                db.rollback()
                logger.exception("Roster sync failed — continuing")
                progress.fail("roster_sync")

            run.current_phase = "financial"
            db.commit()
            logger.info("--- Election: FINANCIAL REFRESH ---")
            progress.begin("financial_refresh")
            try:
                refreshed = await _refresh_financials(db, client, FINANCIALS_BATCH_SIZE)
                run.financials_refreshed = refreshed
                logger.info("Refreshed financials for %d candidates", refreshed)
                progress.complete("financial_refresh", detail=f"{refreshed} refreshed")
            except Exception:
                db.rollback()
                logger.exception("Financial refresh phase failed — continuing")
                progress.fail("financial_refresh")

            run.current_phase = "coverage"
            db.commit()
            logger.info("--- Election: COVERAGE INGESTION ---")
            progress.begin("coverage_ingestion")
            try:
                from app.pipeline.analyze.election_coverage import ingest_race_coverage
                ingested = await ingest_race_coverage(db, client)
                run.coverage_items_ingested = ingested
                logger.info("Ingested %d coverage items", ingested)
                progress.complete("coverage_ingestion", detail=f"{ingested} items")
            except Exception:
                db.rollback()
                logger.exception("Coverage ingestion failed — continuing")
                progress.fail("coverage_ingestion")

            run.current_phase = "posting"
            db.commit()
            logger.info("--- Election: BLUESKY POSTING ---")
            progress.begin("bluesky_posting")
            try:
                from app.pipeline.analyze.election_bluesky import post_race_coverage_updates
                posted = post_race_coverage_updates(db)
                logger.info("Posted %d race coverage updates", posted)
                progress.complete("bluesky_posting", detail=f"{posted} posted")
            except Exception:
                db.rollback()
                logger.exception("Bluesky posting failed — continuing")
                progress.fail("bluesky_posting")

            run.current_phase = "snapshot"
            db.commit()
            logger.info("--- Election: SNAPSHOT ---")
            progress.begin("snapshot")
            try:
                snapshotted = _snapshot_candidates(db)
                logger.info("Snapshotted %d candidates", snapshotted)
                progress.complete("snapshot", detail=f"{snapshotted} snapshotted")
            except Exception:
                db.rollback()
                logger.exception("Snapshot phase failed — continuing")
                progress.fail("snapshot")

        run.current_phase = "finalize"
        run.status = PipelineStatus.COMPLETED
        run.completed_at = utcnow()
        run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("=== ELECTION PIPELINE COMPLETE ===")

        return {
            "status": PipelineStatus.COMPLETED,
            "candidates_synced": run.candidates_synced,
            "financials_refreshed": run.financials_refreshed,
            "coverage_items_ingested": run.coverage_items_ingested,
            "elapsed_seconds": run.elapsed_seconds,
        }
    except Exception as e:
        logger.exception("Election pipeline failed: %s", e)
        summary = "election pipeline failed — see server logs"
        try:
            db.rollback()
            run.status = PipelineStatus.FAILED
            run.completed_at = utcnow()
            run.elapsed_seconds = round(time.time() - start_time, 1)
            run.error_message = summary
            db.commit()
        except Exception:
            logger.exception("Failed to record election pipeline failure")
        return {"status": PipelineStatus.FAILED, "error": summary}
    finally:
        _tracker.stop()
        db.close()
