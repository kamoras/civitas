"""Special elections, vacant seats and the federal election calendar, read
from the sources that state them.

  The FEC's election dates (/v1/election-dates/): every federal primary,
        runoff, general, convention and special election by state, with the
        FEC's own type code and label. Stored whole in
        `federal_election_dates`, for the calendar page; a special's row is
        also what the specials board and the special's live count hang on.
        Read for the calendar year and the site's cycle (one and the same in
        an even year), so an odd year's specials are read at all.
  The FEC's reporting dates (/v1/reporting-dates/): when campaign finance
        reports are due. The FEC lists a row per jurisdiction a date applies
        to without naming it, so rows are kept per (due date, report type).
  The House Clerk's member list (MemberData.xml): a vacant seat is a member
        entry with no bioguide ID, its <predecessor-info> naming who left,
        their party and the day the seat fell vacant, and its <footnote> the
        Clerk's own sentence.
  The Senate's member list (senators_cfm.xml): a seat is vacant when a state
        holds a seat in a class (senate_classes.json, which keeps a state
        while its seat is empty) and no sitting senator of that class is
        listed for it.

Read nightly by the Election run, whatever the season: specials are held in
every month of every year. Every read that fails says so (None, or an
incomplete read) and keeps what is stored; an incomplete calendar read adds
rows but never retracts one, the same rule as the state calendar
(state_election_dates.save_calendar).
"""

import logging
from datetime import date

import httpx
from lxml import etree
from sqlalchemy.orm import Session

from app.models import FecReportingDeadline, FederalElectionDate, SeatVacancy
from app.pipeline.fetch.house_clerk import MEMBER_DATA_URL, _VOTING_DISTRICT
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.fetch.senate_classes import SENATORS_URL, _ROMAN
from app.pipeline.fetch.state_election_dates import _FEC_MAX_PAGES, fetch_fec_rows, is_special_row
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

