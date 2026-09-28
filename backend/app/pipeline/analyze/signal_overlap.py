"""Post-run check that two scored components measured from related data
still carry separate information.

docs/methodology/member-score/v6.11.md names two component pairs built from
related data, the same shape as the two double-counts that shipped as
"distinct signals" and weren't (v6.8: ideology extremity vs. bipartisanship,
r=-0.76; v6.5: the funding pair, r=0.72):

  1. Constituent Alignment: seat-relative vote alignment vs. position
     congruence. Both come from roll calls: one is a crossing rate, the
     other a spatial position.
  2. Legislative Effectiveness: legislative leadership (cosponsorship
     PageRank) vs. bipartisan coalition attraction (cross-party share of
     cosponsors attracted). Both come from the cosponsorship network, which
     is why their combined weight is capped at 40%.

This used to be a script someone had to remember to run against the live
API (scripts/check_signal_correlations.py, which now reads this module).
Every chamber run measures both pairs over the members it just scored, from
the same breakdowns the API serves, persists them (SIGNAL_OVERLAP, read by
GET /api/signal-overlap and the About page), and alerts when a pair reaches
ACTION_R.

The bands are report labels, not scoring constants: |r| >= 0.60 is the
magnitude of the two audits above, both of which led to structural fixes;
0.40-0.60 is worth watching.
"""

import logging

from app.pipeline.analyze.population_reference import ChamberReference

logger = logging.getLogger(__name__)

# The last run's measurement per chamber (/data/signal_overlap.json), over
# the bundled one scripts/check_signal_correlations.py --write records.
SIGNAL_OVERLAP = ChamberReference("signal_overlap")

ACTION_R = 0.60
WATCH_R = 0.40

# (key, dimension, first component label, second component label): the
# labels explain_scores gives the components (score_calculator).
PAIRS = (
    ("constituent", "constituentAlignment", "Seat-relative vote alignment", "Position congruence"),
    ("effectiveness", "legislativeEffectiveness", "Legislative leadership", "Bipartisan coalition attraction"),
)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Plain Pearson r; None under three pairs or when either side has no
    spread."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx * syy) ** 0.5


def band(r: float | None) -> str:
    """"action", "watch" or "ok"; "none" when there was nothing to measure."""
    if r is None:
        return "none"
    return "action" if abs(r) >= ACTION_R else "watch" if abs(r) >= WATCH_R else "ok"


def component_scores(breakdown: dict, dimension: str) -> dict[str, float]:
    """{component label: score} for one dimension of a score breakdown.
    A missing dimension or component contributes nothing."""
    dim = breakdown.get(dimension) or {}
    return {
        c.get("label", ""): float(c["score"])
        for c in (dim.get("components") or [])
        if c.get("score") is not None
    }


def measure(breakdowns: list[dict]) -> dict:
    """{pair key: {"r", "n", "band", "labels"}} over members whose breakdown
    has both of a pair's components (a member without cosponsorship data
    has no coalition component, and is left out of that pair)."""
    out = {}
    for key, dimension, first, second in PAIRS:
        xs, ys = [], []
        for b in breakdowns:
            comps = component_scores(b, dimension)
            if first in comps and second in comps:
                xs.append(comps[first])
                ys.append(comps[second])
        r = pearson(xs, ys)
        out[key] = {
            "r": None if r is None else round(r, 3),
            "n": len(xs),
            "band": band(r),
            "labels": [first, second],
        }
    return out


def chamber_breakdowns(db, chamber: str) -> list[dict]:
    """Every currently serving, scored member's score breakdown, as the API
    serves it. A member whose breakdown fails is skipped (logged): one bad record
    shouldn't blind the check to the rest."""
    if chamber == "senate":
        from app.models import Senator as model
        from app.services.senator_service import get_senator_score_breakdown as breakdown_of
    elif chamber == "house":
        from app.models import Representative as model
        from app.services.representative_service import get_representative_score_breakdown as breakdown_of
    else:
        raise ValueError(f"no signal-overlap check for {chamber!r}")
    scores = (
        model.score_funding_independence, model.score_promise_persistence,
        model.score_constituent_alignment, model.score_funding_diversity,
        model.score_legislative_effectiveness,
    )
    # Members not yet scored (every score still 0, the column default; a
    # scored component is never 0 across the board) would add neutral
    # placeholders to the population, the same rule as hasScorecard.
    ids = [
        row[0] for row in db.query(model.id, *scores).filter(model.is_current.is_(True)).all()
        if any(row[1:])
    ]
    out = []
    for member_id in ids:
        try:
            b = breakdown_of(db, member_id)
        except Exception:
            logger.warning("Score breakdown failed for %s; left out of the overlap check", member_id, exc_info=True)
            continue
        if b:
            out.append(b)
    return out


def record_signal_overlap(db, chamber: str) -> dict | None:
    """Measure both pairs for `chamber`, persist them, and alert on any pair
    in the action band. Never raises: the run has already scored."""
    try:
        result = measure(chamber_breakdowns(db, chamber))
        SIGNAL_OVERLAP.write(chamber, {"pairs": result})
        flagged = [(k, v) for k, v in result.items() if v["band"] == "action"]
        for key, v in result.items():
            logger.info("Signal overlap %s %s: r=%s n=%d [%s]", chamber, key, v["r"], v["n"], v["band"])
        if flagged:
            from app.ops_alerts import send_ops_alert
            from app.time_utils import utcnow

            lines = "\n".join(
                f"- {v['labels'][0]} vs {v['labels'][1]}: r={v['r']:+.3f} (n={v['n']})" for _, v in flagged
            )
            send_ops_alert(
                f"{chamber.title()} score components overlap (|r| >= {ACTION_R})",
                f"Two components measured from related data now move together:\n{lines}\n"
                "The established fix is to cut the redundant weight or restructure "
                "(score_calculator's v6.8/v6.11 notes), not to recalibrate around it.",
                dedupe_key=f"signal-overlap-{chamber}-{utcnow().date().isoformat()}",
            )
        return result
    except Exception:
        logger.exception("Signal-overlap check failed for %s (non-fatal)", chamber)
        return None
