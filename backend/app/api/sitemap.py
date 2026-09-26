"""Sitemap index — every indexable detail page's id and last-modified date.

Backs the frontend's `sitemap.ts`. Before this existed the sitemap listed
only the ~65 static routes and state ballot pages, so search engines had no
way to discover a single member profile, bill, or Action Center issue except
by crawling a client-rendered directory. Those detail pages are most of the
site's content and are what people actually search for ("<senator> voting
record", "<bill number>").

Read path only: plain column selects, no ORM hydration, no model inference.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_REFERENCE_S, cached_json
from app.database import get_db
from app.issue_ids import to_public_id
from app.models import ActionIssue, Justice, President, Representative, Senator
from app.services.bill_service import _collect_bills

router = APIRouter()


def _iso_date(value: datetime | str | None) -> str | None:
    """YYYY-MM-DD, or None when the row carries no usable date.

    Sitemap `lastmod` must be a W3C date; an empty or malformed value is
    worse than omitting it (Google stops trusting a site's lastmod values
    once they prove inaccurate)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value)[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


@router.get("/sitemap")
def sitemap_entries(db: Session = Depends(get_db)) -> JSONResponse:
    """Ids + lastmod for every politician profile, bill, and issue page.

    Politicians: every row `/politicians/{id}` resolves (see
    `politicians._detect_branch`) — including former presidents, which are
    permanent site content, and departed members still inside their
    retirement grace window, whose profiles still render.

    Bills: exactly the set `/bills` lists (`_collect_bills`, the current
    congress's bills from sitting members), so the sitemap never advertises
    a bill page the site itself doesn't link to.

    Issues: every issue, current or retired — the issue archive is a
    permanent record, and each has its own stable public id.
    """
    politicians: list[dict] = []
    for model in (Senator, Representative, President, Justice):
        for pid, updated in db.query(model.id, model.updated_at).all():
            politicians.append({"id": pid, "lastmod": _iso_date(updated)})

    seen_bills: set[str] = set()
    bills: list[dict] = []
    for row in _collect_bills(db):
        bill_id = row.bill.bill_id
        if bill_id in seen_bills:
            continue
        seen_bills.add(bill_id)
        bills.append({"id": bill_id, "lastmod": _iso_date(row.latest_action_date)})

    issues = [
        {"id": to_public_id(iid), "lastmod": _iso_date(date)}
        for iid, date in db.query(ActionIssue.id, ActionIssue.date)
        .order_by(ActionIssue.date.desc())
        .all()
    ]

    return cached_json(
        {"politicians": politicians, "bills": bills, "issues": issues},
        max_age=CACHE_TTL_REFERENCE_S,
    )
