"""Ingest STOCK Act periodic transaction reports for senators and reps.

Runs as a sibling phase after the member pipelines (see scheduler.py's
_nightly_pipeline) rather than inside senate_pipeline.py/house_pipeline.py —
those functions are already large single units and this ingestion is
independent of member scoring. See issue #45 for the source-selection
rationale and the plan this was implemented from.

FETCH -> match filer to a known senator/rep by name -> resolve ticker to a
company name (sec_tickers) -> classify industry (reusing the existing
donor-industry embedding classifier, unmodified) -> compute disclosure
timeliness -> upsert.
"""

import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    PipelineRun, HousePipelineRun, PipelineStatus, Representative, Senator,
    StockTrade, RepStockTrade, StockTradesPipelineRun,
)
from app.pipeline.fetch.house_ptr import fetch_and_parse_ptr as fetch_house_ptr, fetch_ptr_filing_index
from app.pipeline.fetch.ptr_common import TradeRow
from app.pipeline.fetch.sec_tickers import resolve_tickers
from app.pipeline.fetch.senate_ptr import (
    accept_terms as senate_accept_terms,
    fetch_and_parse_ptr as fetch_senate_ptr,
    search_ptr_filings,
)
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock
from app.pipeline.transform.industry_classifier import classify_batch_with_learning
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

STOCK_PIPELINE_STEPS = [
    ("house_ptr",  "fetch", "Ingest House PTR filings"),
    ("senate_ptr", "fetch", "Ingest Senate PTR filings"),
]

# How far back to search on a cold start (no existing trades in the DB).
# Once trades exist, each chamber's search/index window starts from the
# most recent disclosure_date already stored, so this only matters once.
COLD_START_LOOKBACK_DAYS = 120

# In-memory tracker mirroring house_pipeline.py's pattern — lets the admin
# dashboard detect a "stuck" run (DB row still says "running" but this
# tracker says not-running after a restart) rather than only the DB row,
# which a crashed/killed process can never update to "failed" itself.
_tracker = PipelineRunTracker()


def is_stock_pipeline_running() -> bool:
    return _tracker.is_running


def stock_pipeline_age() -> "timedelta | None":
    """Wall-clock age of the in-process stock-trades run, or None when idle."""
    return _tracker.age


def _other_pipeline_running(db: Session) -> bool:
    """Best-effort guard against overlapping with a member pipeline run.

    Reuses the existing PipelineRun/HousePipelineRun "running" status
    rather than introducing a third lock table — see scheduler.py's
    _hourly_action_refresh for the same pattern.

    Staleness-aware since 2026-07-23: an orphaned "running" row (left by
    a killed process — a deploy restarting the container mid-run) used
    to block Stock forever, with no auto-clear anywhere in this check.
    Confirmed live: this is what left stock-trades data stale for 4+
    days after a since-fixed deploy-race incident. A row this old is
    treated as dead, not as "still running" — same STALE_PIPELINE_TIMEOUT
    bar acquire_pipeline_lock uses to actually clear these rows, so this
    check and the thing that eventually cleans them up agree on what
    "stuck" means.
    """
    for model in (PipelineRun, HousePipelineRun):
        running = db.query(model).filter(model.status == PipelineStatus.RUNNING).first()
        if running and utcnow() - running.started_at <= STALE_PIPELINE_TIMEOUT:
            return True
    return False


def _match_senator(db: Session, last: str, first: str) -> Senator | None:
    if not last:
        return None
    candidates = (
        db.query(Senator)
        .filter(Senator.is_current == True, Senator.name.ilike(f"%{last}%"))  # noqa: E712
        .all()
    )
    if len(candidates) == 1:
        return candidates[0]
    if first:
        for c in candidates:
            if first.lower() in c.name.lower():
                return c
    # Ambiguous (multiple same-last-name matches, none disambiguated by
    # first name) — skip rather than guess which one filed the PTR.
    return None


