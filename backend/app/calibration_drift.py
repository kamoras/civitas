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
decisions). The alert asks for a fresh labelled sample and a refit; nothing
here changes a threshold.

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
    "policy_relevance": ("policy_relevance_calibration.json", "articles kept as U.S. policy news"),
    "cluster_pairs": ("cluster_pair_calibration.json", "article pairs joined into one story"),
    "claim_restatement": ("claim_restatement_calibration.json", "claim pairs read as one claim restated"),
    "claim_actor": ("claim_actor_calibration.json", "actor spans admitted by the vote"),
    "trend_relevance": ("trend_gates_calibration.json", "trends voted relevant"),
    "trend_tragedy": ("trend_gates_calibration.json", "trends voted a tragedy"),
    "trend_loaded": ("trend_gates_calibration.json", "trend names voted loaded"),
    "trend_appointment": ("trend_gates_calibration.json", "trends voted about an appointment"),
    "committee_routing": ("committee_routing_calibration.json", "issues routed to a committee"),
    "member_photo": ("member_photo_calibration.json", "members switched to the bioguide portrait"),
}


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


def _calibration_sha(filename: str) -> str:
    """The content of the calibration in use: a refit copy on the data
    volume when there is one, else the bundled file."""
    bundled = pathlib.Path(__file__).resolve().parent / "data" / filename
    doc = read_json_preferring(runtime_data_path(filename), bundled, default={})
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
        since = _since(db, gate, _calibration_sha(filename), today)
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
            f"after this calibration was first used (z = {z:.1f}). What the gate sees has moved since its labels "
            f"were drawn: label a fresh sample of what it sees now, add it to the labelled set, and refit "
            f"(app/data/{filename}; its script is named in the file's _source).")


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
        if why:
            send_ops_alert(f"{GATES[gate][0]} looks stale", why,
                           dedupe_key=f"{condition}-{utcnow():%Y-%W}", condition=condition)
        else:
            resolve_ops_alert(condition)
    return out
