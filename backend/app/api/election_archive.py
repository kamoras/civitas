"""Past general elections, as Civitas recorded them (app/election_archive.py):
the list of archived cycles, each cycle's record (federal ballot, counts,
statewide measures) and its frozen pre-election map.

Served for as long as the row exists, whatever the election phase: this is
what stays after /api/elections/results empties and the map moves on to
the next cycle. Everything is copied from the sources the live pages read;
nothing here is written by a model, and no race is called.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.election_map import csv_text
from app.api.elections import phase_cache_s
from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.election_phase import active_election
from app.models import ElectionArchive

router = APIRouter(prefix="/elections/archive")

# A cycle is a four-digit year: the path's shape, so a race id or anything
# else under /elections/archive/ is a 404 before any query.
CYCLE = Path(..., ge=1000, le=9999, description="The general election's year")


def _row(db: Session, cycle: int) -> ElectionArchive:
    row = db.get(ElectionArchive, cycle)
    if row is None or row.record_json is None:
        raise HTTPException(status_code=404, detail="No archive for that election")
    return row


def _iso(dt) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _counts(record: dict) -> dict:
    results = [r["result"] for r in record["races"] if r["result"]]
    return {
        "races": len(record["races"]),
        "counted": len(results),
        "official": sum(1 for r in results if r["official"]),
        "measures": len(record["measures"]),
        # Statewide offices, legislative seats and judgeships, each counted
        # once (a record copied before these were kept has none).
        "stateContests": sum(
            len(c["statewideRaces"]) + sum(len(ch["districts"]) for ch in c["stateLegRaces"])
            + sum(len(ct["seats"]) for ct in c["judicialRaces"])
            for c in (s.get("stateContests") for s in record["states"]) if c
        ),
        "states": len(record["states"]),
    }


@router.get("")
def archive_index(db: Session = Depends(get_db)):
    """Every archived general election, newest first, with what each holds;
    and the site's current election, so an empty list can say when the next
    archive starts (from that election's day)."""
    election = active_election(db)
    cycles = []
    for row in db.query(ElectionArchive).order_by(ElectionArchive.cycle.desc()):
        if row.record_json is None:
            continue
        cycles.append({
            "cycle": row.cycle, "electionDay": row.election_day,
            "recordAsOf": _iso(row.record_as_of), "mapAsOf": _iso(row.map_as_of),
            "hasMap": row.map_json is not None,
            # Still being copied: the site is on this election's count.
            "stillCounting": election.cycle == row.cycle and election.shows_results,
            **_counts(json.loads(row.record_json)),
        })
    return cached_json({
        "cycles": cycles,
        "current": {"cycle": election.cycle, "electionDay": election.election_day.isoformat(),
                    "phase": election.phase},
    }, max_age=phase_cache_s(election))


@router.get("/{cycle}")
def archive_record(cycle: int = CYCLE, db: Session = Depends(get_db)):
    """One election's record: each federal race with the candidates on its
    ballot and its count as the state reported it (`result`, the live
    results' shape; null where no count was read), and the statewide
    measures, verbatim with their drafters."""
    row = _row(db, cycle)
    election = active_election(db)
    record = json.loads(row.record_json)
    return cached_json(dict(
        record,
        recordAsOf=_iso(row.record_as_of), mapAsOf=_iso(row.map_as_of), hasMap=row.map_json is not None,
        stillCounting=election.cycle == cycle and election.shows_results,
        totals=_counts(record),
    ), max_age=phase_cache_s(election))


def _map(db: Session, cycle: int) -> dict:
    row = _row(db, cycle)
    if row.map_json is None:
        raise HTTPException(status_code=404, detail="No pre-election map was kept for that election")
    # Frozen: never "stale", however old (api/election_map.STALE_AFTER is
    # for a map that should still be refreshing).
    return dict(json.loads(row.map_json), cycle=cycle, stale=False, frozen=True)


@router.get("/{cycle}/map")
def archive_map(cycle: int = CYCLE,
                chamber: str | None = Query(None, pattern="^(senate|house)$",
                                            description="Only this chamber's seats"),
                db: Session = Depends(get_db)):
    """The election's map as its last campaign build left it, in
    GET /api/elections/map's shape, with `frozen` true."""
    data = _map(db, cycle)
    chambers = {name: c for name, c in data["chambers"].items() if chamber in (None, name)}
    return cached_json(dict(data, chambersAvailable=list(data["chambers"]), chambers=chambers),
                       max_age=CACHE_TTL_LIST_S)


@router.get("/{cycle}/map.csv")
def archive_map_csv(cycle: int = CYCLE, db: Session = Depends(get_db)):
    """The frozen map as a table, one row per seat, with the licence notice."""
    data = _map(db, cycle)
    return Response(
        content=csv_text(data), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": f"public, max-age={CACHE_TTL_LIST_S}, stale-while-revalidate={CACHE_TTL_LIST_S}",
                 "Content-Disposition": f'attachment; filename="civitas-{cycle}-election-map-final.csv"'},
    )
