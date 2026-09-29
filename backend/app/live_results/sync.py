"""Election-night results: read each covered state's live count
(fetch/election_results.py), store it per Race (RaceResult), record what
changed as ElectionResultEvents, and open a DEVELOPING Action Center issue
when a seat is changing party (live_results/signals.py).

Runs on its own clock from election day until the results window closes
(scheduler._election_results_sync, election_phase). Every state is read at
once and stored on its own: one state's feed being slow, down or refused
leaves every other state's count moving, and how each read went is kept
(LiveResultRead) so the page can say which absence it is.

What an event is, and what it is not. Events are observed changes in the
source's own numbers — first votes counted, a different candidate on top,
every reporting unit in, the source marking its count official. None of
them is a call: the page words each one from a template around the
source's figures (the same "the page may only say what the count says"
rule as RaceResult).
"""

import asyncio
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.models import (
    Candidate, ElectionResultEvent, LiveResultRead, Race, RaceResult, Representative, Senator,
)
from app.pipeline.candidate_dedup import normalized_surname
from app.ops_alerts import send_ops_alert
from app.pipeline.fetch.election_results import (
    ContestCount,
    StateCount,
    UntrustedCount,
    fetch_state_count,
    live_results_states,
)
from app.pipeline.fetch.poll_close import last_poll_close, polls_closed
from app.pipeline.fetch.state_candidates import _match_candidate, _race_id_for
from app.pipeline.fetch.state_candidates_common import PARTY_CODE_MAP, fec_party, last_name_matches, surname
from app.pipeline.run_tracker import PipelineRunTracker
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# In-process overlap guard for the five-minute sync (scheduler.py), behind
# the RESULTS_SYNC lease (lease.run_tracked).
_results_tracker = PipelineRunTracker()


def results_tracker() -> PipelineRunTracker:
    return _results_tracker


FIRST_RETURNS = "first_returns"
LEAD_CHANGE = "lead_change"
ALL_REPORTING = "all_reporting"
OFFICIAL = "official"
FLIP = "flip"
FLIP_REVERSED = "flip_reversed"

# A seat is reported as changing party only once at least this share of
# its reporting units is in (or the source calls its count official): the
# first precincts to report are routinely unrepresentative — mail ballots
# counted first, small rural units before a city — and a "flip" on 3% of
# precincts is noise dressed as news. An editorial floor ("most of the
# count is in"), not a calibrated value; it gates only the flip signal,
# never what the page shows.
FLIP_MIN_REPORTING_SHARE = 0.5
# Where the units are places rather than precincts — counties, or a
# state's cities and towns — "reporting" means a place has posted its first
# batch, not that its count is in: Colorado's Clarity feed shows 2 of
# CO-8's 3 counties reporting within minutes of the first dump, and its
# counties keep counting for days. Half the places says nothing there, so
# such a flip needs every place in AND this long since the race's first
# votes, or the source's official flag. Editorial, like the share above:
# it keeps a flip off the first dumps of the evening (a Colorado race with
# first votes at 7:30 PM MT qualifies at 1:30 AM at the earliest), not off
# election night — the wording everywhere is "leads", "not final".
COUNTY_FLIP_SETTLE = timedelta(hours=6)

_MEMBER_PARTY = {"D": "DEM", "R": "REP", "I": "IND"}


def _party_group(code: str | None) -> str | None:
    """A partyGroup (DEM/REP/IND/LIB/...), from an FEC code or a
    normalize_party letter. The letter map is asked first: fec_party
    passes a code it doesn't alias straight through, so "R" came back as
    "R" — never equal to a holder's "REP", which read every unmatched
    Republican leader as a seat changing hands."""
    if not code:
        return None
    return PARTY_CODE_MAP.get(code) or fec_party(code) or code


_redrawn_cache: dict | None = None


def redrawn_states(cycle: int) -> set[str]:
    """States voting in `cycle` on a map other than the one sitting members
    were elected on (app/data/redrawn_congressional_maps.json — the same
    list the district crosswalk and outlines are built from)."""
    global _redrawn_cache
    if _redrawn_cache is None:
        path = Path(__file__).resolve().parent.parent / "data" / "redrawn_congressional_maps.json"
        _redrawn_cache = json.loads(path.read_text())
    entries = _redrawn_cache.get("cycles", {}).get(str(cycle), {})
    return {st for st, e in entries.items() if e.get("map") != "CD119"}


