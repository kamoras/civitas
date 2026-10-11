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
resignation, expulsion) without immediately deleting or hiding the
departed member — scores stay intact and visible, with a vacancy banner
instead. Set automatically by pipeline/member_lifecycle.py, which each
night flags anyone missing from the Congress.gov roster and deletes
anyone who left more than RETIREMENT_GRACE_DAYS ago; the admin panel can
still set or clear it by hand. Departed members are excluded from the
Senate/House leaderboards immediately, but stay in this directory for the
whole grace period. Presidents are never removed by any of that.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, PARTY_QUERY_PATTERN, cached_json
from app.database import get_db
from app.member_ids import resolve_member_id
from app.issue_ids import to_public_id
from app.models import ActionIssue, ExploreDocument, Justice, President, Representative, Senator
from app.ordinals import ordinal
from app.photos import bioguide_photo, justice_photo
from app.pipeline.analyze.president_scorer import compute_president_overall_score
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.score_display import displayed_rank
from app.services.senator_service import STATE_NAMES

logger = logging.getLogger(__name__)

router = APIRouter()


def _cached_json(data, max_age: int = CACHE_TTL_DETAIL_S) -> JSONResponse:
    return cached_json(data, max_age=max_age)


# ---------------------------------------------------------------------------
# Score helpers — the shared scorers, at their own precision (the page rounds
# once, for display; rounding here too turned 62.45 into 63 beside a
# scorecard showing 62)
# ---------------------------------------------------------------------------

def _senator_overall(s) -> float | None:
    """Overall score for a Senator or Representative — duck-typed, both
    models expose the same score_* field names, so this one function
    already covers both branches (see call sites below)."""
    scores = [
        s.score_funding_independence,
        s.score_promise_persistence,
        s.score_constituent_alignment,
        s.score_funding_diversity,
        s.score_legislative_effectiveness,
    ]
    # All zeros means not yet scored — pipeline components never produce all-zero
    # (each returns 50 for missing data, never 0).
    if all(v == 0.0 for v in scores):
        return None
    return compute_overall_score(s)


def _president_overall(p: President) -> float | None:
    scores = [
        p.score_public_mandate, p.score_effectiveness, p.score_historical_legacy,
    ]
    # 2026-07 (#218 review S4): these four columns are nullable — a
    # dimension that's genuinely inapplicable or not-yet-computed for
    # this president is None, never 0.0 (see models.py's President
    # comment). `v == 0.0` never matches None, so a fully-unscored
    # president (fresh DB, before the first pipeline run) used to pass
    # this guard and rank at compute_president_overall_score's defensive
    # 0.0 fallback instead of being excluded; score_historical_legacy
    # wasn't even in the checked list at all.
    if all(v is None for v in scores):
        return None
    return compute_president_overall_score(p)


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
                "thumbnailUrl": bioguide_photo(s.bioguide_id),
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
                "thumbnailUrl": bioguide_photo(r.bioguide_id),
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
                "role": f"President ({ordinal(p.number)})",
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
                "thumbnailUrl": justice_photo(j),
                # Justices have a scorecard and no score (justice v3):
                # null is "not scored", never 0.
                "hasScorecard": True,
                "overallScore": None,
                "activeIssueCount": len(issue_map.get(j.id, [])),
            })

    return _cached_json(results)


# ---------------------------------------------------------------------------
# Profile endpoint
# ---------------------------------------------------------------------------

def _detect_branch(pid: str, db: Session) -> tuple[str, object] | None:
    # One person has one id in both chambers (app/member_ids.py), so one id
    # can be a departed row in one chamber and a serving member in the other (a
    # representative who went on to the Senate, or back): the serving one is
    # who the page is about.
    senator = db.query(Senator).filter(Senator.id == pid).first()
    rep = db.query(Representative).filter(Representative.id == pid).first()
    if rep and rep.is_current and not (senator and senator.is_current):
        return ("house", rep)
    if senator:
        return ("senate", senator)
    if rep:
        return ("house", rep)
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
            "thumbnailUrl": bioguide_photo(entity.bioguide_id),
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
            "thumbnailUrl": bioguide_photo(entity.bioguide_id),
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
            "thumbnailUrl": justice_photo(entity),
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
        # The profile still renders without its scorecard, but a failure
        # here is a bug in a service, not a missing record: say so.
        logger.exception("Scorecard for %s (%s) failed to build", pid, branch)
        return None
    return None


def _chamber_rank(branch: str, entity, db: Session) -> dict | None:
    """Where the profile stands on its leaderboard: {"rank", "of"}. The
    leaderboard's order — the overall score as displayed (a whole number,
    rounded half up), ties sharing a standard competition rank — so the
    profile states the rank the leaderboard shows. None for whom the
    leaderboard doesn't rank: a member no longer serving, a sitting
    president (only completed terms are ranked: get_president_leaderboard),
    any justice (justice v3 scores and ranks none), or anyone not scored."""
    scores: dict[str, float] = {}
    if branch == "president":
        if entity.is_current:
            return None
        for p in db.query(President).filter(President.is_current == False).all():  # noqa: E712
            scores[p.id] = compute_president_overall_score(p)
    elif branch in ("senate", "house") and getattr(entity, "is_current", False):
        model = Senator if branch == "senate" else Representative
        for m in db.query(model).filter(model.is_current == True).all():  # noqa: E712
            scores[m.id] = compute_overall_score(m)
    if entity.id not in scores:
        return None
    return {"rank": displayed_rank(scores[entity.id], list(scores.values())), "of": len(scores)}


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
        if any(isinstance(e, dict) and e.get("id") == politician_id for e in related):
            result.append({
                "id": issue.id,
                "publicId": to_public_id(issue.id),
                "title": issue.title,
                "summary": issue.summary,
                "rank": issue.rank,
                "date": issue.date,
                "firstSurfaced": issue.created_at.strftime("%Y-%m-%d"),
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
    """Return full profile for a single politician.

    A renamed member's old id (app/member_ids.py) is answered with the
    member, under their current id: the response's `id` is the one to
    link to, and the page redirects an old URL to it."""
    politician_id = resolve_member_id(db, politician_id)
    result = _detect_branch(politician_id, db)
    if result is None:
        raise HTTPException(status_code=404, detail="Politician not found")

    branch, entity = result
    overall = _senator_overall(entity) if branch in ("senate", "house") else (
        _president_overall(entity) if branch == "president" else None  # justices: not scored
    )
    scorecard = _build_scorecard(branch, politician_id, db)

    return _cached_json({
        "id": politician_id,
        "branch": branch,
        "identity": _build_identity(branch, entity),
        "hasScorecard": overall is not None or branch == "scotus",
        "overallScore": overall,
        "scorecard": scorecard,
        "chamberRank": _chamber_rank(branch, entity, db),
        "activeIssues": _get_active_issues(politician_id, db),
        "governmentRecord": _get_gov_record(politician_id, db),
    })
