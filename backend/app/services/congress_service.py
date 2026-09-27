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
from app.pipeline.congress_activity import eastern_today, last_run
from app.pipeline.fetch.daily_digest import words_to_int

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


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _list_phrase(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# ── Rows to wire shapes ───────────────────────────────────────────

def _event(e: CongressEvent) -> dict:
    return {
        "kind": e.kind, "name": e.name, "text": e.text, "billId": e.bill_id,
        "billLabel": bill_label(e.bill_id), "isResolution": is_resolution(e.bill_id),
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
    if minutes is not None and minutes < 60:
        return f"The {name} met for {_plural(minutes, 'minute')} and took no record votes."
    return f"The {name} met and took no record votes."


def _source_outcome(run: dict | None, day_iso: str, chamber: str) -> str | None:
    if not run:
        return None
    return ((run.get("floorLogs") or {}).get(day_iso) or {}).get(chamber)


def chamber_day(row: CongressDay | None, events: list[CongressEvent], votes: list[RollCall],
                run: dict | None, day_iso: str, chamber: str) -> dict:
    if row is None:
        status = "live" if votes else "no_record"
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


def _session_days(db: Session) -> list[str]:
    days = {d for (d,) in db.query(CongressDay.date).filter(CongressDay.in_session.is_(True))}
    days |= {d for (d,) in db.query(RollCall.date).distinct()}
    return sorted(d for d in days if d)


def latest_day(db: Session) -> date | None:
    today = eastern_today().isoformat()
    past = [d for d in _session_days(db) if d <= today]
    return date.fromisoformat(past[-1]) if past else None


def day_report(db: Session, day: date) -> dict:
    iso = day.isoformat()
    rows = {r.chamber: r for r in db.query(CongressDay).filter(CongressDay.date == iso)}
    events = db.query(CongressEvent).filter(CongressEvent.date == iso).all()
    votes = db.query(RollCall).filter(RollCall.date == iso).all()
    run = last_run(db)
    chambers = {
        c: chamber_day(rows.get(c), [e for e in events if e.chamber == c],
                       [v for v in votes if v.chamber == c], run, iso, c)
        for c in CHAMBERS
    }
    session_days = _session_days(db)
    earlier = [d for d in session_days if d < iso]
    later = [d for d in session_days if d > iso]
    return {
        "date": iso,
        "sentence": " ".join(chambers[c]["sentence"] for c in CHAMBERS),
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
    seen: dict[str, dict] = {}
    for model in (SponsoredBill, RepSponsoredBill):
        for b in db.query(model).filter(
            model.is_law.is_(True), model.latest_action_date >= s, model.latest_action_date <= e,
        ):
            seen.setdefault(b.bill_id, {
                "billId": b.bill_id, "billLabel": bill_label(b.bill_id), "name": b.title,
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
    closest = sorted(decided, key=lambda v: (abs(v.yeas - v.nays), v.date, v.number))[:3]

    days = []
    d = start
    while d <= end:
        iso = d.isoformat()
        entry = {"date": iso}
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


def week_report(db: Session, day: date) -> dict:
    start, end = week_bounds(day)
    out = period_report(db, start, end)
    out["previous"] = (start - timedelta(days=7)).isoformat()
    out["next"] = (start + timedelta(days=7)).isoformat()
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
    out["previous"] = (start - timedelta(days=1)).strftime("%Y-%m")
    out["next"] = (end + timedelta(days=1)).strftime("%Y-%m")
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
