"""Certified results: each state's certified general-election count, read
once a day from election day until the new Congress is sworn in, and what
the page may say from it (app/recount_rules.outcome).

Election night's count is only ever "leading" (sync.py). A race is shown as
won only from here, and only once the state has certified it with no runoff
owed and no recount still possible under the state's law. Nothing here is a
call: the certified figures are the state's, copied, and the wording is a
fixed template around them.

A candidate the state's law elects with no contest on the ballot (Florida
keeps an unopposed candidate off the general ballot and deems them elected,
Fla. Stat. 101.151(7)) is recorded with basis "unopposed" from the state's
own candidate list marking them so (record_unopposed): there is no count to
certify and nothing a recount could change.

Where no certified reader covers a state, the floor is the new Congress: on
and after January 3 the member sworn in for the seat (record_sworn_in) is
recorded with basis "sworn_in", once the roster has been read since the
Congress convened, so a seat is never shown as taken by whoever held it
before.
"""

import json
import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.election_calendar import new_congress_day
from app.models import (
    ElectionResultEvent, HousePipelineRun, PipelineRun, PipelineStatus, Race, RaceCertification, RaceResult,
    Representative, Senator,
)
from app.ops_alerts import send_ops_alert
from app.pipeline.fetch.election_results import (
    FeedUnreadable, UntrustedCount, certified_states, fetch_certified_count, fetch_elected_unopposed,
    source_for_state, unopposed_source, unopposed_states,
)
from app.recount_rules import CERTIFIED, RECOUNT, RUNOFF, Outcome, close_enough_to_contest, outcome
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

CERTIFIED_EVENT = "certified_winner"
SWORN_IN = "sworn_in"
# Elected with no contest on the ballot, as the state's candidate list
# marks it under a law that elects such a candidate (record_unopposed).
UNOPPOSED = "unopposed"
# Bases that name the seat's winner without a count to certify.
_UNCOUNTED = (SWORN_IN, UNOPPOSED)
# A live count taken as certified once the state's legal deadline to
# certify has passed with the count unchanged (apply_deadlines).
DEADLINE = "deadline"
# How long after the deadline before the count is read as certified: a
# state certifying on its last day can post its final figures that day,
# and the live sync reads them within the hour.
DEADLINE_GRACE = timedelta(days=1)

# How long after January 3 the sworn-in floor keeps looking: the roster of
# a Congress that convenes late (a House still electing its Speaker swears
# no one in) is read again each day until then.
SWORN_IN_WINDOW = timedelta(days=30)


def certification_due(election_day: date, today: date) -> bool:
    """From the day after the election until the sworn-in floor closes."""
    return election_day < today <= new_congress_day(election_day) + SWORN_IN_WINDOW


def outcome_for(cert: RaceCertification, today: date) -> Outcome | None:
    """What the stored certification allows the page to say; None for a
    sworn-in record (the seat is filled: there is nothing left to wait for)."""
    if cert.basis not in ("certified", DEADLINE) or not cert.certified_on:
        return None
    tallies = json.loads(cert.tallies or "[]")
    return outcome(cert.state, cert.office, cert.district, [t["votes"] for t in tallies], cert.votes_counted or 0,
                   date.fromisoformat(cert.election_date), date.fromisoformat(cert.certified_on), today)


