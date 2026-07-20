"""Shared score-trend computation for the Senate/House leaderboard endpoints."""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import ScoreSnapshot
from app.time_utils import utcnow

TREND_LOOKBACK_DAYS = 7
TREND_THRESHOLD = 0.5


def compute_score_trend_map(db: Session, entity_type: str) -> dict[str, dict]:
    """Compare latest snapshots to the best available prior snapshot.

    Prefers a snapshot from ~TREND_LOOKBACK_DAYS ago; falls back to the
    oldest available snapshot that is at least 1 day older than the latest.
    Returns {entity_id: {"direction", "change", "previousScore"}}. Was
    copy-pasted (down to the same lookback/threshold constants) between
    senator_service.py and representative_service.py's leaderboards.
    """
    today = utcnow().date()
    target_date = today - timedelta(days=TREND_LOOKBACK_DAYS)

    latest_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == entity_type,
            ScoreSnapshot.date == today.isoformat(),
        )
        .all()
    )
    if not latest_snapshots:
        return {}

    yesterday = (today - timedelta(days=1)).isoformat()
    older_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == entity_type,
            ScoreSnapshot.date <= min(target_date.isoformat(), yesterday),
        )
        .order_by(ScoreSnapshot.date.desc())
        .all()
    )
    older_map: dict[str, float] = {}
    for snap in older_snapshots:
        if snap.entity_id not in older_map:
            older_map[snap.entity_id] = snap.overall_score

    result: dict[str, dict] = {}
    for snap in latest_snapshots:
        prev = older_map.get(snap.entity_id)
        if prev is None:
            result[snap.entity_id] = {"direction": "new", "change": 0.0, "previousScore": None}
        else:
            change = round(snap.overall_score - prev, 2)
            if change > TREND_THRESHOLD:
                direction = "up"
            elif change < -TREND_THRESHOLD:
                direction = "down"
            else:
                direction = "stable"
            result[snap.entity_id] = {"direction": direction, "change": change, "previousScore": prev}
    return result
