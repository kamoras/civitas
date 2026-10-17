"""Special elections, vacant seats and the federal election calendar, as the
pages show them: the specials board, each special's swing against its seat's
lean and the calendar. Which season /elections is in is election_phase.season.

Everything here is computed from stored data (fetch/special_elections.py,
live_results/specials.py, voter_calendar.py) on a read; nothing is typed by
hand and nothing is generated. Each date's status is computed on the state's
own clock, the zone its polls close in (poll_close.state_zone).

Swing is a measurement, never a call. A special's result is put on the lean's
own scale: the Republican share of the two-party vote, minus 50 (positive is
Republican, the sign of a lean), against the seat's usual vote as the
elections pages show it (a House seat's Civitas-computed lean on the lines of
the Congress the seat sits in, district_lean.json; a Senate seat's state lean,
state_pvi.json; both written by district_lean.lean_label). The swing is the
difference, in points, toward whichever party the result moved. It is given
only where both major parties ran (a contest between two Democrats has no
two-party share), only once the count is settled (the source says official,
or every reporting unit is fully in), and says "not final" until the source
says official. The lean is Cook's formula: a two-party presidential share
measured against the nation's, so a result's distance from it mixes the
national mood with the seat's own; the page says so.
"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import broadcast, district_lean
from app.election_phase import active_election
from app.live_results.specials import COUNTED_TYPES
from app.models import BroadcastPost, FecReportingDeadline, FederalElectionDate, SeatVacancy
from app.pipeline.analyze.score_calculator import get_state_pvi_map
from app.pipeline.fetch.election_results import live_results_states
from app.pipeline.fetch.poll_close import last_poll_close, state_zone
from app.state_names import STATE_NAMES
from app.time_utils import EASTERN, congress_in_session, utcnow
from app.voter_calendar import CLOSED, CLOSING_SOON_DAYS, calendar_for_state, registration_closing

CALENDAR_HREF = "/elections/calendar"
FEC_CALENDAR_URL = "https://www.fec.gov/data/election-dates/"
FEC_REPORTING_URL = "https://www.fec.gov/help-candidates-and-committees/dates-and-deadlines/"

# The calendar's window: the month just gone (a deadline or special that
# passed stays on the page, saying so), and the year ahead.
CALENDAR_BACK = timedelta(days=31)
CALENDAR_AHEAD = timedelta(days=365)
# "Soon" on the calendar's lead line: what is coming in the next month.
SOON = timedelta(days=30)
# The specials board lists the year's specials held so far; an earlier one
# is named only as "the last special" when none is more recent.
RECENT = timedelta(days=365)

UPCOMING, VOTING, POLLS_CLOSED, HELD = "upcoming", "voting", "polls_closed", "held"

_PARTY = {"D": "Democrat", "R": "Republican"}


def _zone(state: str) -> ZoneInfo:
    return state_zone(state) or EASTERN


def _local_today(state: str, now: datetime) -> date:
    return now.replace(tzinfo=ZoneInfo("UTC")).astimezone(_zone(state)).date()


def day_status(state: str, day: date, now: datetime) -> tuple[str, int | None]:
    """(status, days left) of an election day on `state`'s clock: upcoming,
    voting (the day, before the state's last polls close), polls_closed (the
    day, after), held (a later day)."""
    today = _local_today(state, now)
    if today < day:
        return UPCOMING, (day - today).days
    if today == day:
        return (VOTING if now < last_poll_close(state, day) else POLLS_CLOSED), 0
    return HELD, None


def seat_label(state: str, office: str, district: int | None) -> str:
    name = STATE_NAMES.get(state, state)
    if office == "S":
        return f"{name}: U.S. Senate"
    if office == "H":
        if district is None:
            return f"{name}: U.S. House"
        return f"{state}-{district}" if district else f"{name} at-large"
    if office == "P":
        return f"{name}: President"
    return name


def usual_vote(row) -> dict | None:
    """The seat's lean as the elections pages show it: {lean, label,
    source, congress}. A House seat's on the lines of the Congress in
    office on the special's day, the seat being filled."""
    held = date.fromisoformat(row.election_date)
    if row.office == "H":
        congress = congress_in_session(datetime(held.year, held.month, held.day, 17))
        lean = district_lean.lean_for(row.state, row.district or 0, congress)
        if lean is None:
            return None
        return {"lean": round(lean, 1), "label": district_lean.lean_label(lean), "source": "computed",
                "congress": congress}
    if row.office == "S":
        lean = get_state_pvi_map().get(row.state)
        if lean is None:
            return None
        return {"lean": lean, "label": district_lean.lean_label(lean), "source": "state", "congress": None}
    return None