def certification_json(cert: RaceCertification | None, today: date) -> dict | None:
    """The page's view of a race's certification (api/elections.py, the
    archive). `winner` is set only when the race may be shown as won."""
    if cert is None:
        return None
    tallies = json.loads(cert.tallies or "[]")
    found = outcome_for(cert, today)
    status = cert.basis if cert.basis in _UNCOUNTED else found.status
    total = cert.votes_counted or 0
    top = tallies[0] if tallies else None
    return {
        "basis": cert.basis,
        "status": status,
        "certifiedOn": cert.certified_on,
        # False: the document states no date, and certifiedOn is the day
        # Civitas first read it as certified.
        "certifiedOnStated": bool(cert.certified_on_stated),
        "recountUntil": found.until.isoformat() if found and found.until else None,
        "statute": found.statute if found else _unopposed_statute(cert),
        "sourceName": cert.source_name,
        "sourceUrl": cert.source_url,
        "votesCounted": cert.votes_counted,
        "winner": ({"name": top["name"], "party": top.get("party"), "candidateId": top.get("candidateId")}
                   if top and status in (CERTIFIED, *_UNCOUNTED) else None),
        "candidates": [
            {"name": t["name"], "party": t.get("party"), "votes": t.get("votes"),
             "pct": round(100 * t["votes"] / total, 1) if total and t.get("votes") is not None else None,
             "candidateId": t.get("candidateId")}
            for t in tallies
        ],
    }


def _unopposed_statute(cert: RaceCertification) -> str | None:
    if cert.basis != UNOPPOSED:
        return None
    block = unopposed_source(source_for_state(cert.state) or {})
    return (block or {}).get("statute")


def _store(db: Session, race: Race, election_day: date, basis: str, certified_on: str | None,
           source_name: str, source_url: str | None, tallies: list[dict], counted: int | None,
           stated: bool = True) -> RaceCertification:
    now = utcnow()
    row = db.get(RaceCertification, race.id)
    if not stated and row is not None and row.basis == "certified" and not row.certified_on_stated and row.certified_on:
        certified_on = row.certified_on  # first read stays the date
    if row is None:
        row = RaceCertification(race_id=race.id, cycle=race.cycle_year, election_date=election_day.isoformat(),
                                state=race.state, office=race.office, district=race.district, basis=basis,
                                source_name=source_name, tallies="[]", changed_at=now)
        db.add(row)
        db.flush()  # found by the next db.get before a commit
    figures = json.dumps(tallies)
    if row.tallies != figures or row.votes_counted != counted or row.certified_on != certified_on:
        row.changed_at = now
    row.basis, row.certified_on, row.source_name, row.source_url = basis, certified_on, source_name, source_url
    row.tallies, row.votes_counted, row.read_at = figures, counted, now
    row.certified_on_stated = stated
    return row


def _announce(db: Session, row: RaceCertification, today: date) -> ElectionResultEvent | None:
    """One event the first time a race may be shown as won (the live
    updates feed words it from a template around these fields)."""
    view = certification_json(row, today)
    if not view or not view["winner"]:
        return None
    said = db.query(ElectionResultEvent.id).filter(
        ElectionResultEvent.race_id == row.race_id, ElectionResultEvent.election_date == row.election_date,
        ElectionResultEvent.kind == CERTIFIED_EVENT,
    ).first()
    if said:
        return None
    event = ElectionResultEvent(race_id=row.race_id, election_date=row.election_date, kind=CERTIFIED_EVENT,
                                detail=json.dumps(view), created_at=utcnow())
    db.add(event)
    db.flush()
    return event


def post_certified(db: Session, events: list[ElectionResultEvent]) -> int:
    """One post per Senate race the first time it may be shown as won: a
    fixed template around the certified figures, never model text. House
    winners stay on the site (435 of them would bury everything else)."""
    from app import broadcast
    from app.live_results.signals import party_letter, race_label
    from app.state_names import STATE_NAMES

    posted = 0
    for event in events:
        race = db.get(Race, event.race_id)
        if race is None or race.office != "S":
            continue
        d = json.loads(event.detail or "{}")
        winner = d.get("winner") or {}
        if not winner.get("name") or not d.get("certifiedOn"):
            continue
        letter = party_letter(winner.get("party"))
        on = date.fromisoformat(d["certifiedOn"])
        when = f" on {on.strftime('%B')} {on.day}" if d.get("certifiedOnStated") else ""
        name = STATE_NAMES.get(race.state, race.state)
        settled = (f"{name}'s deadline to certify the count passed on {on.strftime('%B')} {on.day} with the count "
                   "unchanged" if d.get("basis") == DEADLINE else f"{name} certified the count{when}")
        text = (f"{race_label(race)}: {winner['name']}{f' ({letter})' if letter else ''} is the certified winner. "
                f"{settled}, and no runoff or recount is pending under state law.")
        broadcast.publish(
            db, kind="result", subject=f"result:{event.election_date}:{race.id}:{CERTIFIED_EVENT}",
            title=f"{race_label(race)}: certified", text=text, url=f"{broadcast.SITE_URL}/elections/{race.id}",
            state=race.state, source_url=d.get("sourceUrl"),
        )
        posted += 1
    return posted


