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
frame around the state's own figures, and says "leads" — even once the
state lists its count as official. Civitas never calls a race: an
official count's leader can still face a runoff (Georgia requires a
majority in the general), a recount or a court, and "wins" is a call.

The issue follows the count. Each sync refreshes its facts while the
challenger leads; if the lead reverts to the holder's party (or ties) the
issue is retired (is_current=False) AND rewritten to say the count no
longer shows a change of party — a retired row still shows on the
homepage's record and at its own address. Only the lead going back does
that (sync.lead_is_back): the count dipping below the bar for RAISING a
flip, with the same challenger ahead, leaves the issue as it stands. If the
seat flips again after a reversal, that is a new issue, drafted fresh; the
old one keeps its record of the reversal.
"""

import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import ActionIssue, ActionIssueStatus, RaceResult
from app.pipeline.analyze.early_signal import CONFIRMATION_WINDOW_HOURS
from app.live_results.sync import challenger_leads, event_detail, is_flip, lead_is_back
from app.state_names import STATE_NAMES
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
    """"Democrats" — who hold(s) a seat, for "a seat Democrats hold". A
    holder is always one of the words (sync._MEMBER_PARTY); a code this
    has no words for is never printed raw."""
    return _PARTY_WORDS.get(group or "", (None, None))[1] or "another party's members"


def party_noun(group: str | None) -> str:
    """"Republican" — one candidate's party, as a sentence's subject. A
    feed's party code this has no words for (United Citizen's "UC", a
    state's "N" or "OTH") is never printed raw in a title."""
    return _PARTY_WORDS.get(group or "", (None, None))[0] or "a candidate from another party"


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


def reporting_line(d: dict) -> str:
    """"60 of 100 precincts reporting (60%)", or "" unless the count states
    both figures: a feed can give its units' total with no reporting
    figure (Clarity's PR missing for a poll), and formatting that None
    failed the whole state's sync on every pass."""
    total, reporting = d.get("totalUnits"), d.get("reportingUnits")
    if not total or reporting is None:
        return ""
    share = round(100 * reporting / total)
    return f"{reporting:,} of {total:,} {d.get('unitLabel') or 'precincts'} reporting ({share}%)"


def _person(p: dict) -> str:
    letter = party_letter(p.get("party"))
    return f"{p['name']} ({letter})" if letter else p["name"]


def _content(result: RaceResult) -> dict:
    d = event_detail(result)
    leader, runner = d["leader"], d["runnerUp"]
    noun = party_noun(leader["party"])
    holders = holders_word(result.held_by_party)
    label = race_label(result.race)
    if result.official:
        # Never "wins": an official count's leader has not necessarily won
        # (a Georgia general needs a majority, or goes to a runoff), and
        # Civitas never calls a race.
        title = f"{noun[:1].upper()}{noun[1:]} leads {label} in the count the state lists as official, " \
                f"in a seat {holders} hold"
        lede = (f"{publisher(result.source_name)} lists its count as official. "
                "Civitas does not call races.")
    else:
        title = f"{noun[:1].upper()}{noun[1:]} leads {label} count in a seat {holders} hold"
        lede = ("The count is not final and the lead can change. "
                "Press coverage has not yet confirmed it.")
    against = f" ahead of {_person(runner)}" if runner else ""
    summary = f"{publisher(result.source_name)}'s count shows {_person(leader)}{against}. {lede}"
    facts = [f"{_person(leader)}: {leader['votes']:,} votes, {leader['pct']}%"]
    if runner:
        facts.append(f"{_person(runner)}: {runner['votes']:,} votes, {runner['pct']}%")
    if reporting_line(d):
        facts.append(reporting_line(d))
    holder_noun = _PARTY_WORDS.get(result.held_by_party or "", (None,))[0]
    if holder_noun:
        article = "an" if holder_noun[:1].lower() in "aeiou" else "a"
        facts.append(f"The seat is held by {article} {holder_noun} going into this election")
    else:
        facts.append("The seat is held by another party going into this election")
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


