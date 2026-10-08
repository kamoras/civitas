"""Shared score-trend computation for the Senate/House leaderboard endpoints."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import ScoreSnapshot
from app.pipeline.fetch.congress import congress_of_date
from app.time_utils import utcnow

TREND_LOOKBACK_DAYS = 7
TREND_THRESHOLD = 0.5


def _comparable(older: ScoreSnapshot, latest: ScoreSnapshot, by_congress: bool = True) -> bool:
    """Whether a score change between two snapshots can be read as the
    member's own: the same algorithm version, recorded on both, and the
    same Congress. A methodology change moves everyone's score at once, and
    a new Congress resets the current-term window (AGENTS.md principle 6)
    — the trend chart already marks both as boundaries; the leaderboard's
    week-over-week arrow ignored them and reported the jump as movement. A
    snapshot that recorded no version is of an unknown methodology: read as
    comparable to anything, it made 96 of 100 senators' arrows report
    methodology changes (2026-10-08)."""
    if not older.algorithm_version or older.algorithm_version != latest.algorithm_version:
        return False
    return not by_congress or congress_of_date(older.date) == congress_of_date(latest.date)


def change_on_current_method(snapshots: list[ScoreSnapshot], by_congress: bool = True) -> dict | None:
    """How far the latest score has moved since the earliest snapshot still
    comparable to it (_comparable): {"since": date, "points": change}, or
    None when there is no such earlier snapshot. A profile's score history
    once stated its change "since first snapshot", across every methodology
    change in between (one senator read 24 points down over 22 versions,
    nearly all of it formula changes). `by_congress` is False for a
    president, whose score isn't windowed to a Congress. `snapshots` is
    oldest first."""
    if len(snapshots) < 2:
        return None
    latest = snapshots[-1]
    for snap in snapshots[:-1]:
        if snap.date < latest.date and _comparable(snap, latest, by_congress):
            return {"since": snap.date, "points": round(latest.overall_score - snap.overall_score, 1)}
    return None


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
    latest. Only snapshots on the same algorithm version and Congress count
    (see _comparable); a member whose history is all on another one reads
    "reset". Returns {entity_id: {"direction", "change", "previousScore"}}.
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

    # Every earlier snapshot, newest first. Per member: the newest COMPARABLE
    # one at or before the 7-day target (preferred), else the newest
    # comparable one at least a day older than the latest (young histories).
    older_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == entity_type,
            ScoreSnapshot.date <= day_before_latest,
        )
        .order_by(ScoreSnapshot.date.desc())
        .all()
    )
    latest_by_entity = {snap.entity_id: snap for snap in latest_snapshots}
    preferred: dict[str, float] = {}
    fallback: dict[str, float] = {}
    had_history: set[str] = set()
    for snap in older_snapshots:
        latest = latest_by_entity.get(snap.entity_id)
        if latest is None:
            continue
        had_history.add(snap.entity_id)
        if not _comparable(snap, latest):
            continue
        fallback.setdefault(snap.entity_id, snap.overall_score)
        if snap.date <= target_date:
            preferred.setdefault(snap.entity_id, snap.overall_score)

    result: dict[str, dict] = {}
    for snap in latest_snapshots:
        prev = preferred.get(snap.entity_id, fallback.get(snap.entity_id))
        if prev is None:
            # "reset": there is history, but none on the same methodology and
            # congress, so any difference is not the member's doing.
            direction = "reset" if snap.entity_id in had_history else "new"
            result[snap.entity_id] = {"direction": direction, "change": 0.0, "previousScore": None}
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
