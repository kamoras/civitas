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
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import PipelineRun, HousePipelineRun, Representative, Senator, StockTrade, RepStockTrade
from app.pipeline.fetch.house_ptr import fetch_and_parse_ptr as fetch_house_ptr, fetch_ptr_filing_index
from app.pipeline.fetch.sec_tickers import resolve_tickers
from app.pipeline.fetch.senate_ptr import (
    accept_terms as senate_accept_terms,
    fetch_and_parse_ptr as fetch_senate_ptr,
    search_ptr_filings,
)
from app.pipeline.transform.industry_classifier import classify_batch_with_learning

logger = logging.getLogger(__name__)

# How far back to search on a cold start (no existing trades in the DB).
# Once trades exist, each chamber's search/index window starts from the
# most recent disclosure_date already stored, so this only matters once.
COLD_START_LOOKBACK_DAYS = 120


def _other_pipeline_running(db: Session) -> bool:
    """Best-effort guard against overlapping with a member pipeline run.

    Reuses the existing PipelineRun/HousePipelineRun "running" status
    rather than introducing a third lock table — see scheduler.py's
    _hourly_action_refresh for the same pattern.
    """
    if db.query(PipelineRun).filter(PipelineRun.status == "running").first():
        return True
    if db.query(HousePipelineRun).filter(HousePipelineRun.status == "running").first():
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
    return candidates[0] if len(candidates) == 1 else None


def _match_representative(db: Session, last: str, first: str, state_district: str) -> Representative | None:
    if not last:
        return None
    state = state_district[:2] if state_district else None
    query = db.query(Representative).filter(
        Representative.is_current == True, Representative.name.ilike(f"%{last}%")  # noqa: E712
    )
    if state:
        query = query.filter(Representative.state == state)
    candidates = query.all()
    if len(candidates) == 1:
        return candidates[0]
    if first:
        for c in candidates:
            if first.lower() in c.name.lower():
                return c
    return candidates[0] if len(candidates) == 1 else None


def _compute_days_to_disclose(transaction_date: str, disclosure_date: str) -> int:
    try:
        t = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        d = datetime.strptime(disclosure_date, "%Y-%m-%d").date()
        return (d - t).days
    except ValueError:
        return 0


async def _classify_rows_industry(db: Session, client: httpx.AsyncClient, rows: list[dict]) -> None:
    """Mutate rows in place, setting `industry` from ticker -> company -> embedding."""
    tickers = [r["ticker"] for r in rows if r.get("ticker")]
    if not tickers:
        return
    ticker_to_company = await resolve_tickers(client, db, tickers)
    company_names = list(set(ticker_to_company.values()))
    if not company_names:
        return
    industries, _unknowns = classify_batch_with_learning(company_names, db)
    for row in rows:
        ticker = row.get("ticker")
        if not ticker:
            continue
        company = ticker_to_company.get(ticker.upper())
        if company and company in industries:
            row["industry"] = industries[company]


async def _ingest_house(db: Session, client: httpx.AsyncClient) -> int:
    existing_rep_filing_ids = {row[0] for row in db.query(RepStockTrade.filing_id).all()}

    current_year = date.today().year
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
                days = _compute_days_to_disclose(row["transaction_date"], row["disclosure_date"])
                db.add(RepStockTrade(
                    representative_id=rep.id,
                    ticker=row.get("ticker"),
                    asset_name=row["asset_name"],
                    owner=row["owner"],
                    transaction_type=row["transaction_type"],
                    transaction_date=row["transaction_date"],
                    disclosure_date=row["disclosure_date"],
                    days_to_disclose=days,
                    amount_low=row["amount_low"],
                    amount_high=row["amount_high"],
                    industry=row.get("industry", "UNCLASSIFIED"),
                    source_url=row["source_url"],
                    filing_id=row["filing_id"],
                    parse_confidence=row["parse_confidence"],
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
        since_date = (date.today() - timedelta(days=COLD_START_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

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
            days = _compute_days_to_disclose(row["transaction_date"], row["disclosure_date"])
            db.add(StockTrade(
                senator_id=senator.id,
                ticker=row.get("ticker"),
                asset_name=row["asset_name"],
                owner=row["owner"],
                transaction_type=row["transaction_type"],
                transaction_date=row["transaction_date"],
                disclosure_date=row["disclosure_date"],
                days_to_disclose=days,
                amount_low=row["amount_low"],
                amount_high=row["amount_high"],
                industry=row.get("industry", "UNCLASSIFIED"),
                source_url=row["source_url"],
                filing_id=row["filing_id"],
                parse_confidence=row["parse_confidence"],
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

        house_count = 0
        senate_count = 0
        async with httpx.AsyncClient() as client:
            try:
                house_count = await _ingest_house(db, client)
            except Exception:
                logger.exception("House PTR ingestion failed")
            try:
                senate_count = await _ingest_senate(db, client)
            except Exception:
                logger.exception("Senate PTR ingestion failed")

        logger.info("Stock trades pipeline: %d House rows, %d Senate rows", house_count, senate_count)
        return {"status": "completed", "house_trades": house_count, "senate_trades": senate_count}
    finally:
        db.close()
