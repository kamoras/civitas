"""Federal agencies: the directory and one agency's page.

An agency is the Federal Register's (models.Agency, keyed by its slug),
with USAspending's budget where the two lists' names matched
(fetch/agencies.crosswalk), and the documents Civitas holds from it: the
Register documents it issued (explore_documents.agency_slug, its own and
its sub-agencies') and the executive orders whose text names it. Every
block carries its source and the date it was read.

Not here: lobbying aimed at an agency. The Senate's LDA filings list the
government entities each activity lobbied, but the LDA API cannot filter
filings by entity (its /filings/ parameters, checked 2026-10-15), so a
per-agency figure would mean downloading every filing of a year.
"""

import json
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.issue_ids import to_public_id
from app.models import ActionIssue, ActionIssueStatus, Agency, AgencyBudget, ExploreDocument
from app.pipeline import lexical_index
from app.pipeline.fetch.agencies import FR_SOURCE, USASPENDING_SOURCE, linking_progress
from app.pipeline.fetch.fr_rulemaking import FR_TYPE_LABELS
from app.retractions import retraction_for_issue
from app.time_utils import comment_period_today
from app.services.explore_keys import public_key

router = APIRouter(prefix="/agencies")

# How many of each kind of document an agency page lists; the rest are a
# link to Explore filtered to the agency.
SHOWN = 8
# Executive orders matched by name before the newest SHOWN are taken: the
# keyword index ranks by relevance, the page lists by date.
EO_POOL = 200


def _day(dt) -> str | None:
    return dt.date().isoformat() if dt else None