def seat_holder_party(db: Session, race: Race) -> str | None:
    """The party that held this seat going into the election, or None when
    it can't be known without guessing.

    House: the district's sitting representative — unless the state's map
    was redrawn for this cycle (redrawn_states), when the district of that
    number is a different district and its member held something else.
    Senate: the senator the
    race's incumbent candidate is (a state has two, and nothing stored says
    which seat is up), else — an open seat — the state's senators' party
    only when both share it."""
    if race.office == "H":
        if race.state in redrawn_states(race.cycle_year):
            return None  # the same number names a different district now
        rep = (
            db.query(Representative)
            .filter(Representative.state == race.state, Representative.district == (race.district or 0),
                    Representative.is_current.is_(True))
            .first()
        )
        return _MEMBER_PARTY.get(rep.party) if rep else None
    senators = db.query(Senator).filter(Senator.state == race.state, Senator.is_current.is_(True)).all()
    for cand in race.candidates:
        if cand.incumbent_challenge != "I":
            continue
        last = normalized_surname(cand.name)
        matches = [s for s in senators if last and last_name_matches(last, s.name)]
        if len(matches) == 1:
            return _MEMBER_PARTY.get(matches[0].party)
    parties = {s.party for s in senators}
    if len(senators) == 2 and len(parties) == 1:
        return _MEMBER_PARTY.get(parties.pop())
    return None


def _tallies(race: Race, contest: ContestCount) -> list[dict]:
    """The contest's candidates, most votes first, each matched to one of
    the race's Candidate rows where the shared ballot matcher finds exactly
    one — the same matcher that confirms nominees, so a name that confirmed
    a nominee is the name that carries their count."""
    rows = []
    for name, party, votes in contest.candidates:
        match: Candidate | None = None
        last = surname(name)
        if last:
            match = _match_candidate(list(race.candidates), last, party or "", name)
        rows.append({
            # Who the feed says this is, as it prints them: what a row is
            # compared by between polls (_key), since the display name and
            # party below change when a Candidate row is matched mid-count.
            "sourceName": name,
            "sourceParty": party,
            # The matched candidate's name as the state's ballot prints it
            # where there is one: a results feed can carry an honorific
            # (Arkansas: "Congressman Steve Womack") the ballot doesn't.
            "name": (match.ballot_name if match and match.ballot_name else name),
            "party": _party_group(match.party) if match else _party_group(party),
            "votes": votes,
            "candidateId": match.id if match else None,
        })
    rows.sort(key=lambda r: r["votes"], reverse=True)
    return rows


def _leader(tallies: list[dict]) -> dict | None:
    """Who is ahead, or None before any votes and on an exact tie."""
    if not tallies or tallies[0]["votes"] <= 0:
        return None
    if len(tallies) > 1 and tallies[1]["votes"] == tallies[0]["votes"]:
        return None
    return tallies[0]


def _key(row: dict | None):
    """Who a tally row is, as the source prints them: name and party. Not
    the matched candidateId — a Candidate row appearing between polls (the
    ballot sync runs through the count) re-keyed the same leader and read
    as "Dana Smith moves ahead of Dana Smith"."""
    if not row:
        return None
    if row.get("sourceName"):
        name, party = row["sourceName"], row.get("sourceParty")
    else:
        name, party = row.get("name"), row.get("party")
    return (" ".join(str(name or "").split()).casefold(), party)


def flip_qualifies(result: RaceResult, now: datetime | None = None) -> bool:
    """Enough of the count is in to report a seat changing party. An
    official flag counts only where the state gives no reporting figure,
    or where its units are counties (below): it is known to be wrong in the
    other direction (Enhanced Voting's stayed false a month after Utah's
    canvass, verified 2026-09-28), and a flag claiming "official" beside a
    count half in is the one to doubt.

    Place units (counties, towns): every place in and COUNTY_FLIP_SETTLE
    since the first votes (first_reported_at), or the official flag — see
    the constant."""
    return count_is_mostly_in(
        result.reporting_units, result.total_units, result.unit_label, bool(result.official),
        result.first_reported_at, now or utcnow(),
    )


def count_is_mostly_in(reporting: int | None, total: int | None, unit_label: str | None, official: bool,
                       first_votes_at: datetime | None, now: datetime) -> bool:
    """flip_qualifies' rule on bare figures — so a post can apply it to the
    figures its event recorded, not to wherever the count is now."""
    if not total or reporting is None:
        return official
    if (unit_label or "precincts") != "precincts":
        if official:
            return True
        settled = first_votes_at is not None and now - first_votes_at >= COUNTY_FLIP_SETTLE
        return reporting >= total and settled
    return reporting >= FLIP_MIN_REPORTING_SHARE * total


