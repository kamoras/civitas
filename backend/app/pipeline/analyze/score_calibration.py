"""
Score distribution monitoring across pipeline runs.

Compares score distributions across consecutive snapshot dates and logs
any statistically notable shifts. Runs automatically at the end of each
pipeline run. The admin endpoint exposes the latest report for observability.

Score dimensions map to ScoreSnapshot columns:
  funding_independence      → score_1
  promise_persistence       → score_2
  independent_voting        → score_3
  funding_diversity         → score_4
  legislative_effectiveness → score_5
  overall_score             → overall_score

Only stdlib (statistics, math) — no new dependencies.
"""

import logging
import statistics

from app.database import session_scope
from app.models import ScoreSnapshot

logger = logging.getLogger(__name__)

# ── Drift thresholds (tune at the top of the file) ──────────────────────────
WARN_MEAN_SHIFT_PCT = 5.0    # mean shifted > 5% → warn
ALERT_MEAN_SHIFT_PCT = 15.0  # mean shifted > 15% → alert
WARN_STDEV_SHIFT_PCT = 20.0  # stdev changed > 20% → warn

# Minimum population required to run meaningful statistics.
MIN_COUNT_FOR_STATS = 5

# Canonical dimension names + which ScoreSnapshot column each maps to.
DIMENSIONS: list[tuple[str, str]] = [
    ("funding_independence",      "score_1"),
    ("promise_persistence",       "score_2"),
    ("independent_voting",        "score_3"),
    ("funding_diversity",         "score_4"),
    ("legislative_effectiveness", "score_5"),
    ("overall_score",             "overall_score"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _percentiles(data: list[float]) -> tuple[float, float]:
    """Return (p10, p90) for *data* using statistics.quantiles with n=10.

    statistics.quantiles(data, n=10) returns 9 cut-points (the decile
    boundaries): index 0 is the 10th percentile, index 8 is the 90th.
    Requires len(data) >= 2; caller must guard.
    """
    cuts = statistics.quantiles(data, n=10)
    return cuts[0], cuts[8]


def _dim_stats(values: list[float]) -> dict:
    """Compute descriptive statistics for a list of score values."""
    n = len(values)
    if n < MIN_COUNT_FOR_STATS:
        return {
            "count": n,
            "mean": None,
            "median": None,
            "stdev": None,
            "min": None,
            "max": None,
            "p10": None,
            "p90": None,
        }

    # n >= MIN_COUNT_FOR_STATS (>= 5) is guaranteed by the early return above,
    # so stdev/percentiles always have enough data — no n < 2 fallback needed.
    mean = statistics.mean(values)
    median = statistics.median(values)
    stdev = statistics.stdev(values)
    lo = min(values)
    hi = max(values)
    p10, p90 = _percentiles(values)

    return {
        "count": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "stdev": round(stdev, 4),
        "min": round(lo, 4),
        "max": round(hi, 4),
        "p10": round(p10, 4),
        "p90": round(p90, 4),
    }


def _spearman_rho(prev_map: dict[str, float], curr_map: dict[str, float]) -> float | None:
    """Spearman rank-correlation between matched entity scores.

    Only entities present in *both* snapshots are included. Returns None
    if fewer than 3 paired entities are available.

    Formula: rho = 1 - (6 * sum(d^2)) / (n * (n^2 - 1))
    where d = rank difference for each entity.
    """
    common_ids = sorted(set(prev_map) & set(curr_map))
    n = len(common_ids)
    if n < 3:
        return None

    prev_vals = [prev_map[eid] for eid in common_ids]
    curr_vals = [curr_map[eid] for eid in common_ids]

    def _ranks(vals: list[float]) -> dict[int, float]:
        """Return {index: rank} with average ranks for ties."""
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks: dict[int, float] = {}
        i = 0
        while i < len(order):
            j = i
            while j < len(order) - 1 and vals[order[j + 1]] == vals[order[j]]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-based
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    prev_ranks = _ranks(prev_vals)
    curr_ranks = _ranks(curr_vals)

    # Pearson correlation of the (average) ranks. The classic
    # 1 - 6*sum(d^2)/(n(n^2-1)) shortcut is only exact when there are NO
    # ties — clamped integer scores tie constantly, so the shortcut was
    # systematically biased here. Pearson-of-ranks is the exact tie-aware
    # definition of Spearman's rho.
    xs = [prev_ranks[i] for i in range(n)]
    ys = [curr_ranks[i] for i in range(n)]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = sum((x - mx) ** 2 for x in xs)
    den_y = sum((y - my) ** 2 for y in ys)
    if den_x == 0 or den_y == 0:
        # One side has all-tied values — rank order carries no information.
        return None

    rho = num / ((den_x * den_y) ** 0.5)
    return round(rho, 4)


# ── Public API ────────────────────────────────────────────────────────────────

def compute_distribution(entity_type: str, date: str | None = None) -> dict:
    """Compute distribution statistics for all score dimensions.

    Args:
        entity_type: ``"senator"`` or ``"representative"``.
        date: Snapshot date (``YYYY-MM-DD``). Uses the most recent date
              if None.

    Returns:
        Dict keyed by dimension name, each value a stats dict with keys:
        ``mean``, ``median``, ``stdev``, ``min``, ``max``, ``p10``,
        ``p90``, ``count``.  Also includes a top-level ``"date"`` key
        and ``"_entity_scores"`` (entity_id → score) for Spearman later.
    """
    with session_scope() as db:
        if date is None:
            row = (
                db.query(ScoreSnapshot.date)
                .filter(ScoreSnapshot.entity_type == entity_type)
                .order_by(ScoreSnapshot.date.desc())
                .first()
            )
            if row is None:
                return {}
            date = row[0]

        snapshots = (
            db.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.entity_type == entity_type,
                ScoreSnapshot.date == date,
            )
            .all()
        )

        if not snapshots:
            return {}

        # Build per-dimension value lists and entity→score maps.
        dim_values: dict[str, list[float]] = {dim: [] for dim, _ in DIMENSIONS}
        # _entity_scores: dimension → {entity_id: score} for Spearman
        entity_scores: dict[str, dict[str, float]] = {dim: {} for dim, _ in DIMENSIONS}

        for snap in snapshots:
            for dim, col in DIMENSIONS:
                val = getattr(snap, col, None)
                if val is not None:
                    dim_values[dim].append(float(val))
                    entity_scores[dim][snap.entity_id] = float(val)

        result: dict = {"date": date}
        for dim, _ in DIMENSIONS:
            result[dim] = _dim_stats(dim_values[dim])
            result[dim]["_entity_scores"] = entity_scores[dim]

        return result


def detect_drift(prev_dist: dict, curr_dist: dict) -> list[dict]:
    """Compare two distributions and return a list of drift events.

    Each drift event is a dict with:
      ``dimension``, ``severity`` (``"warn"`` | ``"alert"``),
      ``prev_mean``, ``curr_mean``, ``delta_pct``,
      ``prev_stdev``, ``curr_stdev``,
      ``spearman_shift``, ``message``.

    Args:
        prev_dist: Output of ``compute_distribution`` for the earlier date.
        curr_dist: Output of ``compute_distribution`` for the later date.

    Returns:
        List of drift event dicts (may be empty).
    """
    events: list[dict] = []

    for dim, _ in DIMENSIONS:
        prev = prev_dist.get(dim, {})
        curr = curr_dist.get(dim, {})

        prev_mean = prev.get("mean")
        curr_mean = curr.get("mean")
        prev_stdev = prev.get("stdev")
        curr_stdev = curr.get("stdev")

        if prev_mean is None or curr_mean is None:
            continue  # insufficient data in one snapshot

        # Mean shift (relative to previous mean; guard div-by-zero)
        if prev_mean != 0:
            delta_pct = abs(curr_mean - prev_mean) / abs(prev_mean) * 100.0
        else:
            # prev_mean == 0: any non-zero curr is infinite drift
            delta_pct = 100.0 if curr_mean != 0 else 0.0

        # Stdev shift
        stdev_shift_pct: float | None = None
        if prev_stdev is not None and curr_stdev is not None and prev_stdev != 0:
            stdev_shift_pct = abs(curr_stdev - prev_stdev) / prev_stdev * 100.0

        # Spearman rank correlation shift
        prev_entity = prev.get("_entity_scores", {})
        curr_entity = curr.get("_entity_scores", {})
        spearman = _spearman_rho(prev_entity, curr_entity)

        # Determine severity
        severity: str | None = None
        reasons: list[str] = []

        if delta_pct >= ALERT_MEAN_SHIFT_PCT:
            severity = "alert"
            reasons.append(
                f"mean shifted {delta_pct:.1f}% ({prev_mean:.2f} → {curr_mean:.2f})"
            )
        elif delta_pct >= WARN_MEAN_SHIFT_PCT:
            severity = "warn"
            reasons.append(
                f"mean shifted {delta_pct:.1f}% ({prev_mean:.2f} → {curr_mean:.2f})"
            )

        if stdev_shift_pct is not None and stdev_shift_pct >= WARN_STDEV_SHIFT_PCT:
            if severity is None:
                severity = "warn"
            reasons.append(
                f"stdev changed {stdev_shift_pct:.1f}% "
                f"({prev_stdev:.2f} → {curr_stdev:.2f})"
            )

        if severity is None:
            continue  # no drift for this dimension

        message = "; ".join(reasons)
        if spearman is not None and spearman < 0.90:
            message += f"; rank correlation rho={spearman:.3f} (rank order shifted)"

        events.append({
            "dimension": dim,
            "severity": severity,
            "prev_mean": round(prev_mean, 4),
            "curr_mean": round(curr_mean, 4),
            "delta_pct": round(delta_pct, 2),
            "prev_stdev": round(prev_stdev, 4) if prev_stdev is not None else None,
            "curr_stdev": round(curr_stdev, 4) if curr_stdev is not None else None,
            "spearman_shift": spearman,
            "message": message,
        })

    return events


def generate_calibration_report(entity_type: str = "senator") -> dict | None:
    """Run drift detection between the two most recent snapshot dates.

    Args:
        entity_type: ``"senator"`` or ``"representative"``.

    Returns:
        Dict with keys ``from_date``, ``to_date``, ``entity_type``,
        ``drift_events``, ``distribution``.  Returns ``None`` if fewer
        than 2 distinct snapshot dates exist for ``entity_type``.
    """
    with session_scope() as db:
        # Collect the two most recent distinct dates.
        rows = (
            db.query(ScoreSnapshot.date)
            .filter(ScoreSnapshot.entity_type == entity_type)
            .distinct()
            .order_by(ScoreSnapshot.date.desc())
            .limit(2)
            .all()
        )

    if len(rows) < 2:
        return None

    to_date = rows[0][0]
    from_date = rows[1][0]

    prev_dist = compute_distribution(entity_type, date=from_date)
    curr_dist = compute_distribution(entity_type, date=to_date)

    if not prev_dist or not curr_dist:
        return None

    drift_events = detect_drift(prev_dist, curr_dist)

    # Strip internal entity-score maps before returning (not needed by callers).
    def _strip_internal(dist: dict) -> dict:
        cleaned: dict = {}
        for k, v in dist.items():
            if isinstance(v, dict):
                cleaned[k] = {ik: iv for ik, iv in v.items() if not ik.startswith("_")}
            else:
                cleaned[k] = v
        return cleaned

    return {
        "from_date": from_date,
        "to_date": to_date,
        "entity_type": entity_type,
        "drift_events": drift_events,
        "distribution": _strip_internal(curr_dist),
    }
