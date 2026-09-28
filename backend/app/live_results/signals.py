"""A seat changing party on election night, as a DEVELOPING Action Center
issue — the same primary-source-first lifecycle early_signal.py gives a
roll-call vote: drafted from the source before the press has it, ranked
below every confirmed issue, promoted when coverage matches it
(action_center._promote_developing_issue), retired unconfirmed at its
deadline (early_signal.expire_stale_developing_issues).

Written from templates, never by the model. early_signal asks the LLM for
a vote's summary because a bill needs describing; a count does not, and
the one thing a generated sentence could get wrong here — which way a
race is going — is the whole story. Every sentence below is a fixed
frame around the state's own figures, and says "leads" until the state
itself calls the count official.

The issue follows the count. Each sync refreshes its facts while the flip
holds; if the lead reverts to the holder's party the issue is retired
(is_current=False, never deleted), and it comes back if the flip does,
until its confirmation deadline has passed.
"""

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import ActionIssue, ActionIssueStatus, RaceResult
from app.pipeline.analyze.early_signal import CONFIRMATION_WINDOW_HOURS
from app.live_results.sync import event_detail, is_flip
from app.pipeline.fetch.district_pvi import STATE_NAMES
from app.time_utils import utcnow

SOURCE_TYPE = "election_results"

_PARTY_WORDS = {
    "DEM": ("Democrat", "Democrats"),
    "REP": ("Republican", "Republicans"),
    "IND": ("independent", "independents"),
    "LIB": ("Libertarian", "Libertarians"),
    "GRE": ("Green", "Greens"),
    "CON": ("Constitution Party candidate", "the Constitution Party"),
}
_PARTY_LETTER = {"DEM": "D", "REP": "R", "IND": "I", "LIB": "L", "GRE": "G", "CON": "C"}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def party_letter(group: str | None) -> str:
    return _PARTY_LETTER.get(group or "", group or "")


def holders_word(group: str | None) -> str:
    """"Democrats" — who hold(s) a seat, for "a seat Democrats hold"."""
    return _PARTY_WORDS.get(group or "", (group, group))[1] or "another party"


def race_label(race) -> str:
    state = STATE_NAMES.get(race.state, race.state)
    if race.office == "S":
        return f"{state}'s U.S. Senate{' special election' if race.is_special else ''}"
    if not race.district:
        return f"{state}'s at-large U.S. House seat"
    return f"{state}'s {_ordinal(race.district)} Congressional District"


def _person(p: dict) -> str:
    letter = party_letter(p.get("party"))
    return f"{p['name']} ({letter})" if letter else p["name"]


def _content(result: RaceResult) -> dict:
    d = event_detail(result)
    leader, runner = d["leader"], d["runnerUp"]
    noun = _PARTY_WORDS.get(leader["party"], (leader["party"], leader["party"]))[0]
    holders = holders_word(result.held_by_party)
    label = race_label(result.race)
    if result.official:
        title = f"{noun[:1].upper()}{noun[1:]} wins {label} in the official count, taking a seat {holders} held"
        lede = f"{result.source_name} lists its count as official."
    else:
        title = f"{noun[:1].upper()}{noun[1:]} leads {label} count in a seat {holders} hold"
        lede = ("The count is not final and the lead can change. "
                "Press coverage has not yet confirmed it.")
    against = f" ahead of {_person(runner)}" if runner else ""
    summary = f"{result.source_name}'s count shows {_person(leader)}{against}. {lede}"
    facts = [f"{_person(leader)}: {leader['votes']:,} votes, {leader['pct']}%"]
    if runner:
        facts.append(f"{_person(runner)}: {runner['votes']:,} votes, {runner['pct']}%")
    if d["totalUnits"]:
        share = round(100 * (d["reportingUnits"] or 0) / d["totalUnits"])
        facts.append(f"{d['reportingUnits']:,} of {d['totalUnits']:,} {d['unitLabel']} reporting ({share}%)")
    holder_noun = _PARTY_WORDS.get(result.held_by_party, (result.held_by_party,))[0]
    article = "an" if holder_noun[:1].lower() in "aeiou" else "a"
    facts.append(f"The seat is held by {article} {holder_noun} going into this election")
    race = result.race
    return {
        "title": title[:500],
        "summary": summary,
        "facts": facts,
        "actions": [{
            "text": f"Follow the count for {label}",
            "type": "follow_results",
            "url": f"/elections/states/{race.state}#race-{race.id}",
        }],
    }


def _fill(issue: ActionIssue, result: RaceResult) -> None:
    content = _content(result)
    # Dated the day it last said something: the Action Center lists the
    # newest day's issues, and a flip drafted before midnight Eastern must
    # not drop off the list at the first refresh after it.
    from app.election_phase import election_today

    issue.date = election_today().isoformat()
    facts = json.dumps(content["facts"])
    if issue.facts and issue.facts != facts:
        issue.previous_facts = issue.facts
    issue.title = content["title"]
    issue.summary = content["summary"]
    issue.facts = facts
    issue.fact_sources = json.dumps([result.source_name] * len(content["facts"]))
    issue.actions = json.dumps(content["actions"])
    issue.source_urls = json.dumps([result.source_url] if result.source_url else [])
    issue.source_names = json.dumps([result.source_name])
    issue.primary_source_url = result.source_url


def _create(db: Session, result: RaceResult) -> ActionIssue:
    now = utcnow()
    from app.election_phase import election_today

    today = election_today().isoformat()
    issue = ActionIssue(
        date=today, rank=999, is_current=True, status=ActionIssueStatus.DEVELOPING,
        source_type=SOURCE_TYPE, primary_article_date=today,
        confirmation_deadline=now + timedelta(hours=CONFIRMATION_WINDOW_HOURS),
    )
    _fill(issue, result)
    issue.previous_facts = "[]"
    db.add(issue)
    db.flush()
    result.developing_issue_id = issue.id
    return issue


def update_developing_issues(db: Session, applied: list) -> int:
    """Create, refresh or retire each race's flip issue, from the polls
    sync_state stored (`Applied`s; a held poll never reaches here). Returns
    how many issues were created, retired or brought back.

    A retired issue comes back only on a NEW flip — the lead having
    reverted and then flipped again. Anything else that retired it (the
    Action Center's own refresh retires an unmatched developing issue after
    a day) stays retired: resurrecting on every poll while the flip merely
    held made the issue vanish and reappear every hour."""
    now = utcnow()
    changed = 0
    for outcome in applied:
        result = outcome.result
        if result is None:
            continue
        issue = db.get(ActionIssue, result.developing_issue_id) if result.developing_issue_id else None
        if issue is not None and issue.status != ActionIssueStatus.DEVELOPING:
            continue  # promoted: news coverage owns its content now
        if is_flip(result):
            if issue is None:
                _create(db, result)
                changed += 1
                continue
            if not issue.is_current:
                if not outcome.new_flip:
                    continue
                if issue.confirmation_deadline and issue.confirmation_deadline < now:
                    continue  # expired unconfirmed; not resurrected
                issue.is_current = True
                changed += 1
            _fill(issue, result)
        elif issue is not None and issue.is_current:
            issue.is_current = False
            changed += 1
    return changed
