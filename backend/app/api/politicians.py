"""
Unified politician directory API.

Aggregates senators, representatives, presidents, and justices into a single
browsable endpoint. The directory is independent of scorecard generation — a
politician appears as soon as they are in the database, even if their scorecard
has not been computed yet (hasScorecard=false).

Individual profile pages at /politicians/{id} use branch detection across all
four tables, then compose identity + scorecard + active issues + government
record into a single response.

NOTE: Senator/Representative.is_current marks a seat vacant (death,
resignation, expulsion) without deleting or hiding the departed member —
historical scores stay intact and visible, with a vacancy banner instead.
Set manually via the admin panel; there is no automated vacancy detection.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, PARTY_QUERY_PATTERN, cached_json
from app.config_definitions import JUSTICE_SCORE_WEIGHTS
from app.database import get_db
from app.models import ActionIssue, ExploreDocument, Justice, President, Representative, Senator
from app.pipeline.analyze.president_scorer import compute_president_overall_score
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.services.senator_service import STATE_NAMES

router = APIRouter()


def _bioguide_photo(bioguide_id: str | None) -> str | None:
    if not bioguide_id:
        return None
    return f"https://bioguide.congress.gov/bioguide/photo/{bioguide_id[0]}/{bioguide_id}.jpg"


def _cached_json(data, max_age: int = CACHE_TTL_DETAIL_S) -> JSONResponse:
    return cached_json(data, max_age=max_age)


# ---------------------------------------------------------------------------
# Score helpers — mirrors frontend lib/representation.ts calculations
# ---------------------------------------------------------------------------

def _senator_overall(s) -> float | None:
    """Overall score for a Senator or Representative — duck-typed, both
    models expose the same score_* field names, so this one function
    already covers both branches (see call sites below)."""
    scores = [
        s.score_funding_independence,
        s.score_promise_persistence,
        s.score_independent_voting,
        s.score_funding_diversity,
        s.score_legislative_effectiveness,
    ]
    # All zeros means not yet scored — pipeline components never produce all-zero
    # (each returns 50 for missing data, never 0).
    if all(v == 0.0 for v in scores):
        return None
    return round(compute_overall_score(s), 1)


def _president_overall(p: President) -> float | None:
    scores = [
        p.score_public_mandate,
        p.score_effectiveness, p.score_competence, p.score_agency_alignment,
    ]
    if all(v == 0.0 for v in scores):
        return None
    return round(compute_president_overall_score(p), 1)


def _justice_overall(j: Justice) -> float | None:
    scores = [j.score_consistency, j.score_independence, j.score_bipartisan_agreement, j.score_judicial_restraint]
    if all(v == 0.0 for v in scores):
        return None
    return round(
        j.score_consistency * JUSTICE_SCORE_WEIGHTS["consistency"]
        + j.score_independence * JUSTICE_SCORE_WEIGHTS["independence"]
        + j.score_bipartisan_agreement * JUSTICE_SCORE_WEIGHTS["bipartisan_agreement"]
        + j.score_judicial_restraint * JUSTICE_SCORE_WEIGHTS["judicial_restraint"],
        1,
    )


# ---------------------------------------------------------------------------
# Active-issue cross-reference (no LLM — JSON field scan)
# ---------------------------------------------------------------------------

def _build_active_issue_map(db: Session) -> dict[str, list[int]]:
    """Return {politician_id: [issue_id, ...]} for all current issues."""
    issues = (
        db.query(ActionIssue.id, ActionIssue.related_senators, ActionIssue.related_officials)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    mapping: dict[str, list[int]] = {}
    for issue_id, related_senators, related_officials in issues:
        for field in (related_senators, related_officials):
            if not field:
                continue
            try:
                entries = json.loads(field)
            except (json.JSONDecodeError, TypeError):
                continue
            for entry in entries:
                pid = entry.get("id") if isinstance(entry, dict) else None
                if pid:
                    mapping.setdefault(pid, []).append(issue_id)
    return mapping


# ---------------------------------------------------------------------------
# Directory endpoint
# ---------------------------------------------------------------------------

@router.get("/politicians")
def list_politicians(
    branch: str | None = Query(None, pattern="^(senate|house|president|scotus)$"),
    state: str | None = Query(None, min_length=2, max_length=2),
    party: str | None = Query(None, pattern=PARTY_QUERY_PATTERN),
    q: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return all currently-serving politicians as a unified directory."""
    issue_map = _build_active_issue_map(db)
    results: list[dict] = []
    q_lower = q.lower() if q else None

    if branch in (None, "senate"):
        query = db.query(Senator)
        if state:
            query = query.filter(Senator.state == state.upper())
        if party:
            query = query.filter(Senator.party == party)
        for s in query.order_by(Senator.name).all():
            if q_lower and q_lower not in s.name.lower():
                continue
            overall = _senator_overall(s)
            results.append({
                "id": s.id,
                "branch": "senate",
                "name": s.name,
                "party": s.party,
                "state": s.state,
                "stateName": STATE_NAMES.get(s.state, s.state),
                "district": None,
                "role": "Senator",
                "thumbnailUrl": _bioguide_photo(s.bioguide_id),
                "hasScorecard": overall is not None,
                "overallScore": overall,
                "activeIssueCount": len(issue_map.get(s.id, [])),
                "isCurrent": s.is_current,
                "vacancyReason": s.vacancy_reason,
                "leftOfficeDate": s.left_office_date,
                "leadershipTitle": s.leadership_title,
            })

    if branch in (None, "house"):
        query = db.query(Representative)
        if state:
            query = query.filter(Representative.state == state.upper())
        if party:
            query = query.filter(Representative.party == party)
        for r in query.order_by(Representative.name).all():
            if q_lower and q_lower not in r.name.lower():
                continue
            overall = _senator_overall(r)
            results.append({
                "id": r.id,
                "branch": "house",
                "name": r.name,
                "party": r.party,
                "state": r.state,
                "stateName": STATE_NAMES.get(r.state, r.state),
                "district": getattr(r, "district", None),
                "role": "Representative",
                "thumbnailUrl": _bioguide_photo(r.bioguide_id),
                "hasScorecard": overall is not None,
                "overallScore": overall,
                "activeIssueCount": len(issue_map.get(r.id, [])),
                "isCurrent": r.is_current,
                "vacancyReason": r.vacancy_reason,
                "leftOfficeDate": r.left_office_date,
                "leadershipTitle": r.leadership_title,
            })

    if branch in (None, "president"):
        for p in db.query(President).filter(President.is_current == True).all():  # noqa: E712
            if q_lower and q_lower not in p.name.lower():
                continue
            if party and p.party != party:
                continue
            overall = _president_overall(p)
            results.append({
                "id": p.id,
                "branch": "president",
                "name": p.name,
                "party": p.party,
                "state": None,
                "stateName": None,
                "district": None,
                "role": f"President ({p.number}{_ordinal_suffix(p.number)})",
                "thumbnailUrl": None,
                "hasScorecard": overall is not None,
                "overallScore": overall,
                "activeIssueCount": len(issue_map.get(p.id, [])),
            })

    if branch in (None, "scotus"):
        for j in db.query(Justice).filter(Justice.is_active == True).all():  # noqa: E712
            if q_lower and q_lower not in j.name.lower():
                continue
            if party and (j.appointing_party or "R") != party:
                continue
            overall = _justice_overall(j)
            is_chief = "Chief" in (j.role_title or "")
            results.append({
                "id": j.id,
                "branch": "scotus",
                "name": j.name,
                "party": j.appointing_party or "R",
                "state": None,
                "stateName": None,
                "district": None,
                "role": "Chief Justice" if is_chief else "Associate Justice",
                "thumbnailUrl": j.thumbnail_url,
                "hasScorecard": overall is not None,
                "overallScore": overall,
                "activeIssueCount": len(issue_map.get(j.id, [])),
            })

    return _cached_json(results)


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ---------------------------------------------------------------------------
# Profile endpoint
# ---------------------------------------------------------------------------

