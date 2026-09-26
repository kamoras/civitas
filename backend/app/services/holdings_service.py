"""Read path for a member's disclosed asset holdings (the scorecard's
holdings breakdown). Informational only — not part of any score.

Plain ORM queries and arithmetic over the stored brackets; nothing is
inferred here (AGENTS.md: the read path stays lightweight). The only
derived figure is each slice's `weight`, the sum of bracket midpoints the
chart is drawn with, and the schema documents it as a drawing convention
rather than a value — see HoldingCategorySchema.

Only the requested page of holdings is loaded as rows — ordered and limited
in SQL. The breakdown (categories and totals) is the same for every page and
filter of a report, so it is computed once per stored report and kept in a
small in-process cache keyed by the report's id and ingest time: a new
report, or a re-read one, gets a fresh entry. One backend worker (AGENTS.md)
means one cache.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.config_definitions import HOLDING_CATEGORIES
from app.models import FinancialDisclosure, FinancialHolding, Representative, Senator
from app.schemas import HoldingCategorySchema, HoldingSchema, HoldingsSchema, is_open_ended
from app.services.pagination import paginate_bounds

# The `category` query parameter accepts exactly the category keys.
HOLDING_CATEGORY_PATTERN = "^(" + "|".join(HOLDING_CATEGORIES) + ")$"


def _midpoint(low: float | None, high: float | None) -> float:
    """The drawing weight of one bracket. An open-ended top bracket
    (low == high) contributes its floor; a missing bracket nothing."""
    if low is None or high is None:
        return 0.0
    return (low + high) / 2


def _category_key(category: str | None) -> str:
    return category if category in HOLDING_CATEGORIES else "OTHER"


def _to_schema(h: FinancialHolding) -> HoldingSchema:
    category = _category_key(h.category)
    return HoldingSchema(
        asset_name=h.asset_name,
        account=h.account,
        ticker=h.ticker,
        asset_type=h.asset_type,
        category=category,
        category_label=HOLDING_CATEGORIES[category]["label"],
        owner=h.owner,
        value_text=h.value_text,
        value_low=h.value_low,
        value_high=h.value_high,
    )


def _categories(rows: list[tuple[str, float | None, float | None]]) -> list[HoldingCategorySchema]:
    """The breakdown, from (category, low, high) for every holding.

    Every category the report has holdings in is listed, so each holding
    stays reachable through the legend filter; one with nothing valued above
    zero (weight 0) simply draws no slice."""
    total_weight = sum(_midpoint(low, high) for _, low, high in rows)
    by_category: dict[str, list[tuple[float | None, float | None]]] = {}
    for category, low, high in rows:
        by_category.setdefault(_category_key(category), []).append((low, high))

    categories: list[HoldingCategorySchema] = []
    for key, meta in HOLDING_CATEGORIES.items():
        brackets = by_category.get(key)
        if not brackets:
            continue
        valued = [(low, high) for low, high in brackets if low is not None and high is not None]
        weight = sum(_midpoint(low, high) for low, high in valued)
        categories.append(HoldingCategorySchema(
            category=key,
            label=meta["label"],
            color=meta["color"],
            count=len(brackets),
            unvalued_count=len(brackets) - len(valued),
            zero_value_count=sum(1 for low, high in valued if high == 0),
            value_low=sum(low for low, _ in valued),
            value_high=sum(high for _, high in valued),
            open_ended=any(is_open_ended(low, high) for low, high in valued),
            weight=weight,
            share=weight / total_weight if total_weight else 0.0,
        ))
    # Largest first; HOLDING_CATEGORIES' own order breaks ties (a stable sort).
    categories.sort(key=lambda c: c.weight, reverse=True)
    return categories


@dataclass(frozen=True)
class _Breakdown:
    holdings_count: int
    unvalued_count: int
    total_low: float
    total_high: float
    total_open_ended: bool
    categories: tuple[HoldingCategorySchema, ...]


# More than enough for the members viewed between deploys to stay warm;
# each entry is ~9 category rows.
_BREAKDOWN_CACHE_SIZE = 600
_breakdown_cache: "OrderedDict[tuple[int, datetime | None], _Breakdown]" = OrderedDict()
# Sync routes run in FastAPI's threadpool, so requests touch the cache
# concurrently; every check-and-update of it happens under this lock.
_breakdown_lock = threading.Lock()


def _breakdown(db: Session, disclosure: FinancialDisclosure) -> _Breakdown:
    key = (disclosure.id, disclosure.ingested_at)
    with _breakdown_lock:
        cached = _breakdown_cache.get(key)
        if cached is not None:
            _breakdown_cache.move_to_end(key)
            return cached
    rows = (
        db.query(FinancialHolding.category, FinancialHolding.value_low, FinancialHolding.value_high)
        .filter(FinancialHolding.disclosure_id == disclosure.id)
        .all()
    )
    valued = [(low, high) for _, low, high in rows if low is not None and high is not None]
    result = _Breakdown(
        holdings_count=len(rows),
        unvalued_count=len(rows) - len(valued),
        total_low=sum(low for low, _ in valued),
        total_high=sum(high for _, high in valued),
        total_open_ended=any(is_open_ended(low, high) for low, high in valued),
        categories=tuple(_categories(rows)),
    )
    with _breakdown_lock:
        _breakdown_cache[key] = result
        while len(_breakdown_cache) > _BREAKDOWN_CACHE_SIZE:
            _breakdown_cache.popitem(last=False)
    return result


def _build(db: Session, disclosure: FinancialDisclosure, page: int, per_page: int, category: str | None) -> HoldingsSchema:
    in_report = FinancialHolding.disclosure_id == disclosure.id
    breakdown = _breakdown(db, disclosure)

    listed = db.query(FinancialHolding).filter(in_report)
    if category == "OTHER":
        # OTHER also collects any stored category no longer in the table.
        listed = listed.filter(or_(
            FinancialHolding.category == "OTHER",
            FinancialHolding.category.notin_(list(HOLDING_CATEGORIES)),
        ))
    elif category:
        listed = listed.filter(FinancialHolding.category == category)
    total = listed.count()
    total_pages, page = paginate_bounds(total, page, per_page)
    # Largest first, by the same midpoint the chart uses; a holding with no
    # stated bracket sorts last rather than being dropped.
    page_rows = (
        listed.order_by(
            case((FinancialHolding.value_low.is_(None), 1), else_=0),
            ((FinancialHolding.value_low + FinancialHolding.value_high) / 2).desc(),
            func.lower(FinancialHolding.asset_name),
            FinancialHolding.id,
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return HoldingsSchema(
        available=True,
        report_year=disclosure.report_year,
        report_label=disclosure.report_label,
        filed_date=disclosure.filed_date,
        source_url=disclosure.source_url,
        parsed=disclosure.parsed,
        unreadable_reason=disclosure.unreadable_reason if not disclosure.parsed else None,
        holdings_count=breakdown.holdings_count,
        unvalued_count=breakdown.unvalued_count,
        total_low=breakdown.total_low,
        total_high=breakdown.total_high,
        total_open_ended=breakdown.total_open_ended,
        categories=list(breakdown.categories),
        category_filter=category,
        holdings=[_to_schema(h) for h in page_rows],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


def _latest_disclosure(db: Session, **owner_filter) -> FinancialDisclosure | None:
    return (
        db.query(FinancialDisclosure)
        .filter_by(**owner_filter)
        .order_by(FinancialDisclosure.report_year.desc(), FinancialDisclosure.id.desc())
        .first()
    )


def get_senator_holdings(
    db: Session, senator_id: str, page: int = 1, per_page: int = 15, category: str | None = None,
) -> HoldingsSchema | None:
    """None when the senator doesn't exist; `available=False` when no
    annual report has been ingested for them."""
    if db.query(Senator.id).filter(Senator.id == senator_id).first() is None:
        return None
    disclosure = _latest_disclosure(db, senator_id=senator_id)
    if disclosure is None:
        return HoldingsSchema(available=False)
    return _build(db, disclosure, page, per_page, category)


def get_rep_holdings(
    db: Session, rep_id: str, page: int = 1, per_page: int = 15, category: str | None = None,
) -> HoldingsSchema | None:
    """See get_senator_holdings."""
    if db.query(Representative.id).filter(Representative.id == rep_id).first() is None:
        return None
    disclosure = _latest_disclosure(db, representative_id=rep_id)
    if disclosure is None:
        return HoldingsSchema(available=False)
    return _build(db, disclosure, page, per_page, category)
