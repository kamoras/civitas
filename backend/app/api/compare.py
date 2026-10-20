"""How often two members of one chamber voted the same way this Congress,
for /compare.

Counted over the chamber's roll calls of the sitting Congress on which both
cast a Yea or a Nay. Housekeeping questions (normalize_votes.is_housekeeping:
quorum calls, motions to table, the previous question) are left out, as
Constituent Alignment leaves them out: they are about running the chamber,
split on party lines by habit, and would make any two members of opposite
parties look further apart on what they voted on than they are.

Every figure and sentence is computed here; the page lays them out.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.response_helpers import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.config import settings
from app.database import get_db
from app.member_ids import resolve_member_id
from app.models import Representative, RollCall, RollCallPosition, Senator
from app.pipeline.fetch.floor_logs import NAY_POSITIONS, YEA_POSITIONS
from app.pipeline.transform.normalize_votes import is_housekeeping
from app.services.member_positions import roll_call_member_ids
from app.services.congress_service import bill_label

router = APIRouter()

# How many of the most recent votes the two split on are listed.
SPLITS_SHOWN = 5

_MODEL = {"senate": Senator, "house": Representative}


def _side(position: str) -> str | None:
    p = (position or "").strip().lower()
    if p in YEA_POSITIONS:
        return "Yea"
    if p in NAY_POSITIONS:
        return "Nay"
    return None


def agreement(db: Session, chamber: str, left: str, right: str, congress: int | None = None) -> dict:
    congress = congress or settings.CURRENT_CONGRESS
    model = _MODEL[chamber]
    ids = {"left": resolve_member_id(db, left), "right": resolve_member_id(db, right)}
    members = {m.id: m for m in db.query(model).filter(model.id.in_(list(ids.values())))}
    if any(i not in members for i in ids.values()):
        raise HTTPException(status_code=404, detail=f"Both members must be in the {chamber}")
    vote_ids = roll_call_member_ids(db, chamber, congress, ids)
    by_vote: dict[str, dict] = {}
    rows = (
        db.query(RollCall, RollCallPosition)
        .join(RollCallPosition, RollCallPosition.roll_call_id == RollCall.id)
        .filter(RollCall.chamber == chamber, RollCall.congress == congress,
                RollCallPosition.member_id.in_(list(vote_ids.values())))
        .all()
    )
    side_of = {vote_ids[i]: k for k, i in ids.items() if i in vote_ids}
    for rc, pos in rows:
        if is_housekeeping(rc.question, rc.chamber):
            continue
        entry = by_vote.setdefault(rc.id, {"rc": rc})
        entry[side_of[pos.member_id]] = _side(pos.position)
    both = [e for e in by_vote.values() if e.get("left") and e.get("right")]
    agreed = sum(1 for e in both if e["left"] == e["right"])
    splits = sorted((e for e in both if e["left"] != e["right"]),
                    key=lambda e: (date.fromisoformat(e["rc"].date), e["rc"].session, e["rc"].number), reverse=True)
    n = len(both)
    pct = round(100 * agreed / n) if n else None
    if n:
        sentence = (f"Voted the same way on {agreed} of the {n} roll calls this Congress "
                    f"where both cast a Yea or a Nay ({pct}%).")
    else:
        sentence = "No roll call this Congress has a Yea or a Nay from both."
    return {
        "chamber": chamber,
        "congress": congress,
        "bothVoted": n,
        "agreed": agreed,
        "split": n - agreed,
        "agreementPct": pct,
        "sentence": sentence,
        # The latest roll call read for either member: what the figures
        # are as of (a vote one of them missed still dates the record).
        "asOf": max((e["rc"].date for e in by_vote.values()), default=None),
        "recentSplits": [
            {
                "chamber": e["rc"].chamber, "congress": e["rc"].congress, "session": e["rc"].session,
                "number": e["rc"].number, "date": e["rc"].date, "question": e["rc"].question,
                "title": e["rc"].title, "result": e["rc"].result, "billId": e["rc"].bill_id,
                "billLabel": bill_label(e["rc"].bill_id), "left": e["left"], "right": e["right"],
            }
            for e in splits[:SPLITS_SHOWN]
        ],
    }


@router.get("/compare/agreement")
def compare_agreement(
    left: str = Query(..., max_length=80),
    right: str = Query(..., max_length=80),
    chamber: Literal["senate", "house"] = Query(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return cached_json(agreement(db, chamber, left, right), max_age=CACHE_TTL_LIST_S)
