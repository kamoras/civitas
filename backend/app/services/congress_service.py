"""The Congress reports: a day, a week or a month of what each chamber did.

Built only from what congress_activity.py stored: the Daily Digest's
entries (quoted as published), the chambers' floor logs and the roll
calls. Every count and every summary sentence is computed here, from those
rows, by rule; nothing is generated. A sentence is a template filled with
counts, so it can say "passed 3 bills" and never characterise them.
"""

import re
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import CongressDay, CongressEvent, RepSponsoredBill, RollCall, SponsoredBill
from app.pipeline.congress_activity import digest_cursor, eastern_today, last_run
from app.pipeline.fetch.daily_digest import words_to_int
from app.pipeline.fetch.congress import congress_of_date

CHAMBERS = ("senate", "house")
_CHAMBER_NAME = {"senate": "Senate", "house": "House"}

_BILL_LABELS = {
    "HR": "H.R.", "S": "S.", "HRES": "H. Res.", "SRES": "S. Res.", "HJRES": "H.J. Res.",
    "SJRES": "S.J. Res.", "HCONRES": "H. Con. Res.", "SCONRES": "S. Con. Res.",
}
# Simple and concurrent resolutions never become law; bills and joint
# resolutions do. The reports count them apart ("3 bills, 4 resolutions").
_RESOLUTIONS = {"HRES", "SRES", "HCONRES", "SCONRES"}
# A simple resolution is one chamber's own business: it never goes to the other.
_SIMPLE_RESOLUTIONS = {"HRES", "SRES"}


def bill_label(bill_id: str | None) -> str | None:
    """"HCONRES.89" -> "H. Con. Res. 89", as the Record prints it."""
    if not bill_id or "." not in bill_id:
        return None
    prefix, number = bill_id.split(".", 1)
    label = _BILL_LABELS.get(prefix)
    return f"{label} {number}" if label else None


def _prefix(bill_id: str | None) -> str:
    return (bill_id or "").split(".", 1)[0]


def is_resolution(bill_id: str | None) -> bool:
    return _prefix(bill_id) in _RESOLUTIONS


def passed_both_chambers(bill_id: str | None, passing_chamber: str) -> bool:
    """A measure passed by the chamber it did not start in has passed both:
    it reached the second chamber only by passing the first. Says nothing
    about amendments still to be reconciled."""
    prefix = _prefix(bill_id)
    if not prefix or prefix in _SIMPLE_RESOLUTIONS:
        return False
    origin = "house" if prefix.startswith("H") else "senate"
    return origin != passing_chamber


_NOMINATIONS_RE = re.compile(r"^([\w-]+(?: hundred(?: and)?(?: [\w-]+)?)?) (?:[^.]*? )?nominations\b", re.IGNORECASE)


def nominations_in(text: str) -> int:
    """How many nominations one confirmation entry covers: "3 Coast Guard
    nominations in the rank of admiral." is 3; an entry naming one nominee
    is 1."""
    m = _NOMINATIONS_RE.match(text or "")
    if m:
        n = words_to_int(m.group(1))
        if n:
            return n
    return 1


_FRACTION_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)")
# Senate Rule XXII: cloture takes three-fifths of the senators duly chosen
# and sworn, not of those voting — 60 with no vacancy, fewer with one. A
# Senate roll call lists every sitting senator (yea, nay, present or not
# voting), so its own totals are that count on the day of the vote, with no
# seat count typed here.


def _sworn(rc: RollCall) -> int:
    return sum(n or 0 for n in (rc.yeas, rc.nays, rc.present, rc.not_voting))


