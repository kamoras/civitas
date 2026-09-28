"""Election-night results: read each covered state's live count
(fetch/election_results.py), store it per Race (RaceResult), record what
changed as ElectionResultEvents, and open a DEVELOPING Action Center issue
when a seat is changing party (live_results/signals.py).

Runs on its own clock from election day until the results window closes
(scheduler._election_results_sync, election_phase). Each state is read
independently: one state's feed being down leaves every other state's
count moving.

What an event is, and what it is not. Events are observed changes in the
source's own numbers — first votes counted, a different candidate on top,
every reporting unit in, the source marking its count official. None of
them is a call: the page words each one from a template around the
source's figures (the same "the page may only say what the count says"
rule as RaceResult).
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.models import Candidate, ElectionResultEvent, Race, RaceResult, Representative, Senator
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


def seat_holder_party(db: Session, race: Race) -> str | None:
    """The party that held this seat going into the election, or None when
    it can't be known without guessing.

    House: the district's sitting representative. Senate: the senator the
    race's incumbent candidate is (a state has two, and nothing stored says
    which seat is up), else — an open seat — the state's senators' party
    only when both share it."""
    if race.office == "H":
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
    return (row.get("candidateId") or row.get("name")) if row else None


def flip_qualifies(result: RaceResult) -> bool:
    """Enough of the count is in to report a seat changing party. An
    official flag counts only where the state gives no reporting figure:
    it is known to be wrong in the other direction (Enhanced Voting's
    stayed false a month after Utah's canvass, verified 2026-09-28), and a
    flag claiming "official" beside a count half in is the one to doubt."""
    if not result.total_units or result.reporting_units is None:
        return bool(result.official)
    return result.reporting_units >= FLIP_MIN_REPORTING_SHARE * result.total_units


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


def _last_flip_state(db: Session, race_id: str, election_date: str) -> bool:
    """Whether the last flip the count announced still stands — read from
    the events, not recomputed from the stored count, so a poll whose
    events were held (below) can't silently swallow a flip."""
    last = (
        db.query(ElectionResultEvent.kind)
        .filter(ElectionResultEvent.race_id == race_id, ElectionResultEvent.election_date == election_date,
                ElectionResultEvent.kind.in_((FLIP, FLIP_REVERSED)))
        .order_by(ElectionResultEvent.created_at.desc(), ElectionResultEvent.id.desc())
        .first()
    )
    return bool(last and last[0] == FLIP)


@dataclass
class Applied:
    """What storing one race's count produced."""
    result: RaceResult
    events: list[ElectionResultEvent] = field(default_factory=list)
    # True when the poll said nothing (see apply_count) — a returned flag,
    # not an attribute on the row: the session holds clean rows weakly, so
    # anything set on one may not survive to the caller's next db.get.
    held: bool = False


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
        before = {
            "tallies": json.loads(result.tallies or "[]"), "counted": result.votes_counted or 0,
            "reporting": result.reporting_units, "total": result.total_units,
            "official": result.official,
        }

    old_tallies = before["tallies"] if before else []
    old_counted = before["counted"] if before else 0
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

    kinds: list[tuple[str, dict]] = []
    old_leader, new_leader = _leader(old_tallies), _leader(tallies)
    if old_counted <= 0 < counted:
        kinds.append((FIRST_RETURNS, {}))
    elif old_leader and new_leader and _key(old_leader) != _key(new_leader):
        kinds.append((LEAD_CHANGE, {"previousLeader": {
            "name": old_leader["name"], "party": old_leader.get("party"),
        }}))
    was_all_in = bool(before and before["total"] and before["reporting"] == before["total"])
    if result.total_units and result.reporting_units == result.total_units and not was_all_in and counted > 0:
        kinds.append((ALL_REPORTING, {}))
    if result.official and not (before and before["official"]):
        kinds.append((OFFICIAL, {}))
    flipped = is_flip(result)
    was_flipped = _last_flip_state(db, race.id, result.election_date)
    if flipped and not was_flipped:
        kinds.append((FLIP, {}))
    elif was_flipped and not flipped:
        kinds.append((FLIP_REVERSED, {}))
    # One poll, one story per race: a flip already says who leads, and a
    # race whose first returns arrive complete is simply "all in" — without
    # this the feed told each of those twice, side by side.
    present = {k for k, _ in kinds}
    if FLIP in present or FLIP_REVERSED in present:
        kinds = [(k, x) for k, x in kinds if k != LEAD_CHANGE]
    if ALL_REPORTING in present:
        kinds = [(k, x) for k, x in kinds if k != FIRST_RETURNS]
    events = [_event(db, result, kind, **extra) for kind, extra in kinds]
    return Applied(result, events)


def _contest_race(db: Session, cycle: int, state: str, contest: ContestCount) -> Race | None:
    if contest.office == "S" and contest.is_special:
        race = db.get(Race, f"{cycle}-SEN-{state}-SPECIAL")
        if race is not None:
            return race
    return db.get(Race, _race_id_for(db, cycle, state, contest.office, contest.district))


# A source stamping its count this far in the future has a broken clock or
# is not what it says; a few minutes' skew between servers is normal.
_FUTURE_SKEW = timedelta(hours=1)


def _last_accepted(db: Session, state: str, election_day: date):
    return (
        db.query(RaceResult.source_updated_at, RaceResult.source_version)
        .join(Race, Race.id == RaceResult.race_id)
        .filter(Race.state == state, RaceResult.election_date == election_day.isoformat(),
                RaceResult.source_updated_at.isnot(None))
        .order_by(RaceResult.source_updated_at.desc())
        .first()
    )


def freshness_problem(db: Session, state: str, election_day: date, count: StateCount) -> str | None:
    """Why this read must not replace what is stored: a source that has
    gone BACKWARDS (an older copy from a cache, CDN or mirror), or one
    dated in the future. None when it is fine to store."""
    now = utcnow()
    if count.source_updated and count.source_updated > now + _FUTURE_SKEW:
        return f"source is stamped {count.source_updated.isoformat()}, in the future"
    last = _last_accepted(db, state, election_day)
    if last is None or count.source_updated is None:
        return None
    if count.source_updated < last[0]:
        return f"source went back from {last[0].isoformat()} to {count.source_updated.isoformat()}"
    if (count.source_version or "").isdigit() and (last[1] or "").isdigit() and int(count.source_version) < int(last[1]):
        return f"source version went back from {last[1]} to {count.source_version}"
    return None


async def sync_state(db: Session, client: httpx.AsyncClient, state: str, election_day: date) -> dict:
    if not polls_closed(state, election_day, utcnow()):
        # Nothing is read, stored or said before a state's last polls
        # close (fetch/poll_close.py).
        return {"status": "polls_open", "pollsClose": last_poll_close(state, election_day).isoformat() + "Z"}
    try:
        count = await fetch_state_count(client, state, election_day)
    except UntrustedCount as refused:
        logger.warning("Live results refused for %s: %s", state, refused)
        send_ops_alert(
            f"Live results: {state} feed refused",
            f"{refused}. Nothing from it was stored or published; the page keeps the last trusted count.",
            dedupe_key=f"results-untrusted-{state}-{election_day.isoformat()}",
        )
        return {"status": "untrusted", "reason": str(refused)}
    if count is None:
        return {"status": "unavailable"}
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
    results: list[RaceResult] = []
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
            results.append(applied.result)
    db.flush()
    return {
        "status": "ok", "races": len(results), "contests": len(count.contests),
        "events": events, "results": results,
    }


async def sync_live_results(db: Session, client: httpx.AsyncClient, election_day: date) -> dict:
    """One pass over every covered state; commits per state."""
    from app.live_results.signals import update_developing_issues

    summary: dict[str, dict] = {}
    for state in sorted(live_results_states()):
        try:
            outcome = await sync_state(db, client, state, election_day)
            db.flush()
            outcome["issues"] = update_developing_issues(db, outcome.pop("results", []))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Live results sync failed for %s", state)
            outcome = {"status": "failed"}
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