def is_flip(result: RaceResult) -> bool:
    """The leader is from a different party than the seat's holder, with
    enough of the count in to say so (flip_qualifies)."""
    leader = _leader(json.loads(result.tallies or "[]"))
    return bool(
        leader and result.held_by_party and leader.get("party")
        and leader["party"] != result.held_by_party and flip_qualifies(result)
    )


def event_detail(result: RaceResult, **extra) -> dict:
    tallies = json.loads(result.tallies or "[]")
    counted = result.votes_counted or 0

    def person(row):
        if not row:
            return None
        return {
            "name": row["name"], "party": row.get("party"), "votes": row["votes"],
            "pct": round(100 * row["votes"] / counted, 1) if counted else None,
            "candidateId": row.get("candidateId"),
            "sourceName": row.get("sourceName"),
            "sourceParty": row.get("sourceParty"),
        }

    return {
        "leader": person(_leader(tallies)),
        "runnerUp": person(tallies[1]) if len(tallies) > 1 else None,
        "votesCounted": counted,
        "reportingUnits": result.reporting_units,
        "totalUnits": result.total_units,
        "unitLabel": result.unit_label,
        "heldBy": result.held_by_party,
        "official": result.official,
        **extra,
    }


def _event(db: Session, result: RaceResult, kind: str, **extra) -> ElectionResultEvent:
    event = ElectionResultEvent(
        race_id=result.race_id, election_date=result.election_date, kind=kind,
        detail=json.dumps(event_detail(result, **extra)), created_at=utcnow(),
    )
    db.add(event)
    return event


def contest_is_sane(contest: ContestCount) -> bool:
    """A count that cannot be true as printed: more units reporting than
    exist, a negative vote, or candidates outnumbering the stated total."""
    if contest.total_units is not None and contest.reporting_units is not None:
        if contest.reporting_units < 0 or contest.reporting_units > contest.total_units:
            return False
    if any(v < 0 for _, _, v in contest.candidates):
        return False
    if contest.total_votes is not None and sum(v for _, _, v in contest.candidates) > contest.total_votes:
        return False
    return True


def announced_state(db: Session, race_id: str, election_date: str) -> dict:
    """What the count has already SAID about this race, read from its
    events — the baseline every new event is measured against. Not the last
    stored row: a held poll (apply_count) stores the state's figures but
    announces nothing, and diffing against it would swallow whatever first
    appeared there for good (an official flag published alongside a
    correction that lowered the total was never announced)."""
    rows = (
        db.query(ElectionResultEvent.kind, ElectionResultEvent.detail)
        .filter(ElectionResultEvent.race_id == race_id, ElectionResultEvent.election_date == election_date)
        .order_by(ElectionResultEvent.created_at, ElectionResultEvent.id)
        .all()
    )
    state = {"any": bool(rows), "leader": None, "official": False, "all_in": False, "flip": False}
    for kind, detail in rows:
        leader = (json.loads(detail or "{}") or {}).get("leader")
        if leader:
            state["leader"] = leader
        if kind == OFFICIAL:
            state["official"] = True
        elif kind == ALL_REPORTING:
            state["all_in"] = True
        elif kind == FLIP:
            state["flip"] = True
        elif kind == FLIP_REVERSED:
            state["flip"] = False
    return state


@dataclass
class Applied:
    """What storing one race's count produced."""
    result: RaceResult
    events: list[ElectionResultEvent] = field(default_factory=list)
    # True when the poll said nothing (see apply_count) — a returned flag,
    # not an attribute on the row: the session holds clean rows weakly, so
    # anything set on one may not survive to the caller's next db.get.
    held: bool = False

    @property
    def new_flip(self) -> bool:
        """This poll announced a seat changing party."""
        return any(e.kind == FLIP for e in self.events)


