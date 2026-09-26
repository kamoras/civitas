"""Read path for a member's disclosed asset holdings (the scorecard's
holdings breakdown). Informational only — not part of any score.

Plain ORM queries and arithmetic over the stored brackets; nothing is
inferred here (AGENTS.md: the read path stays lightweight). The only
derived figure is each slice's `weight`, the sum of bracket midpoints the
chart is drawn with, and the schema documents it as a drawing convention
rather than a value — see HoldingCategorySchema.
"""

from sqlalchemy.orm import Session, selectinload

from app.models import FinancialDisclosure, Representative, Senator
from app.pipeline.fetch.fd_common import HOLDING_CATEGORIES
from app.schemas import HoldingCategorySchema, HoldingSchema, HoldingsSchema
from app.services.pagination import paginate_bounds

# The `category` query parameter accepts exactly the category keys.
HOLDING_CATEGORY_PATTERN = "^(" + "|".join(HOLDING_CATEGORIES) + ")$"


def _midpoint(low: float | None, high: float | None) -> float:
    """The drawing weight of one bracket. An open-ended top bracket
    (low == high) contributes its floor; a missing bracket nothing."""
    if low is None or high is None:
        return 0.0
    return (low + high) / 2


def _build(disclosure: FinancialDisclosure, page: int, per_page: int, category: str | None) -> HoldingsSchema:
    holdings = list(disclosure.holdings)

    by_category: dict[str, list] = {}
    for h in holdings:
        by_category.setdefault(h.category if h.category in HOLDING_CATEGORIES else "OTHER", []).append(h)

    valued = [h for h in holdings if h.value_low is not None and h.value_high is not None]
    total_weight = sum(_midpoint(h.value_low, h.value_high) for h in valued)

    def is_open(h) -> bool:
        return h.value_low is not None and h.value_low > 0 and h.value_high == h.value_low

    categories: list[HoldingCategorySchema] = []
    for key, meta in HOLDING_CATEGORIES.items():
        members = [h for h in by_category.get(key, []) if h.value_low is not None and h.value_high is not None]
        weight = sum(_midpoint(h.value_low, h.value_high) for h in members)
        if weight <= 0:
            continue
        categories.append(HoldingCategorySchema(
            category=key,
            label=meta["label"],
            color=meta["color"],
            count=len(by_category.get(key, [])),
            value_low=sum(h.value_low for h in members),
            value_high=sum(h.value_high for h in members),
            open_ended=any(is_open(h) for h in members),
            weight=weight,
            share=weight / total_weight if total_weight else 0.0,
        ))
    categories.sort(key=lambda c: c.weight, reverse=True)

    listed = by_category.get(category, []) if category else holdings
    # Largest first, by the same midpoint the chart uses; a holding with no
    # stated bracket sorts last rather than being dropped.
    listed = sorted(
        listed,
        key=lambda h: (h.value_low is None, -_midpoint(h.value_low, h.value_high), h.asset_name.lower()),
    )
    total = len(listed)
    total_pages, page = paginate_bounds(total, page, per_page)
    page_rows = listed[(page - 1) * per_page: page * per_page]

    return HoldingsSchema(
        available=True,
        report_year=disclosure.report_year,
        filed_date=disclosure.filed_date,
        source_url=disclosure.source_url,
        parsed=disclosure.parsed,
        holdings_count=len(holdings),
        unvalued_count=len(holdings) - len(valued),
        total_low=sum(h.value_low for h in valued),
        total_high=sum(h.value_high for h in valued),
        total_open_ended=any(is_open(h) for h in valued),
        categories=categories,
        category_filter=category,
        holdings=[
            HoldingSchema(
                asset_name=h.asset_name,
                account=h.account,
                ticker=h.ticker,
                asset_type=h.asset_type,
                category=h.category,
                category_label=HOLDING_CATEGORIES.get(h.category, HOLDING_CATEGORIES["OTHER"])["label"],
                owner=h.owner,
                value_text=h.value_text,
                value_low=h.value_low,
                value_high=h.value_high,
            )
            for h in page_rows
        ],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


def _latest_disclosure(db: Session, **owner_filter) -> FinancialDisclosure | None:
    return (
        db.query(FinancialDisclosure)
        .options(selectinload(FinancialDisclosure.holdings))
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
    return _build(disclosure, page, per_page, category)


def get_rep_holdings(
    db: Session, rep_id: str, page: int = 1, per_page: int = 15, category: str | None = None,
) -> HoldingsSchema | None:
    """See get_senator_holdings."""
    if db.query(Representative.id).filter(Representative.id == rep_id).first() is None:
        return None
    disclosure = _latest_disclosure(db, representative_id=rep_id)
    if disclosure is None:
        return HoldingsSchema(available=False)
    return _build(disclosure, page, per_page, category)