def _match_representative(db: Session, last: str, first: str, state_district: str) -> Representative | None:
    if not last:
        return None
    state = state_district[:2] if state_district else None
    # The House FD index supplies the FULL district ("CA27"), and
    # Representative.district exists — so filter on it. Previously only the
    # state was used, leaving same-state same-surname pairs to a fragile
    # first-name substring match that silently skipped the filing every run
    # whenever the formal filing name differed from the display name
    # ("Michael" vs "Mike"). District makes the match exact for all 435
    # voting seats.
    district: int | None = None
    if state_district and len(state_district) > 2 and state_district[2:].isdigit():
        district = int(state_district[2:])

    query = db.query(Representative).filter(
        Representative.is_current == True, Representative.name.ilike(f"%{last}%")  # noqa: E712
    )
    if state:
        query = query.filter(Representative.state == state)
    if district is not None:
        query = query.filter(Representative.district == district)
    candidates = query.all()
    if len(candidates) == 1:
        return candidates[0]
    if first:
        for c in candidates:
            if first.lower() in c.name.lower():
                return c
    # Ambiguous (multiple same-last-name matches, none disambiguated by
    # first name) — skip rather than guess which one filed the PTR.
    return None


def _compute_days_to_disclose(transaction_date: str, disclosure_date: str) -> int:
    try:
        t = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        d = datetime.strptime(disclosure_date, "%Y-%m-%d").date()
        return (d - t).days
    except ValueError:
        return 0


async def _classify_rows_industry(db: Session, client: httpx.AsyncClient, rows: list[TradeRow]) -> None:
    """Mutate rows in place, setting `industry` from ticker -> company -> embedding."""
    tickers = [r.ticker for r in rows if r.ticker]
    if not tickers:
        return
    ticker_to_company = await resolve_tickers(client, db, tickers)
    company_names = list(set(ticker_to_company.values()))
    if not company_names:
        return
    industries, _unknowns = classify_batch_with_learning(company_names, db)
    for row in rows:
        if not row.ticker:
            continue
        company = ticker_to_company.get(row.ticker.upper())
        if company and company in industries:
            row.industry = industries[company]


async def _ingest_house(db: Session, client: httpx.AsyncClient) -> int:
    existing_rep_filing_ids = {row[0] for row in db.query(RepStockTrade.filing_id).all()}

    current_year = utcnow().year
    inserted = 0
    for year in (current_year - 1, current_year):
        filings = await fetch_ptr_filing_index(client, db, year)
        for filing in filings:
            if filing["doc_id"] in existing_rep_filing_ids:
                continue
            rep = _match_representative(db, filing["last"], filing["first"], filing["state_district"])
            if rep is None:
                continue
            rows = await fetch_house_ptr(client, db, filing)
            if not rows:
                continue
            await _classify_rows_industry(db, client, rows)
            for row in rows:
                days = _compute_days_to_disclose(row.transaction_date, row.disclosure_date)
                db.add(RepStockTrade(
                    representative_id=rep.id,
                    ticker=row.ticker,
                    asset_name=row.asset_name,
                    owner=row.owner,
                    transaction_type=row.transaction_type,
                    transaction_date=row.transaction_date,
                    disclosure_date=row.disclosure_date,
                    days_to_disclose=days,
                    amount_low=row.amount_low,
                    amount_high=row.amount_high,
                    industry=row.industry or "UNCLASSIFIED",
                    source_url=row.source_url,
                    filing_id=row.filing_id,
                    parse_confidence=row.parse_confidence,
                ))
                inserted += 1
            existing_rep_filing_ids.add(filing["doc_id"])
    db.commit()
    return inserted


