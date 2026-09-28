"""Election-night Bluesky posts, from the live count's own events
(live_results/sync.py).

Election night is the one time Civitas should be louder than usual, so
these posts have their own budget and the routine race-coverage poster
(election_bluesky.py) stands down while the count is moving. Louder is
not unbounded: only the events a reader following the night would want
pushed to them are posted, a few an hour, most important first.

What is posted, in priority order:
  0. a correction — a flip this account posted has reverted. Posted
     outside every cap and never counted against one, however late in
     the day: leaving a stale "changing hands" post as the account's last
     word is the one outcome worse than noise.
  1. a seat changing party (FLIP), any chamber.
  2. a count the state calls official — for a Senate race, or a flip.
  3. a Senate lead change with most of the count in.
  4. every unit reporting in a Senate race.
House first returns and lead changes stay on the site: 435 seats of them
would bury everything else.

Like the developing issue (live_results/signals.py), every post is a fixed
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
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS, publish_post, strip_hashtags
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
# sync ran while posting was down, or the budget held it back all night.
# Marked considered, never posted.
MAX_EVENT_AGE_HOURS = 2
# A correction is owed however late, but not forever: a day of failed
# publishes means posting is down, and the account's next word is the
# routine coverage, not a day-old retraction.
MAX_CORRECTION_AGE_HOURS = 24

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


def _shares(d: dict) -> str:
    return ", ".join(x for x in (_share(d.get("leader")), _share(d.get("runnerUp"))) if x)


def _fit(*variants: list[str], budget: int = MAX_POST_CHARS) -> str | None:
    """The first variant that fits a post, sentences joined. Variants run
    richest first and drop figures, never the qualifier: cutting a post to
    length took its LAST sentence first — "Not final." — and could cut a
    share mid-number ("49." for 49.6%). None when nothing fits; a post is
    better missed than wrong."""
    for parts in variants:
        text = " ".join(p if p.endswith(".") else f"{p}." for p in parts if p)
        text = strip_hashtags(text)
        if len(text) <= budget:
            return text
    return None


def compose(kind: str, race: Race, d: dict, budget: int = MAX_POST_CHARS) -> str | None:
    """The post for one event, or None when it has nothing true to say.
    `budget` is the room left once the link is added: publish_post cuts
    anything longer at a sentence boundary, which is the qualifier."""
    label = race_label(race)
    leader = d.get("leader")
    reporting, shares = _reporting(d), _shares(d)
    if kind == CORRECTION:
        # FLIP_REVERSED: the holder's party leads again, or nobody does —
        # an exact tie has no leader to name.
        if leader and leader.get("party") == d.get("heldBy"):
            head = f"Update on {label}: {_who(leader)} is ahead again, so the seat no longer shows a change of party"
        elif not leader and (d.get("votesCounted") or 0) > 0:
            head = f"Update on {label}: the count is now tied, so the seat no longer shows a change of party"
        else:
            head = f"Update on {label}: the count no longer shows the seat changing party"
        return _fit([head, reporting], [head], budget=budget)
    if not leader:
        return None
    holders = holders_word(d.get("heldBy"))
    if kind == er.FLIP and d.get("official"):
        head = f"{label}: {_who(leader)} wins in the official count, taking a seat {holders} held"
        return _fit([head, shares], [head], budget=budget)
    if kind == er.FLIP:
        head = f"{label}: {_who(leader)} leads in a seat {holders} hold"
        return _fit([head, shares, reporting, "Not final"], [head, reporting, "Not final"], [head, "Not final"],
                    budget=budget)
    if kind == er.OFFICIAL:
        head = f"{label}: the state lists its count as official"
        return _fit([head, shares], [head], budget=budget)
    if kind == er.LEAD_CHANGE:
        previous = d.get("previousLeader")
        head = f"{label}: {_who(leader)} moves ahead"
        against = f"{head} of {_who(previous)}" if previous else head
        return _fit([against, shares, reporting, "Not final"], [against, reporting, "Not final"],
                    [against, "Not final"], [head, "Not final"], budget=budget)
    if kind == er.ALL_REPORTING:
        if not d.get("totalUnits"):
            return None  # the count no longer states its units; nothing to say "all" of
        head = f"{label}: all {d['totalUnits']:,} {d['unitLabel']} have reported"
        tail = "Counting can continue after every unit reports"
        return _fit([head, shares, tail], [head, tail], budget=budget)
    return None


def _postable_kind(event: ElectionResultEvent, race: Race, result: RaceResult, d: dict,
                   flip_posted: bool) -> str | None:
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
    # "Most of the count in" is the flip's own bar, county units included.
    if event.kind == er.LEAD_CHANGE and er.count_is_mostly_in(
            d.get("reportingUnits"), d.get("totalUnits"), d.get("unitLabel"), bool(d.get("official")),
            result.first_reported_at, event.created_at):
        return er.LEAD_CHANGE
    if event.kind == er.ALL_REPORTING:
        return er.ALL_REPORTING
    return None


def _published_since(db: Session, since, election_date: str | None = None) -> int:
    """Posts that count against the budget — corrections never do."""
    q = db.query(ElectionResultEvent).filter(
        ElectionResultEvent.bsky_posted.is_(True), ElectionResultEvent.bsky_posted_at >= since,
        ElectionResultEvent.kind != er.FLIP_REVERSED,
    )
    if election_date:
        q = q.filter(ElectionResultEvent.election_date == election_date)
    return q.count()


def _still_true(kind: str, result: RaceResult, d: dict) -> bool:
    """Whether a pending event still describes the count: one held back by
    the budget, or retried after a failed publish, can be overtaken before
    its turn comes — a correction included, when the flip it corrects has
    come back."""
    if kind == er.FLIP:
        return er.is_flip(result)
    if kind == CORRECTION:
        return not er.is_flip(result)
    if kind == er.LEAD_CHANGE:
        now_leading = er._leader(json.loads(result.tallies or "[]"))
        return er._key(now_leading) == er._key(d.get("leader"))
    return True


def _as_of_now(result: RaceResult, d: dict) -> dict:
    """What a post says, from the count as it stands — a post can wait up
    to two hours behind the budget, and its event's own figures would be
    that old — keeping only what the event alone knows (who led before)."""
    return er.event_detail(result, **({"previousLeader": d["previousLeader"]} if d.get("previousLeader") else {}))


def post_result_updates(db: Session, election_date: str) -> int:
    """Post this pass's worthwhile events within budget, most important
    first. An event is marked considered (bsky_posted_at) once it is
    settled — posted, not worth a post, overtaken, or too old. One the
    budget or a race's cooldown holds back stays pending for a later pass:
    throwing it away lost the lowest-ranked flips of a busy hour for good.
    A failed publish ends the pass, and what it didn't reach waits too.
    Every post is worded from the count as it stands (_as_of_now)."""
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

    # A correction is owed where the account's latest flip-or-correction
    # post on a race is a flip. Owing one wherever a flip was ever posted
    # sent a second "no longer shows a change of party" after the first,
    # in a race swinging around the line.
    last_claim: dict[str, str] = {}
    for rid, kind in (
        db.query(ElectionResultEvent.race_id, ElectionResultEvent.kind)
        .filter(ElectionResultEvent.bsky_posted.is_(True), ElectionResultEvent.election_date == election_date,
                ElectionResultEvent.kind.in_((er.FLIP, er.FLIP_REVERSED)))
        .order_by(ElectionResultEvent.bsky_posted_at, ElectionResultEvent.id)
    ):
        last_claim[rid] = kind
    posted_flip_races = {rid for rid, kind in last_claim.items() if kind == er.FLIP}
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
        race = db.get(Race, event.race_id)
        result = db.get(RaceResult, event.race_id)
        if race is None or result is None:
            event.bsky_posted_at = now
            continue
        detail = json.loads(event.detail or "{}")
        kind = _postable_kind(event, race, result, detail, event.race_id in posted_flip_races)
        max_age = MAX_CORRECTION_AGE_HOURS if kind == CORRECTION else MAX_EVENT_AGE_HOURS
        if kind is None or now - event.created_at > timedelta(hours=max_age) \
                or not _still_true(kind, result, detail):
            event.bsky_posted_at = now
            continue
        queue.append((_PRIORITY[kind], event.created_at, kind, event, race, detail))
    queue.sort(key=lambda q: (q[0], q[1]))
    # Settle what was decided before any network call: a write transaction
    # held open across logins and sends blocked every other writer.
    db.commit()

    posted = 0
    for priority, created, kind, event, race, detail in queue:
        if event.bsky_posted_at is not None:
            continue  # overtaken by a post earlier in this pass
        correction = kind == CORRECTION
        if not correction and (hour_left <= 0 or election_left <= 0 or race.id in recent_races):
            continue  # held for a later pass
        url = f"{SITE}/elections/states/{race.state}#race-{race.id}"
        room = min(MAX_POST_CHARS, BSKY_MAX_CHARS - len(url) - 1)  # 1 for the space before the link
        text = compose(kind, race, _as_of_now(db.get(RaceResult, race.id), detail), budget=room)
        if not text:
            event.bsky_posted_at = now
            db.commit()
            continue
        if not publish_post(text, url, success_msg=f"Posted result update: {race.id} {kind}",
                            error_context=f"result event {event.id}"):
            # Posting is down or refusing us: stop the pass. Every attempt
            # is a fresh login, and retrying the whole queue every five
            # minutes ran into Bluesky's session limits (about 30 logins
            # per 5 minutes) — which would lock out the routine poster too.
            # The events stay pending for the next pass.
            break
        event.bsky_posted = True
        event.bsky_posted_at = now
        posted += 1
        recent_races.add(race.id)
        if not correction:
            hour_left -= 1
            election_left -= 1
        # The race's post just now says where it stands; its older, lesser
        # events waiting behind it would repeat that, stale, after the
        # cooldown.
        for p2, c2, _, other, _, _ in queue:
            if other.race_id == race.id and other.bsky_posted_at is None and p2 >= priority and c2 <= created:
                other.bsky_posted_at = now
        # Committed per post: a failure later in the pass rolled back
        # posts already published, and the next pass sent them again.
        db.commit()
    db.commit()
    return posted


def counting_is_live(db: Session | None = None) -> bool:
    """While results are on show and a count moved in the last day — when
    the routine race-coverage poster stands down for this one."""
    from app.election_phase import active_election

    election = active_election(db)
    last = election.last_result_change
    return election.shows_results and last is not None and utcnow() - last < timedelta(days=1)
