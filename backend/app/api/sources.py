"""The record's sources, with what each has supplied and how recent it is,
for the homepage's "Built from the public record" panel.

Each row is counted from the stored records themselves, never typed: how
many records the source has supplied in the window the site scores (the
sitting Congress for votes and bills), and the date of the newest one. FEC
filings carry no single record date the site keeps, so that row gives the
day the site last read the FEC instead, and says so. A source with nothing
stored reads 0 and no date, rather than being dropped: an empty source is
something a researcher should see.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from app.api.response_helpers import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.config import settings
from app.database import get_db
from app.models import (
    ApiCache,
    ExploreDocument,
    JusticeVote,
    Representative,
    RepSponsoredBill,
    RollCall,
    Senator,
    SponsoredBill,
    StockTrade,
)

router = APIRouter()

# ExploreDocument.source values (fetch/explore_*): the Congressional Record
# and the Federal Register, each one row.
_CONGRESSIONAL_RECORD = "Congressional Record (GovInfo)"
_FEDERAL_REGISTER = "Federal Register"
_SUPREME_COURT = "Supreme Court (supremecourt.gov)"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10] or None


def _row(label: str, source: str, count: int, noun: str, latest, *, scope: str = "",
         date_label: str = "newest record") -> dict:
    return {
        "label": label,
        "source": source,
        "count": count,
        "coverage": f"{count:,} {noun}{scope}",
        "latest": _iso(latest),
        "dateLabel": date_label,
    }


def _explore(db: Session, source: str) -> tuple[int, str | None]:
    return db.query(func.count(ExploreDocument.id), func.max(ExploreDocument.date)).filter(
        ExploreDocument.source == source).one()


def source_ledger(db: Session) -> list[dict]:
    congress = settings.CURRENT_CONGRESS
    in_congress = f", {_ordinal(congress)} Congress"

    votes, last_vote = db.query(func.count(RollCall.id), func.max(RollCall.date)).filter(
        RollCall.congress == congress).one()

    last_bill = None
    counts = []
    for model in (SponsoredBill, RepSponsoredBill):
        n, latest = db.query(func.count(model.id), func.max(model.latest_action_date)).filter(
            model.congress == congress).one()
        counts.append(n)
        last_bill = max(filter(None, (last_bill, latest)), default=None)
    bills = sum(counts)

    funded = sum(
        db.query(func.count(model.id)).filter(model.is_current.is_(True), model.total_raised > 0).scalar()
        for model in (Senator, Representative)
    )
    fec_read = db.query(func.max(ApiCache.cached_at)).filter(ApiCache.tier == "fec").scalar()

    speeches, last_speech = _explore(db, _CONGRESSIONAL_RECORD)
    rules, last_rule = _explore(db, _FEDERAL_REGISTER)
    opinions, _ = _explore(db, _SUPREME_COURT)
    court_votes, last_decided = db.query(func.count(JusticeVote.id), func.max(JusticeVote.decided_date)).one()
    trades, last_trade = db.query(func.count(StockTrade.id), func.max(StockTrade.disclosure_date)).one()

    return [
        _row("Votes", "Senate.gov · Clerk.House.gov", votes, "roll calls", last_vote, scope=in_congress),
        _row("Bills", "Congress.gov · GovInfo", bills, "bills sponsored by members on file", last_bill,
             scope=in_congress),
        _row("Money", "FEC", funded, "serving members with campaign filings", fec_read,
             date_label="last read"),
        _row("Floor", "Congressional Record (GovInfo)", speeches, "floor speeches", last_speech),
        _row("Rules", "Federal Register", rules, "rules, notices and presidential documents", last_rule),
        _row("Court", "Supreme Court Database · Oyez · supremecourt.gov", court_votes,
             f"justices' votes and {opinions:,} opinions", last_decided),
        _row("Trades", "House Clerk · Senate eFD · OGE", trades, "disclosed stock trades", last_trade),
    ]


@router.get("/sources")
def sources(db: Session = Depends(get_db)) -> JSONResponse:
    return cached_json({"sources": source_ledger(db)}, max_age=CACHE_TTL_LIST_S)