async def _ingest_senate(db: Session, client: httpx.AsyncClient) -> int:
    existing_filing_ids = {row[0] for row in db.query(StockTrade.filing_id).all()}

    latest = db.query(StockTrade.disclosure_date).order_by(StockTrade.disclosure_date.desc()).first()
    if latest and latest[0]:
        since_date = latest[0]
    else:
        since_date = (utcnow().date() - timedelta(days=COLD_START_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    csrf_token = await senate_accept_terms(client)
    if csrf_token is None:
        logger.error("Could not establish a Senate eFD session — skipping Senate PTR ingestion this run")
        return 0

    filings = await search_ptr_filings(client, db, since_date, csrf_token)
    inserted = 0
    for filing in filings:
        filing_id = filing["report_url"].rstrip("/").rsplit("/", 1)[-1]
        if filing_id in existing_filing_ids:
            continue
        senator = _match_senator(db, filing["last"], filing["first"])
        if senator is None:
            continue
        rows = await fetch_senate_ptr(client, db, filing)
        if not rows:
            continue
        await _classify_rows_industry(db, client, rows)
        for row in rows:
            days = _compute_days_to_disclose(row.transaction_date, row.disclosure_date)
            db.add(StockTrade(
                senator_id=senator.id,
                ticker=row.ticker,
                asset_name=row.asset_name,
                owner=row.owner,
                transaction_type=row.transaction_type,
                transaction_date=row.transaction_date,
                disclosure_date=row.disclosure_date,
                days_to_disclose=days,
                amount_low=row.amount_low,
                amount_high=row.amount_high,
                industry=row.industry or "UNCLASSIFIED",
                source_url=row.source_url,
                filing_id=row.filing_id,
                parse_confidence=row.parse_confidence,
            ))
            inserted += 1
        existing_filing_ids.add(filing_id)
    db.commit()
    return inserted


async def run_stock_trades_pipeline() -> dict:
    """Fetch, parse, classify, and store new House + Senate PTR filings.

    Best-effort per chamber: a failure fetching/parsing one chamber's
    filings does not prevent the other from being ingested.
    """
    db: Session = SessionLocal()
    try:
        if _other_pipeline_running(db):
            logger.info("Stock trades pipeline skipped — a member pipeline is currently running")
            return {"status": "skipped", "reason": "member_pipeline_running"}

        # Same reasoning as senate_pipeline.py's own lock: until 2026-07-23
        # this was an unconditional insert with no lock at all, so a row
        # orphaned by a killed process stayed "running" forever, blocking
        # every future Stock run via _other_pipeline_running's check above
        # (which any OTHER pipeline's own stuck row would also trip) and
        # this one (a stuck STOCK row blocking Stock's own next attempt).
        run = acquire_pipeline_lock(db, StockTradesPipelineRun, STALE_PIPELINE_TIMEOUT)
        if run is None:
            logger.info("Stock trades pipeline already running in another process — skipping")
            return {"status": "skipped", "reason": "already_running"}

        _tracker.start()
        start_time = time.time()
        progress = ProgressTracker(run, STOCK_PIPELINE_STEPS, db, start_time)

        house_count = 0
        senate_count = 0
        error_parts: list[str] = []
        async with httpx.AsyncClient() as client:
            progress.begin("house_ptr")
            try:
                house_count = await _ingest_house(db, client)
                progress.complete("house_ptr", detail=f"{house_count} rows")
            except Exception:
                logger.exception("House PTR ingestion failed")
                # Roll back the failed chamber's partial transaction so the
                # session is clean for the Senate phase — without this, a
                # House flush error leaves the session in a failed state and
                # the Senate phase's first query raises PendingRollbackError,
                # so the "best-effort per chamber" design failed BOTH.
                db.rollback()
                error_parts.append("House: failed — see server logs")
                progress.fail("house_ptr")
            progress.begin("senate_ptr")
            try:
                senate_count = await _ingest_senate(db, client)
                progress.complete("senate_ptr", detail=f"{senate_count} rows")
            except Exception:
                logger.exception("Senate PTR ingestion failed")
                db.rollback()
                error_parts.append("Senate: failed — see server logs")
                progress.fail("senate_ptr")

        elapsed = round(time.time() - start_time, 1)
        logger.info("Stock trades pipeline: %d House rows, %d Senate rows", house_count, senate_count)

        run.status = PipelineStatus.FAILED if len(error_parts) == 2 else PipelineStatus.COMPLETED
        run.completed_at = utcnow()
        run.house_trades_ingested = house_count
        run.senate_trades_ingested = senate_count
        run.elapsed_seconds = elapsed
        run.error_message = "; ".join(error_parts) or None
        db.commit()

        return {
            "status": run.status, "house_trades": house_count, "senate_trades": senate_count,
            "elapsed_seconds": elapsed,
        }
    finally:
        _tracker.stop()
        db.close()