def apply_certified(db: Session, state: str, election_day: date, count, today: date) -> dict:
    """Store one state's certified count, race by race."""
    from app.live_results.sync import _contest_race, _tallies, contest_is_sane

    stored, events = 0, []
    seen: dict[str, int] = {}
    pairs = []
    for contest in count.contests:
        if not contest_is_sane(contest):
            continue
        race = _contest_race(db, election_day.year, state, contest)
        if race is None:
            continue
        seen[race.id] = seen.get(race.id, 0) + 1
        pairs.append((race, contest))
    for race, contest in pairs:
        if seen[race.id] > 1:
            logger.warning("%s: %d certified contests map to %s; skipped", state, seen[race.id], race.id)
            continue
        tallies = [{k: t[k] for k in ("name", "party", "votes", "candidateId") if k in t}
                   for t in _tallies(race, contest)]
        stated = count.certified_on is not None
        row = _store(db, race, election_day, "certified", (count.certified_on or today).isoformat(),
                     count.source_name, count.page_url, tallies, contest.votes_counted, stated=stated)
        stored += 1
        if (event := _announce(db, row, today)) is not None:
            events.append(event)
    return {"status": "ok", "races": stored, "events": events,
            "certifiedOn": count.certified_on.isoformat() if count.certified_on else None}


def _alert_unread(state: str, election_day: date, today: date, reason: str) -> None:
    send_ops_alert(
        f"Certified results: {state} could not be read",
        f"{reason}. Nothing was stored from it today; the certified count already on file (if any) stands, "
        "and the sync reads the state's certified results again tomorrow.",
        dedupe_key=f"certified-read-{state}-{today.isoformat()}", condition=f"certified-read-{state}",
    )


