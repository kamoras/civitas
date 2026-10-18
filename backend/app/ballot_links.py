"""An Action Center issue's ballot links, as served: for each state the
issue names (ActionIssue.ballot_links, matched when the issue was written:
action_center.ballot_links), its races, the next registration deadline from
the voter calendar, where to register and the state's official voter
lookup. The deadline and links are read when the issue is served, so a
stored issue never shows yesterday's countdown.

Read path only: JSON and files, no model.
"""

import json
from datetime import date, datetime

from app.pipeline.fetch.ballot_lookup import lookup_for_state
from app.state_names import STATE_NAMES
from app.voter_calendar import calendar_for_state


def race_label(race_id: str) -> str:
    """"Senate", "Senate special" or "GA-2" (at-large: "AK-AL") from a race
    id ("2026-SEN-GA", "2026-SEN-OH-SPECIAL", "2026-HOUSE-GA-2")."""
    parts = race_id.split("-")
    if len(parts) < 3:
        return race_id
    if parts[1] == "SEN":
        return "Senate special" if parts[-1] == "SPECIAL" else "Senate"
    district = parts[3] if len(parts) > 3 else "0"
    return f"{parts[2]}-{'AL' if district in ('0', '00') else int(district)}"


def parse(raw: str | None) -> list[dict]:
    try:
        links = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [link for link in links if isinstance(link, dict) and link.get("state") in STATE_NAMES]


def _deadline(cal: dict) -> dict | None:
    """What the state's voter calendar says comes next: registration while
    it is open ("Register online by Monday, October 19"), else the next
    open deadline in its own words (early voting, a mail ballot), else that
    registration has closed. None without a calendar for the election."""
    reg = cal.get("registration")
    if reg and reg.get("status") == "open":
        return {"kind": "registration", "sentence": reg["sentence"], "closesOn": reg.get("closesOn"),
                "daysLeft": reg.get("daysLeft")}
    nxt = cal.get("next")
    if nxt:
        return {"kind": nxt.get("kind"), "sentence": nxt.get("sentence"), "closesOn": nxt.get("closesOn"),
                "daysLeft": nxt.get("daysLeft")}
    if reg:
        return {"kind": "registration_closed", "sentence": reg["sentence"], "closesOn": None, "daysLeft": None}
    return None


def issue_ballot(raw: str | None, election_day: date, now: datetime | None = None) -> list[dict]:
    """One entry per state, in the order the issue names them: its races
    (each with the nominee the story named, if any), the voter calendar's
    next deadline and register link for `election_day` (None when the
    calendar has none for it), and the state's official lookup."""
    by_state: dict[str, dict] = {}
    for link in parse(raw):
        state = link["state"]
        entry = by_state.get(state)
        if entry is None:
            cal = calendar_for_state(state, election_day, now) or {}
            entry = by_state[state] = {
                "state": state,
                "state_name": STATE_NAMES[state],
                "races": [],
                "deadline": _deadline(cal),
                "register_link": cal.get("register"),
                "lookup": lookup_for_state(state),
            }
        race_id = link.get("raceId")
        if race_id and all(r["id"] != race_id for r in entry["races"]):
            entry["races"].append({"id": race_id, "label": race_label(race_id),
                                   "candidate": link.get("candidate")})
    return list(by_state.values())