def votes_from_threshold(rc: RollCall) -> float:
    """How far the yeas were from what the vote needed, in votes.

    The margin between yeas and nays is only that for a simple majority.
    Cloture that fails 59-41 is a single vote short of 60, and a
    suspension carried 290-140 two-thirds votes clear by 3; ranked by
    yeas minus nays those read as lopsided (18 and 150) and never make a
    "closest votes" list they belong at the top of."""
    m = _FRACTION_RE.match(rc.majority_requirement or "")
    num, den = (int(m.group(1)), int(m.group(2))) if m and int(m.group(2)) else (1, 2)
    if rc.chamber == "senate" and (num, den) == (3, 5):
        needed = _sworn(rc) * num / den
    else:
        needed = (rc.yeas + rc.nays) * num / den
    return abs(rc.yeas - needed)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _list_phrase(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# ── Rows to wire shapes ───────────────────────────────────────────

def _next_step(e: CongressEvent) -> str | None:
    """For a passed measure: "both" (it has now passed both chambers),
    "senate"/"house" (it goes there next), or None (a resolution, or not a
    passage)."""
    if e.kind != "passed" or not e.bill_id or is_resolution(e.bill_id):
        return None
    if passed_both_chambers(e.bill_id, e.chamber):
        return "both"
    return "house" if e.chamber == "senate" else "senate"


def _event(e: CongressEvent) -> dict:
    return {
        # The Congress the day fell in: a bill number names a different
        # bill in each, so a link to the bill needs it.
        "congress": congress_of_date(e.date),
        "kind": e.kind, "name": e.name, "text": e.text, "billId": e.bill_id,
        "billLabel": bill_label(e.bill_id), "isResolution": is_resolution(e.bill_id),
        "nextStep": _next_step(e),
        "time": e.time, "pages": e.pages, "date": e.date, "chamber": e.chamber,
    }


def _vote(rc: RollCall) -> dict:
    return {
        "chamber": rc.chamber, "congress": rc.congress, "session": rc.session, "number": rc.number,
        "date": rc.date, "question": rc.question, "title": rc.title, "result": rc.result,
        "rejected": rc.rejected, "majorityRequirement": rc.majority_requirement,
        "yeas": rc.yeas, "nays": rc.nays, "present": rc.present, "notVoting": rc.not_voting,
        "billId": rc.bill_id, "billLabel": bill_label(rc.bill_id), "sourceUrl": rc.source_url,
    }


def _counts(events: list[CongressEvent], votes: list[RollCall]) -> dict:
    passed = [e for e in events if e.kind == "passed"]
    return {
        "recordVotes": len(votes),
        "billsPassed": sum(1 for e in passed if not is_resolution(e.bill_id)),
        "resolutionsPassed": sum(1 for e in passed if is_resolution(e.bill_id)),
        "failed": sum(1 for e in events if e.kind == "failed"),
        "reported": sum(1 for e in events if e.kind == "reported"),
        "confirmed": sum(nominations_in(e.text) for e in events if e.kind == "confirmed"),
        "committeeMeetings": sum(1 for e in events if e.kind == "committee"),
    }


# ── A day ─────────────────────────────────────────────────────────

def _minutes(convened: str | None, adjourned: str | None) -> int | None:
    """Minutes between two of the record's clock times ("2:30 p.m.",
    "2:33:35 P.M.", "12 noon"), or None when either is missing or the
    day ran past midnight."""
    def parse(t: str | None) -> int | None:
        if not t:
            return None
        t = t.lower().replace(" ", "")
        if "noon" in t:
            return 12 * 60
        m = re.match(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?([ap])\.?m\.?", t)
        if not m:
            return None
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0)
        return hour * 60 + int(m.group(2) or 0)
    a, b = parse(convened), parse(adjourned)
    return b - a if a is not None and b is not None and b >= a else None


def _chamber_sentence(chamber: str, day: dict) -> str:
    name = _CHAMBER_NAME[chamber]
    status = day["status"]
    if status == "not_in_session":
        return f"The {name} did not meet."
    if status == "no_record":
        return f"No record of the {name} for this day yet."
    if status == "no_record_published":
        return f"The {name} has no Congressional Record for this day."
    c = day["counts"]
    parts = []
    if c["billsPassed"]:
        parts.append("passed " + _plural(c["billsPassed"], "bill"))
    if c["resolutionsPassed"]:
        parts.append("agreed to " + _plural(c["resolutionsPassed"], "resolution"))
    if c["confirmed"]:
        parts.append("confirmed " + _plural(c["confirmed"], "nomination"))
    if c["recordVotes"]:
        parts.append("took " + _plural(c["recordVotes"], "record vote"))
    if parts:
        return f"The {name} {_list_phrase(parts)}."
    minutes = day["minutesInSession"]
    if "pro forma session" in (day.get("adjournmentText") or "").lower():
        length = f" for {_plural(minutes, 'minute')}" if minutes is not None else ""
        return f"The {name} met in pro forma session{length}."
    if minutes is not None and minutes < 60:
        return f"The {name} met for {_plural(minutes, 'minute')} and took no record votes."
    return f"The {name} met and took no record votes."


def _source_outcome(run: dict | None, day_iso: str, chamber: str) -> str | None:
    if not run:
        return None
    return ((run.get("floorLogs") or {}).get(day_iso) or {}).get(chamber)


# GPO posts a day's Record by the next evening; a Record still missing
# three days on was not published.
_RECORD_SETTLED_DAYS = 3


def record_not_published(db: Session, day: date, run: dict | None) -> bool:
    """Whether the Congressional Record for `day` is known not to exist.
    GPO publishes it for every day either chamber is in session, so this
    is how a day with no session looks; the page says what is known — no
    Record — rather than asserting who met (GPO very rarely prints two
    small consecutive days as one issue). Known two ways: the back-fill
    cursor is past the day (it stops before any day it could not read),
    or the last run found no Record for a day at least three days old."""
    if day <= digest_cursor(db):
        return True
    iso = day.isoformat()
    settled = day <= eastern_today() - timedelta(days=_RECORD_SETTLED_DAYS)
    return settled and ((run or {}).get("digests") or {}).get(iso) == "absent"


def chamber_day(row: CongressDay | None, events: list[CongressEvent], votes: list[RollCall],
                run: dict | None, day_iso: str, chamber: str, no_record_published: bool = False) -> dict:
    if row is None:
        status = "live" if votes else ("no_record_published" if no_record_published else "no_record")
    elif not row.in_session:
        status = "not_in_session"
    else:
        status = "final" if row.is_final else "live"
    digest = [e for e in events if e.source == "digest"]
    floor = sorted((e for e in events if e.source == "floor_log"), key=lambda e: e.seq)
    by_kind = defaultdict(list)
    for e in sorted(digest, key=lambda e: e.seq):
        by_kind[e.kind].append(_event(e))
    out = {
        "chamber": chamber,
        "status": status,
        "convenedAt": row.convened_at if row else None,
        "adjournedAt": row.adjourned_at if row else None,
        "minutesInSession": _minutes(row.convened_at, row.adjourned_at) if row else None,
        "adjournmentText": row.adjournment_text if row else "",
        "nextMeeting": row.next_meeting if row else None,
        "nextProgram": row.next_program if row else "",
        "billsIntroduced": row.bills_introduced if row else None,
        "resolutionsIntroduced": row.resolutions_introduced if row else None,
        "introducedText": row.introduced_text if row else "",
        "source": row.source if row else None,
        "sourceUrl": row.source_url if row else None,
        "fetchedAt": row.fetched_at.isoformat() if row and row.fetched_at else None,
        # The last run's outcome for this day's floor log: "failed" lets the
        # page say the log is unavailable instead of showing nothing.
        "floorLogStatus": _source_outcome(run, day_iso, chamber),
        "counts": _counts(digest, votes),
        "votes": [_vote(v) for v in sorted(votes, key=lambda v: v.number)],
        "passed": by_kind["passed"],
        "failed": by_kind["failed"],
        "reported": by_kind["reported"],
        "confirmed": by_kind["confirmed"],
        "committees": by_kind["committee"],
        "floorLog": [_event(e) for e in floor],
    }
    out["sentence"] = _chamber_sentence(chamber, out)
    return out


def session_days(db: Session) -> list[str]:
    """Every day either chamber met (in session, or held a roll call), oldest first."""
    days = {d for (d,) in db.query(CongressDay.date).filter(CongressDay.in_session.is_(True))}
    days |= {d for (d,) in db.query(RollCall.date).distinct()}
    return sorted(d for d in days if d)


def record_span(db: Session) -> tuple[date, date] | None:
    """(the first day either chamber is on record as meeting, today): the
    dates a report can say anything about. Outside it a report is only
    placeholders ("no record of the Senate for this day yet"), and a page
    for every such date is an unbounded set of pages a crawler can walk
    (2026-10: one walked dates into 2027 from rotating addresses, each
    counted as a new visitor). None before anything is recorded."""
    days = session_days(db)
    return (date.fromisoformat(days[0]), eastern_today()) if days else None


def latest_day(db: Session) -> date | None:
    today = eastern_today().isoformat()
    past = [d for d in session_days(db) if d <= today]
    return date.fromisoformat(past[-1]) if past else None


def day_report(db: Session, day: date) -> dict:
    iso = day.isoformat()
    rows = {r.chamber: r for r in db.query(CongressDay).filter(CongressDay.date == iso)}
    events = db.query(CongressEvent).filter(CongressEvent.date == iso).all()
    votes = db.query(RollCall).filter(RollCall.date == iso).all()
    run = last_run(db)
    unpublished = not rows and not votes and record_not_published(db, day, run)
    chambers = {
        c: chamber_day(rows.get(c), [e for e in events if e.chamber == c],
                       [v for v in votes if v.chamber == c], run, iso, c, no_record_published=unpublished)
        for c in CHAMBERS
    }
    met = session_days(db)
    earlier = [d for d in met if d < iso]
    later = [d for d in met if d > iso]
    return {
        "date": iso,
        "sentence": (
            "No Congressional Record was published for this day. It is published for every day either chamber is in session."
            if unpublished else " ".join(chambers[c]["sentence"] for c in CHAMBERS)
        ),
        "chambers": chambers,
        "previousDay": earlier[-1] if earlier else None,
        "nextDay": later[0] if later else None,
        "syncedAt": (run or {}).get("finishedAt"),
    }


# ── A week or a month ─────────────────────────────────────────────

def _period(db: Session, start: date, end: date) -> dict:
    """Everything recorded from start through end, inclusive."""
    s, e = start.isoformat(), end.isoformat()
    rows = db.query(CongressDay).filter(CongressDay.date >= s, CongressDay.date <= e).all()
    events = db.query(CongressEvent).filter(
        CongressEvent.date >= s, CongressEvent.date <= e, CongressEvent.source == "digest",
    ).all()
    votes = db.query(RollCall).filter(RollCall.date >= s, RollCall.date <= e).all()
    return {"rows": rows, "events": events, "votes": votes}


def _totals(p: dict, chamber: str) -> dict:
    rows = [r for r in p["rows"] if r.chamber == chamber]
    events = [e for e in p["events"] if e.chamber == chamber]
    votes = [v for v in p["votes"] if v.chamber == chamber]
    days_in_session = {r.date for r in rows if r.in_session} | {v.date for v in votes}
    counts = _counts(events, votes)
    counts["daysInSession"] = len(days_in_session)
    counts["billsIntroduced"] = sum(r.bills_introduced or 0 for r in rows)
    counts["resolutionsIntroduced"] = sum(r.resolutions_introduced or 0 for r in rows)
    # Days whose record is still the live floor log: their passed/reported
    # entries arrive with the Digest, so these totals can still grow.
    counts["daysPending"] = sum(1 for r in rows if r.in_session and not r.is_final)
    return counts


def _period_sentence(totals: dict) -> str:
    sentences = []
    for chamber in CHAMBERS:
        t = totals[chamber]
        name = _CHAMBER_NAME[chamber]
        if not t["daysInSession"]:
            sentences.append(f"The {name} did not meet.")
            continue
        parts = ["met " + _plural(t["daysInSession"], "day")]
        if t["recordVotes"]:
            parts.append("took " + _plural(t["recordVotes"], "record vote"))
        if t["billsPassed"]:
            parts.append("passed " + _plural(t["billsPassed"], "bill"))
        if t["confirmed"]:
            parts.append("confirmed " + _plural(t["confirmed"], "nomination"))
        if len(parts) == 1:
            parts.append("took no record votes")
        sentences.append(f"The {name} {_list_phrase(parts)}.")
    return " ".join(sentences)


def _became_law(db: Session, start: date, end: date) -> list[dict]:
    """Bills whose latest action, dated in the period, is becoming law.
    Read from the sponsored-bill rows, so a bill no current member
    sponsored is not listed."""
    s, e = start.isoformat(), end.isoformat()
    seen: dict[tuple[str, int | None], dict] = {}
    for model in (SponsoredBill, RepSponsoredBill):
        for b in db.query(model).filter(
            model.is_law.is_(True), model.latest_action_date >= s, model.latest_action_date <= e,
        ):
            seen.setdefault((b.bill_id, b.congress), {
                "billId": b.bill_id, "billLabel": bill_label(b.bill_id), "name": b.title, "congress": b.congress,
                "date": b.latest_action_date, "text": b.latest_action,
            })
    return sorted(seen.values(), key=lambda b: b["date"])


def period_report(db: Session, start: date, end: date) -> dict:
    p = _period(db, start, end)
    totals = {c: _totals(p, c) for c in CHAMBERS}
    passed = sorted((e for e in p["events"] if e.kind == "passed"), key=lambda e: (e.date, e.seq))
    both = [_event(e) for e in passed if passed_both_chambers(e.bill_id, e.chamber)]
    one = [_event(e) for e in passed
           if not is_resolution(e.bill_id) and e.bill_id and not passed_both_chambers(e.bill_id, e.chamber)]
    decided = [v for v in p["votes"] if v.yeas + v.nays > 0]
    closest = sorted(decided, key=lambda v: (votes_from_threshold(v), v.date, v.number))[:3]

    days = []
    run = last_run(db)
    d = start
    while d <= end:
        iso = d.isoformat()
        entry = {"date": iso}
        nothing = not any(r.date == iso for r in p["rows"]) and not any(v.date == iso for v in p["votes"])
        entry["noRecordPublished"] = nothing and record_not_published(db, d, run)
        for c in CHAMBERS:
            row = next((r for r in p["rows"] if r.chamber == c and r.date == iso), None)
            votes = [v for v in p["votes"] if v.chamber == c and v.date == iso]
            events = [e for e in p["events"] if e.chamber == c and e.date == iso]
            in_session = bool(votes) or bool(row and row.in_session)
            entry[c] = {
                "inSession": in_session,
                "recorded": row is not None or bool(votes),
                "convenedAt": row.convened_at if row else None,
                "adjournedAt": row.adjourned_at if row else None,
                "minutesInSession": _minutes(row.convened_at, row.adjourned_at) if row else None,
                "counts": _counts(events, votes),
            }
        days.append(entry)
        d += timedelta(days=1)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sentence": _period_sentence(totals),
        "totals": totals,
        "days": days,
        "passedBothChambers": both,
        "passedOneChamber": one,
        "closestVotes": [_vote(v) for v in closest],
        "votes": [_vote(v) for v in sorted(p["votes"], key=lambda v: (v.date, v.chamber, v.number))],
        "becameLaw": _became_law(db, start, end),
        "syncedAt": (last_run(db) or {}).get("finishedAt"),
    }