async def sync_certified(db: Session, client: httpx.AsyncClient, election_day: date, today: date) -> dict:
    """One pass over every state with a certified reader, then the
    re-check of stored certifications whose recount window has since
    closed, then the sworn-in floor."""
    summary: dict[str, dict] = {}
    announced: list[ElectionResultEvent] = []
    for state in sorted(certified_states()):
        try:
            count = await fetch_certified_count(client, state, election_day)
        except (FeedUnreadable, UntrustedCount) as exc:
            summary[state] = {"status": "unavailable", "reason": str(exc)}
            _alert_unread(state, election_day, today, str(exc))
            continue
        except Exception as exc:
            logger.exception("Certified results read failed for %s", state)
            summary[state] = {"status": "failed"}
            _alert_unread(state, election_day, today, type(exc).__name__)
            continue
        if count is None:
            summary[state] = {"status": "not_certified"}
            continue
        try:
            outcome_ = apply_certified(db, state, election_day, count, today)
            db.commit()
            new = outcome_.pop("events")
            outcome_["events"] = len(new)
            summary[state] = outcome_
            announced.extend(new)
        except Exception:
            db.rollback()
            logger.exception("Storing certified results failed for %s", state)
            summary[state] = {"status": "failed"}
    for state in sorted(unopposed_states()):
        try:
            found = await fetch_elected_unopposed(client, state, election_day)
        except (FeedUnreadable, UntrustedCount) as exc:
            summary[f"{state}:unopposed"] = {"status": "unavailable", "reason": str(exc)}
            _alert_unread(state, election_day, today, f"Its list of candidates elected unopposed: {exc}")
            continue
        try:
            stored, new = record_unopposed(db, state, election_day, found, today)
            db.commit()
            summary[f"{state}:unopposed"] = {"status": "ok", "races": stored, "events": len(new)}
            announced.extend(new)
        except Exception:
            db.rollback()
            logger.exception("Storing unopposed winners failed for %s", state)
            summary[f"{state}:unopposed"] = {"status": "failed"}
    # A race stored while a recount could still be asked for is announced
    # the first pass after its window closes, whether or not its state's
    # document was read again today.
    try:
        summary["_deadline"] = apply_deadlines(db, election_day, today)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Certification deadlines pass failed")
    rows = db.query(RaceCertification).filter(RaceCertification.election_date == election_day.isoformat(),
                                              RaceCertification.basis.in_(("certified", DEADLINE))).all()
    late = [event for row in rows if (event := _announce(db, row, today)) is not None]
    db.commit()
    summary["_announced_after_recount_window"] = len(late)
    try:
        summary["_posts"] = post_certified(db, announced + late)
    except Exception:
        db.rollback()
        logger.exception("Certified-winner posting failed")
    if today >= new_congress_day(election_day):
        summary["_sworn_in"] = record_sworn_in(db, election_day)
        db.commit()
    summary["_unsettled"] = refresh_settled(db, election_day, today)
    db.commit()
    return summary


def record_unopposed(
    db: Session, state: str, election_day: date, records: list[dict], today: date,
) -> tuple[int, list[ElectionResultEvent]]:
    """Store each candidate the state's list marks elected unopposed as the
    seat's winner (basis "unopposed"), where the race on file names that
    candidate, and that candidate alone, as on the November ballot. A seat
    the state certified a count for keeps it. Returns (stored, events)."""
    from app.live_results.sync import _filed_surnames, _surnames

    block = unopposed_source(source_for_state(state) or {}) or {}
    stored, events = 0, []
    for rec in records:
        q = db.query(Race).filter(Race.state == state, Race.cycle_year == election_day.year,
                                  Race.office == rec.get("office"), Race.is_special.is_(False))
        if rec.get("office") == "H":
            q = q.filter(Race.district == (rec.get("district") or 0))
        races = q.all()
        if len(races) != 1:
            logger.warning("%s unopposed %s: %d races on file", state, rec.get("display_name"), len(races))
            continue
        race = races[0]
        existing = db.get(RaceCertification, race.id)
        if existing is not None and existing.basis not in _UNCOUNTED:
            continue
        on_ballot = [c for c in race.candidates if c.confirmed_general]
        name = rec.get("display_name") or rec.get("last_name") or ""
        if len(on_ballot) != 1 or not (_surnames([name]) & _filed_surnames([on_ballot[0].name])):
            logger.warning("%s unopposed %s: the race on file confirms %d candidates, not this one alone",
                           state, name, len(on_ballot))
            continue
        row = _store(db, race, election_day, UNOPPOSED, None, block.get("source_name") or "", block.get("url"),
                     [{"name": name, "party": rec.get("party_label") or rec.get("party"), "votes": None,
                       "candidateId": on_ballot[0].id}], None)
        stored += 1
        if (event := _announce(db, row, today)) is not None:
            events.append(event)
    return stored, events


