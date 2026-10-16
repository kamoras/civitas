"""What a stored member score keeps beside it: the breakdown the run worked
it out with, and, when the latest run could not score the member, which
run's score it is and why.

Outside app/pipeline/ on purpose, like score_display: how a score is kept
and labelled must not move the analysis-code fingerprint.
"""

import json
from typing import Any

from app.models import ScoreSnapshot
from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION, compute_overall_score
from app.time_utils import utcnow

# The source a fetcher's "could not read" exception names, by class name so
# this module imports no fetcher: what a held score's note says the run
# could not read. Any other exception names no source.
_UNREAD_SOURCE = {
    "FecUnavailable": "FEC filings",
    "CongressUnavailable": "Congress.gov",
    "GovinfoUnavailable": "GovInfo",
    "SecUnavailable": "SEC records",
}


def stored_breakdown(breakdown: dict | None) -> str | None:
    """The score_breakdown column's value for a run's explain_scores output,
    stamped with when it was worked out; None when the run computed none."""
    if not breakdown:
        return None
    return json.dumps({"scoredAt": utcnow().strftime("%Y-%m-%dT%H:%MZ"), "dimensions": breakdown})


def load_breakdown(entity: Any) -> tuple[dict | None, str | None]:
    """(dimensions, scoredAt) as the run that stored the scores worked them
    out, or (None, None) for a score stored before breakdowns were kept."""
    try:
        kept = json.loads(getattr(entity, "score_breakdown", None) or "null")
    except (TypeError, ValueError):
        return None, None
    if not isinstance(kept, dict) or not isinstance(kept.get("dimensions"), dict):
        return None, None
    return kept["dimensions"], kept.get("scoredAt")


def unread_source(exc: BaseException) -> str | None:
    """The source a run failed to read, from the exception or one it was
    raised from; None when it names none."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        source = _UNREAD_SOURCE.get(type(exc).__name__)
        if source:
            return source
        exc = exc.__cause__ or exc.__context__
    return None


def hold_record(exc: BaseException, failed_on: str, scored_on: str | None, version: str | None) -> str:
    """The score_held column's value for a member the run of `failed_on`
    could not score, whose stored score is the run of `scored_on`'s."""
    return json.dumps({
        "failedOn": failed_on,
        "source": unread_source(exc),
        "scoredOn": scored_on,
        "algorithmVersion": version,
    })


def held(entity: Any) -> dict | None:
    """{failedOn, source, scoredOn, algorithmVersion} when the member's
    stored score is held from an earlier run (score_held), else None."""
    try:
        record = json.loads(getattr(entity, "score_held", None) or "null")
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("failedOn") else None


def keep_rescored(db: Any, entity_type: str, row: Any, key: str, part: dict) -> None:
    """After a rescore rewrote one of `row`'s stored parts (`key`, with its
    explain_scores `part`): put the part's breakdown in the kept breakdown,
    so the arithmetic served is still the stored score's, and write today's
    snapshot of the score, so the score shown can be cited to a run. The
    snapshot carries the running ALGORITHM_VERSION, the code that rescored
    it. The caller commits."""
    dimensions, _ = load_breakdown(row)
    if dimensions is not None:
        dimensions[key] = {k: v for k, v in part.items() if k != "vote_part_status"}
        row.score_breakdown = stored_breakdown(dimensions)
    today = utcnow().date().isoformat()
    db.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == entity_type,
        ScoreSnapshot.entity_id == row.id,
        ScoreSnapshot.date == today,
    ).delete(synchronize_session=False)
    db.add(ScoreSnapshot(
        entity_type=entity_type,
        entity_id=row.id,
        date=today,
        overall_score=compute_overall_score(row),
        score_1=row.score_funding_independence,
        score_2=row.score_promise_persistence,
        score_3=row.score_constituent_alignment,
        score_4=row.score_funding_diversity,
        score_5=row.score_legislative_effectiveness,
        algorithm_version=ALGORITHM_VERSION,
    ))
