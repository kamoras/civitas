"""Service layer for Supreme Court justice data."""

import json
import logging
from collections import defaultdict
from typing import Sequence

from sqlalchemy.orm import Session

from app.models import Justice, JusticeVote
from app.schemas import (
    JusticeAgreementSchema,
    JusticeLeaderboardEntry,
    JusticeLoyaltySchema,
    JusticeSchema,
    JusticeScoreSchema,
)

logger = logging.getLogger(__name__)


# A 95% confidence interval: the estimate ± this many standard errors.
CI_Z = 1.96


def _build_score(j: Justice) -> JusticeScoreSchema:
    """Justice v3 scores no justice: every score is null ("not scored"),
    whatever an earlier version left in the database."""
    return JusticeScoreSchema(loyalty=None, overall=None)


def _loyalty(j: Justice) -> JusticeLoyaltySchema | None:
    """The appointing president's estimated effect on the justice's votes,
    with its 95% confidence interval: shown as information, not a score."""
    if j.appointer_effect is None or j.appointer_effect_se is None:
        return None
    return JusticeLoyaltySchema(
        estimate=j.appointer_effect, se=j.appointer_effect_se,
        ci_low=round(j.appointer_effect - CI_Z * j.appointer_effect_se, 4),
        ci_high=round(j.appointer_effect + CI_Z * j.appointer_effect_se, 4),
        votes_in=j.loyalty_votes_in or 0, votes_out=j.loyalty_votes_out or 0,
        rate_in=j.loyalty_rate_in or 0.0, rate_out=j.loyalty_rate_out or 0.0, through_term=j.loyalty_through_term,
    )


def _agreement(j: Justice, names: dict[str, str]) -> list[JusticeAgreementSchema]:
    """The stored agreement shares ({justice id: share}), named, most
    first. A justice the table no longer holds is left out rather than
    shown by id."""
    try:
        shares = json.loads(j.agreement_matrix or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    rows = [
        JusticeAgreementSchema(id=jid, name=names[jid], share=share)
        for jid, share in shares.items() if jid in names and isinstance(share, (int, float))
    ]
    return sorted(rows, key=lambda r: (-r.share, r.name))


def _justice_names(db: Session) -> dict[str, str]:
    return dict(db.query(Justice.id, Justice.name).all())


def _build_justice_response(j: Justice, names: dict[str, str]) -> JusticeSchema:
    score = _build_score(j)

    return JusticeSchema(
        id=j.id,
        name=j.name,
        last_name=j.last_name,
        role_title=j.role_title,
        appointing_president=j.appointing_president,
        appointing_party=j.appointing_party,
        date_start=j.date_start,
        is_active=j.is_active,
        thumbnail_url=j.thumbnail_url,
        score=score,
        cases_decided=j.cases_decided,
        majority_pct=j.majority_pct,
        dissent_pct=j.dissent_pct,
        unanimous_pct=j.unanimous_pct,
        authored_majority=j.authored_majority,
        authored_dissent=j.authored_dissent,
        authored_concurrence=j.authored_concurrence,
        close_case_majority_pct=j.close_case_majority_pct,
        agreement=_agreement(j, names),
        loyalty=_loyalty(j),
        ideal_points=_ideal_points(j),
    )


def _ideal_points(j: Justice) -> list[tuple[int, float]]:
    try:
        points = json.loads(j.ideal_points or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return points if isinstance(points, list) else []


def get_all_justices(db: Session) -> list[JusticeSchema]:
    rows: Sequence[Justice] = (
        db.query(Justice).filter(Justice.is_active.is_(True)).all()
    )
    names = _justice_names(db)
    return [_build_justice_response(j, names) for j in rows]


def get_justice(db: Session, justice_id: str) -> JusticeSchema | None:
    j = db.query(Justice).filter(Justice.id == justice_id).first()
    if not j:
        return None
    return _build_justice_response(j, _justice_names(db))


def get_justice_leaderboard(db: Session) -> list[JusticeLeaderboardEntry]:
    rows: Sequence[Justice] = (
        db.query(Justice).filter(Justice.is_active.is_(True)).all()
    )
    entries = []
    for j in rows:
        score = _build_score(j)
        entries.append(JusticeLeaderboardEntry(
            id=j.id,
            name=j.name,
            last_name=j.last_name,
            role_title=j.role_title,
            appointing_president=j.appointing_president,
            appointing_party=j.appointing_party,
            date_start=j.date_start,
            is_active=j.is_active,
            thumbnail_url=j.thumbnail_url,
            score=score,
            cases_decided=j.cases_decided,
            majority_pct=j.majority_pct,
            dissent_pct=j.dissent_pct,
            loyalty=_loyalty(j),
        ))
    # Not ranked (justice v3): by seniority, the Chief Justice first, then
    # by the date each took the seat.
    entries.sort(key=lambda e: ("Chief" not in (e.role_title or ""), e.date_start or "9999", e.name))
    return entries


def group_votes_by_case_and_justice(
    votes: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Group a flat vote list into (case_id -> votes, justice_id -> votes)."""
    case_votes: dict[str, list[dict]] = defaultdict(list)
    justice_votes: dict[str, list[dict]] = defaultdict(list)
    for v in votes:
        case_votes[v["case_id"]].append(v)
        justice_votes[v["justice_id"]].append(v)
    return dict(case_votes), dict(justice_votes)


def get_justice_score_breakdown(db: Session, justice_id: str) -> dict | None:
    """The figures behind the justice's appointer estimate, as stored by the
    pipeline (justice_loyalty): the estimate, its standard error and 95%
    interval, the votes under the appointing president and under others and
    the share of each for the government. The score is always null: justice
    v3 scores no justice."""
    j = db.query(Justice).filter(Justice.id == justice_id).first()
    if not j:
        return None
    loyalty = _loyalty(j)
    return {"loyalty": {
        "score": None,
        "components": [],
        "facts": loyalty.model_dump(by_alias=True) if loyalty else None,
    }}


def upsert_justice(db: Session, data: dict, votes: list[dict]) -> None:
    """Create or update a justice record with vote data."""
    jid = data["id"]
    existing = db.query(Justice).filter(Justice.id == jid).first()

    if existing:
        for key, val in data.items():
            if key != "id" and hasattr(existing, key):
                setattr(existing, key, val)
        justice = existing
    else:
        justice = Justice(**{k: v for k, v in data.items() if hasattr(Justice, k)})
        db.add(justice)

    db.query(JusticeVote).filter(JusticeVote.justice_id == jid).delete()

    for v in votes:
        vote = JusticeVote(
            justice_id=jid,
            case_id=v["case_id"],
            case_name=v.get("case_name", ""),
            case_term=v.get("case_term", ""),
            decided_date=v.get("decided_date"),
            vote=v["vote"],
            opinion_type=v.get("opinion_type", "none"),
            is_unanimous=v.get("is_unanimous", False),
            is_close=v.get("is_close", False),
            majority_votes=v.get("majority_votes", 0),
            minority_votes=v.get("minority_votes", 0),
        )
        db.add(vote)

    db.flush()
