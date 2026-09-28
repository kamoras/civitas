"""Election-night Bluesky posts, from the live count's own events
(live_results/sync.py).

Election night is the one time Civitas should be louder than usual, so
these posts have their own budget and the routine race-coverage poster
(election_bluesky.py) stands down while the count is moving. Louder is
not unbounded: only the events a reader following the night would want
pushed to them are posted, a few an hour, most important first.

What is posted, in priority order:
  0. a correction — a flip this account posted has reverted. Always
     posted, outside every cap: leaving a stale "changing hands" post as
     the account's last word is the one outcome worse than noise.
  1. a seat changing party (FLIP), any chamber.
  2. a count the state calls official — for a Senate race, or a flip.
  3. a Senate lead change with most of the count in.
  4. every unit reporting in a Senate race.
House first returns and lead changes stay on the site: 435 seats of them
would bury everything else.

Like the developing issue (election_signals.py), every post is a fixed
template around the state's own figures, never model text, and says
"leads" until the state calls the count official.
"""

import json
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ElectionResultEvent, Race, RaceResult
from app.live_results import sync as er
from app.pipeline.analyze.bluesky_utils import publish_post, strip_hashtags_and_truncate
from app.live_results.signals import holders_word, party_letter, race_label
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

SITE = "https://civitas-research.org"
MAX_POST_CHARS = 240

# The budget. Six an hour is one every ten minutes at the busiest point of
# the night; forty is roughly the flips, official calls and Senate moves of
# a midterm across the covered states. Editorial ceilings, set with the
# account's normal pace in mind (election_bluesky.MAX_POSTS_PER_DAY is 4).
MAX_POSTS_PER_HOUR = 6
MAX_POSTS_PER_ELECTION = 40
# Two posts about one race inside this window read as a ticker.
RACE_COOLDOWN_MINUTES = 20
# An event this old when first considered is history, not news — e.g. the
# sync ran while posting was down. Marked considered, never posted.
MAX_EVENT_AGE_HOURS = 2

CORRECTION = "correction"
_PRIORITY = {CORRECTION: 0, er.FLIP: 1, er.OFFICIAL: 2, er.LEAD_CHANGE: 3, er.ALL_REPORTING: 4}


def _reporting(d: dict) -> str:
    if not d.get("totalUnits"):
        return ""
    share = round(100 * (d.get("reportingUnits") or 0) / d["totalUnits"])
    return f"{d['reportingUnits']:,} of {d['totalUnits']:,} {d['unitLabel']} reporting ({share}%)"


def _who(p: dict | None) -> str:
    if not p:
        return ""
    letter = party_letter(p.get("party"))
    return f"{p['name']} ({letter})" if letter else p["name"]


def _share(p: dict | None) -> str:
    return f"{_who(p)} {p['pct']}%" if p and p.get("pct") is not None else _who(p)


def compose(kind: str, race: Race, d: dict) -> str | None:
    """The post for one event, or None when it has nothing true to say."""
    label = race_label(race)
    leader, runner = d.get("leader"), d.get("runnerUp")
    if not leader:
        return None
    reporting = _reporting(d)
    if kind == CORRECTION:
        text = (f"Update on {label}: {_who(leader)} is ahead again, so the seat no longer shows a change "
                f"of party. {reporting}.")
    elif kind == er.FLIP and d.get("official"):
        text = (f"{label}: {_who(leader)} wins in the official count, taking a seat "
                f"{holders_word(d.get('heldBy'))} held. {_share(leader)}, {_share(runner)}.")
    elif kind == er.FLIP:
        text = (f"{label}: {_who(leader)} leads in a seat {holders_word(d.get('heldBy'))} hold. "
                f"{_share(leader)}, {_share(runner)}. {reporting}. Not final.")
    elif kind == er.OFFICIAL:
        text = f"{label}: the state lists its count as official. {_share(leader)}, {_share(runner)}."
    elif kind == er.LEAD_CHANGE:
        previous = d.get("previousLeader") or {}
        text = (f"{label}: {_who(leader)} moves ahead of {_who(previous)}. "
                f"{_share(leader)}, {_share(runner)}. {reporting}. Not final.")
    elif kind == er.ALL_REPORTING:
        text = (f"{label}: all {d['totalUnits']:,} {d['unitLabel']} have reported. {_share(leader)}, "
                f"{_share(runner)}. Counting can continue after every unit reports.")
    else:
        return None
    return strip_hashtags_and_truncate(text.replace(" .", "."), MAX_POST_CHARS)


