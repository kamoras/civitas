"""
Unified "bills currently moving through Congress" feed.

Unions SponsoredBill (Senate) and RepSponsoredBill (House) across all
current members into a single normalized, filterable, paginated list —
the cross-chamber counterpart to how politicians.py unions Senator/
Representative/President/Justice into one directory.
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Representative, RepSponsoredBill, Senator, SponsoredBill
from app.schemas import BillInFlightSchema, PaginatedBillsSchema


def _bioguide_photo(bioguide_id: str | None) -> str | None:
    if not bioguide_id:
        return None
    return f"https://bioguide.congress.gov/bioguide/photo/{bioguide_id[0]}/{bioguide_id}.jpg"


@dataclass
class _Row:
    bill: BillInFlightSchema
    latest_action_date: str


def _collect_bills(db: Session) -> list[_Row]:
    rows: list[_Row] = []
    min_congress = settings.CURRENT_CONGRESS

    senate_query = (
        db.query(SponsoredBill, Senator)
        .join(Senator, SponsoredBill.senator_id == Senator.id)
        .filter(SponsoredBill.congress >= min_congress)
        .filter(Senator.is_current == True)  # noqa: E712
    )
    for sp, senator in senate_query.all():
        rows.append(_Row(
            bill=BillInFlightSchema(
                bill_id=sp.bill_id,
                title=sp.title,
                chamber="senate",
                sponsor_id=senator.id,
                sponsor_name=senator.name,
                sponsor_party=senator.party,
                sponsor_state=senator.state,
                sponsor_thumbnail_url=_bioguide_photo(senator.bioguide_id),
                introduced_date=sp.introduced_date,
                latest_action=sp.latest_action,
                latest_action_date=sp.latest_action_date,
                stage=sp.stage or "INTRODUCED",
                policy_area=sp.policy_area,
                congress=sp.congress,
                bill_type=sp.bill_type,
                is_law=sp.is_law,
            ),
            latest_action_date=sp.latest_action_date,
        ))

    house_query = (
        db.query(RepSponsoredBill, Representative)
        .join(Representative, RepSponsoredBill.representative_id == Representative.id)
        .filter(RepSponsoredBill.congress >= min_congress)
        .filter(Representative.is_current == True)  # noqa: E712
    )
    for sp, rep in house_query.all():
        rows.append(_Row(
            bill=BillInFlightSchema(
                bill_id=sp.bill_id,
                title=sp.title,
                chamber="house",
                sponsor_id=rep.id,
                sponsor_name=rep.name,
                sponsor_party=rep.party,
                sponsor_state=rep.state,
                sponsor_thumbnail_url=_bioguide_photo(rep.bioguide_id),
                introduced_date=sp.introduced_date,
                latest_action=sp.latest_action,
                latest_action_date=sp.latest_action_date,
                stage=sp.stage or "INTRODUCED",
                policy_area=sp.policy_area,
                congress=sp.congress,
                bill_type=sp.bill_type,
                is_law=sp.is_law,
            ),
            latest_action_date=sp.latest_action_date,
        ))

    return rows


def get_bills_in_flight(
    db: Session,
    stage: str | None = None,
    chamber: str | None = None,
    party: str | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> PaginatedBillsSchema:
    all_rows = _collect_bills(db)

    stage_counts: dict[str, int] = {}
    for row in all_rows:
        stage_counts[row.bill.stage] = stage_counts.get(row.bill.stage, 0) + 1

    filtered = all_rows
    if stage:
        filtered = [r for r in filtered if r.bill.stage == stage]
    if chamber:
        filtered = [r for r in filtered if r.bill.chamber == chamber]
    if party:
        filtered = [r for r in filtered if r.bill.sponsor_party == party]
    if q:
        q_lower = q.lower()
        filtered = [r for r in filtered if q_lower in r.bill.title.lower()]

    filtered.sort(key=lambda r: r.latest_action_date, reverse=True)

    total = len(filtered)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    page_rows = filtered[start:start + per_page]

    return PaginatedBillsSchema(
        bills=[r.bill for r in page_rows],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stage_counts=stage_counts,
    )
