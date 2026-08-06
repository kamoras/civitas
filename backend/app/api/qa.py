"""Question-answering API — natural-language queries over stored data.

Retrieval-first by design: see services/qa.py for why nothing here
generates a figure.
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.public import RateLimit
from app.database import get_db
from app.services.qa import answer_question

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa")


@router.get("")
async def ask(
    _rl: RateLimit,
    q: str = Query(..., min_length=3, max_length=300, description="Natural-language question"),
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Answer a question from stored scorecard, donor, and document data.

    The response carries `citations` for every figure in `answer`, plus
    `latencyMs` and the intent-classification score and margin. Those last
    three are not debug output — they are how we find out whether this
    path is servable on the current hardware and whether the intent gate
    is set correctly against real questions rather than invented ones.
    """
    return answer_question(db, q, limit=limit)
