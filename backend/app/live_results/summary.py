"""The live count, one entry per state, for the homepage while results
are up (election_phase.active_election().shows_results).

Like every sentence about the count (signals.py, bluesky.py), each line
here is a fixed template around the state's own figures, never model
text. It says "leads", with the count "not final" until the state lists
it as official, and never "wins" after: Civitas never calls a race.
"""

from collections import Counter

from sqlalchemy.orm import Session

from app.election_phase import ActiveElection
from app.live_results.signals import holders_word, party_letter, publisher, race_label, reporting_line
from app.live_results.sync import event_detail
from app.models import LiveResultRead, Race, RaceResult
from app.state_names import STATE_NAMES

# The last read of a state's feed went wrong (LiveResultRead.status): the
# figures shown are the last good read's, and the entry says so.
_READ_FAILED = frozenset({"untrusted", "unavailable", "stale", "failed"})


def _who(person: dict) -> str:
    letter = party_letter(person.get("party"))
    return f"{person['name']} ({letter})" if letter else person["name"]


def _race_line(race: Race, result: RaceResult) -> str:
    d = event_detail(result)
    leader, label = d["leader"], race_label(race)
    if leader is None:
        return f"{label}: the count is tied."
    share = f" with {leader['pct']}%" if leader.get("pct") is not None else ""
    if result.official:
        return f"{label}: {_who(leader)} leads{share} in the count the state lists as official."
    reporting = reporting_line(d)
    return f"{label}: {_who(leader)} leads{share}." + (f" {reporting}." if reporting else "")


def _house_line(results: list[RaceResult]) -> str:
    """"6 U.S. House races counted: Democrats lead in 4, Republicans in 2."."""
    leads: Counter[str] = Counter()
    tied = 0
    for result in results:
        leader = event_detail(result)["leader"]
        if leader is None:
            tied += 1
        else:
            leads[leader.get("party") or ""] += 1
    parts = []
    for i, (party, n) in enumerate(leads.most_common()):
        words = holders_word(party) if party_letter(party) else "other candidates"
        parts.append(f"{words} lead in {n}" if i == 0 else f"{words} in {n}")
    if tied:
        parts.append(f"{tied} {'is' if tied == 1 else 'are'} tied")
    return f"{len(results)} U.S. House races counted: {', '.join(parts)}."


def state_summaries(db: Session, election: ActiveElection) -> list[dict]:
    """Every state with votes counted for the election, the most recently
    changed first: its sentences, how many of its counts the state lists
    as official, and where its results are. Empty outside the results
    window."""
    if not election.shows_results:
        return []
    day = election.election_day.isoformat()
    rows = (
        db.query(RaceResult, Race).join(Race, Race.id == RaceResult.race_id)
        .filter(RaceResult.election_date == day, RaceResult.votes_counted > 0)
        .all()
    )
    reads = dict(db.query(LiveResultRead.state, LiveResultRead.status).filter(LiveResultRead.election_date == day))
    by_state: dict[str, list[tuple[RaceResult, Race]]] = {}
    for result, race in rows:
        by_state.setdefault(race.state, []).append((result, race))
    entries = []
    for state, counted in by_state.items():
        counted.sort(key=lambda rr: (rr[1].office != "S", rr[1].is_special, rr[1].district or 0))
        senate = [(r, race) for r, race in counted if race.office == "S"]
        house = [(r, race) for r, race in counted if race.office != "S"]
        lines = [_race_line(race, r) for r, race in senate]
        if len(house) == 1:
            lines.append(_race_line(house[0][1], house[0][0]))
        elif house:
            lines.append(_house_line([r for r, _ in house]))
        official = sum(1 for r, _ in counted if r.official)
        if 0 < official < len(counted):
            lines.append(f"The state lists {official} of these {len(counted)} counts as official; "
                         "the rest are not final.")
        if reads.get(state) in _READ_FAILED:
            lines.append("The last check of the state's feed failed: these are the figures from its last good read.")
        latest = max(r.last_change_at for r, _ in counted)
        entries.append({
            "state": state,
            "stateName": STATE_NAMES.get(state, state),
            "racesCounted": len(counted),
            "official": official,
            "status": "official" if official == len(counted) else "not_final",
            "lines": lines,
            "source": publisher(counted[0][0].source_name),
            "lastChangeAt": latest.isoformat() + "Z",
            "href": f"/elections/states/{state}",
        })
    entries.sort(key=lambda e: e["lastChangeAt"], reverse=True)
    return entries

