"""The spread of a chamber's scores, for the leaderboard's distribution
strip: how many members show each whole-number score, and the quartiles.

It also carries how many serving members each party has, for the
leaderboard's party filter.

A rank is a position, and positions hide how close the field is: on
2026-10-13 the 433 House members held 47 distinct ranks between them, and
half the House scored within thirteen points. The strip shows that, so a
gap of ten places that is one point of score doesn't read as a finding.
"""

from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, Query
from app.api.response_helpers import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.models import Representative, ScoreSnapshot, Senator
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.score_display import score_spread

router = APIRouter()

_MODEL = {"senate": Senator, "house": Representative}
_ENTITY = {"senate": "senator", "house": "representative"}


@router.get("/leaderboard/spread")
def leaderboard_spread(
    chamber: Literal["senate", "house"] = Query("senate"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Serving members only, as the leaderboard ranks them."""
    model = _MODEL[chamber]
    members = db.query(model).filter(model.is_current.is_(True)).all()
    spread = score_spread([compute_overall_score(m) for m in members])
    # The party filter's counts. The House list is paginated, so the page
    # can't count them itself (it showed DEM (0) and REP (0) until this).
    parties = Counter(m.party for m in members)
    # The chamber's latest scoring run: the date a shared picture of the
    # strip says its scores are as of.
    as_of = db.query(func.max(ScoreSnapshot.date)).filter(ScoreSnapshot.entity_type == _ENTITY[chamber]).scalar()
    return cached_json({"chamber": chamber, **spread, "parties": {p: parties[p] for p in ("D", "R", "I")},
                        "asOf": as_of}, max_age=CACHE_TTL_LIST_S)