def tallies_of(row) -> list[dict]:
    try:
        rows = json.loads(row.tallies or "[]")
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


def settled(row) -> bool:
    """The count is settled enough to measure: the source says official, or
    every reporting unit is fully in (never units that have merely begun)."""
    if row.official:
        return True
    return bool(row.units_complete and row.total_units and row.reporting_units == row.total_units)


def swing(row) -> dict | None:
    """The special's two-party result against the seat's usual vote (module
    docstring), or None with nothing to measure."""
    tallies = tallies_of(row)
    if not tallies or not settled(row):
        return None
    dem = sum(t.get("votes") or 0 for t in tallies if t.get("party") == "D")
    rep = sum(t.get("votes") or 0 for t in tallies if t.get("party") == "R")
    usual = usual_vote(row)
    if not dem or not rep or usual is None:
        return None
    result = round(100 * rep / (dem + rep) - 50, 1)
    moved = round(result - usual["lean"], 1)
    toward = "R" if moved > 0 else "D" if moved < 0 else None
    shift = f"{abs(moved):g} points toward {_PARTY[toward]}s" if toward else "no shift from the seat's usual vote"
    return {
        "result": result,
        "resultLabel": district_lean.lean_label(result),
        "usualVote": usual,
        "swing": moved,
        "toward": toward,
        "final": bool(row.official),
        "sentence": (
            f"The result was {district_lean.lean_label(result)} against a usual vote of {usual['label']}: "
            f"{shift}" + ("." if row.official else ", on the count as it stands (not final).")
        ),
    }


