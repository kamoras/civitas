"""Whether a hand-labelled calibration still describes what it gates.

The thresholds in app/data/*_calibration.json were fitted on labelled
samples of what the Action Center, the trend posts, committee routing and
the member-photo check saw when the labels were drawn. Labels can't relabel
themselves, and the refit is already forced whenever they change (each
calibration records the hash of its labels, and a test fails until it is
refit). What can happen silently is the opposite: the labels stay put while
the stream they describe moves (a new administration's vocabulary, a news
cycle, a source's format), and a threshold tuned for one stream quietly
admits or refuses far more of another.

So every gate reports the scores it decided on (`observe`): how many items,
how many passed. The baseline is the gate's own first four weeks after the
calibration in use was first seen (a refit starts a new baseline); the check
compares the last four weeks with it, and alerts when the pass rate has
moved by at least MIN_SHIFT and the move is far outside sampling noise
(a two-proportion z of at least MIN_Z, both windows holding at least MIN_N
decisions). For a hand-labelled gate the alert reports the measured shift;
nothing here changes a threshold, and a refit needs labels drawn from the
stream it now sees.

A gate that refits itself from labels the data supplies (REFITTING) gets a
refit instead of a note: drift makes its refit due at the next data refresh
(data_refresh.request_refit), and only drift still there REFIT_GRACE_DAYS
after the first request is alerted, since then the refit itself did not
bring the gate back. Such a gate refits daily, so its calibration file
changes daily; its baseline follows only the parameters that decide
(PARAMETERS), not the day's measurements.

Counters are kept in memory as gates decide and written per day per gate
to api_cache (tier calibration-drift) after each hourly Action Center
refresh and by the nightly check (`flush`), never from inside a gate. A
failure to record never touches the caller's decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import pathlib
import threading
from collections.abc import Iterable
from datetime import date, timedelta

from app import data_refresh
from app.atomic_write import runtime_data_path
from app.database import SessionLocal
from app.file_cache import read_json_preferring
from app.models import ApiCache
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.pipeline.cache import api_cache_get, api_cache_set
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_TIER = "calibration-drift"
WINDOW = timedelta(days=28)
MIN_N = 200
MIN_SHIFT = 0.15
MIN_Z = 4.0
_KEEP_HOURS = 24 * 365 * 5

# Each gate: the calibration file it reads (whose content defines "the
# calibration in use") and what a pass means, for the alert.
GATES = {
    "trend_tragedy": ("trend_gates_calibration.json", "trends voted a tragedy"),
    "trend_loaded": ("trend_gates_calibration.json", "trend names voted loaded"),
    "committee_routing": ("committee_routing_calibration.json", "issues routed to a committee"),
    "member_photo": ("member_photo_calibration.json", "members switched to the bioguide portrait"),
    "roll_call_links": ("roll_call_link_calibration.json", "stories linked to a related vote in Congress"),
    "near_identical": ("action_thresholds.json", "issue pairs joined as one story by their titles alone"),
    "engagement": ("engagement_calibration.json", "outlet posts reposted as matching an issue"),
}


# A self-refitting gate's file: what defines its calibration in use is its
# parameters, not the measurements a daily refit rewrites beside them.
PARAMETERS = {
    "committee_routing": ("reference", "k", "alpha", "temperature", "threshold"),
}

# Gates that refit themselves from data-supplied labels, by the data_refresh
# file whose refit drift requests.
REFITTING = {
    "committee_routing": "committee_routing_calibration.json",
}
REFIT_GRACE_DAYS = 3

_pending: dict[tuple[str, str], list[int]] = {}
_pending_lock = threading.Lock()


def observe(gate: str, scores: Iterable[float], threshold: float) -> None:
    """Count a batch of decisions (a score at or above `threshold` passed),
    in memory: a gate runs inside its caller's work, which may hold a write
    transaction, so nothing is written here (flush writes)."""
    values = [float(s) for s in scores]
    if not values:
        return
    with _pending_lock:
        day = _pending.setdefault((gate, utcnow().date().isoformat()), [0, 0])
        day[0] += len(values)
        day[1] += sum(v >= threshold for v in values)


def flush() -> None:
    """Write the counted decisions to the day counters, from a job's own
    thread after its work (the hourly refresh, the nightly check). Counts a
    restart drops are only a little less data, never a wrong decision."""
    with _pending_lock:
        pending = dict(_pending)
        _pending.clear()
    if not pending:
        return
    db = SessionLocal()
    try:
        for (gate, day), (n, passed) in pending.items():
            key = f"{gate}|{day}"
            stored = api_cache_get(db, _TIER, key, max_age_hours=_KEEP_HOURS) or {"n": 0, "passed": 0}
            api_cache_set(db, _TIER, key, {"n": stored["n"] + n, "passed": stored["passed"] + passed},
                          normal_ttl_hours=_KEEP_HOURS)
    except Exception:
        logger.warning("Calibration drift counters not written", exc_info=True)
    finally:
        db.close()


def _days(db, gate: str, start: date, end: date) -> tuple[int, int]:
    """(decisions, passes) of `gate` on days in [start, end)."""
    n = passed = 0
    for row in db.query(ApiCache).filter(ApiCache.tier == _TIER, ApiCache.cache_key.like(f"{gate}|%")):
        day = row.cache_key.split("|", 1)[1]
        if day == "since" or not (start.isoformat() <= day < end.isoformat()):
            continue
        data = json.loads(row.data_json)
        n, passed = n + int(data.get("n") or 0), passed + int(data.get("passed") or 0)
    return n, passed


def _calibration_sha(filename: str, keys: tuple[str, ...] | None = None) -> str:
    """The content of the calibration in use (only `keys` when given): a
    refit copy on the data volume when there is one, else the bundled file."""
    bundled = pathlib.Path(__file__).resolve().parent / "data" / filename
    doc = read_json_preferring(runtime_data_path(filename), bundled, default={})
    if keys is not None:
        doc = {k: doc.get(k) for k in keys}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def _since(db, gate: str, sha: str, today: date) -> date:
    """The day the calibration in use was first seen for `gate` (its
    baseline starts there); recorded now if it is new."""
    stored = api_cache_get(db, _TIER, f"{gate}|since", max_age_hours=_KEEP_HOURS) or {}
    if stored.get("sha") != sha:
        stored = {"sha": sha, "date": today.isoformat()}
        api_cache_set(db, _TIER, f"{gate}|since", stored, normal_ttl_hours=_KEEP_HOURS)
    return date.fromisoformat(stored["date"])


def drift(gate: str, today: date | None = None) -> str | None:
    """Why `gate`'s calibration looks stale, or None."""
    filename, meaning = GATES[gate]
    today = today or utcnow().date()
    db = SessionLocal()
    try:
        since = _since(db, gate, _calibration_sha(filename, PARAMETERS.get(gate)), today)
        if today < since + 2 * WINDOW:
            return None  # a baseline and a separate recent window aren't there yet
        n0, p0 = _days(db, gate, since, since + WINDOW)
        n1, p1 = _days(db, gate, today - WINDOW, today + timedelta(days=1))
    finally:
        db.close()
    if n0 < MIN_N or n1 < MIN_N:
        return None
    r0, r1 = p0 / n0, p1 / n1
    pooled = (p0 + p1) / (n0 + n1)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n0 + 1 / n1)) or 1e-9
    z = (r1 - r0) / se
    if abs(r1 - r0) < MIN_SHIFT or abs(z) < MIN_Z:
        return None
    return (f"{meaning}: {r1:.0%} of {n1} in the last four weeks, against {r0:.0%} of {n0} in the four weeks "
            f"after this calibration was first used (z = {z:.1f}): what the gate sees has moved since the labels "
            f"behind app/data/{filename} were drawn.")


