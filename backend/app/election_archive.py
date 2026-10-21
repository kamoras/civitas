"""Each held general election's record, kept after the site moves on.

The site's election is the next one again once the results window closes
(election_phase), and from then on nothing it read about the last one is
safe: the map's file is overwritten by the next cycle's first build
(forecast/build.map_path), past elections' measures are pruned
(election_pipeline._prune_past_measures), and a data reset outside the
window wipes the races, candidates and counts. So from election day until
the window closes, every pass of the live-results sync copies them here
(freeze), into a table a reset keeps:

  map     the map's file as the campaign's last build left it. The build
          stops on election day (forecast/build.build), so the file on disk
          then is the final pre-election map. Copied once, never replaced.
  record  the federal races with the candidates on the ballot, each race's
          count as the state reported it, the statewide offices, state
          legislative seats and judgeships with their nominees (the state
          ballot page's own sections), and the statewide measures, quoted. Re-copied on every pass while the count may still move,
          and frozen by the window's close, after which nothing calls this.

Everything in it is copied, never written: candidate names as the state
printed them, measures verbatim with their drafters (AGENTS.md §7), counts
as the source gave them, "leading" and never "won".
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session, selectinload

from app.api.elections import (
    _ballot_basis_markers,
    _candidate_source,
    _candidate_summary,
    _confirmed_or_all,
    _judicial_marker,
    _judicial_section,
    _measure_json,
    _measure_order,
    _race_complete,
    _result_json,
    _state_leg_section,
    _statewide_marker,
    _statewide_section,
    ballot_state_codes,
)
from app.forecast.build import map_path
from app.live_results.sync import redrawn_states
from app.models import BallotMeasure, ElectionArchive, LiveResultRead, Race, RaceResult
from app.ops_alerts import send_ops_alert
from app.pipeline.fetch.election_results import live_results_states, ranked_choice_states
from app.state_names import STATE_NAMES
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# The candidate fields the archive shows: who was on the ballot, as printed,
# and in which party. Campaign-finance figures moved on after the election
# and are the FEC's to serve; a race's own page linked them while it ran.
CANDIDATE_FIELDS = ("id", "name", "ballotName", "displayName", "party", "partyGroup", "partyLabel", "confirmed",
                    "incumbentChallenge")


def held_map(election_day: date) -> dict | None:
    """The map's file, if it is this election's (its last campaign build),
    without the full poll rows the API never serves either."""
    try:
        with open(map_path()) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("electionDay") != election_day.isoformat():
        return None
    for chamber in data.get("chambers", {}).values():
        for seat in chamber.get("seats", []):
            if "polls" in seat:
                seat["polls"] = {k: v for k, v in seat["polls"].items() if k != "all"}
    return data


def _without(rows: list[dict], key: str) -> list[dict]:
    return [{k: v for k, v in r.items() if k != key} for r in rows]


def state_contests(db: Session, state: str, cycle: int) -> dict | None:
    """The state's own contests as its ballot page showed them: statewide
    offices, legislative seats and judgeships, each with its nominees and
    whose list they came from. None when the state had none on file. The
    place lists a reader filters by (seat `areas`, district `towns`) are
    the page's finding aid, rebuilt from the crosswalks, not ballot
    content, and are left out."""
    statewide, statewide_cov = _statewide_section(db, state, cycle)
    legislature = _state_leg_section(db, state, cycle, _statewide_marker(db, state, cycle))
    judicial, judicial_cov = _judicial_section(db, state, cycle, _judicial_marker(db, state, cycle))
    if not (statewide or legislature or judicial):
        return None
    return {
        "statewideRaces": _without(statewide, "areas"),
        "stateLegRaces": [dict(c, districts=_without(c["districts"], "towns")) for c in legislature],
        "judicialRaces": judicial,
        # Whose list the names are, and whether it was the state's
        # November ballot list or primary results (which can miss an
        # unopposed nominee): the legislature shares the statewide marker.
        "statewideSource": {"sourceName": statewide_cov["sourceName"], "ballotList": statewide_cov["ballotList"]},
        "judicialSource": {"sourceName": judicial_cov["sourceName"], "ballotList": judicial_cov["ballotList"]},
    }


def build_record(db: Session, election_day: date) -> dict:
    """The held election's federal ballot, its counts and its statewide
    measures, in the shapes the live pages already use."""
    cycle = election_day.year
    day = election_day.isoformat()
    races = (db.query(Race).options(selectinload(Race.candidates))
             .filter(Race.cycle_year == cycle, Race.office.in_(("S", "H")))
             .order_by(Race.state, Race.office.desc(), Race.district, Race.id).all())
    results = {r.race_id: r for r in db.query(RaceResult).filter(RaceResult.election_date == day)}
    last_ok = dict(db.query(LiveResultRead.state, LiveResultRead.last_ok_at)
                   .filter(LiveResultRead.election_date == day))
    markers = _ballot_basis_markers(db, cycle)
    out_races = []
    for race in races:
        complete = _race_complete(markers.get(race.state), race.state, race.id)
        on_ballot = _confirmed_or_all(race.candidates, race.state, complete)
        result = results.get(race.id)
        out_races.append({
            "id": race.id, "state": race.state, "office": race.office, "district": race.district,
            "isSpecial": bool(race.is_special),
            "candidateSource": _candidate_source(race.candidates, complete),
            "candidates": [{k: v for k, v in _candidate_summary(c).items() if k in CANDIDATE_FIELDS}
                           for c in on_ballot],
            "result": _result_json(result, race, last_ok.get(race.state)) if result else None,
        })
    measures = sorted(db.query(BallotMeasure).filter(BallotMeasure.election_date == day).all(),
                      key=lambda m: (m.state, _measure_order(m)))
    contests = {st: c for st in sorted(ballot_state_codes()) if (c := state_contests(db, st, cycle))}
    states = sorted({r["state"] for r in out_races} | {m.state for m in measures} | set(contests))
    return {
        "cycle": cycle,
        "electionDay": day,
        "states": [{"state": st, "stateName": STATE_NAMES.get(st, st),
                    # Whose list the candidates are (the ballot-basis marker's
                    # source); null where no state list was read.
                    "candidateListSource": (markers.get(st) or {}).get("sourceName") or None,
                    "stateContests": contests.get(st)}
                   for st in states],
        "liveStates": sorted(live_results_states()),
        "rankedChoiceStates": sorted(ranked_choice_states()),
        "redrawnStates": sorted(redrawn_states(cycle)),
        "races": out_races,
        "measures": [_measure_json(m) for m in measures],
    }


def freeze(db: Session, election_day: date) -> ElectionArchive | None:
    """Copy the held election's map (once) and record (each pass) into its
    archive row. Called by the live-results sync, inside its lease, only
    while that election's results are on show; a database with no races for
    it (wiped, or never built) never replaces a record already copied."""
    cycle = election_day.year
    row = db.get(ElectionArchive, cycle)
    if row is None:
        row = ElectionArchive(cycle=cycle, election_day=election_day.isoformat())
    if row.map_json is None:
        snapshot = held_map(election_day)
        if snapshot is not None:
            row.map_json = json.dumps(snapshot, separators=(",", ":"))
            row.map_as_of = datetime.fromisoformat(snapshot["asOf"].replace("Z", "+00:00")).replace(tzinfo=None)
    record = build_record(db, election_day)
    if record["races"] or any(s["stateContests"] for s in record["states"]):
        row.record_json = json.dumps(record, separators=(",", ":"))
        row.record_as_of = utcnow()
    if row.map_json is None:
        # The map is copied from its file on the first pass after the
        # campaign; once the next cycle's build overwrites it, it is gone.
        send_ops_alert(f"Election archive {cycle}: no pre-election map",
                       f"The {cycle} archive found no map for {election_day.isoformat()} at {map_path()}, so the "
                       "archive will have no pre-election map unless the file is restored before the next "
                       "cycle's first build.",
                       dedupe_key=f"election-archive-map-{cycle}", condition="election-archive-map")
    if row.map_json is None and row.record_json is None:
        return None
    db.add(row)
    db.commit()
    logger.info("Election archive %d: %d races, map %s", cycle, len(record["races"]),
                "kept" if row.map_json else "missing")
    return row
