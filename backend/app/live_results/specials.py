"""A special election's count, read on the night from the state's own feed.

The same readers and trust gates as the general (sync.py): nothing is read
before the state's last polls close (sync.read_state), a test, preview or
mismatched feed is refused (UntrustedCount, and the readers find the special
by its date through election_results.pick_election), an impossible count is
dropped (sync.contest_is_sane), and a feed that goes backwards in time or
version is refused. The count is the source's figures, stored on the
special's FederalElectionDate row; a special is on no cycle's ballot pages,
so it has no Race.

Which contest is the special's: the one for its office and district. On a
federal general's day (a vacancy filled on the November ballot) only a
contest the feed marks special counts, since the seat's regular race is on
the same ballot. Two candidates for one seat are refused rather than
guessed between, as the general's sync refuses them.

Civitas never calls a race here either: the count is "leading" until the
source says official, and no sentence about it is generated (the page words
it from the stored figures, special_elections.py).

A special is read from its election day until RESULTS_GRACE_DAYS after its
count last moved (the general's window, election_phase), and only in a state
whose feed has a reader (election_results.live_results_states). One with no
count an hour after its polls close pages someone, whatever its reads said:
a walled or moved feed fails visibly.
"""

import json
import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.election_phase import RESULTS_GRACE_DAYS, election_today
from app.models import FederalElectionDate
from app.ops_alerts import send_ops_alert
from app.pipeline.fetch.election_results import is_general_day, live_results_states
from app.pipeline.fetch.poll_close import last_poll_close
from app.live_results.sync import (
    NO_COUNT_ALERT_AFTER, StateRead, _FUTURE_SKEW, _scoped_version, contest_is_sane, read_state, refusal_kind,
)
from app.time_utils import eastern_date, utcnow

logger = logging.getLogger(__name__)

# The FEC type codes of a special that is one public contest for the seat:
# the special general, its runoff, and a general runoff. A special primary
# (SP) is a contest per party, and a special convention (SC) is a party's
# choice, not a vote; neither is read. The FEC's own vocabulary, read as a
# form value.
COUNTED_TYPES = frozenset({"SG", "SR", "SGR"})


def window_open(row: FederalElectionDate, today: date) -> bool:
    """Whether `row`'s count is still read on `today` (Eastern)."""
    held = date.fromisoformat(row.election_date)
    if today < held:
        return False
    settled = max(held, eastern_date(row.last_change_at) if row.last_change_at else held)
    return today <= settled + timedelta(days=RESULTS_GRACE_DAYS)


def due_specials(db: Session, today: date | None = None, now: datetime | None = None) -> list[FederalElectionDate]:
    """The specials to read on this pass: counted types, in a covered state,
    inside their window. Once a count has not moved for a day it is read
    hourly (the first five minutes of each hour), as the general's is."""
    today = today or election_today()
    now = now or utcnow()
    covered = live_results_states()
    rows = (
        db.query(FederalElectionDate)
        .filter(FederalElectionDate.is_special.is_(True), FederalElectionDate.type_code.in_(COUNTED_TYPES),
                FederalElectionDate.office.in_(("H", "S")), FederalElectionDate.election_date <= today.isoformat())
        .order_by(FederalElectionDate.election_date)
        .all()
    )
    due = []
    for row in rows:
        if row.state not in covered or not window_open(row, today):
            continue
        if row.last_change_at and now - row.last_change_at > timedelta(days=1) and now.minute >= 5:
            continue
        due.append(row)
    return due


def _seat_contests(row: FederalElectionDate, contests: list) -> list:
    day = date.fromisoformat(row.election_date)
    general_day = is_general_day(day)
    out = []
    for c in contests:
        if c.office != row.office:
            continue
        if row.office == "H" and (c.district if c.district is not None else 0) != (row.district or 0):
            continue
        if general_day and not c.is_special:
            continue
        out.append(c)
    return out


def _went_back(row: FederalElectionDate, count) -> str | None:
    """sync.freshness_problem, against the count stored on this special."""
    if count.source_updated and count.source_updated > utcnow() + _FUTURE_SKEW:
        return f"source is stamped {count.source_updated.isoformat()}, in the future"
    if count.source_updated and row.source_updated_at and count.source_updated < row.source_updated_at:
        return f"source went back from {row.source_updated_at.isoformat()} to {count.source_updated.isoformat()}"
    new, old = _scoped_version(count.source_version), _scoped_version(row.source_version)
    if new and old and new[0] == old[0] and new[1] < old[1]:
        return f"source version went back from {old[1]} to {new[1]}"
    return None


