"""The next cycle, as the official records stand between general elections:
who has registered with the FEC for it, the FEC's next reporting deadline as
a recurring event, and how the sitting Congress compares with the one before.

Every figure is a count over stored official records, computed on the read:

  field         the FEC's candidate filings for the cycle (Candidate rows the
                Election run syncs for the site's cycle, `Candidate.member_
                bioguide` from the congress-legislators bioguide->FEC
                crosswalk). A filing is a registration with the FEC, never a
                place on a ballot: a state puts a candidate on its ballot, by
                its own certification, months later. Open seats are the FEC's
                own coding (`incumbent_challenge` "O"). A sitting member with
                no filing for the cycle is said to have none, never to be
                retiring: no official record states a retirement.
  deadline      the FEC's reporting dates (fetch/special_elections.py), the
                national ones only (special_elections.national_deadlines).
  new Congress  each sitting member's Congress.gov record (terms per Congress
                with their chamber, and birth year: the official fields; no
                other demographic is in an official record), as the pipeline
                stored it (fetch/congress.fetch_member_detail).
"""

import json
from datetime import date
from statistics import median

from sqlalchemy.orm import Session

from app.config import settings
from app.election_calendar import seats_up_for_year
from app.models import (
    BALLOT_ONLY_ID_PREFIX,
    ApiCache,
    Candidate,
    FecReportingDeadline,
    Race,
    Representative,
    Senator,
)
from app.pipeline.fetch.congress import congress_first_year, member_detail_cache_key
from app.special_elections import FEC_REPORTING_URL, national_deadlines

FEC_CANDIDATES_URL = "https://www.fec.gov/data/candidates/"
CONGRESS_GOV_MEMBERS_URL = "https://www.congress.gov/members"

# A reporting deadline is the calendar's lead event from this many days
# before it until this many after: long enough to see it coming, and for the
# reports filed on it to be read. An editorial window, not a measured one.
DEADLINE_LEAD_DAYS = 14
DEADLINE_AFTER_DAYS = 3

_CHAMBER = {"H": "House of Representatives", "S": "Senate"}


def _registered(db: Session, cycle: int) -> list[tuple[Candidate, Race]]:
    """Every FEC filing on a regular federal race of `cycle` (a candidate a
    state's ballot names without an FEC filing is the state's, not the
    FEC's, and is left out)."""
    return (
        db.query(Candidate, Race)
        .join(Race, Race.id == Candidate.race_id)
        .filter(Race.cycle_year == cycle, Race.is_special.is_(False),
                ~Candidate.id.startswith(BALLOT_ONLY_ID_PREFIX))
        .all()
    )


def field(db: Session, cycle: int) -> dict:
    """Who has registered with the FEC for `cycle`'s regular Senate and House
    races, and where each sitting member whose seat is up stands."""
    rows = _registered(db, cycle)
    filings_by_member: dict[str, list[Race]] = {}
    for cand, race in rows:
        if cand.member_bioguide:
            filings_by_member.setdefault(cand.member_bioguide, []).append(race)
    open_seats = sorted({race.id for cand, race in rows if cand.incumbent_challenge == "O"})

    reps = db.query(Representative).filter(Representative.is_current.is_(True)).all()
    house_own, house_other = 0, []
    for r in reps:
        filed = filings_by_member.get(r.bioguide_id or "", [])
        if any(x.office == "H" for x in filed):
            house_own += 1
        elif senate := next((x for x in filed if x.office == "S"), None):
            house_other.append({"id": r.id, "name": r.name, "party": r.party, "state": r.state,
                                "district": r.district, "office": "S", "raceId": senate.id})

    # Exactly one of a state's senators holds the seat that is up; the class
    # isn't stored per senator, so a seat counts as its incumbent's when
    # either of the state's senators filed for that race.
    up = seats_up_for_year(cycle)
    senators = db.query(Senator).filter(Senator.is_current.is_(True), Senator.state.in_(up)).all()
    senate_races = {r.state: r for r in db.query(Race).filter(
        Race.cycle_year == cycle, Race.office == "S", Race.is_special.is_(False))}
    by_state: dict[str, list[Senator]] = {}
    for s in senators:
        by_state.setdefault(s.state, []).append(s)
    senate_filed = sorted(
        st for st, members in by_state.items()
        if any(x.office == "S" and x.state == st for m in members for x in filings_by_member.get(m.bioguide_id or "", ()))
    )
    return {
        "cycle": cycle,
        "registered": len({cand.id for cand, _ in rows}),
        "house": {
            "members": len(reps),
            "filedForHouse": house_own,
            "filedForSenate": house_other,
            "noFiling": len(reps) - house_own - len(house_other),
        },
        "senate": {
            "seatsUp": len(up),
            "racesOnFile": len(senate_races),
            "incumbentFiled": len(senate_filed),
            "noIncumbentFiling": sorted(up - set(senate_filed)),
        },
        # Races with a candidate the FEC codes as running for an open seat.
        "openSeats": len(open_seats),
        "sources": {"fec": FEC_CANDIDATES_URL},
    }


