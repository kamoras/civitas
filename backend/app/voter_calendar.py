"""Each state's voter deadlines, and where each one stands right now.

Read from app/data/election_calendar.json (scripts/build_election_calendar.py:
every rule quoted from the state's own election authority or its statute,
re-read and checked). Served in the state ballot API and the "this week"
strip.

Where a deadline stands is a question about the state's own clock: a
registration deadline of October 19 closes at the end of October 19 where
the voter is, not in UTC. A state spanning two zones carries the zone where
a deadline passes last, so a page never says "closed" while it is still
open somewhere in the state. Every sentence here is a template around the
file's dates; nothing is generated.

A deadline that has passed stays on the page and says so: a deadline that
quietly disappears reads as "there isn't one", which for registration is
the opposite of the truth.
"""

import pathlib
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.atomic_write import runtime_data_path
from app.file_cache import Uncached, files_stamp, read_json
from app.state_names import STATE_NAMES
from app.time_utils import utcnow


_PATH = pathlib.Path(__file__).resolve().parent / "data" / "election_calendar.json"
_cache: dict | None = None

UPCOMING, OPEN, CLOSED = "upcoming", "open", "closed"

_METHOD = {"online": "online", "mail": "by mail", "in_person": "in person", "any": ""}
# Kinds a voter acts on by a deadline, versus a window they can act during.
_WINDOWS = {"early_voting", "same_day_registration"}


_stamp = None


def _newest(paths: tuple) -> dict:
    """Of the copies that hold a calendar, the one for the latest election,
    then the latest built (`builtOn`; a copy without it counts as oldest),
    the refreshed one on a tie. The weekly refresh only rebuilds every few
    days, so a calendar shipped in the image after it (a state added, a
    citation fixed) is used at once rather than after the next refresh."""
    best, unreadable = None, False
    for path in paths:
        try:
            data = read_json(path)
        except OSError:
            unreadable = True
            continue
        if not (isinstance(data, dict) and "states" in data):
            continue
        key = (data.get("electionDate") or "", data.get("builtOn") or "")
        if best is None or key > (best.get("electionDate") or "", best.get("builtOn") or ""):
            best = data
    if unreadable:
        raise Uncached(best or {})
    return best or {}


def _load() -> dict:
    """The calendar in use: the newer of the refreshed copy on the data
    volume (rebuilt for each election by app/data_refresh.py) and the
    bundled one at _PATH (_newest). Re-read when either file changes, so
    the API process sees a refresh the pipeline process wrote."""
    global _cache, _stamp
    paths = (runtime_data_path(_PATH.name), _PATH)
    stamp = files_stamp(paths)
    if _cache is None or stamp != _stamp:
        try:
            _cache = _newest(paths)
        except Uncached as unreadable:
            return unreadable.value
        _stamp = stamp
    return _cache


def register_pages() -> tuple[dict[str, dict], dict | None]:
    """Each state's `register` entry as the calendar file holds it (a
    state with none is absent; North Dakota's says `none`), and the
    registerFallback: the candidates ballot_lookup's nightly check verifies,
    so the register step reads the one copy of each URL."""
    data = _load()
    return ({code: e["register"] for code, e in (data.get("states") or {}).items() if e.get("register")},
            data.get("registerFallback"))


def calendar_election_date() -> str | None:
    """The election the calendar file was built for (ISO date), or None."""
    return _load().get("electionDate")


def short_date(d: date) -> str:
    return f"{d:%b} {d.day}"


def _clock(hhmm: str | None) -> str:
    if not hhmm:
        return ""
    t = time.fromisoformat(hhmm)
    if (t.hour, t.minute) == (12, 0):
        return ", noon"
    hour = t.hour % 12 or 12
    mins = f":{t.minute:02d}" if t.minute else ""
    return f", {hour}{mins} {'a.m.' if t.hour < 12 else 'p.m.'}"


def _countdown(days: int) -> str:
    return "today" if days == 0 else "tomorrow" if days == 1 else f"{days} days"