def _count(row) -> dict | None:
    if row.votes_counted is None:
        return None
    return {
        "tallies": tallies_of(row),
        "votesCounted": row.votes_counted,
        "reportingUnits": row.reporting_units,
        "totalUnits": row.total_units,
        "unitLabel": row.unit_label,
        "unitsComplete": row.units_complete,
        "official": bool(row.official),
        "sourceName": row.result_source_name,
        "sourceUrl": row.result_source_url,
        "lastChangeAt": _iso(row.last_change_at),
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _covered(state: str) -> bool:
    return state in live_results_states()


def special_item(row: FederalElectionDate, now: datetime) -> dict:
    status, days = day_status(row.state, date.fromisoformat(row.election_date), now)
    counted = row.type_code in COUNTED_TYPES
    return {
        "id": row.id,
        "date": row.election_date,
        "state": row.state,
        "stateName": STATE_NAMES.get(row.state, row.state),
        "office": row.office,
        "district": row.district,
        "seat": seat_label(row.state, row.office, row.district),
        "typeCode": row.type_code,
        "label": row.type_label,
        "note": row.note,
        "status": status,
        "daysLeft": days,
        # Why there is no count, when there is none, so an absence says
        # which absence it is.
        "liveCount": (
            "read" if counted and _covered(row.state)
            else "not_read_type" if not counted
            else "not_covered"
        ),
        "readStatus": row.read_status,
        "count": _count(row),
        "swing": swing(row),
    }


def _vacancy(v: SeatVacancy, scheduled: dict | None) -> dict:
    return {
        "id": v.id,
        "office": v.office,
        "state": v.state,
        "stateName": STATE_NAMES.get(v.state, v.state),
        "district": v.district,
        "senateClass": v.senate_class,
        "seat": seat_label(v.state, v.office, v.district),
        "predecessor": v.predecessor,
        "predecessorParty": v.predecessor_party,
        "vacatedOn": v.vacated_on,
        "footnote": v.footnote,
        "sourceUrl": v.source_url,
        "readAt": _iso(v.read_at),
        "nextSpecial": scheduled,
    }


def _seat_key(state: str, office: str, district: int | None) -> tuple:
    return (state, office, (district or 0) if office == "H" else None)


def specials_board(db: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(EASTERN).date()
    rows = (
        db.query(FederalElectionDate)
        .filter(FederalElectionDate.is_special.is_(True), FederalElectionDate.office.in_(("H", "S")))
        .order_by(FederalElectionDate.election_date)
        .all()
    )
    items = [special_item(r, now) for r in rows]
    upcoming = [i for i in items if i["status"] != HELD]
    held = [i for i in reversed(items) if i["status"] == HELD]
    recent = [i for i in held if date.fromisoformat(i["date"]) >= today - RECENT]
    by_seat: dict[tuple, dict] = {}
    for i in upcoming:
        by_seat.setdefault(_seat_key(i["state"], i["office"], i["district"]), i)
    vacancies = db.query(SeatVacancy).order_by(SeatVacancy.office, SeatVacancy.state, SeatVacancy.district).all()
    measured = [i["swing"]["swing"] for i in recent if i["swing"]]
    return {
        "asOf": max((r.listed_on for r in rows), default=None),
        "vacancies": [
            _vacancy(v, by_seat.get(_seat_key(v.state, v.office, v.district))) for v in vacancies
        ],
        "vacanciesReadAt": _iso(max((v.read_at for v in vacancies), default=None)),
        "upcoming": upcoming,
        "recent": recent,
        # Named when the year has none, so an empty board still says when
        # the last special was held.
        "lastSpecial": held[0] if held and not recent else None,
        "averageSwing": _average(measured, len(recent), today),
        "sources": {"fec": FEC_CALENDAR_URL},
    }


def _average(measured: list[float], of: int, today: date) -> dict:
    """The mean swing over the year's settled specials, with how many it
    is taken over and the sentence the page shows."""
    mean = round(sum(measured) / len(measured), 1) if measured else None
    if mean is None:
        sentence = (f"No swing is measured yet: none of the {of} specials held in the last year has a settled "
                    "count between a Democrat and a Republican.") if of else None
    else:
        toward = "no shift" if mean == 0 else f"{abs(mean):g} points toward {_PARTY['R' if mean > 0 else 'D']}s"
        sentence = (f"Average swing against the seats' usual vote: {toward}, over {len(measured)} of the {of} "
                    "specials held in the last year with a settled count between a Democrat and a Republican.")
    return {"swing": mean, "count": len(measured), "of": of, "since": (today - RECENT).isoformat(),
            "sentence": sentence}


def national_deadlines(db: Session, rows: list[FecReportingDeadline]) -> list[FecReportingDeadline]:
    """The reporting deadlines that fall on one date for every filer: a
    report type the FEC lists on a single due date in its whole report year
    (counted over every stored row of that year, not only those shown). A
    type due on many dates (a pre-primary report, due before each state's
    primary) is one the FEC's rows don't say which state each date is for,
    so it can't be shown honestly as a date, and isn't."""
    years = {r.report_year for r in rows}
    dates: dict[tuple, set[str]] = {}
    for kind, year, due in db.query(
        FecReportingDeadline.report_type, FecReportingDeadline.report_year, FecReportingDeadline.due_date,
    ).filter(FecReportingDeadline.report_year.in_(years)):
        dates.setdefault((kind, year), set()).add(due)
    return [r for r in rows if r.label and len(dates.get((r.report_type, r.report_year), ())) == 1]


def election_calendar(db: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(EASTERN).date()
    lo, hi = (today - CALENDAR_BACK).isoformat(), (today + CALENDAR_AHEAD).isoformat()
    rows = (
        db.query(FederalElectionDate)
        .filter(FederalElectionDate.election_date >= lo, FederalElectionDate.election_date <= hi)
        .order_by(FederalElectionDate.election_date, FederalElectionDate.state)
        .all()
    )
    elections = []
    # The states and DC: the FEC also lists the territories' delegate
    # elections, which the elections pages leave out (no voting seat).
    for r in (r for r in rows if r.state in STATE_NAMES):
        status, days = day_status(r.state, date.fromisoformat(r.election_date), now)
        elections.append({
            "id": r.id, "date": r.election_date, "state": r.state,
            "stateName": STATE_NAMES.get(r.state, r.state), "office": r.office, "district": r.district,
            "seat": seat_label(r.state, r.office, r.district), "typeCode": r.type_code, "label": r.type_label,
            "special": r.is_special, "note": r.note, "status": status, "daysLeft": days,
        })

    election = active_election(db, today)
    deadlines = []
    # Every state and DC, for the state filter: a state whose deadlines
    # couldn't be read is still listed, and says so, never left out.
    states = []
    for state in sorted(STATE_NAMES):
        cal = calendar_for_state(state, election.election_day, now)
        states.append({"state": state, "stateName": STATE_NAMES[state],
                       "deadlinesRead": bool(cal and cal["items"] and not cal["unread"]),
                       "register": (cal or {}).get("register")})
        for item in (cal or {}).get("items") or []:
            if item["status"] == CLOSED and item["closesOn"] < lo:
                continue
            deadlines.append({
                "state": state, "stateName": STATE_NAMES[state], "kind": item["kind"], "label": item["label"],
                "sentence": item["sentence"], "date": item["closesOn"], "status": item["status"],
                "daysLeft": item["daysLeft"], "source": item["source"], "republishedBy": item["republishedBy"],
                "register": cal["register"] if item["kind"] == "registration" and item["status"] != CLOSED else None,
            })
    deadlines.sort(key=lambda d: (d["date"], d["state"]))

    reports = (
        db.query(FecReportingDeadline)
        .filter(FecReportingDeadline.due_date >= lo, FecReportingDeadline.due_date <= hi)
        .order_by(FecReportingDeadline.due_date)
        .all()
    )
    fec = []
    for r in national_deadlines(db, reports):
        due = date.fromisoformat(r.due_date)
        fec.append({
            "date": r.due_date, "reportType": r.report_type, "label": r.label, "reportYear": r.report_year,
            "status": UPCOMING if due > today else "due_today" if due == today else HELD,
            "daysLeft": (due - today).days if due >= today else None,
        })

    soon = (today + SOON).isoformat()
    upcoming_dates = sorted(
        [e["date"] for e in elections if e["status"] != HELD]
        + [d["date"] for d in deadlines if d["status"] != CLOSED]
        + [f["date"] for f in fec if f["status"] != HELD]
    )
    return {
        "today": today.isoformat(),
        "window": {"from": lo, "to": hi},
        "elections": elections,
        "voterDeadlines": deadlines,
        "voterDeadlinesFor": election.election_day.isoformat(),
        "states": states,
        "registrationClosing": registration_closing(election.election_day, now),
        "closingSoonDays": CLOSING_SOON_DAYS,
        "fecDeadlines": fec,
        "nextThirtyDays": sum(1 for d in upcoming_dates if d <= soon),
        "nextDate": upcoming_dates[0] if upcoming_dates else None,
        "sources": {
            "electionDates": {"url": FEC_CALENDAR_URL, "readOn": max((r.listed_on for r in rows), default=None)},
            "reportingDates": {"url": FEC_REPORTING_URL, "readOn": max((r.read_on for r in reports), default=None)},
        },
    }


def publish_new_specials(db: Session, new_ids: list[str], today: date | None = None) -> int:
    """One feed entry (never Bluesky: what is posted there is a measured
    volume decision) for each special general or runoff newly on the FEC's
    calendar and still ahead. Returns how many were published."""
    today = today or utcnow().date()
    published = 0
    for rid in new_ids:
        row = db.get(FederalElectionDate, rid)
        if (row is None or not row.is_special or row.type_code not in COUNTED_TYPES
                or row.office not in ("H", "S") or row.election_date < today.isoformat()):
            continue
        subject = f"special:{row.id}"
        if db.query(BroadcastPost.id).filter(BroadcastPost.subject == subject).first():
            continue
        held = date.fromisoformat(row.election_date)
        seat = seat_label(row.state, row.office, row.district)
        when = f"{held:%B} {held.day}, {held.year}"
        broadcast.publish(
            db, kind="special", subject=subject,
            title=f"Special election: {seat}, {when}",
            text=f"{seat}: {(row.type_label or 'special election').lower()} on {when}, as the FEC's election "
                 "calendar lists it.",
            url=f"{broadcast.SITE_URL}{CALENDAR_HREF}#special-{row.id}",
            state=row.state, source_url=FEC_CALENDAR_URL, bluesky=False,
        )
        published += 1
    return published