def deadline_event(db: Session, today: date) -> dict | None:
    """The FEC's reporting deadline the elections pages lead with: one due
    within DEADLINE_LEAD_DAYS ahead or DEADLINE_AFTER_DAYS behind (the
    nearest, a due one first), else the next one ahead; None with none stored.
    `status` is upcoming, due_today or passed."""
    lo = date.fromordinal(today.toordinal() - DEADLINE_AFTER_DAYS).isoformat()
    rows = (db.query(FecReportingDeadline).filter(FecReportingDeadline.due_date >= lo)
            .order_by(FecReportingDeadline.due_date).all())
    national = national_deadlines(db, rows)
    if not national:
        return None
    row = national[0]
    due = date.fromisoformat(row.due_date)
    days = (due - today).days
    return {
        "date": row.due_date,
        "label": row.label,
        "reportType": row.report_type,
        "reportYear": row.report_year,
        "status": "upcoming" if days > 0 else "due_today" if days == 0 else "passed",
        "daysLeft": days if days >= 0 else None,
        "daysAgo": -days if days < 0 else None,
        # Inside the window around its day: the event leads.
        "leads": -DEADLINE_AFTER_DAYS <= days <= DEADLINE_LEAD_DAYS,
        "source": FEC_REPORTING_URL,
    }


def _record(raw: str | None) -> dict | None:
    try:
        record = json.loads(raw) if raw else None
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _congresses(record: dict, chamber: str) -> set[int]:
    return {t["congress"] for t in record.get("terms") or []
            if isinstance(t, dict) and isinstance(t.get("congress"), int) and t.get("chamber") == _CHAMBER[chamber]}


def _chamber_summary(records: list[dict | None], chamber: str, congress: int, first_year: int) -> dict:
    """Turnover and age for one chamber's sitting members, from their
    Congress.gov records (None where no record is stored yet)."""
    # Only a record that already lists this Congress in this chamber: one
    # stored before Congress.gov added the new term would read a returning
    # member as having no term here, and a new Congress as all returning.
    read = [r for r in records if r and congress in _congresses(r, chamber)]
    new = returning = switched = 0
    births: list[int] = []
    for r in read:
        terms = [t for t in r.get("terms") or [] if isinstance(t, dict) and isinstance(t.get("congress"), int)]
        here = {t["congress"] for t in terms if t.get("chamber") == _CHAMBER[chamber]}
        before_here = {c for c in here if c < congress}
        if not before_here:
            new += 1
            if any(t.get("chamber") != _CHAMBER[chamber] and t["congress"] < congress for t in terms):
                switched += 1
        elif congress - 1 not in here:
            returning += 1
        if isinstance(r.get("birthYear"), (int, str)) and str(r["birthYear"]).isdigit():
            births.append(int(r["birthYear"]))
    return {
        "members": len(records),
        # Members whose record lists this Congress yet.
        "read": len(read),
        # New to this chamber this Congress (a former member of the other
        # chamber among them), and back after sitting out the last one.
        "new": new,
        "fromOtherChamber": switched,
        "returning": returning,
        "newShare": round(new / len(read), 3) if read else None,
        # Age at the Congress's first January 3, from birth year: Congress.gov
        # states the year only, so a member's age is this or one less.
        "medianAge": round(first_year - median(births), 1) if births else None,
        "ages": len(births),
    }


def new_congress(db: Session, congress: int | None = None) -> dict:
    """How the sitting Congress compares with the one before it."""
    congress = congress or settings.CURRENT_CONGRESS
    first_year = congress_first_year(congress)
    out = {"congress": congress, "firstYear": first_year,
           "sources": {"congressGov": CONGRESS_GOV_MEMBERS_URL}}
    for chamber, model in (("S", Senator), ("H", Representative)):
        members = db.query(model.bioguide_id).filter(model.is_current.is_(True)).all()
        keys = [member_detail_cache_key(b) for (b,) in members if b]
        stored = {row.cache_key: row.data_json for row in db.query(ApiCache).filter(
            ApiCache.tier == "congress", ApiCache.cache_key.in_(keys))} if keys else {}
        records = [_record(stored.get(member_detail_cache_key(b))) if b else None for (b,) in members]
        out["senate" if chamber == "S" else "house"] = _chamber_summary(records, chamber, congress, first_year)
    return out