def children_of(agencies: list[Agency]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in agencies:
        if a.parent_slug:
            out.setdefault(a.parent_slug, []).append(a.slug)
    return out


def descendants(children: dict[str, list[str]], slug: str) -> list[str]:
    """`slug` and every agency under it, at any depth."""
    out, todo = [], [slug]
    while todo:
        s = todo.pop()
        if s not in out:
            out.append(s)
            todo.extend(children.get(s, []))
    return out


def _document_counts(db: Session) -> Counter:
    return Counter(dict(
        db.query(ExploreDocument.agency_slug, func.count(ExploreDocument.id))
        .filter(ExploreDocument.agency_slug.isnot(None), ExploreDocument.agency_slug != "")
        .group_by(ExploreDocument.agency_slug)
    ))


def agency_options(db: Session) -> list[dict]:
    """The agencies Civitas holds documents from, as a filter's options:
    each department or independent agency by name, its sub-agencies with
    documents right under it, every count its sub-agencies' included."""
    agencies = db.query(Agency).all()
    counts = _document_counts(db)
    children = children_of(agencies)
    by_slug = {a.slug: a for a in agencies}
    out: list[dict] = []

    def walk(slugs: list[str], depth: int) -> None:
        for slug in sorted(slugs, key=lambda s: by_slug[s].name):
            total = sum(counts.get(s, 0) for s in descendants(children, slug))
            if total:
                out.append({"slug": slug, "name": by_slug[slug].name, "depth": depth, "documents": total})
                walk(children.get(slug, []), depth + 1)

    walk([a.slug for a in agencies if not a.parent_slug or a.parent_slug not in by_slug], 0)
    return out


def _register_as_of(db: Session) -> str | None:
    """When Civitas last stored a Federal Register document."""
    return _day(db.query(func.max(ExploreDocument.created_at))
                .filter(ExploreDocument.source == "Federal Register").scalar())


def _years(b: AgencyBudget) -> list[dict] | None:
    """b's year-by-year figures, newest first; None when never read."""
    return json.loads(b.by_year) if b.by_year is not None else None


def current_year(b: AgencyBudget) -> dict | None:
    """b's figures for its current fiscal year, or None when USAspending has
    not reported that year.

    USAspending's toptier list answers 0.0 for an agency with no submission
    for the year yet: on 2026-10-16 each of the 10 agencies it listed at 0.0
    for FY2026 had 0.0 obligations and outlays too, and no FY2026 entry in
    its year-by-year record (no reporting period at all), while 5 of them
    had a nonzero FY2025 there (the Railroad Retirement Board's $17.8B among
    them). So the year counts as reported when the year-by-year record has
    it, or, before that record is read, when the list's figure isn't 0."""
    years = _years(b)
    entry = next((y for y in years or [] if y.get("fiscalYear") == b.fiscal_year), None)
    if entry:
        return entry
    if b.budget_authority:
        return {"fiscalYear": b.fiscal_year, "budgetaryResources": b.budget_authority,
                "obligated": b.obligated, "outlays": b.outlays, "throughPeriod": None}
    return None


def latest_year(b: AgencyBudget) -> dict | None:
    """The newest fiscal year USAspending has figures for: the current one
    when reported, else the last before it."""
    return current_year(b) or next(iter(_years(b) or []), None)


def _budget_row(b: AgencyBudget) -> dict:
    cur = current_year(b)
    latest = latest_year(b)
    return {
        "toptierCode": b.toptier_code,
        "name": b.name,
        "abbreviation": b.abbreviation,
        "slug": b.agency_slug,
        "fiscalYear": b.fiscal_year,
        "fiscalQuarter": b.fiscal_quarter,
        # The current fiscal year's figures, null when it isn't reported yet
        # (never 0 for that); `latest` is the newest year that is.
        "reported": cur is not None,
        "throughPeriod": cur.get("throughPeriod") if cur else None,
        "budgetAuthority": cur.get("budgetaryResources") if cur else None,
        "obligated": cur.get("obligated") if cur else None,
        "outlays": cur.get("outlays") if cur else None,
        "latest": latest,
    }


@router.get("")
def list_agencies(db: Session = Depends(get_db)):
    """Every Register agency with how many of its documents Civitas holds
    (its sub-agencies' included), and USAspending's toptier agencies by this
    fiscal year's budget authority, the unmatched ones named."""
    agencies = db.query(Agency).order_by(Agency.name).all()
    counts = _document_counts(db)
    # Listed by USAspending's last read, this fiscal year's figures first.
    budgets = [b for b in db.query(AgencyBudget) if b.retired_at is None]
    fy = max((b.fiscal_year for b in budgets if b.fiscal_year), default=None)
    budgets = [b for b in budgets if b.fiscal_year == fy]
    rows_by_code = {b.toptier_code: _budget_row(b) for b in budgets}
    budgets.sort(key=lambda b: (not rows_by_code[b.toptier_code]["reported"],
                                -(rows_by_code[b.toptier_code]["budgetAuthority"] or 0), b.name))
    by_slug = {b.agency_slug: b for b in budgets if b.agency_slug}
    children = children_of(agencies)
    rows = []
    for a in agencies:
        tree = descendants(children, a.slug)
        b = by_slug.get(a.slug)
        rows.append({
            "slug": a.slug,
            "name": a.name,
            "shortName": a.short_name,
            "parentSlug": a.parent_slug,
            "children": len(children.get(a.slug, [])),
            "documents": sum(counts.get(s, 0) for s in tree),
            "budgetAuthority": rows_by_code[b.toptier_code]["budgetAuthority"] if b else None,
        })
    reported = [rows_by_code[b.toptier_code] for b in budgets if rows_by_code[b.toptier_code]["reported"]]
    total = sum(r["budgetAuthority"] or 0 for r in reported)
    largest = max((r["budgetAuthority"] or 0 for r in reported), default=0)
    return cached_json({
        "agencies": rows,
        "agenciesSource": FR_SOURCE,
        "agenciesAsOf": _day(max((a.synced_at for a in agencies), default=None)),
        "budget": {
            "fiscalYear": fy,
            "fiscalQuarter": next((b.fiscal_quarter for b in budgets), None),
            "throughPeriod": max((r["throughPeriod"] for r in reported if r["throughPeriod"]), default=None),
            # Over the agencies that reported the year: one not yet reported
            # is neither a zero nor part of the total.
            "total": total,
            "reportedCount": len(reported),
            # share: percent of the year's total; barPercent: of the largest.
            "agencies": [{**r,
                          "share": round(100 * r["budgetAuthority"] / total, 2)
                          if r["reported"] and total else None,
                          "barPercent": round(100 * max(r["budgetAuthority"], 0) / largest, 1)
                          if r["reported"] and largest else 0}
                         for r in (rows_by_code[b.toptier_code] for b in budgets)],
            "unmatched": sorted(b.name for b in budgets if not b.agency_slug),
            "source": USASPENDING_SOURCE,
            "asOf": _day(max((b.synced_at for b in budgets), default=None)),
        } if budgets else None,
        # Why `budget` is null: USAspending never read yet (an outage or a
        # new install), never "no agency has a budget".
        "budgetStatus": "read" if budgets else "not_read",
        "documentsSource": FR_SOURCE,
        "documentsAsOf": _register_as_of(db),
        # Documents stored before agencies were read are still being linked
        # to theirs ({"linked", "total"}), so a count of 0 is not "none";
        # null once every one is.
        "documentsLinking": linking_progress(db),
    }, max_age=CACHE_TTL_LIST_S)


def _doc(d: ExploreDocument, today: str) -> dict:
    is_open = bool(d.comment_url and d.comments_close_on and d.comments_close_on >= today)
    return {
        "id": d.id,
        "key": public_key(d.external_id, d.id),
        "title": d.title,
        "docType": d.doc_type,
        "date": d.date,
        "agencyName": d.agency_name or "",
        "agencySlug": d.agency_slug or "",
        "url": d.url or "",
        "commentsCloseOn": d.comments_close_on or "",
        "commentUrl": d.comment_url if is_open else "",
    }


def _documents(db: Session, slugs: list[str], today: str) -> dict:
    base = db.query(ExploreDocument).filter(ExploreDocument.agency_slug.in_(slugs))
    newest = (ExploreDocument.date.desc(), ExploreDocument.id.desc())
    open_q = base.filter(ExploreDocument.comment_url.isnot(None), ExploreDocument.comment_url != "",
                         ExploreDocument.comments_close_on >= today)
    by_type = dict(base.with_entities(ExploreDocument.doc_type, func.count(ExploreDocument.id))
                   .group_by(ExploreDocument.doc_type))
    return {
        "total": sum(by_type.values()),
        "byType": by_type,
        "openComments": [_doc(d, today) for d in open_q.order_by(
            ExploreDocument.comments_close_on.asc(), ExploreDocument.id.desc()).limit(SHOWN)],
        "openCommentsCount": open_q.count(),
        # As in the directory: while documents are still being linked to
        # their agency, every count here is a count so far.
        "linking": linking_progress(db),
        "proposedRules": [_doc(d, today) for d in base.filter(
            ExploreDocument.doc_type == FR_TYPE_LABELS["Proposed Rule"]).order_by(*newest).limit(SHOWN)],
        "finalRules": [_doc(d, today) for d in base.filter(
            ExploreDocument.doc_type == FR_TYPE_LABELS["Rule"]).order_by(*newest).limit(SHOWN)],
    }


def _executive_orders(db: Session, names: list[str], today: str) -> dict | None:
    """Executive orders whose title or text names the agency by any of
    `names` (each an exact phrase), newest first; None when the keyword
    index is unavailable, which is not "none name it"."""
    if lexical_index._index_unavailable:
        return None
    query = " ".join(f'"{n}"' for n in dict.fromkeys(n.replace('"', "") for n in names if n))
    hits = lexical_index.search_lexical(db, query, limit=EO_POOL, doc_type="Executive Order")
    ids = [h["id"] for h in hits]
    docs = (db.query(ExploreDocument).filter(ExploreDocument.id.in_(ids))
            .order_by(ExploreDocument.date.desc(), ExploreDocument.id.desc()).all()) if ids else []
    return {"count": len(docs), "capped": len(ids) == EO_POOL, "names": list(dict.fromkeys(n for n in names if n)),
            "items": [_doc(d, today) for d in docs[:SHOWN]]}


def _with_bars(years: list[dict]) -> list[dict]:
    """Each year with barPercent: its budgetary resources as a percent of
    the largest year's."""
    largest = max((y.get("budgetaryResources") or 0 for y in years), default=0)
    return [{**y, "barPercent": round(100 * max(y.get("budgetaryResources") or 0, 0) / largest, 1) if largest else 0}
            for y in years]


def _budget(b: AgencyBudget) -> dict:
    return {
        **_budget_row(b),
        "matchScore": round(b.match_score, 3) if b.match_score is not None else None,
        "byYear": _with_bars(json.loads(b.by_year)) if b.by_year else None,
        "retiredAt": _day(b.retired_at),
        "byYearAsOf": _day(b.by_year_synced_at),
        "source": {"name": USASPENDING_SOURCE["name"],
                   "url": f"https://www.usaspending.gov/agency/{b.usaspending_slug}" if b.usaspending_slug
                   else USASPENDING_SOURCE["url"]},
        "asOf": _day(b.synced_at),
    }


def _action_issues(db: Session, slugs: list[str]) -> list[dict]:
    """Action Center issues that cite one of the agency's documents, newest
    first: the issue's own record of which documents it drew on
    (related_explore_ids), never a match on its words. Left out: withdrawn
    issues, duplicates of another, and drafts taken down unconfirmed."""
    agency_docs = db.query(ExploreDocument.id, ExploreDocument.external_id).filter(
        ExploreDocument.agency_slug.in_(slugs)).all()
    doc_ids = {i for i, _ in agency_docs}
    if not doc_ids:
        return []
    # By permanent key where the issue stored one (services/explore_keys.py):
    # a row id can name another document after the row is replaced.
    doc_keys = {public_key(ext, i) for i, ext in agency_docs}
    out = []
    rows = (db.query(ActionIssue.id, ActionIssue.title, ActionIssue.date, ActionIssue.related_explore_ids,
                     ActionIssue.related_explore_keys, ActionIssue.status, ActionIssue.is_current)
            .filter(ActionIssue.related_explore_ids != "[]", ActionIssue.duplicate_of_id.is_(None))
            .order_by(ActionIssue.date.desc(), ActionIssue.id.desc()))
    for issue_id, title, day, related, related_keys, status, is_current in rows:
        if status == ActionIssueStatus.DEVELOPING and not is_current:
            continue
        try:
            keys = json.loads(related_keys or "[]")
            cited = set(keys) & doc_keys if keys else set(json.loads(related or "[]")) & doc_ids
        except (ValueError, TypeError):
            continue
        public_id = to_public_id(issue_id)
        if cited and not retraction_for_issue(public_id):
            out.append({"id": public_id, "title": title, "date": day, "documents": len(cited)})
        if len(out) == SHOWN:
            break
    return out


def _budget_status(own: AgencyBudget | None, budget_of: dict | None, read: bool) -> str:
    """Which of the budget's states the page is in, each worded on its own:
    reported (this year's figures); not_yet_reported (an earlier year's are
    the latest); none (USAspending lists the agency with no figures for any
    year); retired (USAspending's list stopped including it); part_of (a
    sub-agency, in its department's budget); unmatched (USAspending was read
    and no agency of its matched this one by name); not_read (USAspending
    hasn't been read: an outage or a new install, never "no budget")."""
    if own is not None:
        if own.retired_at is not None:
            return "retired"
        if current_year(own):
            return "reported"
        return "not_yet_reported" if latest_year(own) else "none"
    if budget_of is not None:
        return "part_of"
    return "unmatched" if read else "not_read"


@router.get("/{slug}")
def get_agency(slug: str, db: Session = Depends(get_db)):
    """One agency: who it is, where it sits, what it spends, and the
    documents it issued or that name it."""
    agencies = db.query(Agency).all()
    by_slug = {a.slug: a for a in agencies}
    agency = by_slug.get(slug)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    today = comment_period_today()
    counts = _document_counts(db)
    all_budgets = db.query(AgencyBudget).all()
    budgets = {b.agency_slug: b for b in all_budgets if b.agency_slug}
    tree_of = children_of(agencies)
    tree = descendants(tree_of, slug)
    own = budgets.get(slug)
    parent = by_slug.get(agency.parent_slug) if agency.parent_slug else None
    # A sub-agency's spending is reported inside its department's.
    budget_of, up = None, parent
    while own is None and up is not None:
        if up.slug in budgets:
            budget_of = {"slug": up.slug, "name": up.name}
            break
        up = by_slug.get(up.parent_slug) if up.parent_slug else None
    children = sorted((a for a in agencies if a.parent_slug == slug), key=lambda a: a.name)
    return cached_json({
        "slug": agency.slug,
        "name": agency.name,
        "shortName": agency.short_name,
        "description": agency.description,
        "frUrl": agency.fr_url,
        "website": agency.website,
        "parent": {"slug": parent.slug, "name": parent.name} if parent else None,
        "children": [{"slug": c.slug, "name": c.name,
                      "documents": sum(counts.get(s, 0) for s in descendants(tree_of, c.slug))}
                     for c in children],
        "profileSource": {"name": FR_SOURCE["name"], "url": agency.fr_url or FR_SOURCE["url"]},
        "profileAsOf": _day(agency.synced_at),
        "budget": _budget(own) if own else None,
        "budgetOf": budget_of,
        "budgetStatus": _budget_status(own, budget_of, bool(all_budgets)),
        # USAspending's agencies no Register agency's name matched, for an
        # agency with no budget of its own to say which those are.
        "unmatchedBudgets": sorted(b.name for b in all_budgets if not b.agency_slug and b.retired_at is None)
        if own is None and budget_of is None else [],
        "documents": _documents(db, tree, today),
        "actionIssues": _action_issues(db, tree),
        "executiveOrders": _executive_orders(db, [agency.name, own.name if own else None], today),
        "documentsSource": FR_SOURCE,
        "documentsAsOf": _register_as_of(db),
    }, max_age=CACHE_TTL_DETAIL_S)