def _label(item: dict) -> str:
    kind, method = item["kind"], item.get("method") or "any"
    if kind == "registration":
        return {"online": "Online registration", "mail": "Registration by mail",
                "in_person": "In-person registration"}.get(method, "Registration")
    if kind == "same_day_registration":
        return "Same-day registration"
    if kind == "early_voting":
        return "Early in-person voting"
    if kind == "absentee_request":
        return {"online": "Online mail ballot requests", "mail": "Mail ballot requests sent by mail",
                "in_person": "In-person mail ballot requests"}.get(method, "Mail ballot requests")
    if method == "in_person":
        return "Mail ballots returned in person"
    return "Mail ballot return"


def _at(day: date, hhmm: str | None, zone: ZoneInfo) -> datetime:
    """The instant a day's deadline passes: its stated hour, else the day's end."""
    return datetime.combine(day, time.fromisoformat(hhmm) if hhmm else time.max, zone)


def _item(item: dict, now: datetime, today: date, zone: ZoneInfo) -> dict:
    start = date.fromisoformat(item["date"])
    end = date.fromisoformat(item.get("endDate") or item["date"])
    label = _label(item)
    kind = item["kind"]
    if kind in _WINDOWS:
        closes_on, closes = end, _at(end, item.get("endTime"), zone)
        opens = datetime.combine(start, time.fromisoformat(item["time"]) if item.get("time") else time.min, zone)
        status = CLOSED if now > closes else OPEN if now >= opens else UPCOMING
        span = short_date(start) if start == end else f"{short_date(start)} to {short_date(end)}"
        if status == UPCOMING:
            days = (start - today).days
            sentence = f"{label} opens {short_date(start)}" + (f", runs to {short_date(end)}" if end != start else "")
            countdown = _countdown(days)
        elif status == OPEN:
            days = (end - today).days
            sentence = f"{label} is open until {short_date(end)}{_clock(item.get('endTime'))}"
            countdown = _countdown(days)
        else:
            days, countdown = None, None
            sentence = f"{label} ran {span}"
    else:
        # A postmark deadline passes on its own day; the arrival date after
        # it (endDate) is when the ballot must be there, said alongside.
        closes_on, closes = start, _at(start, item.get("time"), zone)
        status = CLOSED if now > closes else OPEN
        days = (start - today).days if status == OPEN else None
        countdown = _countdown(days) if days is not None else None
        when = f"{short_date(start)}{_clock(item.get('time'))}"
        if kind == "ballot_return" and item.get("basis") == "postmarked":
            arrive = f" and arrive by {short_date(end)}{_clock(item.get('endTime'))}" if end != start else ""
            sentence = (f"Mail ballots must be postmarked by {when}{arrive}" if status == OPEN
                        else f"The postmark deadline for mail ballots was {when}")
        elif kind == "ballot_return":
            sentence = (f"{label}: must arrive by {when}" if status == OPEN
                        else f"{label}: the deadline was {when}")
        else:
            # Postmark or arrival only means something for paper sent by mail.
            by_mail = item.get("method") in ("mail", "any", None)
            basis = {"postmarked": " (postmark date)", "received": " (must arrive)"}.get(
                item.get("basis") or "", "") if by_mail else ""
            verb = "close" if kind == "absentee_request" else "closes"
            sentence = (f"{label} {verb} {when}{basis}" if status == OPEN
                        else f"{label} closed {when}")
    src = item["source"]
    return {
        "kind": kind,
        "method": item.get("method"),
        "basis": item.get("basis"),
        "label": label,
        "rule": item["rule"],
        "date": item["date"],
        "endDate": item.get("endDate"),
        "time": item.get("time"),
        "endTime": item.get("endTime"),
        "daysBefore": item.get("daysBefore"),
        "countedDate": item.get("countedDate"),
        "adjustment": item.get("adjustment"),
        "status": status,
        "daysLeft": days,
        "countdown": countdown,
        "sentence": sentence,
        "closesOn": closes_on.isoformat(),
        "_closes": closes,
        "source": {"publisher": src.get("publisher"), "url": src["url"], "type": src["type"]},
        "verification": item["verification"],
        "republishedBy": (item["verification"].get("republished") or {}).get("publisher"),
    }


def _with_countdown(i: dict) -> str:
    c = i["countdown"]
    if not c:
        return i["sentence"]
    if i["status"] == UPCOMING:
        return f"{i['sentence']} ({c if c in ('today', 'tomorrow') else 'in ' + c})"
    return f"{i['sentence']} ({c if c in ('today', 'tomorrow') else c + ' left'})"