def _refit_for_drift(gate: str, why: str | None) -> str | None:
    """A self-refitting gate's drift: request its refit, and return what to
    alert (None until the drift has outlasted REFIT_GRACE_DAYS of refits)."""
    today = utcnow().date()
    db = SessionLocal()
    try:
        key = f"{gate}|refit-requested"
        if not why:
            api_cache_set(db, _TIER, key, {"date": None}, normal_ttl_hours=_KEEP_HOURS)
            return None
        first = (api_cache_get(db, _TIER, key, max_age_hours=_KEEP_HOURS) or {}).get("date")
        if not first:
            first = today.isoformat()
            api_cache_set(db, _TIER, key, {"date": first}, normal_ttl_hours=_KEEP_HOURS)
    finally:
        db.close()
    data_refresh.request_refit(REFITTING[gate], f"calibration drift: {why}")
    if today < date.fromisoformat(first) + timedelta(days=REFIT_GRACE_DAYS):
        logger.info("Calibration drift on %s: refit requested (%s)", gate, why)
        return None
    return (f"{why} Refits from data-supplied labels have been requested every day since {first} and the "
            f"drift remains: see the refit's record in app/data/{REFITTING[gate]} (_refreshed.kept).")


def check_all() -> dict[str, str | None]:
    """Each gate's drift, alerted per gate (calibration-drift-<gate>) and
    resolved when it clears."""
    flush()
    out = {}
    for gate in GATES:
        try:
            why = drift(gate)
        except Exception:
            logger.exception("Calibration drift check for %s failed", gate)
            continue
        out[gate] = why
        condition = f"calibration-drift-{gate.replace('_', '-')}"
        if gate in REFITTING:
            try:
                why = _refit_for_drift(gate, why)
            except Exception:
                logger.exception("Calibration drift refit request for %s failed", gate)
        if why:
            send_ops_alert(f"{GATES[gate][0]} looks stale", why,
                           dedupe_key=f"{condition}-{utcnow():%Y-%W}", condition=condition)
        else:
            resolve_ops_alert(condition)
    return out
