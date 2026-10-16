"""The 2026 general-election map: each seat's structural lean and, where
polls exist, the poll-informed range of its vote margin.

Ranges, not results, and never a race call: the file holds no chance of
anyone winning. Served from /data/election_map.json, which the pipeline
process writes (app.forecast.build). From election day on the map yields
to the results map: both routes answer 404 once active_election() leaves
the campaign phase.

The poll figures and everything built on them are derived from Wikipedia
(Wikipedia contributors) under CC BY-SA 4.0, and every response says so.
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.election_phase import CAMPAIGN, active_election
from app.file_cache import new_reload_lock, reload_if_moved
from app.forecast.build import map_path
from app.forecast.polls import POLL_ATTRIBUTION, POLL_LICENSE, POLL_LICENSE_URL

router = APIRouter(prefix="/elections")

_lock = new_reload_lock()
_cache: dict | None = None
_stamp = None

HIDDEN = "The election map is shown during the campaign only; from election day on, the results map replaces it."
NOT_BUILT = "The election map has not been built yet."


def _load() -> dict | None:
    global _cache, _stamp
    path = map_path()

    def read():
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    with _lock:
        _cache, _stamp = reload_if_moved([path], _cache, _stamp, read)
        return _cache


def current_map() -> dict:
    """The map's data while it may be shown; HTTPException otherwise."""
    election = active_election()
    if election.phase != CAMPAIGN:
        raise HTTPException(status_code=404, detail=HIDDEN)
    data = _load()
    if not data:
        raise HTTPException(status_code=503, detail=NOT_BUILT)
    if data.get("electionDay") != election.election_day.isoformat():
        raise HTTPException(status_code=404, detail=HIDDEN)
    return data


@router.get("/map")
def election_map():
    """Every 2026 Senate seat: its usual vote (structural lean) and, where
    Democrats and Republicans both have a nominee, the poll-informed 80%
    range of the margin, with the polls behind it and the method's
    held-out calibration."""
    return cached_json(current_map(), max_age=CACHE_TTL_LIST_S)


CSV_COLUMNS = (
    "seat", "state", "special", "democrat", "republican", "incumbent_party",
    "usual_vote_lean", "usual_vote_label", "status", "structural_margin", "range80_low", "range80_high",
    "range95_low", "range95_high", "poll_average", "polls_used", "polls_listed", "latest_poll",
    "differs_from_usual", "difference_direction",
)


def csv_text(data: dict) -> str:
    out = io.StringIO()
    out.write(f"# Civitas 2026 general-election map, as of {data['asOf']}. Ranges, not results. Not a race call.\n")
    out.write(f"# Margins are Republican minus Democratic share of the two-party vote, in points (negative = D).\n")
    out.write(f"# Usual vote: Civitas's computed lean from official returns ({data['sources']['lean']['file']}).\n")
    out.write(f"# Polls: {POLL_ATTRIBUTION}, {POLL_LICENSE} ({POLL_LICENSE_URL}). The poll-derived columns are "
              f"shared under the same licence; reuse them with attribution and under {POLL_LICENSE}.\n")
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for s in data["seats"]:
        ranged = s["status"] == "range"
        w.writerow([
            s["id"], s["state"], s["special"], s.get("dem") or "", s.get("rep") or "", s.get("incumbentParty") or "",
            s["lean"], s["leanLabel"], s["status"],
            s.get("prior", "") if ranged else "",
            *(s["range80"] if ranged else ("", "")), *(s["range95"] if ranged else ("", "")),
            s.get("pollAverage") if ranged and s.get("pollAverage") is not None else "",
            s["polls"]["used"] if ranged else "", s.get("polls", {}).get("count", ""),
            (s["polls"].get("latest") or "") if ranged else "",
            s["difference"]["strength"] if ranged else "",
            (s["difference"]["direction"] or "") if ranged else "",
        ])
    return out.getvalue()


@router.get("/map.csv")
def election_map_csv():
    """The map as a table, one row per seat, with the licence notice."""
    data = current_map()
    return Response(
        content=csv_text(data), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": f"public, max-age={CACHE_TTL_LIST_S}, stale-while-revalidate={CACHE_TTL_LIST_S}",
                 "Content-Disposition": 'attachment; filename="civitas-2026-election-map.csv"'},
    )