def _registration_summary(items: list[dict]) -> str | None:
    """One plain sentence on where registration stands: when each way to
    register closes, or, once one has, what closed and what is still open."""
    reg = [i for i in items if i["kind"] in ("registration", "same_day_registration")]
    if not reg:
        return None
    closed = [i for i in reg if i["status"] == CLOSED]
    still = [i for i in reg if i["status"] != CLOSED]
    left = "; ".join(_with_countdown(i) for i in still)
    if not closed:
        return f"{left}."
    gone = "; ".join(f"{i['label']} closed {short_date(date.fromisoformat(i['closesOn']))}" for i in closed)
    if not still:
        return f"{gone}. No way to register for this election is still open."
    return f"{gone}. Still open: {left}."


# How a registration deadline reads as an instruction, by method.
_REGISTER_HOW = {"online": "Register online", "mail": "Mail your registration", "in_person": "Register in person",
                 "any": "Register"}
# Registration closing within this many days is listed in the "closing
# soon" strips (/elections and the homepage's This week).
CLOSING_SOON_DAYS = 10


def long_date(d: date) -> str:
    return f"{d:%a}, {d:%b} {d.day}"


def _registration_line(items: list[dict], entry: dict, state: str) -> dict | None:
    """The first thing a page says about registering: by when, and how,
    from the deadlines the file verified. The regular deadline that closes
    last (online is listed first on a tie), and the same-day or late
    registration that follows it in the state's own words; once regular
    registration has closed, the same-day rule alone, or that registration
    is closed. A state with no registration (North Dakota) says so from its
    quoted source. None where the file read no registration deadline."""
    name = STATE_NAMES.get(state.upper(), state)
    if (entry.get("register") or {}).get("none"):
        return {"status": "none", "sentence": f"{name} does not have voter registration.",
                "closesOn": None, "daysLeft": None, "countdown": None, "sameDay": None, "source": None}
    regular = [i for i in items if i["kind"] == "registration"]
    same_day = [i for i in items if i["kind"] == "same_day_registration" and i["status"] != CLOSED]
    open_regular = [i for i in regular if i["status"] == OPEN]
    after = same_day[0]["rule"] if same_day else None
    if open_regular:
        last = max(open_regular, key=lambda i: (i["_closes"], i.get("method") == "online"))
        when = f"{long_date(date.fromisoformat(last['closesOn']))}{_clock(last.get('time'))}"
        basis = {"postmarked": " (postmark date)", "received": " (must arrive)"}.get(last.get("basis") or "", "") \
            if last.get("method") in ("mail", "any", None) else ""
        sentence = f"{_REGISTER_HOW.get(last.get('method') or 'any', 'Register')} by {when}{basis}"
        return {"status": OPEN, "sentence": sentence, "closesOn": last["closesOn"], "daysLeft": last["daysLeft"],
                "countdown": last["countdown"], "sameDay": after, "source": last["source"]}
    if same_day:
        first = same_day[0]
        lead = "Regular registration has closed. " if regular else ""
        return {"status": "same_day", "sentence": f"{lead}{first['rule']}", "closesOn": first["closesOn"],
                "daysLeft": first["daysLeft"], "countdown": first["countdown"], "sameDay": None,
                "source": first["source"]}
    if regular:
        return {"status": CLOSED, "sentence": "Registration for this election has closed.", "closesOn": None,
                "daysLeft": None, "countdown": None, "sameDay": None, "source": None}
    return None


def _register_link(entry: dict, data: dict) -> dict | None:
    """Where to register: the state's own page the build script checked,
    else vote.gov. `confirmed` is false where a bot wall kept the check
    from reading it (the page says so, as for the voter lookup)."""
    reg = entry.get("register") or data.get("registerFallback")
    if not reg or reg.get("none"):
        return None
    return {"url": reg["url"], "publisher": reg.get("publisher"), "confirmed": reg.get("status") == "verified",
            "statePage": reg is entry.get("register")}