def apply_count(
    db: Session, race: Race, contest: ContestCount, state: StateCount, election_day: date,
) -> Applied:
    """Store one race's count and return what it produced.

    A count whose total went DOWN is stored — it is what the state now
    shows — but announces nothing that poll: totals fall when a county
    pulls a bad upload (North Dakota removed 196 test ballots from Mercer
    County's 2024 election-night count), and a lead or flip "caused" by a
    correction mid-flight is not news. The next poll announces whatever
    the corrected count holds. A held result is flagged for the caller,
    which keeps it away from the Action Center too."""
    now = utcnow()
    tallies = _tallies(race, contest)
    counted = contest.votes_counted
    result = db.get(RaceResult, race.id)
    if result is not None and result.election_date != election_day.isoformat():
        # An earlier election's count for the same race id (a runoff read
        # later, a re-used id): start over rather than diff across elections.
        db.query(ElectionResultEvent).filter(ElectionResultEvent.race_id == race.id).delete()
        db.delete(result)
        db.flush()
        result = None
    before = None
    if result is None:
        result = RaceResult(
            race_id=race.id, election_date=election_day.isoformat(), source_name=state.source_name,
            held_by_party=seat_holder_party(db, race), first_reported_at=now, last_change_at=now,
            votes_counted=0, tallies="[]", official=False,
        )
        db.add(result)
    else:
        # The stored row only decides whether the figures changed and
        # whether the total fell; what to ANNOUNCE is measured against
        # announced_state.
        before = {"tallies": json.loads(result.tallies or "[]"), "counted": result.votes_counted or 0}

    old_tallies = before["tallies"] if before else []
    old_counted = before["counted"] if before else 0
    if counted > 0 and old_counted == 0:
        # first_reported_at is when votes were first counted, not when the
        # row was made: a feed read at poll close lists every race at zero.
        result.first_reported_at = now
    if before is None or [(_key(t), t["votes"]) for t in tallies] != [(_key(t), t["votes"]) for t in old_tallies] \
            or counted != old_counted:
        result.last_change_at = now
    result.tallies = json.dumps(tallies)
    result.votes_counted = counted
    result.reporting_units = contest.reporting_units
    result.total_units = contest.total_units
    result.unit_label = state.unit_label
    result.official = state.official
    result.source_name = state.source_name
    result.source_url = state.page_url
    result.source_updated_at = state.source_updated
    result.source_version = state.source_version
    result.fetched_at = now

    if before and counted < old_counted:
        logger.warning("%s: votes counted fell %d -> %d; stored, nothing announced this poll",
                       race.id, old_counted, counted)
        return Applied(result, held=True)

    said = announced_state(db, race.id, result.election_date)
    kinds: list[tuple[str, dict]] = []
    old_leader, new_leader = said["leader"], _leader(tallies)
    if not said["any"] and counted > 0:
        kinds.append((FIRST_RETURNS, {}))
    elif old_leader and new_leader and _key(old_leader) != _key(new_leader):
        kinds.append((LEAD_CHANGE, {"previousLeader": {
            "name": old_leader["name"], "party": old_leader.get("party"),
        }}))
    if result.total_units and result.reporting_units == result.total_units and not said["all_in"] and counted > 0:
        kinds.append((ALL_REPORTING, {}))
    if result.official and not said["official"]:
        kinds.append((OFFICIAL, {}))
    flipped = is_flip(result)
    if flipped and not said["flip"]:
        kinds.append((FLIP, {}))
    elif said["flip"] and not flipped:
        kinds.append((FLIP_REVERSED, {}))
    # One poll, one story per race: a flip already says who leads, and a
    # race whose first returns arrive complete is simply "all in" — without
    # this the feed told each of those twice, side by side.
    present = {k for k, _ in kinds}
    if FLIP in present or FLIP_REVERSED in present:
        kinds = [(k, x) for k, x in kinds if k != LEAD_CHANGE]
    if ALL_REPORTING in present or FLIP in present:
        kinds = [(k, x) for k, x in kinds if k != FIRST_RETURNS]
    events = [_event(db, result, kind, **extra) for kind, extra in kinds]
    return Applied(result, events)


def _contest_race(db: Session, cycle: int, state: str, contest: ContestCount) -> Race | None:
    """The race a contest's count belongs to. A special election (the rest
    of a term) goes only to a race kept for it: House seats have none, and
    a Senate special with no "-SPECIAL" race on file has none either.
    Sending it to the regular race put two contests on one seat, and the
    sync, refusing to guess between them, dropped the regular count too."""
    if contest.is_special:
        if contest.office != "S":
            return None
        return db.get(Race, f"{cycle}-SEN-{state}-SPECIAL")
    return db.get(Race, _race_id_for(db, cycle, state, contest.office, contest.district))


# A source stamping its count this far in the future has a broken clock or
# is not what it says; a few minutes' skew between servers is normal.
_FUTURE_SKEW = timedelta(hours=1)


