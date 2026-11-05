"""Which sitting members will still be serving once the held election's
Congress is sworn in: read between election day and January 3, the lame
duck, by the daily member spotlight (pipeline/analyze/bluesky_spotlight.py),
so a post never features a member on their way out.

A member is returning only when the record says so, never by a guess:

  - re-elected: a race on the held ballot names the member as a candidate
    (Candidate.member_bioguide) and that candidate is its settled leader
    (RaceResult.settled_at: projected, or a certified winner) or its
    certified winner;
  - a senator whose seat wasn't up: no Senate race in the state on the held
    ballot, or every one has another member running as its incumbent.

Anyone else (retiring, defeated, a race still open, an open seat in the
senator's state, which could be either senator's) is not returning yet.
That is stricter than the truth for some senators, and only ever leaves a
member out of the pool, never puts one in.
"""

import json
from datetime import date

from sqlalchemy.orm import Session

from app.models import Candidate, Race, RaceCertification, RaceResult, Representative, Senator


def _winners(db: Session, cycle: int, election_day: date, today: date) -> set[str]:
    """Candidate ids the record shows elected, or settled ahead."""
    from app.live_results.certified import certification_json

    day = election_day.isoformat()
    won: set[str] = set()
    for result in db.query(RaceResult).filter(RaceResult.election_date == day, RaceResult.settled_at.isnot(None)):
        tallies = json.loads(result.tallies or "[]")
        if tallies and tallies[0].get("candidateId"):
            won.add(tallies[0]["candidateId"])
    for cert in db.query(RaceCertification).filter(RaceCertification.election_date == day,
                                                   RaceCertification.cycle == cycle):
        winner = (certification_json(cert, today) or {}).get("winner")
        if winner and winner.get("candidateId"):
            won.add(winner["candidateId"])
    return won


def returning(db: Session, election_day: date, today: date) -> set[tuple[str, str]]:
    """(chamber, member id) of every sitting member the record shows serving
    in the Congress the held election seats."""
    cycle = election_day.year
    races = {r.id: r for r in db.query(Race).filter(Race.cycle_year == cycle, Race.is_special.is_(False))}
    candidates = db.query(Candidate).filter(Candidate.race_id.in_(list(races)), Candidate.member_bioguide.isnot(None))
    running: dict[str, list[Candidate]] = {}
    for cand in candidates:
        running.setdefault(cand.member_bioguide, []).append(cand)
    won = _winners(db, cycle, election_day, today)

    out: set[tuple[str, str]] = set()
    for model, chamber in ((Representative, "house"), (Senator, "senate")):
        for member in db.query(model).filter(model.is_current.is_(True)):
            mine = running.get(member.bioguide_id or "", [])
            if any(c.id in won for c in mine):
                out.add((chamber, member.id))
            elif chamber == "senate" and not mine and _seat_not_up(member, races.values(), running):
                out.add((chamber, member.id))
    return out


def _seat_not_up(senator: Senator, races, running: dict[str, list[Candidate]]) -> bool:
    """No Senate race in the senator's state, or each one has another
    member running in it as the incumbent."""
    for race in races:
        if race.office != "S" or race.state != senator.state:
            continue
        incumbent_elsewhere = any(
            c.race_id == race.id and c.incumbent_challenge == "I"
            for bioguide, cands in running.items() if bioguide != senator.bioguide_id for c in cands
        )
        if not incumbent_elsewhere:
            return False
    return True
