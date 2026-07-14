"""
Unified "bills currently moving through Congress" feed.

Unions SponsoredBill (Senate) and RepSponsoredBill (House) across all
current members into a single normalized, filterable, paginated list —
the cross-chamber counterpart to how politicians.py unions Senator/
Representative/President/Justice into one directory.
"""
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ActionIssue, Representative, RepSponsoredBill, Senator, SponsoredBill
from app.schemas import BillInFlightSchema, PaginatedBillsSchema


def _bioguide_photo(bioguide_id: str | None) -> str | None:
    if not bioguide_id:
        return None
    return f"https://bioguide.congress.gov/bioguide/photo/{bioguide_id[0]}/{bioguide_id}.jpg"


def _bill_mention_counts(db: Session) -> dict[str, int]:
    """Return {bill_id: mention_count} across current Action Center issues.

    Mirrors politicians._build_active_issue_map's JSON-field-scan approach —
    no LLM, just counting how many currently-live issues reference each
    bill (ActionIssue.related_bill_ids entries are {name, id, url} dicts
    with `id` already in our bill_id format, e.g. "HR.22", produced by
    action_center._resolve_bills).
    """
    issues = (
        db.query(ActionIssue.related_bill_ids)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    counts: dict[str, int] = {}
    for (related_bill_ids,) in issues:
        if not related_bill_ids:
            continue
        try:
            entries = json.loads(related_bill_ids)
        except Exception:
            continue
        for entry in entries:
            bill_id = entry.get("id")
            if bill_id:
                counts[bill_id] = counts.get(bill_id, 0) + 1
    return counts


@dataclass
class _Row:
    bill: BillInFlightSchema
    latest_action_date: str
    mention_count: int


def _collect_bills(db: Session) -> list[_Row]:
    rows: list[_Row] = []
    min_congress = settings.CURRENT_CONGRESS
    mention_counts = _bill_mention_counts(db)

    senate_query = (
        db.query(SponsoredBill, Senator)
        .join(Senator, SponsoredBill.senator_id == Senator.id)
        .filter(SponsoredBill.congress >= min_congress)
        .filter(Senator.is_current == True)  # noqa: E712
    )
    for sp, senator in senate_query.all():
        mention_count = mention_counts.get(sp.bill_id, 0)
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
                mention_count=mention_count,
            ),
            latest_action_date=sp.latest_action_date,
            mention_count=mention_count,
        ))

    house_query = (
        db.query(RepSponsoredBill, Representative)
        .join(Representative, RepSponsoredBill.representative_id == Representative.id)
        .filter(RepSponsoredBill.congress >= min_congress)
        .filter(Representative.is_current == True)  # noqa: E712
    )
    for sp, rep in house_query.all():
        mention_count = mention_counts.get(sp.bill_id, 0)
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
                mention_count=mention_count,
            ),
            latest_action_date=sp.latest_action_date,
            mention_count=mention_count,
        ))

    return rows


def get_bills_in_flight(
    db: Session,
    stage: str | None = None,
    chamber: str | None = None,
    party: str | None = None,
    q: str | None = None,
    sort: str = "recent",
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

    if sort == "hot":
        # "Active in Congress right now" — bills currently referenced by a
        # live Action Center issue, most-mentioned first, ties broken by
        # how recently Congress acted on them.
        filtered = [r for r in filtered if r.mention_count > 0]
        filtered.sort(key=lambda r: (r.mention_count, r.latest_action_date), reverse=True)
    else:
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