def _republished(items: list[dict], state: str) -> str | None:
    """Said visibly when deadlines were read from a county election office's
    printing because the state's own source couldn't be (the build script
    tries the state's own first, every run): whose printing, and why."""
    reps = [i["verification"].get("republished") for i in items if i["verification"]["status"] == "republished"]
    if not reps:
        return None
    name = STATE_NAMES.get(state.upper(), state)
    publishers = sorted({r["publisher"] for r in reps})
    walled = all("bot wall" in (r.get("stateSourceRead") or "") for r in reps)
    which = "These deadlines are" if len(reps) == len(items) else f"{len(reps)} of these deadlines are"
    why = "blocks automated reads" if walled else "couldn't be read when the list was rebuilt"
    return f"{which} as published by the {' and the '.join(publishers)}; {name}'s own source {why}."


def _note(entry: dict, state: str) -> str | None:
    """The state's own note, and — when the calendar was carried to this
    election and some deadlines could not be confirmed for it — that some
    are missing, so a missing deadline never reads as there being none."""
    parts = [entry.get("note")] if entry.get("note") else []
    if entry.get("incomplete"):
        parts.append(
            f"Not every one of {STATE_NAMES.get(state.upper(), state)}'s deadlines is confirmed for this election yet; "
            "one not listed here may still apply, so check with your county election office."
        )
    return " ".join(parts) or None


def calendar_for_state(state: str, election_day: date, now: datetime | None = None) -> dict | None:
    """`state`'s voter calendar for `election_day`, with each deadline's
    status on the state's own clock; None when the file has no calendar for
    that election (a new cycle before the file is regenerated says nothing
    rather than last cycle's dates)."""
    data = _load()
    entry = (data.get("states") or {}).get(state.upper())
    if not entry or data.get("electionDate") != election_day.isoformat():
        return None
    zone = ZoneInfo(entry["timeZone"])
    local_now = (now or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    today = local_now.date()
    items = [_item(i, local_now, today, zone) for i in entry.get("items") or []]
    items.sort(key=lambda i: (i["_closes"], i["kind"]))
    nxt = next((i for i in items if i["status"] != CLOSED), None)
    line = _registration_line(items, entry, state)
    return {
        "timeZone": entry["timeZone"],
        "localToday": today.isoformat(),
        "note": _note(entry, state),
        "next": {k: v for k, v in nxt.items() if k != "_closes"} if nxt else None,
        "registrationSummary": _registration_summary(items),
        "registration": line,
        "republished": _republished(items, state),
        "register": _register_link(entry, data),
        "electionDay": election_day.isoformat(),
        "items": [{k: v for k, v in i.items() if k != "_closes"} for i in items],
        # A state none of whose deadlines could be read from an official
        # source lists none, rather than dates from anywhere else, and says
        # so; the page sends the reader to the state's own lookup.
        "unread": (
            f"We couldn't read {STATE_NAMES.get(state.upper(), state)}'s voter deadlines from its "
            "election board, so we don't list any."
        ) if entry.get("unread") else None,
    }


def next_deadlines(election_day: date, now: datetime | None = None) -> list[dict]:
    """Every state's next deadline, soonest first, for the "this week" strip."""
    rows = []
    for state in sorted((_load().get("states") or {})):
        cal = calendar_for_state(state, election_day, now)
        if cal and cal["next"]:
            n = cal["next"]
            rows.append({"state": state, "sentence": n["sentence"], "countdown": n["countdown"],
                         "status": n["status"], "daysLeft": n["daysLeft"], "kind": n["kind"],
                         "closesOn": n["closesOn"],
                         "register": cal["register"] if n["kind"] == "registration" else None})
    rows.sort(key=lambda r: (r["closesOn"], r["state"]))
    return rows



def registration_closing(election_day: date, now: datetime | None = None,
                         within: int = CLOSING_SOON_DAYS) -> list[dict]:
    """The states whose regular registration closes within `within` days
    on their own clock (today included), soonest first, each with how to
    register and whether same-day registration follows."""
    rows = []
    for state in sorted((_load().get("states") or {})):
        cal = calendar_for_state(state, election_day, now)
        line = (cal or {}).get("registration")
        if not line or line["status"] != OPEN or line["daysLeft"] is None or line["daysLeft"] > within:
            continue
        rows.append({"state": state, "stateName": STATE_NAMES.get(state, state), "sentence": line["sentence"],
                     "closesOn": line["closesOn"], "daysLeft": line["daysLeft"], "countdown": line["countdown"],
                     "sameDay": line["sameDay"], "register": cal["register"]})
    rows.sort(key=lambda r: (r["closesOn"], r["state"]))
    return rows
