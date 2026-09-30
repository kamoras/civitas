"""Election-night posts, from the live count's own events
(live_results/sync.py). Each is published like every other Civitas post
(app/broadcast.publish): stored first, as an entry in the Elections feed,
then sent to Bluesky when an account is configured. The bsky_posted /
bsky_posted_at columns on ElectionResultEvent keep their names and mean
"published", on any channel.

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
"leads" — "not final" until the state lists its count as official, and
never "wins" after: Civitas never calls a race, and an official count's
leader can still face a runoff, a recount or a court.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import broadcast
from app.models import BroadcastPost, ElectionResultEvent, Race, RaceResult
from app.live_results import sync as er
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS, strip_hashtags
from app.live_results.signals import holders_word, party_letter, race_label, reporting_line
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

SITE = broadcast.SITE_URL
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
# sync was down, or the budget held it back all night. Marked considered,
# never posted.
MAX_EVENT_AGE_HOURS = 2
# A correction is owed however late, but not forever: a day on, the
# account's next word is the routine coverage, not a day-old retraction.
MAX_CORRECTION_AGE_HOURS = 24

CORRECTION = "correction"
# The feed entry's title for each kind of post (_title).
_TITLES = {
    CORRECTION: "no longer shows a change of party",
    er.FLIP: "another party leads in the count, not final",
    er.OFFICIAL: "count listed as official",
    er.LEAD_CHANGE: "lead changes",
    er.ALL_REPORTING: "every unit reporting",
}
_PRIORITY = {CORRECTION: 0, er.FLIP: 1, er.OFFICIAL: 2, er.LEAD_CHANGE: 3, er.ALL_REPORTING: 4}


def _title(kind: str, race: Race, d: dict) -> str:
    """The feed title, following the same official branch as compose()."""
    if kind == er.FLIP and d.get("official"):
        return f"{race_label(race)}: another party leads in the count listed as official"
    return f"{race_label(race)}: {_TITLES[kind]}"


def _subject(election_date: str, race_id: str, kind: str) -> str:
    """Keyed by election, so a runoff weeks later has its own budget."""
    return f"result:{election_date}:{race_id}:{kind}"


def _reporting(d: dict) -> str:
    return reporting_line(d)


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
        # Only these two undo a flip (sync.lead_is_back). Anything else —
        # the same challenger ahead on a count that merely dipped below the
        # bar for raising one — has nothing to correct, and "no longer
        # shows the seat changing party" said then was false.
        if leader and d.get("heldBy") and leader.get("party") == d.get("heldBy"):
            head = f"Update on {label}: {_who(leader)} is ahead again, so the seat no longer shows a change of party"
        elif not leader and (d.get("votesCounted") or 0) > 0:
            head = f"Update on {label}: the count is now tied, so the seat no longer shows a change of party"
        else:
            return None
        return _fit([head, reporting], [head], budget=budget)
    if not leader:
        return None
    holders = holders_word(d.get("heldBy"))
    if kind == er.FLIP and d.get("official"):
        # "Leads", never "wins": the state listing its count as official is
        # not a result (a Georgia general short of a majority goes to a
        # runoff), and Civitas never calls a race.
        head = f"{label}: {_who(leader)} leads in the count the state lists as official, in a seat {holders} hold"
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


@dataclass
class _History:
    """What this election's result posts have already said, read from the
    published posts (broadcast_posts) — the record of what was actually
    said — rather than the events, which record what the count did: an
    event can be held back, skipped or refused a post, and a count's events
    can be raised again (a data reset outside the results window wipes
    them; database.reset_all_data keeps them inside it)."""

    # race -> (kind, Bluesky status) of its latest flip-or-correction post
    last_claim: dict[str, tuple[str, str]] = field(default_factory=dict)
    said: set[tuple[str, str]] = field(default_factory=set)  # (race, kind)
    recent: set[str] = field(default_factory=set)  # races posted about in the cooldown
    last_hour: int = 0  # posts that spend the budget — corrections never do
    this_election: int = 0


def _history(db: Session, election_date: str, now: datetime) -> _History:
    h = _History()
    rows = (
        db.query(BroadcastPost.subject, BroadcastPost.published_at, BroadcastPost.bsky_status)
        .filter(BroadcastPost.kind == "result", BroadcastPost.subject.startswith(f"result:{election_date}:"))
        .order_by(BroadcastPost.published_at, BroadcastPost.id)
    )
    for subject, published_at, bsky_status in rows:
        _, _, race_id, kind = subject.split(":", 3)
        h.said.add((race_id, kind))
        if kind in (er.FLIP, CORRECTION):
            h.last_claim[race_id] = (kind, bsky_status)
        if published_at >= now - timedelta(minutes=RACE_COOLDOWN_MINUTES):
            h.recent.add(race_id)
        if kind != CORRECTION:
            h.this_election += 1
            if published_at >= now - timedelta(hours=1):
                h.last_hour += 1
    return h


def _already_said(kind: str, race_id: str, h: _History) -> bool:
    """An event the account has already put out: a flip while its flip is
    still the standing claim, or a count's one official or all-reporting
    moment — however many times the count raises them."""
    if kind == er.FLIP:
        return h.last_claim.get(race_id, ("", ""))[0] == er.FLIP
    return kind in (er.OFFICIAL, er.ALL_REPORTING) and (race_id, kind) in h.said


def _still_true(kind: str, result: RaceResult, d: dict) -> bool:
    """Whether a pending event still describes the count: one held back by
    the budget can be overtaken before its turn comes — a correction
    included, when the flip it corrects has come back."""
    if kind == er.FLIP:
        # The challenger still ahead: the bar for RAISING a flip was met when
        # the event was, and a count dipping below it since (figures missing
        # for a poll, units added) doesn't unsay it — dropping the post then
        # lost it for good, as the flip is never raised twice.
        return er.challenger_leads(result)
    if kind == CORRECTION:
        # The lead itself back with the holder's party (or tied) — not
        # merely the count dipping below the bar for raising a flip.
        return er.lead_is_back(result)
    if kind == er.LEAD_CHANGE:
        now_leading = er._leader(json.loads(result.tallies or "[]"))
        return er._key(now_leading) == er._key(d.get("leader"))
    # A post is worded from the count as it stands (_as_of_now), so what it
    # announces has to still be so: a held "official" post after the flag
    # switched back off said "official", and an all-reporting post after
    # units were added said "all 105 precincts have reported" at 100 of 105.
    if kind == er.OFFICIAL:
        return bool(result.official)
    if kind == er.ALL_REPORTING:
        return bool(result.total_units) and result.reporting_units == result.total_units
    return True


# Kinds the sync raises once per count (announced_state): one that is not
# true right now — the official flag off for a poll, units added — is held
# rather than settled, as settling it lost the post for good when the count
# came back within the age cap.
_RAISED_ONCE = frozenset({er.OFFICIAL, er.ALL_REPORTING})


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
    Every post is worded from the count as it stands (_as_of_now).

    Posts are published whether or not Bluesky is configured: the feed is
    the record. A send Bluesky refuses is not sent again (broadcast.
    NO_RETRY_KINDS): resent later, word for word, it could describe a count
    that has since moved or reverted. The next event's post says where the
    count stands. A correction goes to Bluesky only when the flip it
    corrects did; the feed always gets it."""
    now = utcnow()
    h = _history(db, election_date, now)
    pending = (
        db.query(ElectionResultEvent)
        .filter(ElectionResultEvent.bsky_posted_at.is_(None), ElectionResultEvent.election_date == election_date)
        .all()
    )
    if not pending:
        db.commit()
        return 0

    # A correction is owed where the account's latest flip-or-correction
    # post on a race is a flip. Owing one wherever a flip was ever posted
    # sent a second "no longer shows a change of party" after the first,
    # in a race swinging around the line.
    posted_flip_races = {rid for rid, (kind, _) in h.last_claim.items() if kind == er.FLIP}
    recent_races = h.recent
    hour_left = MAX_POSTS_PER_HOUR - h.last_hour
    election_left = MAX_POSTS_PER_ELECTION - h.this_election

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
                or _already_said(kind, race.id, h):
            event.bsky_posted_at = now
            continue
        if not _still_true(kind, result, detail):
            if kind in _RAISED_ONCE:
                continue  # held: the count raises it once, and it may be true again within the age cap
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
        details = _as_of_now(db.get(RaceResult, race.id), detail)
        text = compose(kind, race, details, budget=room)
        if not text:
            event.bsky_posted_at = now
            db.commit()
            continue
        event.bsky_posted = True
        event.bsky_posted_at = now
        # The race's post says where it stands; its older, lesser events
        # waiting behind it would repeat that, stale, after the cooldown.
        for p2, c2, _, other, _, _ in queue:
            if other.race_id == race.id and other.bsky_posted_at is None and p2 >= priority and c2 <= created:
                other.bsky_posted_at = now
        # publish() commits these marks in the same transaction that stores
        # the post, before any network call: a crash can't publish an event
        # twice, or mark one published that never was.
        # A correction of a flip Bluesky never showed would read, there, as
        # retracting something the account never said.
        # "sending" is a send that may have gone out; "pending" on a result
        # post is one never tried (they are never retried), so not shown.
        to_bluesky = not correction or h.last_claim.get(race.id, ("", ""))[1] in ("sending", "sent")
        post = broadcast.publish(
            db, kind="result", subject=_subject(election_date, race.id, kind), title=_title(kind, race, details),
            text=text, url=url, state=race.state, bluesky=to_bluesky,
        )
        h.said.add((race.id, kind))
        if kind in (er.FLIP, CORRECTION):
            h.last_claim[race.id] = (kind, post.bsky_status)
        posted += 1
        recent_races.add(race.id)
        if not correction:
            hour_left -= 1
            election_left -= 1
    db.commit()
    return posted


def counting_is_live(db: Session | None = None) -> bool:
    """While results are on show and a count moved in the last day — when
    the routine race-coverage poster stands down for this one."""
    from app.election_phase import active_election

    election = active_election(db)
    last = election.last_result_change
    return election.shows_results and last is not None and utcnow() - last < timedelta(days=1)