def _postable_kind(event: ElectionResultEvent, race: Race, d: dict, flip_posted: bool) -> str | None:
    """Which post this event earns, if any (see the module docstring)."""
    if event.kind == er.FLIP_REVERSED:
        return CORRECTION if flip_posted else None
    if event.kind == er.FLIP:
        return er.FLIP
    senate = race.office == "S"
    if event.kind == er.OFFICIAL and (senate or (d.get("heldBy") and d.get("leader")
                                               and d["leader"].get("party") != d["heldBy"])):
        return er.OFFICIAL
    if not senate:
        return None
    if event.kind == er.LEAD_CHANGE and d.get("totalUnits") and \
            (d.get("reportingUnits") or 0) >= er.FLIP_MIN_REPORTING_SHARE * d["totalUnits"]:
        return er.LEAD_CHANGE
    if event.kind == er.ALL_REPORTING:
        return er.ALL_REPORTING
    return None


def _published_since(db: Session, since, election_date: str | None = None) -> int:
    q = db.query(ElectionResultEvent).filter(
        ElectionResultEvent.bsky_posted.is_(True), ElectionResultEvent.bsky_posted_at >= since,
    )
    if election_date:
        q = q.filter(ElectionResultEvent.election_date == election_date)
    return q.count()


def post_result_updates(db: Session, election_date: str) -> int:
    """Post this pass's worthwhile events within budget. Every event is
    marked considered, posted or not, so none is weighed twice."""
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return 0
    now = utcnow()
    pending = (
        db.query(ElectionResultEvent)
        .filter(ElectionResultEvent.bsky_posted_at.is_(None), ElectionResultEvent.election_date == election_date)
        .all()
    )
    if not pending:
        return 0

    posted_flip_races = {
        rid for (rid,) in db.query(ElectionResultEvent.race_id).filter(
            ElectionResultEvent.kind == er.FLIP, ElectionResultEvent.bsky_posted.is_(True),
            ElectionResultEvent.election_date == election_date,
        )
    }
    recent_races = {
        rid for (rid,) in db.query(ElectionResultEvent.race_id).filter(
            ElectionResultEvent.bsky_posted.is_(True),
            ElectionResultEvent.bsky_posted_at >= now - timedelta(minutes=RACE_COOLDOWN_MINUTES),
        )
    }
    hour_left = MAX_POSTS_PER_HOUR - _published_since(db, now - timedelta(hours=1))
    election_left = MAX_POSTS_PER_ELECTION - _published_since(db, now - timedelta(days=90), election_date)

    queue = []
    for event in pending:
        event.bsky_posted_at = now
        if now - event.created_at > timedelta(hours=MAX_EVENT_AGE_HOURS):
            continue
        race = db.get(Race, event.race_id)
        result = db.get(RaceResult, event.race_id)
        if race is None or result is None:
            continue
        detail = json.loads(event.detail or "{}")
        kind = _postable_kind(event, race, detail, event.race_id in posted_flip_races)
        # A flip that has already reverted by the time it would post is not
        # news; its reversal will not be a correction either.
        if kind == er.FLIP and not er.is_flip(result):
            continue
        if kind:
            queue.append((_PRIORITY[kind], event.created_at, kind, event, race, detail))
    queue.sort(key=lambda q: (q[0], q[1]))

    posted = 0
    for _, _, kind, event, race, detail in queue:
        correction = kind == CORRECTION
        if not correction:
            if hour_left <= 0 or election_left <= 0 or race.id in recent_races:
                continue
        text = compose(kind, race, detail)
        if not text:
            continue
        url = f"{SITE}/elections/states/{race.state}#race-{race.id}"
        if publish_post(text, url, success_msg=f"Posted result update: {race.id} {kind}",
                        error_context=f"result event {event.id}"):
            event.bsky_posted = True
            posted += 1
            recent_races.add(race.id)
            if not correction:
                hour_left -= 1
                election_left -= 1
    db.commit()
    return posted


def counting_is_live() -> bool:
    """While results are on show and a count moved in the last day — when
    the routine race-coverage poster stands down for this one."""
    from app.election_phase import active_election

    election = active_election()
    last = election.last_result_change
    return election.shows_results and last is not None and utcnow() - last < timedelta(days=1)