def _detect_branch(pid: str, db: Session) -> tuple[str, object] | None:
    row = db.query(Senator).filter(Senator.id == pid).first()
    if row:
        return ("senate", row)
    row = db.query(Representative).filter(Representative.id == pid).first()
    if row:
        return ("house", row)
    row = db.query(President).filter(President.id == pid).first()
    if row:
        return ("president", row)
    row = db.query(Justice).filter(Justice.id == pid).first()
    if row:
        return ("scotus", row)
    return None


def _build_identity(branch: str, entity) -> dict:
    if branch == "senate":
        return {
            "name": entity.name,
            "party": entity.party,
            "state": entity.state,
            "stateName": STATE_NAMES.get(entity.state, entity.state),
            "role": "Senator",
            "thumbnailUrl": _bioguide_photo(entity.bioguide_id),
            "yearsInOffice": entity.years_in_office,
            "contactFormUrl": entity.contact_form_url or "",
            "websiteUrl": entity.website_url or "",
            "officePhone": entity.office_phone or "",
            "officeAddress": entity.office_address or "",
            "isCurrent": entity.is_current,
            "vacancyReason": entity.vacancy_reason,
            "leftOfficeDate": entity.left_office_date,
            "leadershipTitle": entity.leadership_title,
            "committees": json.loads(entity.committees or "[]"),
        }
    if branch == "house":
        return {
            "name": entity.name,
            "party": entity.party,
            "state": entity.state,
            "stateName": STATE_NAMES.get(entity.state, entity.state),
            "district": getattr(entity, "district", None),
            "role": "Representative",
            "thumbnailUrl": _bioguide_photo(entity.bioguide_id),
            "contactFormUrl": entity.contact_form_url or "",
            "websiteUrl": entity.website_url or "",
            "isCurrent": entity.is_current,
            "vacancyReason": entity.vacancy_reason,
            "leftOfficeDate": entity.left_office_date,
            "officePhone": entity.office_phone or "",
            "officeAddress": entity.office_address or "",
            "leadershipTitle": entity.leadership_title,
            "committees": json.loads(entity.committees or "[]"),
        }
    if branch == "president":
        return {
            "name": entity.name,
            "party": entity.party,
            "role": "President",
            "number": entity.number,
            "termStart": entity.term_start,
            "termEnd": entity.term_end,
            "isCurrent": entity.is_current,
        }
    if branch == "scotus":
        return {
            "name": entity.name,
            "party": entity.appointing_party,
            "role": entity.role_title or "Associate Justice",
            "appointingPresident": entity.appointing_president,
            "dateStart": entity.date_start,
            "thumbnailUrl": entity.thumbnail_url,
            "isActive": entity.is_active,
        }
    return {}