def _district(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def row_id(row: dict) -> str | None:
    """The id a calendar row is stored under, or None for a row missing
    the fields that make one (a date, a state, an office, a type)."""
    held, state = row.get("election_date"), (row.get("election_state") or "").strip().upper()
    office, code = (row.get("office_sought") or "").strip().upper(), (row.get("election_type_id") or "").strip()
    if not held or len(state) != 2 or not office or not code:
        return None
    district = _district(row.get("election_district"))
    return f"{held}-{state}-{office}-{district if district is not None else 0}-{code}"


def save_election_rows(db: Session, year: int, rows: list[dict], *, complete: bool, read_on: str) -> list[str]:
    """Upsert the FEC's rows for `year`. A complete read is the calendar:
    a row it no longer lists is deleted, unless a count is stored on it
    (that special was held; its count stays). Returns the ids of rows new
    to the table."""
    seen: set[str] = set()
    new: list[str] = []
    for row in rows:
        rid = row_id(row)
        if rid is None or rid in seen:
            continue
        seen.add(rid)
        stored = db.get(FederalElectionDate, rid)
        if stored is None:
            stored = FederalElectionDate(id=rid, official=False)
            db.add(stored)
            new.append(rid)
        stored.election_date = row["election_date"]
        stored.election_year = int(row.get("election_year") or year)
        stored.state = row["election_state"].strip().upper()
        stored.office = row["office_sought"].strip().upper()
        stored.district = _district(row.get("election_district"))
        stored.type_code = row["election_type_id"].strip()
        stored.type_label = (row.get("election_type_full") or "").strip() or None
        stored.is_special = is_special_row(row)
        stored.note = (row.get("election_notes") or "").strip() or None
        stored.listed_on = read_on
    if complete:
        for stale in db.query(FederalElectionDate).filter(
            FederalElectionDate.election_year == year, FederalElectionDate.id.notin_(seen),
        ):
            if stale.votes_counted is None:
                db.delete(stale)
    db.flush()
    return new


def reporting_years(today: date) -> list[int]:
    """The report years read: last year's (its year-end report is due the
    January after), this year's and next year's. Whole years, not a window
    of due dates, because whether a report type falls on one date for every
    filer is a question about its whole year (special_elections.py)."""
    return [today.year - 1, today.year, today.year + 1]


async def fetch_reporting_deadlines(client: httpx.AsyncClient, year: int) -> tuple[list[dict], bool]:
    """(rows, complete) of the FEC's reporting dates for report year `year`."""
    from app.pipeline.fetch.fec import _fetch_with_retry

    rows: list[dict] = []
    for page in range(1, _FEC_MAX_PAGES + 1):
        payload = await _fetch_with_retry(
            client,
            f"https://api.open.fec.gov/v1/reporting-dates/?report_year={year}&per_page=100&page={page}",
        )
        if not payload:
            logger.warning("FEC reporting dates %d page %d failed — read incomplete", year, page)
            return rows, False
        rows += payload.get("results") or []
        pagination = payload.get("pagination") or {}
        if page >= pagination.get("pages", 1):
            count = pagination.get("count")
            return rows, isinstance(count, int) and len(rows) == count
    logger.warning("FEC reporting dates %d have more than %d pages — read incomplete", year, _FEC_MAX_PAGES)
    return rows, False


def save_reporting_deadlines(db: Session, year: int, rows: list[dict], *, complete: bool, read_on: str) -> int:
    """Upsert one report year's deadlines, grouped per (due date, report
    type); a complete read retracts the year's rows it no longer lists."""
    grouped: dict[str, dict] = {}
    for row in rows:
        due, kind = row.get("due_date"), (row.get("report_type") or "").strip()
        if not due or not kind:
            continue
        entry = grouped.setdefault(f"{due}-{kind}", {
            "due_date": due, "report_type": kind, "rows": 0,
            "label": (row.get("report_type_full") or "").strip() or None,
            "report_year": row.get("report_year") or year,
        })
        entry["rows"] += 1
    for rid, entry in grouped.items():
        stored = db.get(FecReportingDeadline, rid) or FecReportingDeadline(id=rid)
        for k, v in entry.items():
            setattr(stored, k, v)
        stored.read_on = read_on
        db.add(stored)
    if complete:
        db.query(FecReportingDeadline).filter(
            FecReportingDeadline.report_year == year, FecReportingDeadline.id.notin_(grouped),
        ).delete(synchronize_session=False)
    db.flush()
    return len(grouped)


def _iso(raw: str | None) -> str | None:
    raw = (raw or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
    except ValueError:
        return None


def parse_house_vacancies(xml: bytes) -> list[dict] | None:
    """Every vacant voting seat in the Clerk's member list, or None when
    the list can't be read (or lists no member at all: a changed layout)."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    vacancies, members = [], 0
    for member in root.iterfind("members/member"):
        info = member.find("member-info")
        if info is None:
            continue
        if (info.findtext("bioguideID") or "").strip():
            members += 1
            continue
        state_el = info.find("state")
        state = (state_el.get("postal-code") or "").strip() if state_el is not None else ""
        district_text = (info.findtext("district") or "").strip()
        if len(state) != 2 or not _VOTING_DISTRICT.fullmatch(district_text):
            continue  # a delegate's seat, or an entry this can't place
        statedistrict = (member.findtext("statedistrict") or "").strip()
        district = int(statedistrict[2:]) if statedistrict[2:].isdigit() else 0
        pred = member.find("predecessor-info")
        vacated = pred.find("pred-vacate-date") if pred is not None else None
        vacancies.append({
            "id": f"H-{state}-{district}", "office": "H", "state": state, "district": district,
            "senate_class": None,
            "predecessor": (pred.findtext("pred-official-name") or "").strip() or None if pred is not None else None,
            "predecessor_party": (pred.findtext("pred-party") or "").strip()[:1] or None if pred is not None else None,
            "vacated_on": _iso(vacated.get("date")) if vacated is not None else None,
            "footnote": (info.findtext("footnote") or "").strip() or None,
            "source_url": MEMBER_DATA_URL,
        })
    return vacancies if members else None


def parse_senate_vacancies(xml: bytes, classes: dict[int, frozenset[str]]) -> list[dict] | None:
    """Every class seat with no sitting senator, or None when the list
    can't be read. A read in which some state lists no senator at all is
    refused too: both seats of one state empty at once is not something the
    Senate's list has shown, and a list cut short looks exactly like it."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    seated: set[tuple[int, str]] = set()
    for member in root.iterfind("member"):
        number = _ROMAN.get((member.findtext("class") or "").strip().removeprefix("Class ").strip())
        state = (member.findtext("state") or "").strip()
        if number and len(state) == 2:
            seated.add((number, state))
    states = frozenset().union(*classes.values()) if classes else frozenset()
    if not seated or any(not any((n, s) in seated for n in classes) for s in states):
        return None
    return [
        {"id": f"S-{state}-{number}", "office": "S", "state": state, "district": None,
         "senate_class": number, "predecessor": None, "predecessor_party": None,
         "vacated_on": None, "footnote": None, "source_url": SENATORS_URL}
        for number, members in sorted(classes.items()) for state in sorted(members)
        if (number, state) not in seated
    ]


async def _get(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        resp = await client.get(url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("%s unavailable: %s", url, e)
        return None
    return resp.content


def save_vacancies(db: Session, office: str, vacancies: list[dict]) -> None:
    """Replace one chamber's vacancies with a read that succeeded."""
    db.query(SeatVacancy).filter(SeatVacancy.office == office).delete(synchronize_session=False)
    now = utcnow()
    for v in vacancies:
        db.add(SeatVacancy(**v, read_at=now))
    db.flush()


async def refresh_special_elections(db: Session, client: httpx.AsyncClient, today: date, cycle: int) -> dict:
    """Read and store the calendar, the reporting deadlines and both
    chambers' vacancies. Returns {"new": [ids new to the calendar],
    "problems": [what could not be read]}; never raises for a source."""
    from app.election_calendar import senate_classes

    read_on = today.isoformat()
    problems: list[str] = []
    new: list[str] = []
    for year in sorted({today.year, cycle}):
        rows, complete = await fetch_fec_rows(client, year)
        if rows:
            new += save_election_rows(db, year, rows, complete=complete, read_on=read_on)
        if not complete:
            problems.append(f"FEC election dates {year}: read incomplete, nothing retracted")
    for year in reporting_years(today):
        rows, complete = await fetch_reporting_deadlines(client, year)
        if rows:
            save_reporting_deadlines(db, year, rows, complete=complete, read_on=read_on)
        if not complete:
            problems.append(f"FEC reporting dates {year}: read incomplete, nothing retracted")

    house = parse_house_vacancies(await _get(client, MEMBER_DATA_URL) or b"")
    if house is None:
        problems.append("House Clerk member list: unreadable, House vacancies kept as stored")
    else:
        save_vacancies(db, "H", house)
    senate = parse_senate_vacancies(await _get(client, SENATORS_URL) or b"", senate_classes())
    if senate is None:
        problems.append("Senate member list: unreadable or cut short, Senate vacancies kept as stored")
    else:
        save_vacancies(db, "S", senate)
    db.commit()
    return {"new": new, "problems": problems}
