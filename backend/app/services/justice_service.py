"""Service layer for Supreme Court justice data."""

import json
import logging
from typing import Sequence

from sqlalchemy.orm import Session

from app.models import Justice, JusticeVote
from app.schemas import (
    JusticeLeaderboardEntry,
    JusticeSchema,
    JusticeScoreSchema,
)

logger = logging.getLogger(__name__)

_SCORE_WEIGHTS = {
    "consistency": 0.35,
    "independence": 0.30,
    "bipartisan_agreement": 0.15,
    "judicial_restraint": 0.20,
}


def _weighted_total(score: JusticeScoreSchema) -> float:
    return (
        score.consistency * _SCORE_WEIGHTS["consistency"]
        + score.independence * _SCORE_WEIGHTS["independence"]
        + score.bipartisan_agreement * _SCORE_WEIGHTS["bipartisan_agreement"]
        + score.judicial_restraint * _SCORE_WEIGHTS["judicial_restraint"]
    )


def _build_score(j: Justice) -> JusticeScoreSchema:
    return JusticeScoreSchema(
        consistency=j.score_consistency,
        independence=j.score_independence,
        bipartisan_agreement=j.score_bipartisan_agreement,
        judicial_restraint=j.score_judicial_restraint,
    )


def _build_justice_response(j: Justice) -> JusticeSchema:
    score = _build_score(j)
    try:
        agreement = json.loads(j.agreement_matrix or "{}")
    except (json.JSONDecodeError, TypeError):
        agreement = {}

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
        cross_bloc_pct=j.cross_bloc_pct,
        agreement_matrix=agreement,
        summary=j.summary or "",
    )


def get_all_justices(db: Session) -> list[JusticeSchema]:
    rows: Sequence[Justice] = (
        db.query(Justice).filter(Justice.is_active.is_(True)).all()
    )
    return [_build_justice_response(j) for j in rows]


def get_justice(db: Session, justice_id: str) -> JusticeSchema | None:
    j = db.query(Justice).filter(Justice.id == justice_id).first()
    if not j:
        return None
    return _build_justice_response(j)


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
            is_active=j.is_active,
            thumbnail_url=j.thumbnail_url,
            score=score,
            cases_decided=j.cases_decided,
            majority_pct=j.majority_pct,
            dissent_pct=j.dissent_pct,
            cross_bloc_pct=j.cross_bloc_pct,
        ))
    entries.sort(key=lambda e: _weighted_total(e.score), reverse=True)
    return entries


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