def _stored(db: Session, state: str, election_day: date):
    return (
        db.query(RaceResult.source_updated_at, RaceResult.source_version)
        .join(Race, Race.id == RaceResult.race_id)
        .filter(Race.state == state, RaceResult.election_date == election_day.isoformat())
        .all()
    )


def freshness_problem(db: Session, state: str, election_day: date, count: StateCount) -> str | None:
    """Why this read must not replace what is stored: a source that has
    gone BACKWARDS (an older copy from a cache, CDN or mirror), or one
    dated in the future. None when it is fine to store. The stamp and the
    version are each checked on their own, so a source that gives only one
    of them is still protected by it."""
    now = utcnow()
    if count.source_updated and count.source_updated > now + _FUTURE_SKEW:
        return f"source is stamped {count.source_updated.isoformat()}, in the future"
    stored = _stored(db, state, election_day)
    stamps = [u for u, _ in stored if u is not None]
    if count.source_updated is not None and stamps and count.source_updated < max(stamps):
        return f"source went back from {max(stamps).isoformat()} to {count.source_updated.isoformat()}"
    versions = [int(v) for _, v in stored if (v or "").isdigit()]
    if (count.source_version or "").isdigit() and versions and int(count.source_version) < max(versions):
        return f"source version went back from {max(versions)} to {count.source_version}"
    return None


def refusal_kind(reason: str | None) -> str:
    """A refusal's reason with its figures taken out ("ND election 346 is a
    preview" and "… 348 is a preview" are one kind), hashed short for an
    alert's dedupe key."""
    import hashlib
    import re

    return hashlib.sha256(re.sub(r"\d+", "#", reason or "").encode()).hexdigest()[:10]


@dataclass
class StateRead:
    """One state's feed, read — before anything touches the database, so
    every state can be read at once (sync_live_results)."""
    status: str  # "read", "polls_open", "untrusted", "unavailable", "failed"
    count: StateCount | None = None
    reason: str | None = None


async def read_state(client: httpx.AsyncClient, state: str, election_day: date) -> StateRead:
    if not polls_closed(state, election_day, utcnow()):
        # Nothing is read, stored or said before a state's last polls
        # close (fetch/poll_close.py).
        return StateRead("polls_open")
    try:
        count = await fetch_state_count(client, state, election_day)
    except UntrustedCount as refused:
        return StateRead("untrusted", reason=str(refused))
    except Exception as exc:  # one state's broken feed never stops the pass
        logger.exception("Live results read failed for %s", state)
        return StateRead("failed", reason=type(exc).__name__)
    if count is None:
        return StateRead("unavailable")
    return StateRead("read", count=count)


def _record_read(db: Session, state: str, election_day: date, status: str) -> None:
    now = utcnow()
    row = db.get(LiveResultRead, (state, election_day.isoformat()))
    if row is None:
        row = LiveResultRead(state=state, election_date=election_day.isoformat(), status=status)
        db.add(row)
        db.flush()  # found by the next read's db.get, even before a commit
    row.status = status
    row.checked_at = now
    if status == "ok":
        row.last_ok_at = now


def apply_state(db: Session, state: str, election_day: date, read: StateRead) -> dict:
    """Store what one state's read says, and record how the read went."""
    outcome = _apply_state(db, state, election_day, read)
    _record_read(db, state, election_day, outcome["status"])
    return outcome