def _build_scorecard(branch: str, pid: str, db: Session) -> dict | None:
    try:
        if branch == "senate":
            from app.services.senator_service import get_senator_by_id
            s = get_senator_by_id(db, pid)
            return s.model_dump(by_alias=True) if s else None
        if branch == "house":
            from app.services.representative_service import get_representative_by_id
            r = get_representative_by_id(db, pid)
            return r.model_dump(by_alias=True) if r else None
        if branch == "president":
            from app.services.president_service import get_president
            p = get_president(db, pid)
            return p.model_dump(by_alias=True) if p else None
        if branch == "scotus":
            from app.services.justice_service import get_justice
            j = get_justice(db, pid)
            return j.model_dump(by_alias=True) if j else None
    except Exception:
        return None
    return None


def _get_active_issues(politician_id: str, db: Session) -> list[dict]:
    issues = db.query(ActionIssue).filter(ActionIssue.is_current == True).all()  # noqa: E712
    result = []
    for issue in issues:
        related: list[dict] = []
        for field in (issue.related_senators, issue.related_officials):
            if field:
                try:
                    related.extend(json.loads(field))
                except (json.JSONDecodeError, TypeError):
                    pass
        if any(e.get("id") == politician_id for e in related):
            result.append({
                "id": issue.id,
                "title": issue.title,
                "summary": issue.summary,
                "rank": issue.rank,
                "date": issue.date,
                "policyAreas": json.loads(issue.policy_areas or "[]"),
            })
    return result


def _get_gov_record(politician_id: str, db: Session) -> dict:
    total = (
        db.query(ExploreDocument)
        .filter(ExploreDocument.politician_id == politician_id)
        .count()
    )
    recent = (
        db.query(ExploreDocument)
        .filter(ExploreDocument.politician_id == politician_id)
        .order_by(desc(ExploreDocument.date))
        .limit(5)
        .all()
    )
    return {
        "totalDocs": total,
        "recentDocs": [
            {
                "id": d.id,
                "docType": d.doc_type,
                "title": d.title,
                "date": d.date,
                "url": d.url,
                "source": d.source,
            }
            for d in recent
        ],
    }


@router.get("/politicians/{politician_id}")
def get_politician(politician_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return full profile for a single politician."""
    result = _detect_branch(politician_id, db)
    if result is None:
        raise HTTPException(status_code=404, detail="Politician not found")

    branch, entity = result
    overall = _senator_overall(entity) if branch in ("senate", "house") else (
        _president_overall(entity) if branch == "president" else _justice_overall(entity)
    )
    scorecard = _build_scorecard(branch, politician_id, db)

    return _cached_json({
        "id": politician_id,
        "branch": branch,
        "identity": _build_identity(branch, entity),
        "hasScorecard": overall is not None,
        "overallScore": overall,
        "scorecard": scorecard,
        "activeIssues": _get_active_issues(politician_id, db),
        "governmentRecord": _get_gov_record(politician_id, db),
    })


@router.get("/politicians/{politician_id}/documents")
def get_politician_documents(
    politician_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    doc_type: str | None = Query(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return paginated government documents (floor speeches, EOs, opinions) for a politician."""
    if _detect_branch(politician_id, db) is None:
        raise HTTPException(status_code=404, detail="Politician not found")

    q = db.query(ExploreDocument).filter(ExploreDocument.politician_id == politician_id)
    if doc_type:
        q = q.filter(ExploreDocument.doc_type == doc_type)

    total = q.count()
    docs = q.order_by(desc(ExploreDocument.date)).offset((page - 1) * per_page).limit(per_page).all()

    return _cached_json({
        "total": total,
        "page": page,
        "perPage": per_page,
        "docs": [
            {
                "id": d.id,
                "docType": d.doc_type,
                "title": d.title,
                "summary": d.summary,
                "date": d.date,
                "url": d.url,
                "source": d.source,
                "chamber": d.chamber,
            }
            for d in docs
        ],
    })
