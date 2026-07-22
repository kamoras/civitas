"""Shared score-trend computation for the Senate/House leaderboard endpoints."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import ScoreSnapshot
from app.time_utils import utcnow

TREND_LOOKBACK_DAYS = 7
TREND_THRESHOLD = 0.5


def compute_score_trend_map(db: Session, entity_type: str) -> dict[str, dict]:
    """Compare latest snapshots to the best available prior snapshot.

    "Latest" is the most recent snapshot DATE on record, not literally
    today — requiring ``date == today`` made every leaderboard trend
    silently disappear whenever the nightly pipeline hadn't run yet (early
    UTC hours) or had failed, and the docstring's "falls back to the oldest
    snapshot at least 1 day older" behavior was dead code (``min(target,
    yesterday)`` always chose the 7-day target, so 1-6-day-old priors were
    never used). Prefers a snapshot from ~TREND_LOOKBACK_DAYS before the
    latest; falls back to the newest snapshot at least 1 day older than the
    latest. Returns {entity_id: {"direction", "change", "previousScore"}}.
    Was copy-pasted (down to the same lookback/threshold constants) between
    senator_service.py and representative_service.py's leaderboards.
    """
    latest_date_row = (
        db.query(ScoreSnapshot.date)
        .filter(ScoreSnapshot.entity_type == entity_type)
        .order_by(ScoreSnapshot.date.desc())
        .first()
    )
    if latest_date_row is None:
        return {}
    latest_date_str = latest_date_row[0]

    latest_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == entity_type,
            ScoreSnapshot.date == latest_date_str,
        )
        .all()
    )
    if not latest_snapshots:
        return {}

    try:
        latest_date = date.fromisoformat(latest_date_str)
    except ValueError:
        latest_date = utcnow().date()
    target_date = (latest_date - timedelta(days=TREND_LOOKBACK_DAYS)).isoformat()
    day_before_latest = (latest_date - timedelta(days=1)).isoformat()

    # Preferred: the newest snapshot at or before the 7-day target.
    older_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == entity_type,
            ScoreSnapshot.date <= target_date,
        )
        .order_by(ScoreSnapshot.date.desc())
        .all()
    )
    # Fallback for young snapshot histories: any snapshot at least 1 day
    # older than the latest (the behavior the old docstring promised but
    # never delivered), so members stop reading as "new" for their first
    # week of history.
    if not older_snapshots:
        older_snapshots = (
            db.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.entity_type == entity_type,
                ScoreSnapshot.date <= day_before_latest,
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