def week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def _on_record(span: tuple[date, date] | None, first: date, last: date) -> bool:
    """Whether a period overlaps the days on record (record_span)."""
    return span is not None and last >= span[0] and first <= span[1]


def week_report(db: Session, day: date) -> dict:
    start, end = week_bounds(day)
    out = period_report(db, start, end)
    # Only a neighbour with a record to show: an always-present link let a
    # crawler walk weeks without end in both directions.
    span = record_span(db)
    before, after = start - timedelta(days=7), start + timedelta(days=7)
    out["previous"] = before.isoformat() if _on_record(span, before, before + timedelta(days=6)) else None
    out["next"] = after.isoformat() if _on_record(span, after, after + timedelta(days=6)) else None
    return out


def month_report(db: Session, year: int, month: int) -> dict:
    start, end = month_bounds(year, month)
    out = period_report(db, start, end)
    weeks = []
    monday, _ = week_bounds(start)
    while monday <= end:
        w_start, w_end = max(monday, start), min(monday + timedelta(days=6), end)
        p = _period(db, w_start, w_end)
        totals = {c: _totals(p, c) for c in CHAMBERS}
        weeks.append({"week": monday.isoformat(), "start": w_start.isoformat(), "end": w_end.isoformat(),
                      "sentence": _period_sentence(totals), "totals": totals})
        monday += timedelta(days=7)
    out["weeks"] = weeks
    span = record_span(db)
    before = month_bounds((start - timedelta(days=1)).year, (start - timedelta(days=1)).month)
    after = month_bounds((end + timedelta(days=1)).year, (end + timedelta(days=1)).month)
    out["previous"] = before[0].strftime("%Y-%m") if _on_record(span, *before) else None
    out["next"] = after[0].strftime("%Y-%m") if _on_record(span, *after) else None
    return out


def bill_days(db: Session, bill_id: str) -> list[dict]:
    """The days a bill appears in the Congress record (for its detail
    page's "in the daily reports" list): Digest entries and roll calls."""
    rows = db.query(CongressEvent).filter(CongressEvent.bill_id == bill_id).all()
    out: dict[tuple[str, str], dict] = {}
    for e in rows:
        out.setdefault((e.date, e.chamber), {"date": e.date, "chamber": e.chamber, "entries": []})["entries"].append(
            {"kind": e.kind, "text": e.text, "source": e.source})
    for v in db.query(RollCall).filter(RollCall.bill_id == bill_id):
        out.setdefault((v.date, v.chamber), {"date": v.date, "chamber": v.chamber, "entries": []})["entries"].append(
            {"kind": "vote", "text": f"{v.question}: {v.result}, {v.yeas}-{v.nays}", "source": "roll_call"})
    return sorted(out.values(), key=lambda d: d["date"], reverse=True)