def apply_special(db: Session, row: FederalElectionDate, read: StateRead) -> str:
    """Store what one read of the state's feed says about this special.
    Returns the read status recorded on the row."""
    status = read.status if read.status != "read" else "ok"
    if read.status == "untrusted":
        logger.warning("Special %s refused: %s", row.id, read.reason)
    elif read.status == "read":
        count = read.count
        contests = [c for c in _seat_contests(row, count.contests) if contest_is_sane(c)]
        problem = _went_back(row, count)
        if problem:
            logger.warning("Special %s not stored: %s", row.id, problem)
            status = "stale"
        elif len(contests) > 1:
            logger.warning("Special %s: %d contests for the seat, none stored", row.id, len(contests))
            status = "ambiguous"
        elif not contests:
            status = "not_listed"
        else:
            contest = contests[0]
            tallies = sorted(
                ({"name": n, "party": p, "votes": v} for n, p, v in contest.candidates),
                key=lambda t: -t["votes"],
            )
            encoded = json.dumps(tallies)
            if encoded != row.tallies or contest.votes_counted != row.votes_counted:
                row.last_change_at = utcnow()
            row.tallies = encoded
            row.votes_counted = contest.votes_counted
            row.reporting_units = contest.reporting_units
            row.total_units = contest.total_units
            row.unit_label = count.unit_label
            row.units_complete = count.units_complete
            row.official = bool(count.official)
            row.result_source_name = count.source_name
            row.result_source_url = count.page_url
            row.source_updated_at = count.source_updated
            row.source_version = count.source_version
    row.read_status = status
    row.read_checked_at = utcnow()
    return status


def _alerts(row: FederalElectionDate, previous: str | None, read: StateRead) -> None:
    """Sent after the special's write has committed: an alert is recorded
    through its own session, which would wait on an open write (sync.py)."""
    if read.status == "untrusted" and previous == "untrusted":
        # The second refusal in a row, not the first: a feed republished
        # mid-read is refused once and reads cleanly on the next pass.
        send_ops_alert(
            f"Live results: {row.state} special refused",
            f"{row.id}: {read.reason}. Refused on consecutive reads; nothing from it was stored.",
            dedupe_key=f"special-untrusted-{row.id}-{refusal_kind(read.reason)}",
        )
    if row.read_status == "not_listed" and row.votes_counted is not None:
        # The feed stopped listing a contest it had counted (renamed past
        # the reader?): its last count stays shown, and someone looks.
        send_ops_alert(
            f"Live results: {row.state} feed no longer lists the special",
            f"{row.id} had a count but the state's latest read did not list its contest. The page keeps "
            "its last count. Check the contest's label in the feed.",
            dedupe_key=f"special-dropped-{row.id}",
        )
    day = date.fromisoformat(row.election_date)
    if row.votes_counted is not None or utcnow() < last_poll_close(row.state, day) + NO_COUNT_ALERT_AFTER:
        return
    send_ops_alert(
        f"Live results: no count stored for the {row.state} special",
        f"{row.id}: polls closed over {int(NO_COUNT_ALERT_AFTER.total_seconds() // 3600)}h ago and no count is "
        f"stored; the last read was '{row.read_status}'. Check the state's results feed: blocked, moved, not "
        "published, or the special's contest not matched to its seat.",
        dedupe_key=f"special-missing-{row.id}",
    )


async def sync_specials(db: Session, client: httpx.AsyncClient, rows: list[FederalElectionDate]) -> dict:
    """Read and store each due special, committing each on its own."""
    summary = {}
    for row in rows:
        try:
            previous = row.read_status
            read = await read_state(client, row.state, date.fromisoformat(row.election_date))
            summary[row.id] = apply_special(db, row, read)
            db.commit()
            _alerts(row, previous, read)
        except Exception:
            db.rollback()
            logger.exception("Special results sync failed for %s", row.id)
            summary[row.id] = "failed"
    return summary