def apply_deadlines(db: Session, election_day: date, today: date) -> dict:
    """For each state with a certification deadline on file and no
    certified count read, the live count of each race, taken as certified
    once the deadline is past (by DEADLINE_GRACE) and the count has not
    changed since the deadline day, with every reporting unit in where the
    source counts them. Dated the deadline: the state certified on or before
    it, so a recount window counted from it is never too short. A count that
    moves after the deadline (a recount, a correction) is no longer read
    this way, and a winner already taken from it is withdrawn and alerted."""
    from app import certification_deadlines

    stored, withdrawn = 0, []
    for state in sorted(certification_deadlines.states()):
        last_day = certification_deadlines.deadline(state, election_day)
        if last_day is None or today <= last_day + DEADLINE_GRACE:
            continue
        rows = (db.query(RaceResult, Race).join(Race, Race.id == RaceResult.race_id)
                .filter(Race.state == state, RaceResult.election_date == election_day.isoformat()).all())
        for result, race in rows:
            existing = db.get(RaceCertification, race.id)
            if existing is not None and existing.basis != DEADLINE:
                continue  # read from the state's own certification, or sworn in
            settled = (
                (result.votes_counted or 0) > 0
                and result.last_change_at.date() <= last_day
                and not (result.total_units and result.reporting_units != result.total_units)
            )
            if not settled:
                if existing is not None:
                    db.delete(existing)
                    db.flush()
                    withdrawn.append(race.id)
                continue
            tallies = [{k: t[k] for k in ("name", "party", "votes", "candidateId") if k in t}
                       for t in json.loads(result.tallies or "[]")]
            _store(db, race, election_day, DEADLINE, last_day.isoformat(), result.source_name, result.source_url,
                   tallies, result.votes_counted)
            stored += 1
    if withdrawn:
        send_ops_alert(
            "Certified results: a count moved after its state's certification deadline",
            f"{', '.join(withdrawn)}: the live count changed after the state's deadline to certify, so it is no "
            "longer taken as the certified count, and any winner shown from it is withdrawn. The race waits for "
            "the state's own certification or the new Congress.",
            dedupe_key=f"certified-deadline-moved-{election_day.isoformat()}-{','.join(withdrawn)}",
            condition="certified-deadline-moved",
        )
    return {"stored": stored, "withdrawn": withdrawn}


def _roster_read_since(db: Session, model, since: datetime) -> bool:
    return db.query(model.id).filter(
        model.completed_at.isnot(None), model.completed_at >= since,
        model.status.in_((PipelineStatus.COMPLETED, PipelineStatus.PARTIAL)),
    ).first() is not None


def _convened_at(election_day: date) -> datetime:
    """Noon Eastern on January 3 (U.S. Const. amend. XX), as naive UTC:
    17:00 UTC, Eastern being on standard time in January."""
    day = new_congress_day(election_day)
    return datetime(day.year, day.month, day.day, 17, 0)


def record_sworn_in(db: Session, election_day: date) -> int:
    """For each of the election's races with no certified winner on file,
    the member now serving in the seat, where the roster has been read
    since the new Congress convened and exactly one of the race's
    candidates is that member. Returns how many were recorded."""
    from app.live_results.sync import _filed_surnames, _surnames

    convened = _convened_at(election_day)
    house_read = _roster_read_since(db, HousePipelineRun, convened)
    senate_read = _roster_read_since(db, PipelineRun, convened)
    today = utcnow().date()
    races = db.query(Race).filter(Race.cycle_year == election_day.year, Race.office.in_(("S", "H"))).all()
    done = 0
    for race in races:
        existing = db.get(RaceCertification, race.id)
        if existing is not None and (existing.basis in _UNCOUNTED or certification_json(existing, today)["winner"]):
            continue
        names = _filed_surnames(c.name for c in race.candidates if c.confirmed_general)
        if not names:
            continue
        if race.office == "H":
            if not house_read:
                continue
            members = db.query(Representative).filter(
                Representative.state == race.state, Representative.district == (race.district or 0),
                Representative.is_current.is_(True), Representative.sworn_date >= new_congress_day(election_day).isoformat(),
            ).all()
        else:
            if not senate_read:
                continue
            members = db.query(Senator).filter(Senator.state == race.state, Senator.is_current.is_(True)).all()
        hits = [m for m in members if _surnames([m.name]) & names]
        if len(hits) != 1:
            continue
        member = hits[0]
        party = {"D": "DEM", "R": "REP", "I": "IND"}.get(member.party, member.party)
        _store(db, race, election_day, SWORN_IN, None, "Congress.gov member roster",
               f"https://www.congress.gov/member/{member.bioguide_id}" if member.bioguide_id else None,
               [{"name": member.name, "party": party, "votes": None, "candidateId": None}], None)
        done += 1
    return done


