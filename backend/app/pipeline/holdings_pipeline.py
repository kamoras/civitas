"""Ingest each member's latest annual financial disclosure (asset holdings).

Runs inside the stock-trades pipeline as two more best-effort phases (see
stock_pipeline.run_stock_trades_pipeline): the sources are the same two
disclosure systems the trade ingest reads, and so is the filer matching.

For every sitting member it keeps exactly one report — the newest one —
and replaces the member's previous report when a newer one is found. A
report covers holdings at one calendar year end, so an older report is
superseded, not accumulated. Nothing is derived from the value brackets
here; the read path (holdings_service) reports them as disclosed.
"""

import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.models import FinancialDisclosure, FinancialHolding
from app.pipeline.fetch.fd_common import HoldingRow
from app.pipeline.fetch.house_fd import fetch_and_parse_annual as fetch_house_annual, fetch_annual_filing_index
from app.pipeline.fetch.senate_fd import (
    fetch_and_parse_annual as fetch_senate_annual,
    is_annual_title,
    is_senator_filing,
    search_annual_filings,
)
from app.pipeline.fetch.senate_ptr import accept_terms as senate_accept_terms
from app.pipeline.filer_matching import match_representative, match_senator
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Annual reports cover the previous calendar year and are due in May, with
# extensions into August — so a member's newest report is for last year, or
# (before they file) the year before.
_YEARS_BACK = 2


def _replace_disclosure(
    db: Session, *, owner_filter: dict, filing_id: str, report_year: int | None,
    filed_date: str | None, source_url: str, holdings: list[HoldingRow] | None,
) -> int:
    """Swap a member's stored report for this one. Returns holdings stored."""
    for old in db.query(FinancialDisclosure).filter_by(**owner_filter).all():
        db.delete(old)
    disclosure = FinancialDisclosure(
        **owner_filter,
        filing_id=filing_id,
        report_year=report_year,
        filed_date=filed_date,
        source_url=source_url,
        parsed=holdings is not None,
    )
    for row in holdings or []:
        disclosure.holdings.append(FinancialHolding(
            asset_name=row.asset_name,
            account=row.account,
            ticker=row.ticker,
            asset_type=row.asset_type,
            category=row.category,
            owner=row.owner,
            value_text=row.value_text,
            value_low=row.value_low,
            value_high=row.value_high,
        ))
    db.add(disclosure)
    return len(holdings or [])


def _stored_filing_ids(db: Session, column) -> dict[str, str]:
    return {
        owner_id: filing_id
        for owner_id, filing_id in db.query(column, FinancialDisclosure.filing_id).filter(column.isnot(None)).all()
    }


async def ingest_house_holdings(db: Session, client: httpx.AsyncClient) -> int:
    """Store each representative's newest annual report. Returns holdings stored."""
    current_year = utcnow().year
    # Candidate filings per representative, most preferred first: the
    # newest calendar year, then the latest filed within it (an amendment
    # supersedes the original it amends).
    per_rep: dict[str, list[dict]] = {}
    for year in range(current_year - 1, current_year - 1 - _YEARS_BACK, -1):
        filings = await fetch_annual_filing_index(client, db, year)
        filings = sorted(filings, key=lambda f: f.get("filing_date") or "", reverse=True)
        for filing in filings:
            rep = match_representative(db, filing["last"], filing["first"], filing["state_district"])
            if rep is None:
                continue
            per_rep.setdefault(rep.id, []).append(filing)

    stored = _stored_filing_ids(db, FinancialDisclosure.representative_id)
    inserted = 0
    for rep_id, filings in per_rep.items():
        for filing in filings:
            if stored.get(rep_id) == filing["doc_id"]:
                break  # already have the newest report
            report = await fetch_house_annual(client, db, filing)
            if report is None:
                # Couldn't fetch it this run: keep whatever is stored rather
                # than falling back to an older report.
                break
            status = (report.filer_status or "").lower()
            if status and status != "member":
                # A candidate for the seat who shares the member's surname
                # and district — not this member's report.
                continue
            inserted += _replace_disclosure(
                db,
                owner_filter={"representative_id": rep_id},
                filing_id=filing["doc_id"],
                report_year=filing.get("year"),
                filed_date=filing.get("filing_date"),
                source_url=filing["pdf_url"],
                holdings=report.holdings,
            )
            break
    db.commit()
    return inserted


_CY_RE = re.compile(r"\bCY\s*(\d{4})\b", re.I)
_DATE_YEAR_RE = re.compile(r"\b\d{2}/\d{2}/(\d{4})\b")


def _senate_report_year(filing: dict) -> int | None:
    """The calendar year a Senate report's holdings describe.

    "Annual Report for CY 2025" states it; a "New Filer Report for
    03/24/2026" describes that date; a paper filing's link reads just
    "Annual Report", so it falls back to the year before it was filed.
    """
    title = filing.get("title") or ""
    if m := _CY_RE.search(title):
        return int(m.group(1))
    if m := _DATE_YEAR_RE.search(title):
        return int(m.group(1))
    filed = filing.get("filed_date") or ""
    return int(filed[:4]) - 1 if filed[:4].isdigit() else None


async def ingest_senate_holdings(db: Session, client: httpx.AsyncClient) -> int:
    """Store each senator's newest annual report. Returns holdings stored."""
    if await senate_accept_terms(client) is None:
        logger.error("Could not establish a Senate eFD session — skipping Senate holdings this run")
        return 0

    current_year = utcnow().year
    filings = await search_annual_filings(f"{current_year - _YEARS_BACK}-01-01")
    per_senator: dict[str, list[dict]] = {}
    for filing in filings:
        if not is_senator_filing(filing) or not is_annual_title(filing.get("title") or ""):
            continue
        senator = match_senator(db, filing["last"], filing["first"])
        if senator is None:
            continue
        per_senator.setdefault(senator.id, []).append(filing)

    stored = _stored_filing_ids(db, FinancialDisclosure.senator_id)
    inserted = 0
    for senator_id, candidates in per_senator.items():
        candidates.sort(key=lambda f: (_senate_report_year(f) or 0, f.get("filed_date") or ""), reverse=True)
        filing = candidates[0]
        filing_id = filing["report_url"].rstrip("/").rsplit("/", 1)[-1]
        if stored.get(senator_id) == filing_id:
            continue
        holdings = await fetch_senate_annual(client, db, filing)
        if holdings is None and not filing.get("is_paper"):
            # An electronic report that failed to load this run: keep what's
            # stored. A paper report is recorded as unparsed and linked.
            continue
        inserted += _replace_disclosure(
            db,
            owner_filter={"senator_id": senator_id},
            filing_id=filing_id,
            report_year=_senate_report_year(filing),
            filed_date=filing.get("filed_date"),
            source_url=filing["report_url"],
            holdings=holdings,
        )
    db.commit()
    return inserted
