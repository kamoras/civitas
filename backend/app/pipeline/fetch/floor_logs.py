"""Each chamber's own same-day floor log, and roll-call tallies.

The Daily Digest (daily_digest.py) is the final record of a day, but GPO
publishes it the next day. During the day each chamber publishes its own
floor log, and both are structured XML:

- House: the Clerk's floor summary, one file per legislative day
  (clerk.house.gov/floor/YYYYMMDD.xml; /FloorSummary/ redirects there). Every floor action is
  timestamped and coded (act-id H20100 "The House convened", H61000
  adjourned), with the bill it concerns in <action_item>.
- Senate: the floor activity log behind senate.gov's "Senate Floor
  Activity" page (.../floor_activity/MM_DD_YYYY_Senate_Floor.xml): every
  measure acted on with its number, sponsor, title and status lines
  ("Passed Senate without amendment by Unanimous Consent."), plus the
  opening and adjournment paragraphs.

Wording is kept as published. The only derived values are the convening
and adjournment times, read off the chamber's own entries.
"""

import re
from datetime import date

from lxml import etree

_TIME_RE = re.compile(r"(\d{1,2}(?::\d{2})?\s?[ap]\s?\.?\s?m\.?|12 noon|noon)", re.IGNORECASE)

# ── Bill numbers in the chambers' own spellings ───────────────────

_BILL_PREFIXES = {"HR", "HRES", "HJRES", "HCONRES", "S", "SRES", "SJRES", "SCONRES"}


def bill_id_from_number(text: str | None) -> str | None:
    """"H R 9576" (House roll call), "H.Con.Res. 89" (Senate floor log),
    "S. 4668" -> the site's bill id ("HR.9576", "HCONRES.89", "S.4668").
    None for anything that is not a bill or resolution (a nomination
    "PN123", an empty field)."""
    m = re.match(r"^\s*([A-Za-z][A-Za-z .]*?)\s*(\d+)\s*$", text or "")
    if not m:
        return None
    prefix = re.sub(r"[^A-Za-z]", "", m.group(1)).upper()
    return f"{prefix}.{int(m.group(2))}" if prefix in _BILL_PREFIXES else None


def _text(el) -> str:
    """An element's text, whitespace collapsed. The Senate's XML wraps
    lines anywhere ("10 a\n.m.", "on today\n."), so a space the wrap left
    before punctuation is removed."""
    if el is None:
        return ""
    text = " ".join("".join(el.itertext()).split())
    text = re.sub(r"\b([ap]) \.m\.", r"\1.m.", text)
    return re.sub(r" ([.,;:])(?=\s|$)", r"\1", text)


# ── House ─────────────────────────────────────────────────────────

def house_floor_url(day: date) -> str:
    return f"https://clerk.house.gov/floor/{day:%Y%m%d}.xml"


def parse_house_floor(xml: bytes) -> dict | None:
    """The Clerk's floor summary -> {finished, next_meeting_iso, convened_at,
    adjourned_at, events} with events oldest first; None if unreadable."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    actions = root.findall(".//floor_action")
    finished_el = root.find(".//legislative_day_finished")
    events = []
    convened = adjourned = None
    for seq, act in enumerate(reversed(actions)):  # the file lists newest first
        time_el = act.find("action_time")
        clock = _text(time_el).rstrip(" -")
        description = _text(act.find("action_description"))
        code = act.get("act-id", "")
        if code == "H20100" and convened is None:
            convened = clock
        if code == "H61000":
            adjourned = clock
        events.append({
            "kind": "floor", "seq": seq, "time": clock, "name": _text(act.find("action_item")),
            "text": description, "bill_id": bill_id_from_number(_text(act.find("action_item"))),
            "code": code,
        })
    return {
        "finished": (finished_el is not None and _text(finished_el).lower() == "yes"),
        "next_meeting_iso": finished_el.get("next-legislative-day-convenes") if finished_el is not None else None,
        "convened_at": convened,
        "adjourned_at": adjourned,
        "events": events,
    }


# ── Senate ────────────────────────────────────────────────────────

def senate_floor_url(day: date) -> str:
    return f"https://www.senate.gov/legislative/LIS/floor_activity/{day:%m_%d_%Y}_Senate_Floor.xml"


def parse_senate_floor(xml: bytes) -> dict | None:
    """The Senate floor log -> {in_session, convened_at, adjourned_at,
    adjournment_text, next_meeting, events}; one event per measure acted on
    (its status lines joined with " -- ", as the Senate prints them) and one
    per section of prose.

    On a day the Senate does not meet it still publishes a file, holding
    only <convsched> ("The Senate is scheduled to reconvene at 3 p.m.
    Monday, September 28, 2026."): no opening, no sections. That is a day
    not in session, never a day it met and did nothing."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    intro = _text(root.find("intro_text"))
    events: list[dict] = []
    adjournment = ""

    def add(**kw):
        events.append({"kind": "floor", "seq": len(events), "time": None, **kw})

    if intro:
        add(name="", text=intro, bill_id=None)
    for section in root.iter("section"):
        title = _text(section.find("section_title"))
        content = _text(section.find("content"))
        if section.get("type") == "adjournment":
            adjournment = content
        documents = section.findall("document")
        for doc in documents:
            number = _text(doc.find("docnum"))
            status = " -- ".join(_text(s) for s in doc.findall("document_status_text") if _text(s))
            sponsor = _text(doc.find("sponsor_name"))
            add(name=f"{number} ({sponsor})" if sponsor else number,
                text=f"{_text(doc.find('document_title'))} -- {status}" if status else _text(doc.find("document_title")),
                bill_id=bill_id_from_number(number))
        if content and not documents:
            add(name=title, text=content, bill_id=None)

    convened = _TIME_RE.search(intro)
    adjourned = _TIME_RE.search(adjournment)
    schedule = _text(root.find("convsched"))
    return {
        "in_session": bool(events),
        "next_meeting": schedule or None,
        "convened_at": convened.group(1) if convened else None,
        "adjourned_at": adjourned.group(1) if adjourned else None,
        "adjournment_text": adjournment,
        "events": events,
    }


# ── Roll calls ────────────────────────────────────────────────────

def senate_roll_call_url(congress: int, session: int, number: int) -> str:
    return (f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
            f"vote{congress}{session}/vote_{congress}_{session}_{number:05d}.xml")


def house_roll_call_url(year: int, number: int) -> str:
    return f"https://clerk.house.gov/evs/{year}/roll{number:03d}.xml"


YEA_POSITIONS = {"yea", "aye", "guilty"}
NAY_POSITIONS = {"nay", "no", "not guilty"}


def tally(members: list[dict]) -> dict:
    """{yeas, nays, present, not_voting} counted from each member's
    recorded position, so the stored totals always equal the stored
    positions (the House records "Aye"/"No" on some questions)."""
    out = {"yeas": 0, "nays": 0, "present": 0, "not_voting": 0}
    for m in members:
        cast = (m.get("voteCast") or "").strip().lower()
        if cast in YEA_POSITIONS:
            out["yeas"] += 1
        elif cast in NAY_POSITIONS:
            out["nays"] += 1
        elif cast == "present":
            out["present"] += 1
        else:
            out["not_voting"] += 1
    return out