def alert_uncovered(db: Session, election_day: date) -> list[str]:
    """States with races but no certified reader: their winners are shown
    only once the new Congress is sworn in. A fact, alerted once a cycle."""
    with_races = {s for (s,) in db.query(Race.state).filter(Race.cycle_year == election_day.year,
                                                           Race.office.in_(("S", "H"))).distinct()}
    from app import certification_deadlines

    missing = sorted(with_races - certified_states() - certification_deadlines.states())
    if missing:
        send_ops_alert(
            f"Certified results: {len(missing)} states have no certified reader",
            f"{', '.join(missing)}: their {election_day.year} winners are shown once the new Congress is sworn "
            "in, from the member roster, and not at certification.",
            dedupe_key=f"certified-uncovered-{election_day.isoformat()}-{','.join(missing)}",
            condition="certified-uncovered",
        )
    return missing


def certifications_for(db: Session, election_day: date, today: date) -> dict[str, dict]:
    """{race id: certification_json} for every race of the election with a
    certification on file."""
    rows = db.query(RaceCertification).filter(RaceCertification.election_date == election_day.isoformat()).all()
    return {row.race_id: certification_json(row, today) for row in rows}


def race_is_settled(result: RaceResult, cert: RaceCertification | None, today: date) -> bool:
    """Nothing left to decide: a certified winner (or the member sworn in),
    or a projection (sync.projection_holds) the certified count, where
    read, hasn't sent to a runoff, a recount or the margin a recount request
    is tied to. A projected race whose state lets any candidate ask for a
    recount at any margin is settled: a margin past every runoff and
    margin-tied recount is not what such a recount reverses."""
    if cert is not None:
        status = cert.basis if cert.basis in _UNCOUNTED else outcome_for(cert, today).status
        if status in (CERTIFIED, *_UNCOUNTED):
            return True
        if status in (RUNOFF, RECOUNT):
            return False
        # A recount that may still be asked for: settled only where the
        # certified count still clears the projection's margin, and the
        # margin is not one the law ties a recount to.
        from app.live_results.sync import PROJECTION_MIN_MARGIN

        tallies = json.loads(cert.tallies or "[]")
        votes = sorted((t["votes"] for t in tallies), reverse=True)
        counted = cert.votes_counted or 0
        lead = (votes[0] - (votes[1] if len(votes) > 1 else 0)) / counted if votes and counted else 0.0
        if lead < PROJECTION_MIN_MARGIN or close_enough_to_contest(cert.state, cert.office, cert.district, [t["votes"] for t in tallies],
                                   cert.votes_counted or 0, int(cert.election_date[:4])):
            return False
    return bool(result.projected)


def refresh_settled(db: Session, election_day: date, today: date) -> int:
    """Stamp each race's settled_at (RaceResult): when it first became
    settled, cleared when it no longer is (a withdrawn projection, a
    certified count inside a recount). Run after every live and certified
    pass; election_phase ends the results window from it. Returns how many
    races are unsettled."""
    day = election_day.isoformat()
    certs = {c.race_id: c for c in db.query(RaceCertification).filter(RaceCertification.election_date == day)}
    open_races = 0
    now = utcnow()
    for result in db.query(RaceResult).filter(RaceResult.election_date == day):
        if race_is_settled(result, certs.get(result.race_id), today):
            if result.settled_at is None:
                result.settled_at = now
        else:
            result.settled_at = None
            open_races += 1
    return open_races
