"""Candidate roster + fundraising + coverage ingestion for federal
election cycles (2026-07, midterm-elections feature).

Independent pipeline: no data dependency on Senate/House/President's own
runs, same reasoning as supplementary_pipeline.py's own extraction from
senate_pipeline.py. Phases:

  1. Roster sync — every declared candidate for the cycle (bulk FEC fetch,
     not a per-race lookup — see fetch.fec.fetch_all_candidates) upserted
     into Race/Candidate rows, per-candidate fault isolation.
  2. Financial refresh — FEC's per-candidate totals endpoint is rate-
     limited to 1 request/4 sec and there are ~6,900 candidates in a
     midterm cycle, so this is prioritized (incumbents first, then active
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
from datetime import timedelta

import httpx
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.election_calendar import (
    CLASS_I_STATES,
    CLASS_II_STATES,
    CLASS_III_STATES,
    next_election_day,
    seats_up_for_year,
)
from app.http_client import make_async_client
from app.models import Candidate, ElectionPipelineRun, PipelineStatus, Race, RaceCoverageItem, ScoreSnapshot
from app.pipeline.fetch.fec import fetch_all_candidates, fetch_candidate_financials
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

def current_election_cycle() -> int:
    """The election cycle currently in progress, e.g. 2026 up through
    election night, then 2028 — computed from the calendar (reusing
    next_election_day, the same statutory rule the roster's special-
    election detection relies on) so a new cycle needs no code change."""
    return next_election_day(utcnow().date()).year

ELECTION_PIPELINE_STEPS = [
    ("roster_sync",          "roster",              "Sync candidate roster"),
    ("financial_refresh",    "financial",           "Refresh candidate financials"),
    ("confirmed_candidates", "confirmed_candidates", "Confirm general-election candidates"),
    ("coverage_ingestion",   "coverage",             "Ingest race coverage"),
    ("bluesky_posting",      "posting",              "Post race coverage updates"),
    ("snapshot",             "snapshot",             "Snapshot candidate fundraising"),
]

# Candidates refreshed per run at FEC's 0.25 req/s rate limit — 500 candidates
# is ~33 minutes, a small slice of the cycle's total candidates, so the full
# set cycles through over multiple nightly runs rather than one multi-hour pass.
FINANCIALS_BATCH_SIZE = 500

# Every senator belongs to exactly one class, so the union of the three
# class sets is precisely the 50 states — the only jurisdictions that hold
# federal Senate/House elections. FEC candidate files also include DC and
# territorial delegate filings (DC, PR, GU, VI, AS, MP); those are
# deliberately excluded from the roster: PR's Resident Commissioner isn't
# even elected in midterm years, and mixing non-voting delegate seats
# unlabeled into a "House races" directory misstates what's on the ballot.
STATES_WITH_FEDERAL_RACES = CLASS_I_STATES | CLASS_II_STATES | CLASS_III_STATES

# Coverage items older than this are pruned outright — the coverage feed is
# a live-coverage surface (race detail shows the latest 50), not an archive,
# and without pruning the table grows every 15 minutes all season (2026-07
# review: unbounded growth on a Pi's SQLite).
COVERAGE_RETENTION_DAYS = 90

_tracker = PipelineRunTracker()


def is_election_pipeline_running() -> bool:
    return _tracker.is_running


def election_pipeline_age():
    """Wall-clock age of the in-process election pipeline run, or None when idle."""
    return _tracker.age


def _race_id(cycle: int, office: str, state: str, district: int | None, is_special: bool = False) -> str:
    if office == "S":
        base = f"{cycle}-SEN-{state}"
        return f"{base}-SPECIAL" if is_special else base
    return f"{cycle}-HOUSE-{state}-{district if district is not None else 0}"


def _on_ballot_in(raw: dict, cycle: int) -> bool:
    """True only if this FEC candidate record confirms a `cycle` election.

    fetch_all_candidates already queries by election_year, but each record
    is re-validated here so a wrong upstream match can't mint a race for a
    state with no election that year (2026-07 review F1: the original
    `cycle=` query returned every candidate whose committee merely FILED
    in the period — sitting senators up in 2028/2030, early 2028
    declarers, prior-cycle committees winding down — which fabricated
    phantom Senate races in ~15 states). Same cycle-vs-election-year
    distinction financials_election_year (fec.py) documents for totals.
    """
    years = raw.get("election_years") or []
    return raw.get("candidate_election_year") == cycle or cycle in years


def _sync_roster(db: Session, cycle: int, candidates_raw: list[dict]) -> int:
    """Upsert Race + Candidate rows from raw FEC candidate records.

    Validation per record: must confirm an election in `cycle`
    (_on_ballot_in), and must be in one of the 50 states
    (STATES_WITH_FEDERAL_RACES — DC/territorial delegate filings excluded,
    see that constant's comment).

    Special elections: a Senate candidate on the `cycle` ballot in a state
    whose class seat is NOT up that year (election_calendar's rotation) can
    only be running in a special election, so the race is keyed
    "{cycle}-SEN-{ST}-SPECIAL" with is_special=True — e.g. 2026's FL and OH
    specials (both Class 3 seats vacated mid-term) get their own races
    instead of being conflated with a regular seat that doesn't exist. The
    one shape this can't distinguish is a special held in a state whose
    OTHER seat is also up regularly that year (GA 2020): FEC candidate
    records carry no per-seat field to split on, so both would key to the
    regular race — a genuine gap in the source data, not an unread field
    (none of 2026's known specials are in Class II states).

    Commits per candidate: a record that fails at flush time poisons the
    whole SQLAlchemy session for every later record if the failure
    surfaces mid-batch (the pre-fix per-record try/except caught the
    exception but left the session in PendingRollbackError, and the final
    batch commit then rolled back everything — the exact one-bad-row-
    blanks-the-table failure president_pipeline._sync_roster was fixed
    for). Per-record commit + rollback-on-error gives real isolation, same
    as _refresh_financials below.
    """
    synced = 0
    skipped_off_ballot = 0
    skipped_non_state = 0
    regular_senate_states = seats_up_for_year(cycle)
    for raw in candidates_raw:
        try:
            candidate_id = raw.get("candidate_id")
            state = raw.get("state")
            office = raw.get("office")
            if not candidate_id or not state or office not in ("H", "S"):
                continue
            if state not in STATES_WITH_FEDERAL_RACES:
                skipped_non_state += 1
                continue
            if not _on_ballot_in(raw, cycle):
                skipped_off_ballot += 1
                continue
            district = raw.get("district_number") if office == "H" else None
            is_special = office == "S" and state not in regular_senate_states
            race_id = _race_id(cycle, office, state, district, is_special)

            race = db.query(Race).filter(Race.id == race_id).first()
            if race is None:
                race = Race(
                    id=race_id, cycle_year=cycle, office=office,
                    state=state, district=district, is_special=is_special,
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
            cand.candidate_status = raw.get("candidate_status")
            db.commit()
            synced += 1
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to sync candidate %s — skipping", raw.get("candidate_id"),
            )
    if skipped_off_ballot or skipped_non_state:
        logger.info(
            "Roster sync skipped %d records without a confirmed %d election "
            "and %d non-state (DC/territory) filings",
            skipped_off_ballot, cycle, skipped_non_state,
        )
    return synced


def _prioritize_for_financial_refresh(db: Session, limit: int) -> list[Candidate]:
    """Never-synced candidates first, then oldest-synced first; within each
    group, incumbents before active fundraisers before everyone else.

    Candidates synced within the FEC cache TTL are excluded entirely
    (2026-07 review M3): fetch_candidate_financials serves from ApiCache
    inside that window, so re-selecting a fresh candidate consumes a batch
    slot to read back identical numbers. Without this floor, all ~470
    incumbents (priority 0) re-occupied the head of every nightly batch
    doing exactly that, leaving ~30 real slots for thousands of
    challengers — a ~2-month rotation. With it, freshly-synced candidates
    drop out of the pool and the batch is spent entirely on stale ones.
    """
    priority = case(
        (Candidate.incumbent_challenge == "I", 0),
        (Candidate.has_raised_funds.is_(True), 1),
        else_=2,
    )
    stale_before = utcnow() - timedelta(hours=settings.PIPELINE_CACHE_TTL_HOURS)
    return (
        db.query(Candidate)
        .filter(or_(
            Candidate.last_financials_sync.is_(None),
            Candidate.last_financials_sync < stale_before,
        ))
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
    """Fundraising snapshot per candidate, via the same shared
    ScoreSnapshot table senators/reps/presidents already use for trend
    charts. overall_score holds cash_on_hand — a fundraising figure, not
    an evaluative score; ScoreSnapshot's shape is reused as-is rather than
    adding a parallel table for one more "value over time per entity" case.

    Changed-only, not daily-unconditional (2026-07 review): thousands of
    candidates snapshotted every night regardless of change would add ~10x
    the platform's existing daily snapshot volume (millions of rows/year
    on the Pi's SQLite), while FEC totals for most candidates move only
    when a quarterly filing lands. A snapshot is written only when the
    candidate's figures differ from their latest existing snapshot —
    identical information for a trend chart (flat segments carry no data a
    start/end pair doesn't), at a small fraction of the rows.
    """
    today = utcnow().strftime("%Y-%m-%d")
    db.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == "candidate",
        ScoreSnapshot.date == today,
    ).delete()

    latest_date = (
        db.query(
            ScoreSnapshot.entity_id,
            func.max(ScoreSnapshot.date).label("latest"),
        )
        .filter(ScoreSnapshot.entity_type == "candidate")
        .group_by(ScoreSnapshot.entity_id)
        .subquery()
    )
    latest_rows = {
        row.entity_id: row
        for row in (
            db.query(ScoreSnapshot)
            .join(
                latest_date,
                (ScoreSnapshot.entity_id == latest_date.c.entity_id)
                & (ScoreSnapshot.date == latest_date.c.latest),
            )
            .filter(ScoreSnapshot.entity_type == "candidate")
            .all()
        )
    }

    written = 0
    candidates = db.query(Candidate).filter(Candidate.cash_on_hand.isnot(None)).all()
    for cand in candidates:
        prev = latest_rows.get(cand.id)
        values = (
            cand.cash_on_hand or 0.0,
            cand.contributions or 0.0,
            cand.disbursements or 0.0,
        )
        if prev is not None and (prev.overall_score, prev.score_1, prev.score_2) == values:
            continue
        db.add(ScoreSnapshot(
            entity_type="candidate",
            entity_id=cand.id,
            date=today,
            overall_score=values[0],
            score_1=values[1],
            score_2=values[2],
        ))
        written += 1
    db.commit()
    return written


def _prune_stale_coverage(db: Session) -> int:
    """Delete coverage items older than COVERAGE_RETENTION_DAYS — see that
    constant's comment. Returns rows deleted."""
    cutoff = utcnow() - timedelta(days=COVERAGE_RETENTION_DAYS)
    deleted = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.fetched_at < cutoff)
        .delete()
    )
    if deleted:
        db.commit()
        logger.info("Pruned %d coverage items older than %d days", deleted, COVERAGE_RETENTION_DAYS)
    return deleted


async def run_election_pipeline(cycle: int | None = None) -> dict:
    """Sync candidate rosters, refresh a prioritized batch of financials,
    ingest race coverage, post grounded Bluesky updates, and snapshot
    fundraising. Returns a summary dict with counts."""
    cycle = cycle if cycle is not None else current_election_cycle()
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

        async with make_async_client() as client:
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

            run.current_phase = "confirmed_candidates"
            db.commit()
            logger.info("--- Election: CONFIRMED CANDIDATES ---")
            progress.begin("confirmed_candidates")
            try:
                from app.pipeline.fetch.state_candidates import (
                    crawl_for_new_sources,
                    sync_confirmed_candidates,
                    sync_primary_ballots,
                )

                # Weekly, not nightly: this sweeps every state that has no
                # hand-verified source, and what it looks for — a state
                # standing up a results portal, a new cycle's file
                # appearing — moves on the scale of weeks, not hours. Same
                # self-gating shape as ops_alerts' weekly checks. Runs
                # BEFORE the sync so anything it proves out contributes the
                # same night.
                if utcnow().weekday() == 6:
                    leads = await crawl_for_new_sources(db, client, cycle)
                    adopted = {s: r for s, r in leads.items() if r.startswith("adopted")}
                    logger.info(
                        "Source crawl: %d state(s) adopted%s",
                        len(adopted), f" — {adopted}" if adopted else "",
                    )

                confirm_result = await sync_confirmed_candidates(db, client, cycle)
                confirmed_total = sum(r["confirmed"] for r in confirm_result.values())
                logger.info("Confirmed candidates: %s", confirm_result)

                # Who is on each state's PRIMARY ballot — the answer for
                # the months before any primary, when the alternative is
                # showing every FEC filer. Never overrides a confirmed
                # nominee; see _confirmed_or_all.
                primary_result = await sync_primary_ballots(db, client, cycle)
                if primary_result:
                    logger.info("Primary ballots: %s", primary_result)
                progress.complete(
                    "confirmed_candidates", detail=f"{confirmed_total} confirmed",
                )
            except Exception:
                db.rollback()
                logger.exception("Confirmed-candidate sync failed — continuing")
                progress.fail("confirmed_candidates")

            # Coverage + posting share an in-process tracker with the
            # 15-minute election-season refresh (scheduler.py) so the two
            # entry points can't interleave — concurrent passes would
            # double-ingest and double-post (2026-07 review B3). If a
            # refresh is mid-flight right now, skip these two phases; the
            # in-season cadence re-covers them within 15 minutes.
            from app.pipeline.analyze.election_coverage import (
                coverage_tracker,
                ingest_race_coverage,
                is_coverage_refresh_running,
            )

            if is_coverage_refresh_running():
                logger.info(
                    "Election coverage/posting phases skipped — a coverage "
                    "refresh is already running",
                )
                progress.complete("coverage_ingestion", detail="skipped (refresh running)")
                progress.complete("bluesky_posting", detail="skipped (refresh running)")
            else:
                coverage_tracker().start()
                try:
                    run.current_phase = "coverage"
                    db.commit()
                    logger.info("--- Election: COVERAGE INGESTION ---")
                    progress.begin("coverage_ingestion")
                    try:
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
                finally:
                    coverage_tracker().stop()

            run.current_phase = "snapshot"
            db.commit()
            logger.info("--- Election: SNAPSHOT ---")
            progress.begin("snapshot")
            try:
                snapshotted = _snapshot_candidates(db)
                _prune_stale_coverage(db)
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