def _apply_state(db: Session, state: str, election_day: date, read: StateRead) -> dict:
    if read.status == "polls_open":
        return {"status": "polls_open", "pollsClose": last_poll_close(state, election_day).isoformat() + "Z"}
    if read.status == "untrusted":
        logger.warning("Live results refused for %s: %s", state, read.reason)
        previous = db.get(LiveResultRead, (state, election_day.isoformat()))
        # Alert on the second refusal in a row, not the first: a feed
        # republished mid-read (Tally's version changing between calls) is
        # refused once and reads cleanly five minutes later. Keyed by the
        # kind of refusal, not just the state and day — one transient
        # refusal used to spend the only alert, so a later persistent one
        # (demo mode, two same-day generals) was never raised.
        if previous is not None and previous.status == "untrusted":
            send_ops_alert(
                f"Live results: {state} feed refused",
                f"{read.reason}. Refused on consecutive reads; nothing from it was stored or published, and "
                "the page keeps the last trusted count.",
                dedupe_key=f"results-untrusted-{state}-{election_day.isoformat()}-{refusal_kind(read.reason)}",
            )
        return {"status": "untrusted", "reason": read.reason}
    if read.status != "read":
        return {"status": read.status, **({"reason": read.reason} if read.reason else {})}
    count = read.count
    problem = freshness_problem(db, state, election_day, count)
    if problem:
        logger.warning("Live results for %s not stored: %s", state, problem)
        return {"status": "stale", "reason": problem}
    by_race: dict[str, list[ContestCount]] = {}
    for contest in count.contests:
        if not contest_is_sane(contest):
            logger.warning("%s: an impossible count for %s %s dropped", state, contest.office, contest.district)
            continue
        race = _contest_race(db, election_day.year, state, contest)
        if race is not None:
            by_race.setdefault(race.id, []).append(contest)
    results: list[Applied] = []
    events = 0
    for race_id, contests in by_race.items():
        if len(contests) > 1:
            # Two contests the feed labels the same seat — a state electing
            # both Senate seats with neither marked special. Pairing either
            # with the race would be a guess.
            logger.warning("%s: %d contests map to %s — skipped", state, len(contests), race_id)
            continue
        applied = apply_count(db, db.get(Race, race_id), contests[0], count, election_day)
        events += len(applied.events)
        if not applied.held:
            results.append(applied)
    db.flush()
    return {
        "status": "ok", "races": len(results), "contests": len(count.contests),
        "events": events, "results": results,
    }


async def sync_state(db: Session, client: httpx.AsyncClient, state: str, election_day: date) -> dict:
    """Read and store one state (sync_live_results does every state)."""
    return apply_state(db, state, election_day, await read_state(client, state, election_day))


# States read at once. The feeds sit on a handful of vendors' hosts, and
# each read is a few requests; eight keeps any one host's share modest
# while a slow or down feed (up to ~90 s with retries) no longer holds up
# the states after it.
MAX_CONCURRENT_READS = 8


async def sync_live_results(db: Session, client: httpx.AsyncClient, election_day: date) -> dict:
    """One pass over every covered state: every feed read at once, then
    each state stored and committed on its own, so one state's feed being
    slow, down or refused leaves every other state's count moving."""
    from app.live_results.signals import update_developing_issues

    states = sorted(live_results_states())
    gate = asyncio.Semaphore(MAX_CONCURRENT_READS)

    async def one(state: str) -> StateRead:
        async with gate:
            return await read_state(client, state, election_day)

    reads = await asyncio.gather(*(one(state) for state in states))
    summary: dict[str, dict] = {}
    for state, read in zip(states, reads):
        try:
            outcome = apply_state(db, state, election_day, read)
            db.flush()
            outcome["issues"] = update_developing_issues(db, outcome.pop("results", []))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Live results sync failed for %s", state)
            outcome = {"status": "failed"}
            try:
                _record_read(db, state, election_day, "failed")
                db.commit()
            except Exception:
                db.rollback()
        summary[state] = outcome
    try:
        check_stalled_feeds(db, election_day)
    except Exception:
        logger.exception("Stalled-feed check failed")
    from app.live_results.bluesky import post_result_updates

    try:
        summary["_bluesky"] = {"posted": post_result_updates(db, election_day.isoformat())}
    except Exception:
        db.rollback()
        logger.exception("Election-night Bluesky posting failed")
    return summary


# A covered state whose source has not moved for this long, while some of
# its units are still out, gets an ops alert: vendors' own pages refresh
# every 2-15 minutes on election night, so two hours of silence with the
# count incomplete is a feed that stopped, not a quiet stretch.
STALL_ALERT_AFTER = timedelta(hours=2)


def check_stalled_feeds(db: Session, election_day: date) -> list[str]:
    now = utcnow()
    stalled = []
    for state in sorted(live_results_states()):
        if now < last_poll_close(state, election_day) + STALL_ALERT_AFTER:
            continue
        rows = (
            db.query(RaceResult).join(Race, Race.id == RaceResult.race_id)
            .filter(Race.state == state, RaceResult.election_date == election_day.isoformat()).all()
        )
        if not rows:
            continue
        incomplete = any(r.total_units and (r.reporting_units or 0) < r.total_units for r in rows)
        latest = max(r.last_change_at for r in rows)
        if incomplete and now - latest > STALL_ALERT_AFTER:
            stalled.append(state)
            send_ops_alert(
                f"Live results: {state} count has stopped moving",
                f"No change since {latest.isoformat()}Z with units still out. Check the state's own results page.",
                dedupe_key=f"results-stalled-{state}-{election_day.isoformat()}",
            )
    return stalled