def _first_row(result: RaceResult) -> dict | None:
    """The count's top row, as event_detail words a person — for a tie,
    where event_detail names no leader."""
    tallies = json.loads(result.tallies or "[]")
    if not tallies:
        return None
    row, counted = tallies[0], result.votes_counted or 0
    return {"name": row["name"], "party": row.get("party"), "votes": row["votes"],
            "pct": round(100 * row["votes"] / counted, 1) if counted else None}


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
    # On a tie there is no leader, but the summary says "the top two tied":
    # list both of them, not only the runner-up.
    top = leader or _first_row(result)
    facts = [f"{_person(p)}: {p['votes']:,} votes, {p['pct']}%" for p in (top, runner) if p]
    if reporting_line(d):
        facts.append(reporting_line(d))
    race = result.race
    return {
        "title": f"{label[:1].upper()}{label[1:]}{_REVERTED_TITLE_END}"[:500],
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


# The fixed end of _reverted_content's title (the start is the race's
# label, whose wording can change between deploys).
_REVERTED_TITLE_END = " count no longer shows a change of party"


def _says_reverted(issue: ActionIssue) -> bool:
    return (issue.title or "").endswith(_REVERTED_TITLE_END)


def update_developing_issues(db: Session, applied: list) -> int:
    """Create, refresh or retire each race's flip issue, from the polls
    sync_state stored (`Applied`s; a held poll never reaches here). Returns
    how many issues were created or retired.

    A NEW flip — the lead having reverted and then flipped again — opens a
    new issue; the retired one keeps its record. Anything else that retired
    an issue (the Action Center's own refresh retires an unmatched
    developing issue a day after it was created) leaves it retired, its
    figures kept current: resurrecting on every poll while the flip merely
    held made the issue vanish and reappear every hour."""
    changed = 0
    for outcome in applied:
        result = outcome.result
        if result is None:
            continue
        issue = db.get(ActionIssue, result.developing_issue_id) if result.developing_issue_id else None
        if issue is not None and issue.status != ActionIssueStatus.DEVELOPING:
            continue  # promoted: news coverage owns its content now
        if result.held_by_party is None or not (result.votes_counted or 0):
            # Nothing to judge a flip or its reversal by: no known holder,
            # or a feed listing zeros for a moment.
            continue
        if issue is None:
            # A race whose link names a row that no longer exists had an
            # issue: the Action Center's cleanup deletes day-old unposted
            # issues after 14 days, and a retired flip's date is frozen. A
            # slow count (Washington, Utah) outlasts that, and redrafting it
            # on the next pass told a two-week-old flip as news. Only a flip
            # this poll announced is a new story then.
            if is_flip(result) and (result.developing_issue_id is None or outcome.new_flip):
                _create(db, result)
                changed += 1
            continue
        if lead_is_back(result):
            # The lead went back (or is tied): say so, on the row the
            # homepage and the issue's own address still show.
            was_current = issue.is_current
            if was_current:
                issue.is_current = False
                changed += 1
            _fill(issue, result, content=_reverted_content(result), touch_date=was_current)
            continue
        if not challenger_leads(result):
            continue  # a leader of no known party: the issue stays as it stands
        if not issue.is_current:
            if _says_reverted(issue):
                if outcome.new_flip:
                    # A flip after a reversal is a new story, drafted fresh:
                    # the Action Center's refresh retires an unmatched
                    # developing row a day after it was CREATED, so reviving
                    # the old one after that lived only until the next hour.
                    # The old row keeps saying the count no longer showed a
                    # change of party — which it didn't, then.
                    _create(db, result)
                    changed += 1
                # Not yet announced again (too little of the count in): the
                # reverted row keeps its word rather than being rewritten
                # as a flip nobody announced.
                continue
            # Retired while the flip held: stays retired, but its own page
            # keeps showing the count as it stands.
            _fill(issue, result, touch_date=False)
            continue
        # Current: refreshed while the challenger leads, whether or not the
        # count still clears the bar for RAISING a flip (sync.is_flip) —
        # what it says ("leads", "not final") stays true either way.
        _fill(issue, result)
    return changed
