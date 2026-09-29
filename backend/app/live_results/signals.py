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
holds; if the lead reverts to the holder's party (or ties) the issue is
retired (is_current=False, never deleted) AND rewritten to say the count no
longer shows a change of party — a retired row still shows on the
homepage's record and at its own address — and it comes back if the flip
does, until its confirmation deadline has passed.
"""

import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import ActionIssue, ActionIssueStatus, RaceResult
from app.pipeline.analyze.early_signal import CONFIRMATION_WINDOW_HOURS
from app.live_results.sync import event_detail, is_flip
from app.pipeline.fetch.district_pvi import STATE_NAMES
from app.time_utils import utcnow

SOURCE_TYPE = "election_results"
# Who held a seat going in is Civitas's record of the sitting member, not a
# figure from the state's count.
HOLDER_SOURCE = "Civitas member records"

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


def publisher(source_name: str) -> str:
    """"Arkansas Secretary of State", from the config's label "Arkansas
    Secretary of State (Tally ENR election-night results)": the trailing
    parenthetical names the vendor system, which a sentence meant for the
    public doesn't need."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", source_name or "").strip() or source_name


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
        lede = f"{publisher(result.source_name)} lists its count as official."
    else:
        title = f"{noun[:1].upper()}{noun[1:]} leads {label} count in a seat {holders} hold"
        lede = ("The count is not final and the lead can change. "
                "Press coverage has not yet confirmed it.")
    against = f" ahead of {_person(runner)}" if runner else ""
    summary = f"{publisher(result.source_name)}'s count shows {_person(leader)}{against}. {lede}"
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
        # Every fact is the state's count but the last: who held the seat
        # comes from Civitas's own member records, not the state's feed.
        "fact_sources": [publisher(result.source_name)] * (len(facts) - 1) + [HOLDER_SOURCE],
        "actions": [{
            "text": f"Follow the count for {label}",
            "type": "follow_results",
            "url": f"/elections/states/{race.state}#race-{race.id}",
        }],
    }


def _reverted_content(result: RaceResult) -> dict:
    """What the issue says once the count no longer shows the seat changing
    party. A retired row still shows on the homepage's record and at its
    own address, so retiring it alone left "leads in a seat Democrats hold"
    standing there after the lead went back."""
    d = event_detail(result)
    leader, runner = d["leader"], d["runnerUp"]
    label = race_label(result.race)
    holders = holders_word(result.held_by_party)
    if leader:
        now = f"{publisher(result.source_name)}'s latest count shows {_person(leader)} ahead."
    else:
        now = f"{publisher(result.source_name)}'s latest count shows the top two tied."
    status = ("The state lists this count as official." if result.official
              else "The count is not final.")
    summary = (f"The count earlier showed a candidate from another party leading in a seat {holders} "
               f"hold. {now} {status}")
    facts = [f"{_person(p)}: {p['votes']:,} votes, {p['pct']}%" for p in (leader, runner) if p]
    if d["totalUnits"]:
        share = round(100 * (d["reportingUnits"] or 0) / d["totalUnits"])
        facts.append(f"{d['reportingUnits']:,} of {d['totalUnits']:,} {d['unitLabel']} reporting ({share}%)")
    race = result.race
    return {
        "title": f"{label[:1].upper()}{label[1:]} count no longer shows a change of party"[:500],
        "summary": summary,
        "facts": facts,
        "actions": [{
            "text": f"Follow the count for {label}",
            "type": "follow_results",
            "url": f"/elections/states/{race.state}#race-{race.id}",
        }],
    }


def _fill(issue: ActionIssue, result: RaceResult, *, content: dict | None = None, touch_date: bool = True) -> None:
    content = content or _content(result)
    # Dated the day it last said something new: the Action Center lists
    # the newest day's issues, and a flip drafted before midnight Eastern
    # must not drop off the list at the first refresh after it. A retired
    # issue's figures are kept current without moving it up the record.
    from app.election_phase import election_today

    if touch_date:
        issue.date = election_today().isoformat()
    facts = json.dumps(content["facts"])
    if issue.facts and issue.facts != facts:
        issue.previous_facts = issue.facts
    issue.title = content["title"]
    issue.summary = content["summary"]
    issue.facts = facts
    issue.fact_sources = json.dumps(
        content.get("fact_sources") or [publisher(result.source_name)] * len(content["facts"])
    )
    issue.actions = json.dumps(content["actions"])
    issue.source_urls = json.dumps([result.source_url] if result.source_url else [])
    issue.source_names = json.dumps([publisher(result.source_name)])
    issue.primary_source_url = result.source_url
    # Stamped with the facts, from the same poll: the page's "count as of"
    # line and OFFICIAL/NOT FINAL tag describe exactly these figures, not
    # a later poll the issue never took (a held one, for instance).
    issue.count_as_of = result.fetched_at
    issue.count_official = bool(result.official)


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
                expired = issue.confirmation_deadline and issue.confirmation_deadline < now
                if not outcome.new_flip or expired:
                    # Stays retired (or expired unconfirmed), but its own
                    # page keeps showing the count as it stands.
                    _fill(issue, result, touch_date=False)
                    continue
                issue.is_current = True
                changed += 1
            _fill(issue, result)
        elif issue is not None:
            # The lead went back (or is tied): say so, on the row the
            # homepage and the issue's own address still show.
            was_current = issue.is_current
            if was_current:
                issue.is_current = False
                changed += 1
            _fill(issue, result, content=_reverted_content(result), touch_date=was_current)
    return changed
